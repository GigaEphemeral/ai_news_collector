# -*- coding: utf-8 -*-
"""阶段05 修复回归测试:趋势主题去噪 + 代表条目来源。

CC 反馈:趋势结论里有很多垃圾干扰项(acme/9router/backend/email 等非 AI 标签),
且主题趋势只有计数、没有相关来源。本测试锁定两点修复:
  - GitHub extra.topics 仅保留命中 AI 关键词者,非 AI 标签不成为主题;
  - topic_trend 输出带代表条目(标题+url)作为来源证据。
"""
import json
import os
import tempfile

from src.storage.db import Database
from src.pipeline.topic_stats import compute_topic_stats, _tag_matches
from src.reporting.report import collect_candidates
from src.reporting.trend import build_trends, topic_trend

AI_KW = ['ai', 'llm', 'gpt', 'agent', 'rag', 'anthropic', 'openai', 'deepseek',
         'gemini', 'claude', 'codex', 'neural', 'multimodal', 'agentic']


def _make_db():
    d = tempfile.mkdtemp(prefix='ai_news_mvp_test_')
    return Database(os.path.join(d, 'test.sqlite3')), d


def _seed(db, stat_date):
    """GitHub 条目:混合 AI 与非 AI 标签;HF 一条走关键词路径。"""
    sid = db.upsert_source('github:search', 'GitHub', 'github')
    now = f'{stat_date}T08:00:00+00:00'
    db.upsert_item(platform='github', title='acme/agentkit',
                   url='https://github.com/acme/agentkit', summary='AI agent framework',
                   published_at=None, content_hash=1, canonical_key=0,
                   category='opensource',
                   extra={'full_name': 'acme/agentkit', 'topics': ['ai-agents', 'acme', '9router'],
                          'stargazers_count': 100},
                   source_id=sid, first_seen_at=now, native_id='acme/agentkit')
    db.upsert_item(platform='github', title='acme/backend', url='https://github.com/acme/backend',
                   summary='web backend', published_at=None, content_hash=2, canonical_key=0,
                   category='opensource',
                   extra={'full_name': 'acme/backend', 'topics': ['backend', 'databases', 'email'],
                          'stargazers_count': 50},
                   source_id=sid, first_seen_at=now, native_id='acme/backend')


def test_tag_matcher_filters_non_ai():
    """标签匹配:非 AI 标签(acme/9router/backend/email)不命中;AI 标签命中。"""
    assert _tag_matches('ai-agents', AI_KW)
    assert _tag_matches('llm-ui', AI_KW)
    assert not _tag_matches('acme', AI_KW)
    assert not _tag_matches('9router', AI_KW)
    assert not _tag_matches('backend', AI_KW)
    assert not _tag_matches('email', AI_KW)   # 修复:email 含 'ai' 但非 AI 标签
    assert not _tag_matches('databases', AI_KW)
    assert _tag_matches('chatgpt', AI_KW)     # 长关键词子串仍命中


def test_compute_topic_stats_filters_github_junk():
    """GitHub 非 AI 标签不成为主题(acme/9router/backend 不在主题列表)。"""
    db, d = _make_db()
    stat_date = '2026-10-14'
    _seed(db, stat_date)
    n = compute_topic_stats(db, stat_date, AI_KW, max_topics=3)
    topics = db.topics_with_stat_on(stat_date)
    assert 'ai-agents' in topics
    for junk in ('acme', '9router', 'backend', 'databases', 'email'):
        assert junk not in topics, f'非 AI 标签 {junk} 不应成为主题'
    db.close()


def test_topic_trend_has_representative_source():
    """topic_trend 输出带代表条目(标题+url)来源证据。"""
    db, d = _make_db()
    stat_date = '2026-10-14'
    _seed(db, stat_date)
    compute_topic_stats(db, stat_date, AI_KW, max_topics=3)
    # 手工构造 topic_daily_stats 行(带 representative_item_id),验证趋势带来源
    row = db.conn.execute(
        "SELECT representative_item_id FROM topic_daily_stats WHERE topic='ai-agents'"
    ).fetchone()
    assert row and row['representative_item_id'] is not None
    t = topic_trend('ai-agents', db.topic_stat_rows('ai-agents'), __import__(
        'datetime').date(2026, 10, 14), {'windows': {'24h': 2, '7d': 7, '30d': 30},
                                         'topic': {}})
    title, url = db.item_title_url(row['representative_item_id'])
    assert title and url
    db.close()


def test_build_trends_attaches_representative():
    """build_trends 的主题趋势带 代表条目 证据行。"""
    db, d = _make_db()
    stat_date = '2026-10-14'
    _seed(db, stat_date)
    compute_topic_stats(db, stat_date, AI_KW, max_topics=3)
    trends = build_trends(db, stat_date,
                          {'windows': {'24h': 2, '7d': 7, '30d': 30},
                           'project': {'young_days': 30}, 'topic': {}},
                          AI_KW, limit_projects=5, limit_topics=10)
    topics = trends['topics']
    assert topics, '应有主题趋势'
    for t in topics:
        assert any('代表条目' in e for e in t['evidence']), f'{t["topic"]} 缺代表条目来源'
    db.close()


# ---------------------------------------------------------------------------
# CC 反馈(2026-10-17):每天 08:00 定时运行时,昨天已入库的条目今天再采集
# 只有 last_seen 刷新、first_seen 不变 → 旧逻辑(只按 first_seen 选候选)导致日报为空。
# 修复:collect_candidates 改为 items_active_on(first_seen OR last_seen 落在当日)。
# ---------------------------------------------------------------------------
def test_daily_report_includes_refreshed_items():
    db, d = _make_db()
    sid = db.upsert_source('github:search', 'GitHub', 'github')
    # 模拟:10-16 首次入库,10-17 定时运行刷新 last_seen
    db.upsert_item(platform='github', title='acme/repo', url='https://github.com/acme/repo',
                   summary='AI agent framework', published_at=None, content_hash=1,
                   canonical_key=0, category='opensource',
                   extra={'full_name': 'acme/repo', 'stargazers_count': 100,
                          'created_at': '2026-10-01T00:00:00Z', 'topics': ['ai', 'agents']},
                   source_id=sid, first_seen_at='2026-10-16T00:00:00+00:00',
                   seen_at='2026-10-17T00:00:00+00:00', native_id='acme/repo')
    cfg = {'report': {'new_days': 30, 'opensource_max': 5, 'solutions_max': 5,
                      'max_highlights': 3},
           'trend': {'project': {'young_days': 30}},
           'keywords': {'ai': ['ai', 'llm', 'agent']}}
    # 修复前(仅 first_seen):10-17 候选为空 → 日报全空
    # 修复后(first_seen OR last_seen):10-17 有候选
    c16 = collect_candidates(db, '2026-10-16', cfg)
    c17 = collect_candidates(db, '2026-10-17', cfg)
    assert len(c16) == 1
    assert len(c17) == 1, '10-17 当天刷新过的条目应进入当日日报候选'
    assert c17[0]['url'] == 'https://github.com/acme/repo'
    db.close()
