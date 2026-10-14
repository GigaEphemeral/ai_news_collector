# -*- coding: utf-8 -*-
"""阶段03 全表结构 / item_sources / runs / topic_daily_stats / FTS5 同步测试。"""
import json
import os
import tempfile

from src.db import Database


def _make_db():
    d = tempfile.mkdtemp(prefix='ai_news_mvp_test_')
    return Database(os.path.join(d, 'test.sqlite3')), d


def test_all_tables_exist():
    db, d = _make_db()
    rows = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r['name'] for r in rows}
    assert {'sources', 'items', 'item_sources', 'project_snapshots',
            'topic_daily_stats', 'runs', 'daily_reports'} <= names
    assert 'items_fts' in names  # FTS5 虚拟表
    db.close()


def test_fts_triggers_keep_index_in_sync():
    db, d = _make_db()
    assert db.fts_available
    sid = db.upsert_source('github:search', 'GitHub', 'github')
    db.upsert_item(platform='github', title='Gemini 4 Argon release notes',
                   url='https://github.com/a/b', summary='agent llm rag',
                   published_at='2026-10-14T00:00:00+00:00', content_hash=1,
                   category='opensource', extra={}, source_id=sid)
    assert db.count_fts() == 1
    hits = db.search_items('gemini')
    assert len(hits) == 1 and hits[0]['title'].startswith('Gemini')
    # 触发器:更新 title 后索引同步
    db.conn.execute("UPDATE items SET title='Agent framework v2' WHERE url=?",
                    ('https://github.com/a/b',))
    db.conn.commit()
    assert len(db.search_items('gemini')) == 0
    assert len(db.search_items('framework')) == 1
    # 触发器:删除后索引同步
    db.conn.execute('DELETE FROM items WHERE url=?', ('https://github.com/a/b',))
    db.conn.commit()
    assert db.count_fts() == 0
    db.close()


def test_fts_multi_word_or_query():
    db, d = _make_db()
    sid = db.upsert_source('hackernews:topstories', 'HN', 'hackernews')
    db.upsert_item(platform='hackernews', title='A new RAG framework',
                   url='https://news.ycombinator.com/item?id=1', summary='',
                   published_at=None, content_hash=0, category='discussion',
                   extra={}, source_id=sid)
    db.upsert_item(platform='hackernews', title='Gemini benchmark results',
                   url='https://news.ycombinator.com/item?id=2', summary='',
                   published_at=None, content_hash=0, category='discussion',
                   extra={}, source_id=sid)
    hits = db.search_items('llm OR gemini')
    assert len(hits) == 1 and 'Gemini' in hits[0]['title']
    db.close()


def test_fts_rebuild_for_existing_rows():
    """阶段02 旧库升级:连接时对存量 items 执行 rebuild,旧数据可检索。"""
    d = tempfile.mkdtemp(prefix='ai_news_mvp_test_')
    path = os.path.join(d, 'old.sqlite3')
    # 先模拟阶段02 旧库(只有 items 表,阶段02 的完整列,无 items_fts)
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute('''CREATE TABLE items (
      id INTEGER PRIMARY KEY,
      source_id INTEGER,
      platform TEXT,
      title TEXT NOT NULL,
      url TEXT UNIQUE NOT NULL,
      summary TEXT,
      published_at TEXT,
      content_hash INTEGER,
      category TEXT,
      extra TEXT,
      first_seen_at TEXT,
      last_seen_at TEXT)''')
    conn.execute(
        "INSERT INTO items (platform, title, url, summary, first_seen_at, last_seen_at) "
        "VALUES ('rss', 'Old gemini item', 'https://a/1', 'x', '2026-10-01T00:00:00+00:00', "
        "'2026-10-01T00:00:00+00:00')")
    conn.commit()
    conn.close()
    # 阶段03 打开:建表 + FTS + 存量 rebuild
    db = Database(path)
    assert db.fts_available
    assert db.count_fts() == 1
    hits = db.search_items('gemini')
    assert len(hits) == 1 and hits[0]['title'] == 'Old gemini item'
    db.close()


def test_item_sources_cross_mapping():
    db, d = _make_db()
    s1 = db.upsert_source('https://a.com/feed', 'Blog A', 'rss')
    s2 = db.upsert_source('hackernews:topstories', 'HN', 'hackernews')
    _, item_id = db.upsert_item(platform='rss', title='AI Agent news',
                                url='https://a.com/p1', summary='',
                                published_at=None, content_hash=0,
                                category='news', extra={}, source_id=s1,
                                source_url='https://a.com/feed')
    # 同一事件被第二个来源发现 → item_sources 新增一行
    db.upsert_item(platform='hackernews', title='AI Agent news',
                   url='https://a.com/p1', summary='',
                   published_at=None, content_hash=0,
                   category='news', extra={}, source_id=s2,
                   source_url='hackernews:topstories')
    rows = db.conn.execute(
        'SELECT item_id, source_id, source_url FROM item_sources '
        'WHERE item_id=? ORDER BY source_id', (item_id,)).fetchall()
    assert len(rows) == 2
    db.close()


def test_runs_start_finish():
    db, d = _make_db()
    rid = db.start_run('2026-10-14', started_at='2026-10-14T00:00:00+00:00')
    db.finish_run(rid, 'ok', per_source_stats={'github': {'ok': 1}},
                  errors={}, items_inserted=3, items_updated=1,
                  trends={'date': '2026-10-14', 'projects': []})
    row = db.conn.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone()
    assert row['status'] == 'ok'
    assert row['items_inserted'] == 3
    assert json.loads(row['per_source_stats'])['github']['ok'] == 1
    assert json.loads(row['trends'])['date'] == '2026-10-14'
    db.close()


def test_topic_daily_stats_compute():
    db, d = _make_db()
    sid = db.upsert_source('github:search', 'GitHub', 'github')
    # 两条 GitHub 条目,topic 都是 ai-agents / llm;一条 HN 命中 agent
    for i, (title, topics) in enumerate([
        ('Repo A', ['ai-agents', 'llm']),
        ('Repo B', ['llm']),
    ]):
        db.upsert_item(platform='github', title=title,
                       url=f'https://github.com/x/r{i}', summary='s',
                       published_at=None, content_hash=i, category='opensource',
                       extra={'stars': 100 + i, 'topics': topics}, source_id=sid,
                       first_seen_at='2026-10-14T00:00:00+00:00')
    s2 = db.upsert_source('hackernews:topstories', 'HN', 'hackernews')
    db.upsert_item(platform='hackernews', title='Agentic coding agents on rise',
                   url='https://news.ycombinator.com/item?id=9', summary='',
                   published_at=None, content_hash=9, category='discussion',
                   extra={'points': 500}, source_id=s2,
                   first_seen_at='2026-10-14T01:00:00+00:00')
    n = db.compute_topic_stats('2026-10-14', ['ai', 'llm', 'agent'], max_topics=3)
    assert n >= 3  # ai-agents / llm / agent
    row = db.conn.execute(
        "SELECT * FROM topic_daily_stats WHERE stat_date='2026-10-14' AND topic='llm'"
    ).fetchone()
    assert row['count'] == 2
    assert row['source_count'] == 1
    # GitHub topics 原样入库:ai-agents / llm;HN 命中关键词 → agent(粒度不同,不强行合并)
    arow = db.conn.execute(
        "SELECT * FROM topic_daily_stats WHERE stat_date='2026-10-14' AND topic='ai-agents'"
    ).fetchone()
    assert arow['count'] == 1 and arow['source_count'] == 1
    g = db.conn.execute(
        "SELECT * FROM topic_daily_stats WHERE stat_date='2026-10-14' AND topic='agent'"
    ).fetchone()
    assert g['count'] == 1 and g['source_count'] == 1
    db.close()
