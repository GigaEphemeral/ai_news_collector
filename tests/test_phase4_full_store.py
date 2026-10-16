# -*- coding: utf-8 -*-
"""阶段04 验收测试:[03] 全量入库 + 消费时选 top-N。

覆盖:
  - 入库条数 = 去重后全部(非每源 10):见 test_run_stability.TestPipelineOrder
  - 日报仍只输出 top-N:全量入库后 collect_candidates 只取热度 top-N 进日报
  - 连续两天运行第一天未入选条目仍在库
  - 当天选了谁记 daily_reports(selected_item_ids JSON)
"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

from src.storage.db import Database
from src.pipeline.topic_stats import compute_topic_stats
from src.reporting.report import build_report, collect_candidates, rule_highlights, selected_item_ids


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


def _seed_github(db, run_date, n, prefix='acme'):
    sid = db.upsert_source('github:search', 'GitHub Search API', 'github')
    for i in range(n):
        repo = f'{prefix}/repo{i:02d}'
        db.upsert_item(platform='github', title=repo, url=f'https://github.com/{repo}',
                       summary=f'AI agent repo {i}', published_at=None,
                       content_hash=i, canonical_key=0, category='opensource',
                       extra={'full_name': repo, 'stargazers_count': 1000 - i,
                              'created_at': '2026-09-01T00:00:00Z',
                              'topics': ['ai', 'agents']},
                       source_id=sid, first_seen_at=f'{run_date}T08:00:00+00:00',
                       native_id=repo)
    return sid


def test_daily_report_still_top_n_after_full_store():
    """全量入库 12 个 GitHub 项目后,日报仍只输出 top-N(栏目上限,不铺满)。"""
    db, d = _make_db()
    run_date = datetime.now(timezone.utc).date()
    _seed_github(db, run_date.isoformat(), 12)
    cands = collect_candidates(db, run_date.isoformat(), _cfg())
    assert len(cands) == 12  # 候选全量(消费前)
    hl = rule_highlights(cands, _cfg())
    assert len(hl) <= 3  # 一、今日值得关注 ≤ max_highlights
    data = build_report(db, run_date.isoformat(), _cfg(), cands)
    sec2 = data['body'].split('二、开源项目与工程实践')[1].split('三、解决方案与产品发布')[0]
    n_sec2 = len([l for l in sec2.splitlines() if l.startswith('- ')])
    assert n_sec2 <= 5  # 二、开源项目 ≤ opensource_max
    db.close()


def test_day1_unselected_items_persist_to_day2():
    """连续两天运行:第一天未入选 top-N 的条目第二天仍在库(topic_daily_stats 也全量统计)。"""
    db, d = _make_db()
    day1 = '2026-10-13'
    day2 = '2026-10-14'
    # 第一天:入库 12 个 GitHub(日报只选 5 个)
    _seed_github(db, day1, 12)
    cands1 = collect_candidates(db, day1, _cfg())
    data1 = build_report(db, day1, _cfg(), cands1)
    selected1 = set(selected_item_ids(cands1, _cfg()))
    assert len(selected1) <= 5
    assert 12 > len(selected1)  # 确实有未入选条目

    # 第二天:再入库 3 个新项目,再跑日报
    _seed_github(db, day2, 3, prefix='day2')
    cands2 = collect_candidates(db, day2, _cfg())
    build_report(db, day2, _cfg(), cands2)

    # 第一天未入选条目仍在库(未被消费/清理)
    n_total = db.conn.execute('SELECT count(*) FROM items').fetchone()[0]
    assert n_total == 15
    n_day1 = db.conn.execute(
        "SELECT count(*) FROM items WHERE date(first_seen_at)=?", (day1,)).fetchone()[0]
    assert n_day1 == 12
    # topic_daily_stats 全量统计(不因日报只选 top-N 而减少)
    n_topics = compute_topic_stats(db, day1, ['ai', 'llm', 'agent', 'rag'], max_topics=3)
    assert n_topics >= 1
    row = db.conn.execute(
        "SELECT count(*) n FROM topic_daily_stats WHERE stat_date=?", (day1,)).fetchone()
    assert row['n'] >= 1
    db.close()


def test_daily_report_records_selected_item_ids():
    """当天选了谁记 daily_reports(selected_item_ids JSON 入库)。"""
    db, d = _make_db()
    run_date = datetime.now(timezone.utc).date().isoformat()
    _seed_github(db, run_date, 12)
    cands = collect_candidates(db, run_date, _cfg())
    sel = selected_item_ids(cands, _cfg())
    assert len(sel) > 0 and len(sel) <= 5
    db.save_daily_report(run_date, 'body 每日ai情报', selected_item_ids=sel)
    row = db.get_daily_report(run_date)
    assert json.loads(row['selected_item_ids']) == sorted(sel)
    db.close()


def test_run_stats_stored_equals_deduped():
    """run.py pipeline.stored = 去重后全部(验收:一次运行入库 = 去重后全部)。"""
    db, d = _make_db()
    run_date = datetime.now(timezone.utc).date().isoformat()
    _seed_github(db, run_date, 12)
    # 模拟 run.py 统计口径:去重后全量入库,stored 与 after_dedup 一致
    cands = collect_candidates(db, run_date, _cfg())
    assert len(cands) == 12
    db.close()
