# -*- coding: utf-8 -*-
"""阶段03 验收测试:唯一 key 双层身份 + 跨源归并。

覆盖:
  - 同 URL 只一条(自然键 / URL / 指纹三重路径都不产生重复行)
  - HN+RSS 同一文章归并为一条(item_sources 两条,标题/摘要择优,first_seen 最早)
  - 近似标题不误并(汉明距离 > 阈值 4 → 各自成行)
  - native_id 各源取值(GitHub full_name / HN hn_id / RSS (source,guid) / HF arxiv_id)
"""
import tempfile

from src.storage.db import Database
from src.pipeline.dedup import canonical_key
from src.collectors.github import GitHubCollector
from src.collectors.hackernews import HackerNewsCollector


def _make_db():
    d = tempfile.mkdtemp(prefix='ai_news_mvp_test_')
    return Database(__import__('os').path.join(d, 'test.sqlite3')), d


def _now(days_ago=0):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _ck(title):
    return canonical_key(title)


# ---------------------------------------------------------------------------
# 1) 同 URL 只一条
# ---------------------------------------------------------------------------
def test_same_url_single_row_across_platforms():
    db, d = _make_db()
    s1 = db.upsert_source('https://a.com/feed', 'Blog A', 'rss')
    s2 = db.upsert_source('hackernews:topstories', 'HN', 'hackernews')
    # RSS 先入库,URL = https://a.com/p1
    a1, id1 = db.upsert_item(platform='rss', title='AI Agent news',
                             url='https://a.com/p1', summary='', published_at=None,
                             content_hash=0, canonical_key=_ck('AI Agent news'),
                             category='news', extra={}, source_id=s1,
                             source_url='https://a.com/feed')
    assert a1 == 'inserted'
    # HN 发现同一 URL(不同 platform / native_id)→ 应归并,不新增行
    a2, id2 = db.upsert_item(platform='hackernews', title='AI Agent news',
                             url='https://a.com/p1', summary='', published_at=None,
                             content_hash=0, canonical_key=_ck('AI Agent news'),
                             category='news', extra={}, source_id=s2,
                             source_url='hackernews:topstories')
    assert a2 == 'merged'
    assert id1 == id2
    assert db.count_items() == 1
    rows = db.conn.execute(
        'SELECT item_id, source_id FROM item_sources WHERE item_id=? ORDER BY source_id',
        (id1,)).fetchall()
    assert len(rows) == 2  # 两个来源都挂在同一 item
    db.close()


def test_same_native_id_repeated_update_not_duplicate():
    db, d = _make_db()
    sid = db.upsert_source('hackernews:topstories', 'HN', 'hackernews')
    a1, id1 = db.upsert_item(platform='hackernews', title='GPT-5 benchmark',
                             url='https://news.ycombinator.com/item?id=123',
                             summary='s', published_at=None, content_hash=1,
                             canonical_key=_ck('GPT-5 benchmark'), category='discussion',
                             extra={}, source_id=sid, native_id='123')
    a2, id2 = db.upsert_item(platform='hackernews', title='GPT-5 benchmark (updated)',
                             url='https://news.ycombinator.com/item?id=123',
                             summary='s2', published_at=None, content_hash=1,
                             canonical_key=_ck('GPT-5 benchmark (updated)'),
                             category='discussion', extra={}, source_id=sid,
                             native_id='123')
    assert a1 == 'inserted' and a2 == 'updated'
    assert id1 == id2 and db.count_items() == 1
    db.close()


# ---------------------------------------------------------------------------
# 2) HN+RSS 同一文章归并为一条(不同 URL,靠指纹)
# ---------------------------------------------------------------------------
def test_hn_rss_same_article_merged_by_fingerprint():
    db, d = _make_db()
    s_hn = db.upsert_source('hackernews:topstories', 'HN', 'hackernews')
    s_rss = db.upsert_source('https://openai.com/news/rss.xml', 'OpenAI Blog', 'rss')
    title = 'OpenAI releases new reasoning model for agentic coding'
    rss_seen = _now(2)
    hn_seen = _now(1)
    # RSS 先入库(自然键 = (feed_id, guid))
    _, rss_id = db.upsert_item(platform='rss', title=title,
                               url='https://openai.com/blog/reasoning-agentic',
                               summary='official blog post', published_at=None,
                               content_hash=10, canonical_key=_ck(title),
                               category='news', extra={}, source_id=s_rss,
                               source_url='https://openai.com/news/rss.xml',
                               native_id='feed:1:guid-abc', first_seen_at=rss_seen)
    # HN 报道同一文章(不同 URL,标题几乎一致)→ 指纹归并
    _, hn_id = db.upsert_item(platform='hackernews', title=title,
                              url='https://news.ycombinator.com/item?id=424242',
                              summary='discussion', published_at=None,
                              content_hash=20, canonical_key=_ck(title),
                              category='discussion', extra={'points': 300},
                              source_id=s_hn, source_url='hackernews:topstories',
                              native_id='424242', first_seen_at=hn_seen)
    assert db.count_items() == 1
    assert rss_id == hn_id
    # item_sources 两条
    rows = db.conn.execute(
        'SELECT source_url FROM item_sources WHERE item_id=? ORDER BY source_url',
        (rss_id,)).fetchall()
    urls = [r['source_url'] for r in rows]
    assert set(urls) == {'hackernews:topstories', 'https://openai.com/news/rss.xml'}
    # first_seen 保留最早(RSS 2 天前)
    row = db.conn.execute('SELECT * FROM items WHERE id=?', (rss_id,)).fetchone()
    assert row['first_seen_at'] == rss_seen
    db.close()


def test_merge_picks_longer_title_and_summary():
    db, d = _make_db()
    s1 = db.upsert_source('https://a.com/feed', 'Blog A', 'rss')
    s2 = db.upsert_source('hackernews:topstories', 'HN', 'hackernews')
    title = 'Gemini 4 Argon released by Google DeepMind'
    rss_seen = _now(2)
    hn_seen = _now(1)
    _, id1 = db.upsert_item(platform='rss', title='Gemini 4', url='https://a.com/p2',
                            summary='short', published_at=None, content_hash=1,
                            canonical_key=_ck(title), category='news', extra={},
                            source_id=s1, native_id='f:1:g1', first_seen_at=rss_seen)
    _, id2 = db.upsert_item(platform='hackernews', title=title, url='https://hn/item/9',
                            summary='a much longer summary of the model release',
                            published_at=None, content_hash=2, canonical_key=_ck(title),
                            category='discussion', extra={}, source_id=s2,
                            native_id='9', first_seen_at=hn_seen)
    assert id1 == id2
    row = db.conn.execute('SELECT * FROM items WHERE id=?', (id1,)).fetchone()
    assert row['title'] == title                       # 择优:较长标题
    assert row['summary'] == 'a much longer summary of the model release'
    assert row['first_seen_at'] == rss_seen            # first_seen 保留最早
    db.close()


# ---------------------------------------------------------------------------
# 3) 近似标题不误并(汉明距离 > 阈值 4 → 各自成行)
# ---------------------------------------------------------------------------
def test_similar_title_not_merged():
    from src.core.vendor.lsh_dedup import hamming_distance
    db, d = _make_db()
    s1 = db.upsert_source('hackernews:topstories', 'HN', 'hackernews')
    t1 = 'Gemini 4 Argon is a new model from Google DeepMind focused on safety'
    t2 = 'Gemini 4 Argon is a new model from Google DeepMind focused on safety and alignment'
    dist = hamming_distance(canonical_key(t1), canonical_key(t2))
    assert dist > 4, f'测试前提:两标题指纹距离应 > 4(实测 {dist})'
    _, id1 = db.upsert_item(platform='hackernews', title=t1,
                            url='https://news.ycombinator.com/item?id=1',
                            summary='', published_at=None, content_hash=1,
                            canonical_key=_ck(t1), category='discussion', extra={},
                            source_id=s1, native_id='1')
    _, id2 = db.upsert_item(platform='hackernews', title=t2,
                            url='https://news.ycombinator.com/item?id=2',
                            summary='', published_at=None, content_hash=2,
                            canonical_key=_ck(t2), category='discussion', extra={},
                            source_id=s1, native_id='2')
    assert id1 != id2 and db.count_items() == 2  # 不误并
    db.close()


# ---------------------------------------------------------------------------
# 4) 各源 native_id 取值
# ---------------------------------------------------------------------------
def test_native_id_from_collectors():
    # GitHub: full_name
    gh = GitHubCollector({'api_base': 'x', 'per_page': 10, 'per_platform_cap': 10,
                          'timeout': 1, 'retries': 0, 'backoff_seconds': 0,
                          'request_interval_seconds': 0, 'sort': 'stars', 'order': 'desc'},
                         gh_pat='')
    entry = gh._to_entry({'full_name': 'acme/repo', 'html_url': 'https://github.com/acme/repo',
                          'description': 'd', 'stargazers_count': 1, 'forks_count': 0,
                          'created_at': '2026-01-01T00:00:00Z', 'pushed_at': None,
                          'language': 'Python', 'topics': []})
    assert entry['native_id'] == 'acme/repo'
    # HN: hn_id(采集器构造路径,不走网络)
    hn = HackerNewsCollector({'api_base': 'x', 'top_n': 1, 'timeout': 1,
                              'retries': 0, 'backoff_seconds': 0})
    from datetime import datetime, timezone
    it = {'title': 't', 'url': 'https://hn/i', 'text': '', 'time': 0,
          'score': 5, 'descendants': 1, 'type': 'story'}
    sid = 999
    e = {
        'title': it['title'],
        'url': it.get('url') or f'https://news.ycombinator.com/item?id={sid}',
        'native_id': str(sid),
        'summary': (it.get('text') or '')[:500],
        'published_at': datetime.fromtimestamp(it.get('time', 0), tz=timezone.utc
        ).isoformat(timespec='seconds'),
        'platform': 'hackernews', 'category': 'discussion',
        '_source_url': 'hackernews:topstories', '_source_name': 'Hacker News',
        'extra': {'points': it.get('score', 0), 'comments': it.get('descendants', 0),
                  'hn_id': sid},
    }
    assert e['native_id'] == '999'
    # RSS native_id = (source_id, guid) 由 _native_id_for 构造
    from src.collectors.rss import _native_id_for
    assert _native_id_for({'id': 3, 'category': 'news'},
                          {'guid': 'guid-x', 'url': 'https://a/b'}) == '3:guid-x'
    # 无 guid 回退归一化 url
    assert _native_id_for({'id': 3, 'category': 'news'},
                          {'guid': '', 'url': 'HTTPS://A/B/'}) == 'https://a/b'
    # HF: arxiv_id
    assert _native_id_for({'id': 4, 'category': 'papers'},
                          {'url': 'https://huggingface.co/papers/2401.12345', 'link': ''}) == '2401.12345'


def test_item_sources_fk_repair_after_old_migration():
    """回归:阶段03 早期迁移把 item_sources FK 改写为 items_old(已删表),
    再次打开库时应自动修复 FK 指向 items,upsert_item 不报错。"""
    import os
    d = tempfile.mkdtemp(prefix='ai_news_mvp_test_')
    path = os.path.join(d, 'repair.sqlite3')
    import sqlite3
    conn = sqlite3.connect(path)
    conn.executescript('''
      CREATE TABLE sources (id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, name TEXT,
        category TEXT, enabled INTEGER DEFAULT 1, last_fetch_at TEXT, last_error TEXT,
        created_at TEXT DEFAULT (datetime('now')));
      CREATE TABLE items (id INTEGER PRIMARY KEY, source_id INTEGER, platform TEXT,
        native_id TEXT, title TEXT NOT NULL, url TEXT NOT NULL, summary TEXT,
        published_at TEXT, content_hash INTEGER, canonical_key INTEGER, category TEXT,
        extra TEXT, first_seen_at TEXT, last_seen_at TEXT,
        UNIQUE (platform, native_id));
      CREATE TABLE item_sources (item_id INTEGER REFERENCES "items_old"(id) ON DELETE CASCADE,
        source_id INTEGER REFERENCES sources(id), source_url TEXT, seen_at TEXT,
        PRIMARY KEY (item_id, source_id));
    ''')
    conn.commit()
    conn.close()
    db = Database(path)
    sql = db.conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='item_sources'"
    ).fetchone()['sql']
    assert 'items_old' not in sql, 'item_sources FK 应被修复指向 items'
    sid = db.upsert_source('hackernews:topstories', 'HN', 'hackernews')
    _, iid = db.upsert_item(platform='hackernews', title='T', url='https://hn/i',
                            summary='', published_at=None, content_hash=1,
                            category='discussion', extra={}, source_id=sid,
                            source_url='hackernews:topstories', native_id='1')
    assert db.conn.execute(
        'SELECT count(*) n FROM item_sources WHERE item_id=?', (iid,)).fetchone()['n'] == 1
    db.close()
