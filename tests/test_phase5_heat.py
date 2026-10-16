# -*- coding: utf-8 -*-
"""阶段05 验收测试:RSS/HF 热度分层 + 「其他资讯汇总」段 + HF 热度落库。

覆盖:
  - HF 条目 extra = {arxiv_id, upvotes, github_stars, github_repo, is_hf_papers} 落库可查
  - RSS 热度分层(消费时规则,不落库):层1 有真实热度(HF upvotes+stars / 跨源 HN points)
    → 按信号;层2 无信号但 source_count≥2 → 按重复度+新鲜度;层3 → 按(新鲜度,关键词权重)
  - 日报含「其他资讯汇总」段(未入选 top-N 的 RSS/HF 聚合,不丢弃)
"""
import json
import os
import tempfile
from datetime import datetime, timezone

from src.storage.db import Database
from src.collectors.rss import _native_id_for
from src.pipeline.ranking import rss_hf_heat_sort_key
from src.pipeline.dedup import canonical_key as _ck
from src.reporting.report import build_report, collect_candidates


def _make_db():
    d = tempfile.mkdtemp(prefix='ai_news_mvp_test_')
    return Database(os.path.join(d, 'test.sqlite3')), d


def _cfg():
    return {
        'report': {'highlights_cap': 8, 'max_highlights': 3, 'opensource_max': 5,
                   'solutions_max': 5, 'new_days': 30, 'summary_max_chars': 80,
                   'max_chars': 6000, 'required_keyword': '每日ai情报'},
        'trend': {'windows': {'24h': 2, '7d': 7, '30d': 30},
                  'project': {'young_days': 30}, 'topic': {}},
        'keywords': {'ai': ['ai', 'llm', 'agent', 'rag']},
    }


def _seed_hf(db, run_date):
    """HF 条目:带 upvotes/github_stars/arxiv_id 热度字段。"""
    sid = db.upsert_source('https://huggingface.co/api/daily_papers', 'HF Daily Papers', 'hf_papers')
    now = f'{run_date}T09:00:00+00:00'
    papers = [  # 到达顺序与热度信号顺序不同:验证按信号重排(非到达顺序)
        ('2401.00001', 'LLM agents for coding', 40, 100),
        ('2401.00002', 'RAG pipeline optimization', 120, 500),
        ('2401.00003', 'Diffusion for robotics', 80, 300),
    ]
    ids = []
    for arxiv, title, up, stars in papers:
        a, iid = db.upsert_item(platform='hf_papers', title=title,
                                url=f'https://huggingface.co/papers/{arxiv}',
                                summary=f'{title} summary', published_at=None,
                                content_hash=up, canonical_key=_ck(title), category='papers',
                                extra={'arxiv_id': arxiv, 'upvotes': up,
                                       'github_stars': stars, 'github_repo': 'acme/x',
                                       'is_hf_papers': True},
                                source_id=sid, first_seen_at=now, native_id=arxiv)
        ids.append(iid)
    return ids


def _seed_rss_hn_cross(db, run_date):
    """RSS 条目被 HN 讨论(跨源合并,extra 带 points)。"""
    rss_sid = db.upsert_source('https://a.com/feed', 'Blog A', 'rss')
    hn_sid = db.upsert_source('hackernews:topstories', 'HN', 'hackernews')
    now = f'{run_date}T09:00:00+00:00'
    title = 'Agentic coding on the rise'
    ck = _ck(title)
    # RSS 先入库,自然键 (feed_id, guid)
    _, rss_id = db.upsert_item(platform='rss', title=title,
                               url='https://a.com/p1', summary='discussion about agents',
                               published_at=None, content_hash=1, canonical_key=ck,
                               category='news', extra={}, source_id=rss_sid,
                               source_url='https://a.com/feed',
                               first_seen_at=now, native_id='f:1:g1')
    # HN 归并同一文章 → extra 带 points(指纹归并)
    _, hn_id = db.upsert_item(platform='hackernews', title=title,
                              url='https://news.ycombinator.com/item?id=555',
                              summary='discussion', published_at=None, content_hash=2,
                              canonical_key=ck, category='discussion',
                              extra={'points': 300, 'hn_id': 555}, source_id=hn_sid,
                              source_url='hackernews:topstories',
                              first_seen_at=now, native_id='555')
    assert rss_id == hn_id
    return rss_id


def test_hf_extra_landed_in_db():
    """HF upvotes/stars/arxiv_id 落库可查(阶段05 验收)。"""
    db, d = _make_db()
    run_date = datetime.now(timezone.utc).date().isoformat()
    ids = _seed_hf(db, run_date)
    for iid, arxiv in zip(ids, ('2401.00001', '2401.00002', '2401.00003')):
        row = db.conn.execute('SELECT extra FROM items WHERE id=?', (iid,)).fetchone()
        ex = json.loads(row['extra'])
        assert ex['arxiv_id'] == arxiv
        assert ex['is_hf_papers'] is True
        assert ex['upvotes'] > 0 and ex['github_stars'] > 0
    db.close()


def test_rss_heat_layer_1_hn_points_first():
    """层1:跨源命中 HN 用 points 的 RSS 排在最前(重复度高/被讨论者优先)。"""
    db, d = _make_db()
    run_date = datetime.now(timezone.utc).date().isoformat()
    _seed_rss_hn_cross(db, run_date)
    # 另加一条无热度、无跨源的 RSS(层3)
    rss_sid = db.upsert_source('https://b.com/feed', 'Blog B', 'rss')
    now = f'{run_date}T10:00:00+00:00'
    db.upsert_item(platform='rss', title='plain llm note', url='https://b.com/q',
                   summary='', published_at=None, content_hash=9,
                   canonical_key=_ck('plain llm note'), category='news', extra={},
                   source_id=rss_sid, first_seen_at=now, native_id='f:2:g9')
    cands = collect_candidates(db, run_date, _cfg())
    rss = [c for c in cands if c['platform'] == 'rss']
    assert len(rss) == 2
    # 层1(被 HN 讨论)应在层3(plain)之前
    assert rss[0]['heat_layer'] == 1 and rss[0]['heat_signal'] == 300
    assert rss[1]['heat_layer'] == 3
    # 排序键验证
    k1 = rss_hf_heat_sort_key(rss[0], _cfg())
    k3 = rss_hf_heat_sort_key(rss[1], _cfg())
    assert k1 < k3
    db.close()


def test_hf_heat_layer_by_upvotes_stars():
    """层1 HF:按 upvotes+stars 信号排序(非到达顺序)。"""
    db, d = _make_db()
    run_date = datetime.now(timezone.utc).date().isoformat()
    _seed_hf(db, run_date)
    cands = collect_candidates(db, run_date, _cfg())
    hf = [c for c in cands if c['platform'] == 'hf_papers']
    assert len(hf) == 3
    assert all(c['heat_layer'] == 1 for c in hf)
    # 信号 = upvotes + github_stars,降序
    signals = [c['heat_signal'] for c in hf]
    assert signals == sorted(signals, reverse=True)
    assert signals[0] == 120 + 500
    # 非到达顺序:信号最高的 'RAG pipeline optimization'(seed 第 2 位)排到第一
    titles = [c['title'] for c in hf]
    assert titles[0] == 'RAG pipeline optimization'
    assert titles[-1] == 'LLM agents for coding'
    db.close()


def test_report_contains_section6_others():
    """日报含「其他资讯汇总」段:未入选 top-N 的 RSS/HF 聚合。"""
    db, d = _make_db()
    run_date = datetime.now(timezone.utc).date().isoformat()
    _seed_hf(db, run_date)          # 3 个 HF
    _seed_rss_hn_cross(db, run_date)  # 1 个 RSS(被 HN 讨论)
    cands = collect_candidates(db, run_date, _cfg())
    data = build_report(db, run_date, _cfg(), cands)
    assert '六、其他资讯汇总' in data['body']
    # 至少聚合了未入选三、解决方案的条目(共 4 个候选,top 5 → 全部入选三)
    # 用 solutions_max=2 验证聚合段存在且不丢条目
    cfg2 = _cfg()
    cfg2['report']['solutions_max'] = 2
    data2 = build_report(db, run_date, cfg2, cands)
    sec6 = data2['body'].split('六、其他资讯汇总')[1]
    lines = [l for l in sec6.splitlines() if l.startswith('- ')]
    assert len(lines) >= 2  # 3 个候选,2 个入选 → 至少 1-2 条聚合
    db.close()


def test_hf_native_id_arxiv():
    """HF native_id = arxiv_id(回退 url)。"""
    assert _native_id_for({'id': 9, 'category': 'papers'},
                          {'url': 'https://huggingface.co/papers/2401.99999'}) == '2401.99999'
