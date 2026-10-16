# -*- coding: utf-8 -*-
"""SQLite 存储单元测试(sources / items)。"""
import os
import tempfile

from src.storage.db import Database


def _make_db():
    d = tempfile.mkdtemp(prefix='ai_news_mvp_test_')
    return Database(os.path.join(d, 'test.sqlite3')), d


def test_schema_tables():
    db, d = _make_db()
    rows = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('sources','items') ORDER BY name").fetchall()
    assert [r['name'] for r in rows] == ['items', 'sources']
    db.close()


def test_source_upsert_idempotent():
    db, d = _make_db()
    sid1 = db.upsert_source('github:search', 'GitHub', 'github', last_fetch_at='2026-01-01')
    sid2 = db.upsert_source('github:search', 'GitHub', 'github', last_fetch_at='2026-01-02')
    assert sid1 == sid2
    assert db.count_sources() == 1
    db.close()


def test_item_insert_then_url_conflict_updates_last_seen():
    db, d = _make_db()
    sid = db.upsert_source('github:search', 'GitHub', 'github')
    action1, id1 = db.upsert_item(
        platform='github', title='Repo A', url='https://github.com/a/b',
        summary='s', published_at='2026-01-01T00:00:00+00:00',
        content_hash=123, category='opensource', extra={'stars': 10}, source_id=sid)
    assert action1 == 'inserted'
    action2, id2 = db.upsert_item(
        platform='github', title='Repo A', url='https://github.com/a/b',
        summary='s', published_at='2026-01-01T00:00:00+00:00',
        content_hash=123, category='opensource', extra={'stars': 11}, source_id=sid)
    assert action2 == 'updated'
    assert id1 == id2
    assert db.count_items() == 1
    row = db.conn.execute('SELECT first_seen_at, last_seen_at FROM items WHERE url=?',
                          ('https://github.com/a/b',)).fetchone()
    assert row['first_seen_at'] != row['last_seen_at']
    db.close()


def test_item_extra_json_roundtrip():
    db, d = _make_db()
    sid = db.upsert_source('github:search', 'GitHub', 'github')
    extra = {'stars': 100, 'forks': 5, 'topics': ['llm', 'agent'], 'language': 'Python'}
    db.upsert_item(platform='github', title='R', url='https://github.com/x/y',
                   summary='', published_at=None, content_hash=0,
                   category='opensource', extra=extra, source_id=sid)
    row = db.conn.execute('SELECT extra FROM items WHERE url=?',
                          ('https://github.com/x/y',)).fetchone()
    import json
    assert json.loads(row['extra']) == extra
    db.close()
