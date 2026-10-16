# -*- coding: utf-8 -*-
"""日报模板测试(阶段04):关键词/长度/栏目/链接/规则降级选择/幂等保存。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from conftest import trend_cfg

from src.storage.db import Database
from src.reporting.report import (NO_HOTSPOT_TEXT, build_report, collect_candidates,
                        rule_highlights, validate_report)

RUN_DATE = '2026-10-14'


def mini_cfg():
    return {
        'report': {
            'required_keyword': '每日ai情报',
            'max_chars': 6000,
            'highlights_cap': 8,
            'max_highlights': 3,
            'opensource_max': 5,
            'solutions_max': 5,
            'trends_projects_max': 3,
            'trends_topics_max': 3,
        },
        'trend': trend_cfg(),
        'keywords': {'ai': ['ai', 'llm', 'agent', 'gemini', 'claude', 'rag']},
    }


def _make_db():
    import tempfile
    d = tempfile.mkdtemp(prefix='ai_news_mvp_test_report_')
    return Database(os.path.join(d, 'test.sqlite3'))


def _seed(db, n_github=2, n_rss=2):
    """受控种子数据:github + rss + 快照(昨日/今日,供 24h 增量)。"""
    now = '2026-10-14T08:00:00+00:00'
    for i, repo in enumerate(('acme/agentkit', 'acme/ragflow'), 1):
        db.upsert_snapshot(repo, '2026-10-13', stars=100 + i * 10, forks=10,
                           pushed_at='2026-10-13T08:00:00Z',
                           created_at=f'2026-10-{10 + i:02d}T00:00:00Z')
        db.upsert_snapshot(repo, RUN_DATE, stars=100 + i * 60, forks=12,
                           pushed_at='2026-10-14T08:00:00Z',
                           created_at=f'2026-10-{10 + i:02d}T00:00:00Z',
                           topics=['ai', 'agents'], description='AI agent tooling')
    sid = db.upsert_source('github:search', 'GitHub Search API', 'github')
    for i, repo in enumerate(('acme/agentkit', 'acme/ragflow'), 1):
        db.upsert_item(platform='github', title=repo, url=f'https://github.com/{repo}',
                       summary='AI agent tooling framework', published_at=now,
                       content_hash=i, category='opensource', extra={
                           'full_name': repo, 'stargazers_count': 100 + i * 60,
                           'created_at': f'2026-10-{10 + i:02d}T00:00:00Z',
                           'topics': ['ai', 'agents'],
                       }, source_id=sid, first_seen_at=now)
    sid2 = db.upsert_source('https://openai.com/news/rss.xml', 'OpenAI Blog', 'rss')
    db.upsert_item(platform='rss', title='Gemini 4 release notes', url='https://openai.com/blog/x',
                   summary='A new model release', published_at=now, content_hash=9,
                   category='news', extra={}, source_id=sid2, first_seen_at=now)
    db.upsert_item(platform='rss', title='RAG patterns in production', url='https://hf.co/blog/y',
                   summary='Engineering practice', published_at=now, content_hash=10,
                   category='news', extra={}, source_id=sid2, first_seen_at=now)
    return db


def test_body_contains_keyword():
    db = _seed(_make_db())
    cands = collect_candidates(db, RUN_DATE, mini_cfg())
    data = build_report(db, RUN_DATE, mini_cfg(), cands)
    assert mini_cfg()['report']['required_keyword'] in data['body']
    assert data['validation'] == []
    db.close()


def test_five_sections_in_order():
    db = _seed(_make_db())
    cands = collect_candidates(db, RUN_DATE, mini_cfg())
    data = build_report(db, RUN_DATE, mini_cfg(), cands)
    for i, sec in enumerate(('一、今日值得关注', '二、开源项目与工程实践',
                             '三、解决方案与产品发布', '四、趋势观察', '五、采集状态')):
        assert sec in data['body']
        assert data['body'].index(sec) < data['body'].index('五、采集状态') or sec == '五、采集状态'
    assert data['body'].index('一、今日值得关注') < data['body'].index('二、开源项目与工程实践') < \
           data['body'].index('三、解决方案与产品发布') < data['body'].index('四、趋势观察') < \
           data['body'].index('五、采集状态')
    db.close()


def test_length_within_limit():
    db = _seed(_make_db())
    cands = collect_candidates(db, RUN_DATE, mini_cfg())
    data = build_report(db, RUN_DATE, mini_cfg(), cands)
    assert len(data['body']) <= mini_cfg()['report']['max_chars']
    db.close()


def test_items_have_original_links():
    db = _seed(_make_db())
    cands = collect_candidates(db, RUN_DATE, mini_cfg())
    data = build_report(db, RUN_DATE, mini_cfg(), cands)
    body = data['body']
    sec2 = body.split('二、开源项目与工程实践')[1].split('三、解决方案与产品发布')[0]
    sec3 = body.split('三、解决方案与产品发布')[1].split('四、趋势观察')[0]
    for line in sec2.strip().splitlines():
        if line.startswith('-'):
            assert 'http' in line, f'开源栏目条目缺链接: {line}'
    for line in sec3.strip().splitlines():
        if line.startswith('-'):
            assert 'http' in line, f'解决方案栏目条目缺链接: {line}'
    db.close()


def test_empty_candidates_hotspot_text():
    db = _make_db()  # 无任何条目
    cands = collect_candidates(db, RUN_DATE, mini_cfg())
    data = build_report(db, RUN_DATE, mini_cfg(), cands)
    assert NO_HOTSPOT_TEXT in data['body']
    # 栏目允许为空(二/三/四显示占位,不凑数)
    db.close()


def test_rule_highlights_cap_and_fields():
    db = _seed(_make_db())
    cands = collect_candidates(db, RUN_DATE, mini_cfg())
    hl = rule_highlights(cands, mini_cfg())
    assert 0 < len(hl) <= mini_cfg()['report']['max_highlights']
    for h in hl:
        assert h['name'] and h['url'].startswith('http')
        assert h['reason'] and h['facts']
    db.close()


def test_collect_candidates_github_24h_delta():
    db = _seed(_make_db())
    cands = collect_candidates(db, RUN_DATE, mini_cfg())
    gh = [c for c in cands if c['platform'] == 'github']
    assert len(gh) == 2
    by_name = {c['name']: c for c in gh}
    # 昨日 110 → 今日 160 ⇒ 24h +50;创建于 2026-10-11 ⇒ created_days=3
    assert by_name['acme/agentkit']['stars_24h'] == 50
    assert by_name['acme/ragflow']['stars_24h'] == 100
    assert by_name['acme/agentkit']['created_days'] == 3
    db.close()


def test_validate_report_flags_missing_keyword_and_overflow():
    cfg = mini_cfg()
    assert validate_report('每日ai情报 正文', cfg) == []
    issues = validate_report('没有关键词的正文', cfg)
    assert any('关键词' in i for i in issues)
    cfg2 = dict(cfg, report={**cfg['report'], 'max_chars': 10})
    assert validate_report('每日ai情报 ' + 'x' * 100, cfg2)
    assert any('长度' in i for i in validate_report('每日ai情报 ' + 'x' * 100, cfg2))


def test_save_daily_report_idempotent_not_overwrite_sent():
    db = _seed(_make_db())
    db.save_daily_report(RUN_DATE, 'body v1')
    row = db.get_daily_report(RUN_DATE)
    assert row['sent_status'] == 'pending'
    db.mark_report_sent(RUN_DATE)
    # 已 sent 的日报不被覆盖(防止重新生成覆盖"已送达"事实)
    assert db.save_daily_report(RUN_DATE, 'body v2') == 'skipped_sent'
    assert db.get_daily_report(RUN_DATE)['body'] == 'body v1'
    assert db.get_daily_report(RUN_DATE)['sent_status'] == 'sent'
    db.close()


def test_save_daily_report_replaces_pending():
    db = _seed(_make_db())
    db.save_daily_report(RUN_DATE, 'body v1')
    assert db.save_daily_report(RUN_DATE, 'body v2') == 'replaced'
    assert db.get_daily_report(RUN_DATE)['body'] == 'body v2'
    db.close()


# ---------------------------------------------------------------------------
# AIQI-7 问题1:今日值得关注 新生代优先 + 上榜理由可解释
# ---------------------------------------------------------------------------
def test_rule_highlights_new_generation_first():
    """有快照时:新生代优先;老项目仅增量达阈值才上榜;上榜理由基于真实字段。"""
    db = _seed(_make_db())
    cands = collect_candidates(db, RUN_DATE, mini_cfg())
    hl = rule_highlights(cands, mini_cfg())
    names = [h['name'] for h in hl]
    # agentkit 创建 10-11、ragflow 创建 10-12 均为新生代;两个都入选
    assert 'acme/agentkit' in names and 'acme/ragflow' in names
    for h in hl:
        assert h['reason']
        assert '规则标签' not in h['reason']  # 不再用模型/规则模糊话术
        assert h['facts']


def test_rule_highlights_excludes_old_without_delta():
    """首日(无快照):老项目(如 ollama 1206 天)不上榜,宁缺毋滥不凑数。"""
    db = _make_db()
    now = '2026-10-14T08:00:00+00:00'
    sid = db.upsert_source('github:search', 'GitHub Search API', 'github')
    # 新生代 + 老项目(创建 1206 天,无快照无法算增量)
    for i, (repo, created) in enumerate((('acme/newproj', '2026-10-11T00:00:00Z'),
                                         ('ollama/ollama', '2023-07-01T00:00:00Z'))):
        db.upsert_item(platform='github', title=repo, url=f'https://github.com/{repo}',
                       summary='AI tool', published_at=now, content_hash=i,
                       category='opensource', extra={
                           'full_name': repo, 'stargazers_count': 100 if i == 0 else 183114,
                           'created_at': created, 'topics': ['ai'],
                       }, source_id=sid, first_seen_at=now)
    cands = collect_candidates(db, RUN_DATE, mini_cfg())
    hl = rule_highlights(cands, mini_cfg())
    names = [h['name'] for h in hl]
    assert 'acme/newproj' in names
    assert 'ollama/ollama' not in names
    # 上榜理由带「初步判断」(快照不足)
    new_h = next(h for h in hl if h['name'] == 'acme/newproj')
    assert '初步判断' in new_h['reason']
    db.close()


def test_rule_highlights_old_with_real_growth_allowed():
    """老项目只要 24h 增量显著(≥阈值)也允许上榜(成员修正:不否认老项目价值)。"""
    db = _make_db()
    now = '2026-10-14T08:00:00+00:00'
    sid = db.upsert_source('github:search', 'GitHub Search API', 'github')
    db.upsert_snapshot('acme/oldbutgrowing', '2026-10-13', stars=1000, forks=10,
                       pushed_at='2026-10-13T08:00:00Z', created_at='2024-01-01T00:00:00Z')
    db.upsert_snapshot('acme/oldbutgrowing', RUN_DATE, stars=1300, forks=12,
                       pushed_at='2026-10-14T08:00:00Z', created_at='2024-01-01T00:00:00Z',
                       topics=['ai'], description='old project still growing')
    db.upsert_item(platform='github', title='acme/oldbutgrowing',
                   url='https://github.com/acme/oldbutgrowing',
                   summary='old project still growing', published_at=now, content_hash=1,
                   category='opensource', extra={
                       'full_name': 'acme/oldbutgrowing', 'stargazers_count': 1300,
                       'created_at': '2024-01-01T00:00:00Z', 'topics': ['ai'],
                   }, source_id=sid, first_seen_at=now)
    cands = collect_candidates(db, RUN_DATE, mini_cfg())
    hl = rule_highlights(cands, mini_cfg())
    names = [h['name'] for h in hl]
    assert 'acme/oldbutgrowing' in names  # 24h +300 ≥ 阈值 50 → 上榜
    h = next(h for h in hl if h['name'] == 'acme/oldbutgrowing')
    assert '24h Star +300(前一日 1000)' in h['reason']
    db.close()


# ---------------------------------------------------------------------------
# AIQI-7 问题2:解决方案与产品发布 一行精简摘要
# ---------------------------------------------------------------------------
def test_section3_resume_from_existing_summary():
    """有 summary 的条目:显示一行精简摘要(≤ summary_max_chars)。"""
    db = _seed(_make_db())
    cands = collect_candidates(db, RUN_DATE, mini_cfg())
    data = build_report(db, RUN_DATE, mini_cfg(), cands)
    sec3 = data['body'].split('三、解决方案与产品发布')[1].split('四、趋势观察')[0]
    lines = [l for l in sec3.strip().splitlines() if l.startswith('-')]
    assert lines
    for l in lines:
        assert ' — ' in l
        body = l.split(' — ', 1)[1]
        assert body.strip()  # 非空


def test_section3_empty_summary_notes_reason():
    """summary 为空:注明无法产生摘要的理由,不显示空白。"""
    db = _make_db()
    sid = db.upsert_source('https://deepmind.google/blog/rss.xml', 'DeepMind', 'rss')
    db.upsert_item(platform='rss', title='Developing Gemini 4 Argon responsibly',
                   url='https://deepmind.google/blog/x', summary='',
                   published_at='2026-10-14T08:00:00+00:00', content_hash=1,
                   category='news', extra={}, source_id=sid,
                   first_seen_at='2026-10-14T08:00:00+00:00')
    cands = collect_candidates(db, RUN_DATE, mini_cfg())
    data = build_report(db, RUN_DATE, mini_cfg(), cands)
    sec3 = data['body'].split('三、解决方案与产品发布')[1].split('四、趋势观察')[0]
    assert '原文未提供摘要' in sec3
    db.close()


def test_section3_fetch_summary_config_effect(monkeypatch):
    """fetch_summary=true 时对入选条目抓正文首段;false(默认)不抓取只注明理由。"""
    import src.summarization.solution_summarizer as summarize
    calls = {'n': 0}
    def fake_fetch(url, cfg):
        calls['n'] += 1
        return 'This is the first paragraph of the article body about gemini 4 argon.'
    monkeypatch.setattr(summarize, '_fetch_first_paragraph', fake_fetch)

    # 默认 false:不抓取
    db = _make_db()
    sid = db.upsert_source('https://deepmind.google/blog/rss.xml', 'DeepMind', 'rss')
    db.upsert_item(platform='rss', title='Developing Gemini 4 Argon responsibly',
                   url='https://deepmind.google/blog/x', summary='',
                   published_at='2026-10-14T08:00:00+00:00', content_hash=1,
                   category='news', extra={}, source_id=sid,
                   first_seen_at='2026-10-14T08:00:00+00:00')
    cfg = dict(mini_cfg(), report=dict(mini_cfg()['report'], fetch_summary=False))
    cands = collect_candidates(db, RUN_DATE, cfg)
    build_report(db, RUN_DATE, cfg, cands)
    assert calls['n'] == 0
    db.close()

    # true:抓正文首段
    db2 = _make_db()
    sid2 = db2.upsert_source('https://deepmind.google/blog/rss.xml', 'DeepMind', 'rss')
    db2.upsert_item(platform='rss', title='Developing Gemini 4 Argon responsibly',
                    url='https://deepmind.google/blog/y', summary='',
                    published_at='2026-10-14T08:00:00+00:00', content_hash=2,
                    category='news', extra={}, source_id=sid2,
                    first_seen_at='2026-10-14T08:00:00+00:00')
    cfg2 = dict(mini_cfg(), report=dict(mini_cfg()['report'], fetch_summary=True))
    cands2 = collect_candidates(db2, RUN_DATE, cfg2)
    data2 = build_report(db2, RUN_DATE, cfg2, cands2)
    assert calls['n'] == 1
    assert 'first paragraph of the article' in data2['body']
    db2.close()


# ---------------------------------------------------------------------------
# AIQI-7 问题3:采集状态分段显示(发现 → 筛选 → 去重 → 限量)
# ---------------------------------------------------------------------------
def test_section5_segmented_stats():
    db = _seed(_make_db())
    cands = collect_candidates(db, RUN_DATE, mini_cfg())
    stats = {
        'platforms': {'github': {'fetched_raw': 29, 'passed_rules': 4, 'after_cap': 2},
                     'hackernews': {'fetched': 25, 'passed_rules': 1, 'after_cap': 1},
                     'rss': {'fetched': 1036, 'passed_rules': 2, 'after_cap': 2},
                     'hf_papers': {'fetched': 5, 'passed_rules': 1, 'after_cap': 1}},
        'pipeline': {'discovered': {'github': 29, 'hackernews': 25, 'rss': 1036, 'hf_papers': 5},
                     'after_filter': {'github': 4, 'hackernews': 1, 'rss': 2, 'hf_papers': 1},
                     'after_dedup': 8,
                     'after_cap': {'github': 2, 'hackernews': 1, 'rss': 2, 'hf_papers': 1}},
        'dedup': {'after_dedup': 8},
    }
    data = build_report(db, RUN_DATE, mini_cfg(), cands, stats=stats)
    sec5 = data['body'].split('五、采集状态')[1]
    assert '发现:1095' in sec5
    assert '筛选:8' in sec5
    assert '去重:8' in sec5
    assert '限量:6' in sec5
    db.close()
