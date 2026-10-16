# -*- coding: utf-8 -*-
"""排序与上榜理由测试(阶段04修复 AIQI-7,问题1):新生代优先、老项目增量达阈值才上榜、
首日/快照不足降级、上榜理由可解释。纯规则,不评分,不联网。"""
import json
import tempfile
import os

from conftest import trend_cfg

from src.storage.db import Database
from src.pipeline.ranking import (github_sort_key, github_eligible, selection_reason,
                         github_entry_sort_key)


def cfg_r(**kw):
    base = {'sort_by': 'freshness', 'new_days': 30, 'old_repo_min_delta_24h': 50,
            'old_repo_min_growth_pct': 0.5, 'recent_push_days': 7}
    base.update(kw)
    return base


def test_freshness_young_first():
    """freshness:新生代(创建 ≤ 30 天)排最前;层内按 24h 增量降序;老项目增量达阈值第二。"""
    young_big = {'created_days': 2, 'stars_24h': 10, 'stars': 100, 'pushed_days': 0}
    young_small = {'created_days': 29, 'stars_24h': 200, 'stars': 500, 'pushed_days': 1}
    old_growing = {'created_days': 300, 'stars_24h': 60, 'stars': 1000, 'pushed_days': 0}
    old_stale = {'created_days': 1206, 'stars_24h': None, 'stars': 183114, 'pushed_days': 5}
    items = [old_stale, young_big, old_growing, young_small]
    items.sort(key=lambda c: github_sort_key(c, cfg_r()))
    assert items[0] is young_small       # 新生代 + 增量高者优先
    assert items[1] is young_big
    assert items[2] is old_growing      # 老项目增量达阈值(60 ≥ 50)第二层
    assert items[3] is old_stale        # 无增量老项目垫底


def test_freshness_old_without_delta_last():
    """老项目无 24h 增量数据(如 ollama 1206 天)→ 排最后,不顶新生代。"""
    old_no_delta = {'created_days': 1206, 'stars_24h': None, 'stars': 183114, 'pushed_days': 0}
    young = {'created_days': 3, 'stars_24h': None, 'stars': 100, 'pushed_days': 0}
    assert github_sort_key(young, cfg_r()) < github_sort_key(old_no_delta, cfg_r())


def test_eligible_with_delta_data():
    """快照足够(has_delta_data=True):新生代全入选;老项目仅增量达阈值入选。"""
    cfg = cfg_r()
    young = {'created_days': 3, 'stars_24h': 5, 'stars': 100, 'pushed_days': 0}
    old_big_delta = {'created_days': 300, 'stars_24h': 60, 'stars': 1000, 'pushed_days': 0}
    old_growth_pct = {'created_days': 300, 'stars_24h': 20, 'stars': 200, 'pushed_days': 0}  # 10% ≥ 0.5%
    old_small = {'created_days': 300, 'stars_24h': 10, 'stars': 10000, 'pushed_days': 0}  # 0.1% < 0.5%, 10 < 50
    old_none = {'created_days': 300, 'stars_24h': None, 'stars': 1000, 'pushed_days': 0}
    assert github_eligible(young, cfg, has_delta_data=True)
    assert github_eligible(old_big_delta, cfg, has_delta_data=True)
    assert github_eligible(old_growth_pct, cfg, has_delta_data=True)
    assert not github_eligible(old_small, cfg, has_delta_data=True)
    assert not github_eligible(old_none, cfg, has_delta_data=True)


def test_eligible_first_day_only_young_pushed():
    """快照不足(has_delta_data=False):仅新生代且近期 push 入选;老项目一律不上榜。"""
    cfg = cfg_r()
    young_pushed = {'created_days': 3, 'stars_24h': None, 'stars': 100, 'pushed_days': 0}
    young_no_push = {'created_days': 3, 'stars_24h': None, 'stars': 100, 'pushed_days': 40}
    old = {'created_days': 300, 'stars_24h': None, 'stars': 183114, 'pushed_days': 0}
    assert github_eligible(young_pushed, cfg, has_delta_data=False)
    assert not github_eligible(young_no_push, cfg, has_delta_data=False)
    assert not github_eligible(old, cfg, has_delta_data=False)


def test_sort_by_growth_and_stars():
    cfg = cfg_r(sort_by='growth')
    a = {'created_days': 300, 'stars_24h': 200, 'stars': 1000, 'pushed_days': 0}
    b = {'created_days': 3, 'stars_24h': None, 'stars': 500, 'pushed_days': 0}
    assert github_sort_key(a, cfg) < github_sort_key(b, cfg)  # 有增量在前
    cfg2 = cfg_r(sort_by='stars')
    a2 = {'created_days': 1206, 'stars_24h': None, 'stars': 183114, 'pushed_days': 0}
    b2 = {'created_days': 3, 'stars_24h': 200, 'stars': 100, 'pushed_days': 0}
    assert github_sort_key(a2, cfg2) < github_sort_key(b2, cfg2)  # star 总数优先


def test_selection_reason_concrete():
    """上榜理由基于库中真实字段,可解释(不用模型模糊话术)。"""
    cfg = cfg_r()
    r = selection_reason({'platform': 'github', 'name': 'x/y',
                          'created_days': 3, 'stars_24h': 250,
                          'stars': 1250, 'pushed_days': 0}, cfg, has_delta_data=True)
    assert '新生代(创建 3 天)' in r
    assert '24h Star +250(前一日 1000)' in r
    assert '近 24h 有更新' in r
    assert '初步判断' not in r

    r2 = selection_reason({'platform': 'github', 'name': 'x/y',
                           'created_days': 3, 'stars_24h': None,
                           'stars': 100, 'pushed_days': 0}, cfg, has_delta_data=False)
    assert '初步判断' in r2  # 快照不足标注初步判断

    r3 = selection_reason({'platform': 'hackernews', 'stars': 200}, cfg)
    assert r3 == 'HN 200 分'


def test_entry_sort_key_uses_db_prev_snapshot():
    """run.py 限量前排序:24h 增量取库中昨日快照差值。"""
    d = tempfile.mkdtemp(prefix='ai_news_mvp_rank_')
    db = Database(os.path.join(d, 't.sqlite3'))
    db.upsert_snapshot('acme/agentkit', '2026-10-13', stars=1000, forks=5,
                       pushed_at='2026-10-13T08:00:00Z', created_at='2026-10-01T00:00:00Z')
    entry = {'extra': {'full_name': 'acme/agentkit', 'stargazers_count': 1250,
                       'created_at': '2026-10-01T00:00:00Z',
                       'pushed_at': '2026-10-14T08:00:00Z'}}
    k = github_entry_sort_key(entry, cfg_r(), '2026-10-14', db)
    # 昨日 1000 → 今日 1250 ⇒ 24h +250;创建 13 天 → 新生代层 0
    assert k[0] == 0
    assert k[1] == -250
    db.close()
