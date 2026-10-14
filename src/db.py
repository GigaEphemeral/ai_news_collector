# -*- coding: utf-8 -*-
"""轻量 SQLite 存储:sources / items 两张表(表结构按阶段01审计报告 E.2)。

阶段02 仅建两张表:
  - sources:来源配置/启用/最后成功时间/错误
  - items:  情报条目;url UNIQUE 为主键级去重(同一 URL 再次出现时视为重复出现,
            只更新 last_seen_at,不重复插入;first_seen_at 保留首次发现时间)
  - items.extra:JSON 列,存平台特有轻量字段(如 GitHub 的 stars/forks/topics 等,
            阶段03 将抽出为 project_snapshots)
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
CREATE INDEX IF NOT EXISTS idx_items_platform ON items(platform);
CREATE INDEX IF NOT EXISTS idx_items_published_at ON items(published_at);
"""


class Database:
    def __init__(self, db_path):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        logging.info(f'SQLite 就绪: {db_path}')

    def upsert_source(self, url, name, category, last_fetch_at=None, last_error=None):
        """插入或更新来源;返回 source id。"""
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
                    content_hash, category, extra, source_id):
        """统一字段入库;url 冲突时更新 last_seen_at。

        返回 (action, item_id),action ∈ {'inserted', 'updated'}。
        """
        now = datetime.now(timezone.utc).isoformat()  # 微秒精度,区分同秒内 insert/update
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
             now, now),
        )
        self.conn.commit()
        row = self.conn.execute(
            'SELECT id FROM items WHERE url=?', (url,)).fetchone()
        action = 'updated' if existed else 'inserted'
        return action, row['id']

    def count_items(self):
        return self.conn.execute('SELECT count(*) FROM items').fetchone()[0]

    def count_sources(self):
        return self.conn.execute('SELECT count(*) FROM sources').fetchone()[0]

    def close(self):
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass
