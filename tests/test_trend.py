# -*- coding: utf-8 -*-
"""趋势引擎单元测试:五态(新出现/持续升温/突然增长/进入成熟期/热度回落)、
24h/7d/30d 窗口、首日基线、重复降权。全部用受控合成数据,不依赖网络。"""
import json
from datetime import date

from src.trend import (repo_trend, topic_trend, build_trends,
                       STATE_NEW, STATE_WARMING, STATE_SURGE,
                       STATE_MATURE, STATE_DECLINE)
from conftest import trend_cfg

AI_KW = ['ai', 'llm', 'agent', 'rag', 'gemini', 'claude']


def _snap(repo, d, stars, created='2025-06-01', pushed='2026-10-14T08:00:00Z',
          topics=None, desc='AI agent tooling framework'):
    return {'repo': repo, 'date': d, 'stars': stars, 'forks': 10,
            'pushed_at': pushed, 'created_at': created, 'description': desc,
            'language': 'Python', 'topics': json.dumps(topics or ['ai', 'agents']),
            'fetched_at': None}


def _series(repo, dates_stars, **kw):
    return [_snap(repo, d, s, **kw) for d, s in dates_stars]


def _stat(topic, d, count, source_count=1):
    return {'topic': topic, 'stat_date': d, 'count': count,
            'source_count': source_count, 'representative_item_id': 1}


D14 = date(2026, 10, 14)


# ---------------------------------------------------------------------------
# 项目趋势
# ---------------------------------------------------------------------------
def test_new_project_first_day():
    t = repo_trend('owner/new', _series('owner/new', [('2026-10-14', 120)],
                                        created='2026-10-01'), D14, trend_cfg(), AI_KW)
    assert t['state'] == STATE_NEW
    assert t['confidence'] == '初步判断'
    assert t['windows']['24h']['status'] == '窗口不足'
    assert t['windows']['7d']['status'] == '窗口不足'
    assert t['windows']['30d']['status'] == '窗口不足'
    assert any('首次入选' in e for e in t['evidence'])
    assert any('尚待观察' in e for e in t['evidence'])
    assert any('新项目' in e for e in t['evidence'])  # 创建 13 天前 → 新鲜度证据


def test_warming_over_days():
    t = repo_trend('owner/warm', _series('owner/warm', [
        ('2026-10-10', 100), ('2026-10-11', 105), ('2026-10-12', 112),
        ('2026-10-13', 121), ('2026-10-14', 132)]), D14, trend_cfg(), AI_KW)
    assert t['state'] == STATE_WARMING
    assert t['confidence'] == '初步判断'          # 历史不足 7 天
    assert t['windows']['24h']['status'] == 'ok'
    assert t['windows']['7d']['status'] == '窗口不足'
    assert t['numbers']['delta_24h'] == 11
    assert any('增量 +11' in e for e in t['evidence'])


def test_sudden_growth():
    t = repo_trend('owner/surge', _series('owner/surge', [
        ('2026-10-08', 100), ('2026-10-09', 101), ('2026-10-10', 102),
        ('2026-10-11', 103), ('2026-10-12', 104), ('2026-10-13', 105),
        ('2026-10-14', 120)]), D14, trend_cfg(), AI_KW)
    assert t['state'] == STATE_SURGE
    assert t['confidence'] is None               # 7 天历史,可下结论
    assert t['windows']['7d']['status'] == 'ok'
    assert t['windows']['30d']['status'] == '窗口不足'
    assert t['numbers']['delta_24h'] == 15
    assert any('增量 +15' in e for e in t['evidence'])


def test_decline():
    t = repo_trend('owner/decl', _series('owner/decl', [
        ('2026-10-10', 100), ('2026-10-11', 110), ('2026-10-12', 105),
        ('2026-10-13', 95), ('2026-10-14', 90)]), D14, trend_cfg(), AI_KW)
    assert t['state'] == STATE_DECLINE
    assert t['confidence'] == '初步判断'          # 历史不足 7 天
    assert t['numbers']['delta_24h'] == -5
    assert any('增量 -5' in e for e in t['evidence'])


def test_maturity_high_stars_flat():
    stars = [1_000_000 + i * 10 for i in range(8)]
    dates = [(date(2026, 10, 7 + i).isoformat(), s) for i, s in enumerate(stars)]
    t = repo_trend('owner/mature', _series('owner/mature', dates,
                                           created='2020-01-01'),
                   D14, trend_cfg(), AI_KW)
    assert t['state'] == STATE_MATURE
    assert t['confidence'] is None
    assert t['windows']['7d']['status'] == 'ok'
    assert any('成熟' in e or '日均' in e for e in t['evidence'])


def test_stagnation_downgrade():
    dates = [(date(2026, 10, 7 + i).isoformat(), 100) for i in range(8)]
    t = repo_trend('owner/static', _series('owner/static', dates,
                                           created='2024-01-01'),
                   D14, trend_cfg(), AI_KW)
    assert t['downgraded'] is True
    assert '降权' in ' '.join(t['evidence'])
    assert any('连续 7 日入选' in e for e in t['evidence'])


def test_two_day_positive_is_warming_preliminary():
    t = repo_trend('owner/two', _series('owner/two', [
        ('2026-10-13', 1000), ('2026-10-14', 1100)]), D14, trend_cfg(), AI_KW)
    assert t['state'] == STATE_WARMING
    assert t['confidence'] == '初步判断'
    assert t['windows']['7d']['status'] == '窗口不足'


def test_two_day_flat_is_honest_waiting():
    t = repo_trend('owner/flat', _series('owner/flat', [
        ('2026-10-13', 1000), ('2026-10-14', 1000)]), D14, trend_cfg(), AI_KW)
    assert t['state'] == STATE_NEW
    assert any('尚待观察' in e for e in t['evidence'])


def test_missing_yesterday_window():
    # 有两天快照但中间断档 → 24h 窗口不足,不臆造增量
    t = repo_trend('owner/gap', _series('owner/gap', [
        ('2026-10-10', 100), ('2026-10-14', 150)]), D14, trend_cfg(), AI_KW)
    assert t['windows']['24h']['status'] == '窗口不足'
    assert t['state'] == STATE_NEW
    assert any('昨日无快照' in e for e in t['evidence'])


def test_ai_relevance_evidence():
    t = repo_trend('owner/rel', _series('owner/rel', [('2026-10-14', 50)],
                                        topics=['ai-agents', 'llm'], desc='RAG toolkit'),
                   D14, trend_cfg(), AI_KW)
    assert any('AI 相关性' in e and '命中' in e for e in t['evidence'])


# ---------------------------------------------------------------------------
# 主题趋势
# ---------------------------------------------------------------------------
def test_topic_first_day():
    t = topic_trend('agent', [_stat('agent', '2026-10-14', 3, 2)], D14, trend_cfg())
    assert t['state'] == STATE_NEW
    assert t['confidence'] == '初步判断'
    assert t['windows']['24h']['status'] == '窗口不足'


def test_topic_warming_multi_source():
    rows = [_stat('llm', '2026-10-10', 1), _stat('llm', '2026-10-11', 2),
            _stat('llm', '2026-10-12', 3, 2), _stat('llm', '2026-10-13', 4),
            _stat('llm', '2026-10-14', 5, 2)]
    t = topic_trend('llm', rows, D14, trend_cfg())
    assert t['state'] == STATE_WARMING
    assert t['confidence'] == '初步判断'
    assert any('增量 +1' in e for e in t['evidence'])


def test_topic_sudden_growth():
    rows = [_stat('rag', '2026-10-08', 1), _stat('rag', '2026-10-09', 1),
            _stat('rag', '2026-10-10', 1), _stat('rag', '2026-10-11', 1),
            _stat('rag', '2026-10-12', 1), _stat('rag', '2026-10-13', 1),
            _stat('rag', '2026-10-14', 6)]
    t = topic_trend('rag', rows, D14, trend_cfg())
    assert t['state'] == STATE_SURGE
    assert any('增量 +5' in e for e in t['evidence'])


def test_topic_decline():
    rows = [_stat('agent', '2026-10-10', 5), _stat('agent', '2026-10-11', 5),
            _stat('agent', '2026-10-12', 4), _stat('agent', '2026-10-13', 3),
            _stat('agent', '2026-10-14', 2)]
    t = topic_trend('agent', rows, D14, trend_cfg())
    assert t['state'] == STATE_DECLINE


def test_topic_maturity():
    rows = [_stat('embedding', (date(2026, 10, 5 + i)).isoformat(), 4)
            for i in range(10)]
    t = topic_trend('embedding', rows, D14, trend_cfg())
    assert t['state'] == STATE_MATURE


# ---------------------------------------------------------------------------
# 汇总:首日基线
# ---------------------------------------------------------------------------
def test_build_trends_first_day_baseline(db):
    db.upsert_snapshot('owner/a', '2026-10-14', stars=100, forks=1,
                       pushed_at='2026-10-14T08:00:00Z', created_at='2026-10-01T00:00:00Z')
    db.upsert_snapshot('owner/b', '2026-10-14', stars=200, forks=2,
                       pushed_at='2026-10-14T08:00:00Z', created_at='2025-01-01T00:00:00Z')
    out = build_trends(db, '2026-10-14', trend_cfg(), AI_KW)
    assert out['first_snapshot_day'] is True
    assert len(out['projects']) == 2
    for p in out['projects']:
        assert p['confidence'] == '初步判断'
        assert any('首日运行' in e for e in p['evidence'])
        assert p['state'] == STATE_NEW
    # 顺序固定(展示用,非评分排序)
    assert out['projects'][0]['repo'] == 'owner/a'


def test_build_trends_second_day_not_first(db):
    db.upsert_snapshot('owner/a', '2026-10-13', stars=90, forks=1,
                       pushed_at='2026-10-13T08:00:00Z', created_at='2026-10-01T00:00:00Z')
    db.upsert_snapshot('owner/a', '2026-10-14', stars=110, forks=1,
                       pushed_at='2026-10-14T08:00:00Z', created_at='2026-10-01T00:00:00Z')
    out = build_trends(db, '2026-10-14', trend_cfg(), AI_KW)
    assert out['first_snapshot_day'] is False
    p = out['projects'][0]
    assert p['state'] == STATE_WARMING
    assert p['numbers']['delta_24h'] == 20
    assert not any('首日运行' in e for e in p['evidence'])
