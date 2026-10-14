# -*- coding: utf-8 -*-
"""SQLite 历史库:阶段03 完整表结构 + FTS5 全文检索。

阶段02 仅有 sources / items 两张表;阶段03 补齐(表结构按阶段01审计报告 E.2、
方案文档第五节):
  - sources          来源配置(阶段02 已有,保持不变)
  - items            情报条目(阶段02 已有,保持不变)
  - item_sources     同一事件被多个来源发现的跨源映射
  - project_snapshots GitHub 项目每日快照(star_snapshots 思路并入,PK=repo+date)
  - topic_daily_stats 每天每主题条目数/来源数/代表情报
  - runs             每次运行的开始/结束/状态/错误
  - daily_reports    日报与发送状态(阶段04 使用,本阶段建表)
  - items_fts        FTS5 external-content 虚拟表 + 触发器(items_ai/items_ad/items_au)

对已存在的阶段02 数据库自动迁移:新表用 CREATE TABLE IF NOT EXISTS;items_fts
在连接时对存量 items 执行 rebuild,保证旧数据可检索。
"""
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  category TEXT,
  enabled INTEGER DEFAULT 1,
  last_fetch_at TEXT,
  last_error TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  source_id INTEGER REFERENCES sources(id),
  platform TEXT,
  title TEXT NOT NULL,
  url TEXT UNIQUE NOT NULL,
  summary TEXT,
  published_at TEXT,
  content_hash INTEGER,
  category TEXT,
  extra TEXT,
  first_seen_at TEXT,
  last_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS item_sources (
  item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
  source_id INTEGER REFERENCES sources(id),
  source_url TEXT,
  seen_at TEXT,
  PRIMARY KEY (item_id, source_id)
);
CREATE TABLE IF NOT EXISTS project_snapshots (
  repo TEXT NOT NULL,
  date TEXT NOT NULL,
  stars INTEGER,
  forks INTEGER,
  pushed_at TEXT,
  created_at TEXT,
  description TEXT,
  language TEXT,
  topics TEXT,                 -- JSON 数组(≤5)
  fetched_at TEXT,
  PRIMARY KEY (repo, date)
);
CREATE TABLE IF NOT EXISTS topic_daily_stats (
  stat_date TEXT NOT NULL,
  topic TEXT NOT NULL,
  count INTEGER DEFAULT 0,
  source_count INTEGER DEFAULT 0,
  representative_item_id INTEGER,
  PRIMARY KEY (stat_date, topic)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  run_date TEXT,
  started_at TEXT,
  finished_at TEXT,
  status TEXT,                 -- running / ok / partial / failed
  per_source_stats TEXT,       -- JSON
  errors TEXT,                 -- JSON
  items_inserted INTEGER DEFAULT 0,
  items_updated INTEGER DEFAULT 0,
  trends TEXT                  -- JSON(趋势输出)
);
CREATE TABLE IF NOT EXISTS daily_reports (
  report_date TEXT PRIMARY KEY,
  body TEXT,
  generated_at TEXT,
  sent_status TEXT DEFAULT 'pending',
  sent_at TEXT,
  retry_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_platform ON items(platform);
CREATE INDEX IF NOT EXISTS idx_items_published_at ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_first_seen ON items(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_date ON project_snapshots(date);
CREATE INDEX IF NOT EXISTS idx_snapshots_repo ON project_snapshots(repo);
CREATE INDEX IF NOT EXISTS idx_topic_stats_topic ON topic_daily_stats(topic);
CREATE INDEX IF NOT EXISTS idx_topic_stats_date ON topic_daily_stats(stat_date);
CREATE INDEX IF NOT EXISTS idx_item_sources_item ON item_sources(item_id);
"""

# FTS5 external-content 表:索引 items 的 title/summary,触发器保持同步
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
  title, summary, content=items, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(rowid, title, summary) VALUES (new.id, new.title, new.summary);
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, title, summary)
  VALUES ('delete', old.id, old.title, old.summary);
END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, title, summary)
  VALUES ('delete', old.id, old.title, old.summary);
  INSERT INTO items_fts(rowid, title, summary) VALUES (new.id, new.title, new.summary);
END;
"""


def utcnow_iso():
    """UTC 当前时间 ISO(微秒精度,与阶段02 一致)。"""
    return datetime.now(timezone.utc).isoformat()


def match_keywords(text, keywords):
    """返回 text 中命中的关键词列表(大小写不敏感)。"""
    if not text:
        return []
    lower = text.lower()
    return [kw for kw in (keywords or []) if kw and kw in lower]


class Database:
    def __init__(self, db_path):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript(SCHEMA)
        self.fts_available = False
        try:
            self.conn.executescript(FTS_SCHEMA)
            # 存量 items 需要一次 rebuild 才进入索引(审计实测:external-content + 触发器可用)
            n_items = self.conn.execute('SELECT count(*) FROM items').fetchone()[0]
            if n_items:
                self.conn.execute("INSERT INTO items_fts(items_fts) VALUES ('rebuild')")
            self.conn.commit()
            self.fts_available = True
        except sqlite3.OperationalError as e:
            logging.error(f'FTS5 不可用,全文检索降级: {e}')
            self.conn.rollback()
        self.conn.commit()
        logging.info(f'SQLite 就绪: {db_path}(FTS5={"可用" if self.fts_available else "降级"})')

    # ---------- sources / items(阶段02,保持兼容) ----------
    def upsert_source(self, url, name, category, last_fetch_at=None, last_error=None):
        self.conn.execute(
            '''INSERT INTO sources (url, name, category, enabled, last_fetch_at, last_error)
               VALUES (?, ?, ?, 1, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                 name=excluded.name, category=excluded.category, enabled=1,
                 last_fetch_at=excluded.last_fetch_at, last_error=excluded.last_error''',
            (url, name, category, last_fetch_at, last_error),
        )
        self.conn.commit()
        return self.get_source_id(url)

    def get_source_id(self, url):
        row = self.conn.execute('SELECT id FROM sources WHERE url=?', (url,)).fetchone()
        return row['id'] if row else None

    def upsert_item(self, platform, title, url, summary, published_at,
                    content_hash, category, extra, source_id,
                    source_url=None, first_seen_at=None, seen_at=None):
        """统一字段入库;url 冲突时更新 last_seen_at。

        同时维护 item_sources(跨源映射)。可选覆盖 first_seen_at/seen_at(演示/回填用)。

        返回 (action, item_id),action ∈ {'inserted', 'updated'}。
        """
        now = seen_at or utcnow_iso()
        fsa = first_seen_at or now
        existed = self.conn.execute(
            'SELECT id FROM items WHERE url=?', (url,)).fetchone()
        self.conn.execute(
            '''INSERT INTO items (source_id, platform, title, url, summary,
                                  published_at, content_hash, category, extra,
                                  first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET last_seen_at=excluded.last_seen_at''',
            (source_id, platform, title, url, summary, published_at,
             content_hash, category, json.dumps(extra or {}, ensure_ascii=False),
             fsa, now),
        )
        row = self.conn.execute('SELECT id FROM items WHERE url=?', (url,)).fetchone()
        item_id = row['id']
        if source_id is not None:
            self.conn.execute(
                '''INSERT INTO item_sources (item_id, source_id, source_url, seen_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(item_id, source_id) DO UPDATE SET
                     source_url=excluded.source_url, seen_at=excluded.seen_at''',
                (item_id, source_id, source_url or '', now),
            )
        self.conn.commit()
        action = 'updated' if existed else 'inserted'
        return action, item_id

    def count_items(self):
        return self.conn.execute('SELECT count(*) FROM items').fetchone()[0]

    def count_sources(self):
        return self.conn.execute('SELECT count(*) FROM sources').fetchone()[0]

    # ---------- project_snapshots ----------
    def upsert_snapshot(self, repo, date, stars, forks, pushed_at,
                        created_at=None, description=None, language=None,
                        topics=None, fetched_at=None):
        """写入/更新某仓库某日快照(PK=repo+date,重复运行幂等)。"""
        self.conn.execute(
            '''INSERT INTO project_snapshots
                 (repo, date, stars, forks, pushed_at, created_at,
                  description, language, topics, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(repo, date) DO UPDATE SET
                 stars=excluded.stars, forks=excluded.forks,
                 pushed_at=excluded.pushed_at, created_at=excluded.created_at,
                 description=excluded.description, language=excluded.language,
                 topics=excluded.topics, fetched_at=excluded.fetched_at''',
            (repo, date, stars, forks, pushed_at, created_at, description,
             language, json.dumps(topics or [], ensure_ascii=False), fetched_at),
        )
        self.conn.commit()

    def snapshot_rows(self, repo):
        """某仓库全部快照,按 date 升序。"""
        return self.conn.execute(
            'SELECT * FROM project_snapshots WHERE repo=? ORDER BY date',
            (repo,)).fetchall()

    def snapshot_repos_on(self, date):
        """在指定日期有快照的仓库列表。"""
        return [r['repo'] for r in self.conn.execute(
            'SELECT DISTINCT repo FROM project_snapshots WHERE date=? ORDER BY repo',
            (date,)).fetchall()]

    def has_snapshot_before(self, date):
        """是否存在严格早于 date 的快照(用于判断是否首日基线)。"""
        row = self.conn.execute(
            'SELECT count(*) AS n FROM project_snapshots WHERE date < ?',
            (date,)).fetchone()
        return row['n'] > 0

    # ---------- topic_daily_stats ----------
    def upsert_topic_stat(self, stat_date, topic, count, source_count,
                          representative_item_id=None):
        self.conn.execute(
            '''INSERT INTO topic_daily_stats
                 (stat_date, topic, count, source_count, representative_item_id)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(stat_date, topic) DO UPDATE SET
                 count=excluded.count, source_count=excluded.source_count,
                 representative_item_id=excluded.representative_item_id''',
            (stat_date, topic, count, source_count, representative_item_id),
        )
        self.conn.commit()

    def compute_topic_stats(self, stat_date, keywords, max_topics=3):
        """按日期汇总主题统计(条目数/来源数/代表情报)。

        计数口径:items.first_seen_at 落在 stat_date 当天首次出现的条目。
        主题来源:
          - GitHub:   extra.topics(取前 max_topics,无 topics 归入 opensource)
          - 其他平台: 标题/摘要命中关键词;无命中归入 category/platform。
        代表情报 = 该主题当天互动最高条目(Star/Pointer 最高,平手取最小 id)。
        返回写入的主题数。
        """
        rows = self.conn.execute(
            'SELECT * FROM items WHERE date(first_seen_at)=?',
            (stat_date,)).fetchall()
        agg = {}
        for r in rows:
            extra = {}
            try:
                extra = json.loads(r['extra'] or '{}')
            except (ValueError, TypeError):
                pass
            if r['platform'] == 'github':
                topics = (extra.get('topics') or [])[:max_topics] or ['opensource']
            else:
                topics = match_keywords(f"{r['title']} {r['summary'] or ''}", keywords)
                topics = topics[:max_topics] or [r['category'] or r['platform']]
            score = extra.get('stars') or extra.get('points') or 0
            for t in topics:
                a = agg.setdefault(t, {'count': 0, 'sources': set(), 'score': -1, 'rep': None})
                a['count'] += 1
                a['sources'].add(r['platform'])
                if score > a['score'] or (score == a['score'] and
                                          (a['rep'] is None or r['id'] < a['rep'])):
                    a['score'], a['rep'] = score, r['id']
        for t, a in agg.items():
            self.upsert_topic_stat(stat_date, t, a['count'], len(a['sources']), a['rep'])
        return len(agg)

    def topic_stat_rows(self, topic):
        """某主题全部日统计,按 stat_date 升序。"""
        return self.conn.execute(
            'SELECT * FROM topic_daily_stats WHERE topic=? ORDER BY stat_date',
            (topic,)).fetchall()

    def topic_stats_exist_before(self, date):
        """是否存在严格早于 date 的主题统计(判断是否首日主题基线)。"""
        row = self.conn.execute(
            'SELECT count(*) AS n FROM topic_daily_stats WHERE stat_date < ?',
            (date,)).fetchone()
        return row['n'] > 0

    def topics_with_stat_on(self, date):
        return [r['topic'] for r in self.conn.execute(
            'SELECT DISTINCT topic FROM topic_daily_stats WHERE stat_date=? ORDER BY topic',
            (date,)).fetchall()]

    # ---------- runs ----------
    def start_run(self, run_date, started_at=None):
        cur = self.conn.execute(
            'INSERT INTO runs (run_date, started_at, status) VALUES (?, ?, ?)',
            (run_date, started_at or utcnow_iso(), 'running'))
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id, status, per_source_stats=None, errors=None,
                   items_inserted=0, items_updated=0, trends=None):
        self.conn.execute(
            '''UPDATE runs SET finished_at=?, status=?, per_source_stats=?,
                 errors=?, items_inserted=?, items_updated=?, trends=?
               WHERE id=?''',
            (utcnow_iso(), status,
             json.dumps(per_source_stats or {}, ensure_ascii=False),
             json.dumps(errors or {}, ensure_ascii=False),
             items_inserted, items_updated,
             json.dumps(trends or {}, ensure_ascii=False),
             run_id))
        self.conn.commit()

    # ---------- FTS5 ----------
    def search_items(self, query, limit=20):
        """FTS5 全文检索 items;query 为 FTS MATCH 语法(如 'llm OR agent')。"""
        if not self.fts_available:
            raise RuntimeError('FTS5 不可用,无法执行全文检索')
        return self.conn.execute(
            '''SELECT i.* FROM items_fts f
               JOIN items i ON i.id = f.rowid
               WHERE items_fts MATCH ?
               ORDER BY bm25(items_fts)
               LIMIT ?''', (query, limit)).fetchall()

    def count_fts(self):
        if not self.fts_available:
            return 0
        return self.conn.execute('SELECT count(*) FROM items_fts').fetchone()[0]

    def close(self):
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass
