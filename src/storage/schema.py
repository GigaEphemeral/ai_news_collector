# -*- coding: utf-8 -*-
"""SQLite 表结构(阶段02 从 storage/db.py 拆出):SCHEMA / FTS_SCHEMA / ensure_schema。

阶段03 完整表结构 + FTS5 全文检索:
  - sources          来源配置
  - items            情报条目
  - item_sources     同一事件被多个来源发现的跨源映射
  - project_snapshots GitHub 项目每日快照(PK=repo+date)
  - topic_daily_stats 每天每主题条目数/来源数/代表情报
  - runs             每次运行的开始/结束/状态/错误
  - daily_reports    日报与发送状态
  - items_fts        FTS5 external-content 虚拟表 + 触发器

对已存在的阶段02 数据库自动迁移:新表用 CREATE TABLE IF NOT EXISTS;items_fts
在连接时对存量 items 执行 rebuild,保证旧数据可检索。
"""
import logging
import sqlite3

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
  native_id TEXT,               -- 各源自然键:GitHub full_name / HN hn_id / RSS (source_id,guid) / HF arxiv_id
  title TEXT NOT NULL,
  url TEXT NOT NULL,            -- 普通列(不再 UNIQUE;同 URL 由自然键 + 指纹合并保证只一条)
  summary TEXT,
  published_at TEXT,
  content_hash INTEGER,
  canonical_key INTEGER,        -- 内容指纹:标题归一化 SimHash(跨源模糊合并用)
  category TEXT,
  extra TEXT,
  first_seen_at TEXT,
  last_seen_at TEXT,
  UNIQUE (platform, native_id)  -- 阶段03:自然键唯一约束替代 url UNIQUE
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
  retry_count INTEGER DEFAULT 0,
  selected_item_ids TEXT       -- 阶段04:当天日报选中的 item id 列表(JSON 数组)
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


# 阶段03:新 items 表结构(url 不再 UNIQUE,自然键 UNIQUE(platform, native_id),
# 新增 native_id / canonical_key 列)。旧库 items 为 url UNIQUE,需重建迁移。
ITEMS_V3 = """
CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  source_id INTEGER REFERENCES sources(id),
  platform TEXT,
  native_id TEXT,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  summary TEXT,
  published_at TEXT,
  content_hash INTEGER,
  canonical_key INTEGER,
  category TEXT,
  extra TEXT,
  first_seen_at TEXT,
  last_seen_at TEXT,
  UNIQUE (platform, native_id)
);
"""

# items 相关索引(迁移重建用;与 SCHEMA 保持一致)
ITEMS_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_items_platform ON items(platform);
CREATE INDEX IF NOT EXISTS idx_items_published_at ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_first_seen ON items(first_seen_at);
"""


def _items_needs_v3_migration(conn):
    """旧 items 表缺 native_id 列 → 需要重建迁移。"""
    cols = {r['name'] for r in conn.execute('PRAGMA table_info(items)')}
    return 'native_id' not in cols


def _migrate_items_v3(conn):
    """把旧 items(url UNIQUE)重建为新结构(native_id 回退为 url)。

    rename items 会触发 SQLite 重写其他表(如 item_sources)对 items 的
    FK 引用,因此迁移期间关闭外键,结束后恢复。"""
    conn.execute('PRAGMA foreign_keys=OFF')
    try:
        conn.execute('ALTER TABLE items RENAME TO items_old')
        conn.executescript(ITEMS_V3)
        conn.execute(
            '''INSERT INTO items (id, source_id, platform, native_id, title, url, summary,
                                  published_at, content_hash, canonical_key, category, extra,
                                  first_seen_at, last_seen_at)
               SELECT id, source_id, platform, url, title, url, summary,
                      published_at, content_hash, NULL, category, extra,
                      first_seen_at, last_seen_at FROM items_old''')
        conn.execute('DROP TABLE items_old')
        # 旧表 rename 时索引随之改名,重建新表索引
        conn.executescript(ITEMS_INDEXES)
        conn.commit()
    finally:
        conn.execute('PRAGMA foreign_keys=ON')
    logging.info('items 表已迁移到阶段03 结构(url UNIQUE → UNIQUE(platform, native_id))')


def _migrate_daily_reports(conn):
    """阶段04:给已存在的 daily_reports 表补 last_error / selected_item_ids 列。"""
    for ddl in (
        'ALTER TABLE daily_reports ADD COLUMN last_error TEXT',
        'ALTER TABLE daily_reports ADD COLUMN selected_item_ids TEXT',
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # 列已存在
    conn.commit()


def ensure_schema(conn):
    """建表 + 迁移 + FTS5 初始化;返回 fts_available(bool)。"""
    conn.executescript(SCHEMA)
    # 旧 items 缺 native_id → 重建迁移;迁移会 rename items,FTS 触发器/虚拟表
    # 的 content=items 引用会失效,因此先清掉 FTS 再重建(见 _setup_fts)。
    if _items_needs_v3_migration(conn):
        _drop_fts(conn)
        _migrate_items_v3(conn)
    _migrate_daily_reports(conn)
    return _setup_fts(conn)


def _drop_fts(conn):
    """删除 FTS 虚拟表与同步触发器(迁移前清理,避免引用失效表)。"""
    try:
        conn.execute('DROP TRIGGER IF EXISTS items_ai')
        conn.execute('DROP TRIGGER IF EXISTS items_ad')
        conn.execute('DROP TRIGGER IF EXISTS items_au')
        conn.execute('DROP TABLE IF EXISTS items_fts')
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()


def _setup_fts(conn):
    """重建 FTS 虚拟表 + 触发器,并对存量 items rebuild;返回 fts_available。"""
    fts_available = False
    try:
        conn.executescript(FTS_SCHEMA)
        # 存量 items 需要一次 rebuild 才进入索引(审计实测:external-content + 触发器可用)
        n_items = conn.execute('SELECT count(*) FROM items').fetchone()[0]
        if n_items:
            conn.execute("INSERT INTO items_fts(items_fts) VALUES ('rebuild')")
        conn.commit()
        fts_available = True
    except sqlite3.OperationalError as e:
        logging.error(f'FTS5 不可用,全文检索降级: {e}')
        conn.rollback()
    conn.commit()
    return fts_available
