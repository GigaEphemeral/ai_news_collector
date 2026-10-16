# -*- coding: utf-8 -*-
"""SQLite 历史库:阶段03 完整表结构 + FTS5 全文检索(阶段02 按领域归入 storage)。

表结构/迁移/FTS5 初始化拆到同目录 schema.py(ensure_schema),本模块只保留
Database 类的方法;主题日统计 compute_topic_stats 搬至 pipeline/topic_stats.py。
"""
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

from .schema import ensure_schema


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
        self.fts_available = ensure_schema(self.conn)
        logging.info(f'SQLite 就绪: {db_path}(FTS5={"可用" if self.fts_available else "降级"})')

    # ---------- daily_reports(阶段04:日报正文 + 发送状态) ----------
    def get_daily_report(self, report_date):
        return self.conn.execute(
            'SELECT * FROM daily_reports WHERE report_date=?', (report_date,)).fetchone()

    def save_daily_report(self, report_date, body, generated_at=None):
        """保存/更新某日日报正文。

        幂等规则:已成功发送(sent)的日报不覆盖,防止重新生成覆盖"已送达"事实;
        pending/failed/uncertain 可重新生成(重置为 pending,允许从库重发)。
        返回 'saved' / 'replaced' / 'skipped_sent'。
        """
        row = self.get_daily_report(report_date)
        if row and row['sent_status'] == 'sent':
            return 'skipped_sent'
        now = generated_at or utcnow_iso()
        self.conn.execute(
            '''INSERT INTO daily_reports (report_date, body, generated_at,
                                          sent_status, retry_count)
               VALUES (?, ?, ?, 'pending', 0)
               ON CONFLICT(report_date) DO UPDATE SET
                 body=excluded.body, generated_at=excluded.generated_at,
                 sent_status='pending', sent_at=NULL, retry_count=0,
                 last_error=NULL''',
            (report_date, body, now))
        self.conn.commit()
        return 'replaced' if row else 'saved'

    def mark_report_sent(self, report_date, sent_at=None):
        """发送成功后置 sent(只有成功才调用)。"""
        self.conn.execute(
            "UPDATE daily_reports SET sent_status='sent', sent_at=?, last_error=NULL "
            "WHERE report_date=?", (sent_at or utcnow_iso(), report_date))
        self.conn.commit()

    def mark_report_failed(self, report_date, error=None):
        """明确失败(HTTP 返回非 0 且重试耗尽)。"""
        self.conn.execute(
            "UPDATE daily_reports SET sent_status='failed', retry_count=retry_count+1, "
            "last_error=? WHERE report_date=?", (error, report_date))
        self.conn.commit()

    def mark_report_uncertain(self, report_date, error=None):
        """结果不确定(超时/响应无法解析)→ 停止重发,避免重复发送。"""
        self.conn.execute(
            "UPDATE daily_reports SET sent_status='uncertain', retry_count=retry_count+1, "
            "last_error=? WHERE report_date=?", (error, report_date))
        self.conn.commit()

    def items_first_seen_on(self, date):
        """某日首次出现的去重后条目(日报候选/采集状态统计用)。"""
        return self.conn.execute(
            'SELECT * FROM items WHERE date(first_seen_at)=? ORDER BY id', (date,)).fetchall()

    def latest_run_for_date(self, date):
        """指定日期的最近一次运行记录(standalone 生成日报时反推采集统计)。"""
        return self.conn.execute(
            'SELECT * FROM runs WHERE run_date=? ORDER BY started_at DESC, id DESC LIMIT 1',
            (date,)).fetchone()

    def failed_sources(self):
        """当前 last_error 非空的来源(采集状态:失败源)。"""
        return self.conn.execute(
            "SELECT url, name, last_error FROM sources "
            "WHERE last_error IS NOT NULL AND last_error != ''").fetchall()

    def count_sources_enabled(self):
        return self.conn.execute(
            'SELECT count(*) FROM sources WHERE enabled=1').fetchone()[0]

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

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
