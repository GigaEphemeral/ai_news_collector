# -*- coding: utf-8 -*-
"""日报模板测试(阶段04):关键词/长度/栏目/链接/规则降级选择/幂等保存。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from conftest import trend_cfg

from src.db import Database
from src.report import (NO_HOTSPOT_TEXT, build_report, collect_candidates,
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
