#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQI-7 日报质量修复演示(阶段04修复验收)。

用受控合成数据在一个临时 SQLite 库中模拟:
  - 2 天快照(2026-10-13 / 2026-10-14),可算 24h 增量;
  - 新生代项目(创建 3 天)+ 老项目有显著增量(24h +300)+ 老项目无增量(如 ollama 1206 天);
  - 解决方案 RSS:一条有 summary、一条 summary 为空;
  - 采集状态分段(发现 → 筛选 → 去重 → 限量)。

验证修复(问题1/2/3,成员反馈):
  1. 「今日值得关注」优先新生代;老项目仅在有真实增量时上榜;上榜理由基于库中真实字段可解释;
     无增量老项目(ollama 1206 天)不上榜、不凑数;首日/快照不足标注「初步判断」。
  2. 「解决方案与产品发布」每条带一行精简摘要;summary 为空注明理由(默认 fetch_summary=false 不抓取)。
  3. 采集状态分段显示;处理顺序 筛选→去重→限量 体现在入库条目数与 pipeline 统计。

运行:
    ./.venv/bin/python scripts/demo_report_aiqi7.py   # 项目根下(虚拟环境在项目根)
    # 部署环境下虚拟环境在项目根上一级 code/.venv,可写:
    #   ../.venv/bin/python scripts/demo_report_aiqi7.py
"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.db import Database
from src.report import build_report, collect_candidates, rule_highlights

RUN_DATE = date(2026, 10, 14)
D_PREV = RUN_DATE - timedelta(days=1)


def d(offset=0):
    return (RUN_DATE - timedelta(days=offset)).isoformat()


def seed(db):
    now = f'{RUN_DATE}T08:00:00+00:00'
    gh = db.upsert_source('github:search', 'GitHub Search API', 'github')

    def add_repo(repo, stars, created, pushed, delta):
        prev_stars = stars - delta
        db.upsert_snapshot(repo, D_PREV.isoformat(), stars=prev_stars, forks=5,
                           pushed_at=f'{D_PREV}T08:00:00Z', created_at=created)
        db.upsert_snapshot(repo, RUN_DATE.isoformat(), stars=stars, forks=6,
                           pushed_at=pushed, created_at=created,
                           topics=['ai', 'agents'], description=f'{repo} description')
        db.upsert_item(platform='github', title=repo, url=f'https://github.com/{repo}',
                       summary=f'{repo} description', published_at=pushed,
                       content_hash=hash(repo) % 1000, category='opensource',
                       extra={'full_name': repo, 'stargazers_count': stars,
                              'created_at': created, 'topics': ['ai', 'agents']},
                       source_id=gh, first_seen_at=now)

    # 新生代:创建 3 天,24h +20
    add_repo('acme/agentkit', 150, '2026-10-11T00:00:00Z', '2026-10-14T07:00:00Z', delta=20)
    # 老项目有显著增量:创建 300 天,24h +300
    add_repo('acme/ragflow', 3000, '2025-12-18T00:00:00Z', '2026-10-14T07:00:00Z', delta=300)
    # 老项目无增量:ollama 1206 天,24h 0(不应被顶上来凑数)
    add_repo('ollama/ollama', 183114, '2023-07-01T00:00:00Z', '2026-10-14T07:00:00Z', delta=0)

    # 解决方案 RSS:一条有 summary,一条为空
    rss = db.upsert_source('https://openai.com/news/rss.xml', 'OpenAI Blog', 'rss')
    db.upsert_item(platform='rss', title='The EU next chapter in AI is agentic',
                   url='https://openai.com/index/the-eus-next-chapter-in-ai-is-agentic',
                   summary='New analysis shows agentic AI adoption growing across EU enterprises.',
                   published_at=f'{RUN_DATE}T06:00:00+00:00', content_hash=101,
                   category='news', extra={}, source_id=rss, first_seen_at=now)
    db.upsert_item(platform='rss', title='Developing Gemini 4 Argon responsibly and safely',
                   url='https://deepmind.google/blog/developing-gemini-4-argon-responsibly-and-safely/',
                   summary='',  # 空 summary,验证注明理由
                   published_at=f'{RUN_DATE}T05:00:00+00:00', content_hash=102,
                   category='news', extra={}, source_id=rss, first_seen_at=now)

    # HN 一条
    hn = db.upsert_source('hackernews:topstories', 'Hacker News', 'hackernews')
    db.upsert_item(platform='hackernews', title='AgentKit: the agent harness performance system',
                   url='https://news.ycombinator.com/item?id=9001',
                   summary='Discussion about the agent harness',
                   published_at=f'{RUN_DATE}T04:00:00+00:00', content_hash=103,
                   category='discussion', extra={'points': 240, 'comments': 20, 'hn_id': 9001},
                   source_id=hn, first_seen_at=now)

    # 采集状态(模拟 run.py 的分段统计;实际运行由 run.py 生成)
    stats = {
        'platforms': {'github': {'fetched_raw': 30, 'passed_rules': 3, 'after_cap': 3},
                      'hackernews': {'fetched': 25, 'passed_rules': 1, 'after_cap': 1},
                      'rss': {'fetched': 1036, 'passed_rules': 2, 'after_cap': 2},
                      'hf_papers': {'fetched': 5, 'passed_rules': 0, 'after_cap': 0}},
        'pipeline': {'discovered': {'github': 30, 'hackernews': 25, 'rss': 1036, 'hf_papers': 5},
                     'after_filter': {'github': 3, 'hackernews': 1, 'rss': 2, 'hf_papers': 0},
                     'after_dedup': 6,
                     'after_cap': {'github': 3, 'hackernews': 1, 'rss': 2, 'hf_papers': 0}},
        'dedup': {'after_dedup': 6},
    }
    return stats


def main():
    cfg = load_config()
    tmp = tempfile.mkdtemp(prefix='ai_news_demo_aiqi7_')
    db = Database(os.path.join(tmp, 'demo.sqlite3'))
    stats = seed(db)

    cands = collect_candidates(db, RUN_DATE.isoformat(), cfg, trends=None)
    print('=' * 76)
    print(f'AIQI-7 日报质量修复演示(2 天快照,RUN_DATE={RUN_DATE})')
    print('=' * 76)

    print('\n--- 候选排序(GitHub,新生代优先 + 老项目增量) ---')
    for c in cands:
        if c['platform'] == 'github':
            print(f"  {c['name']:<22} 创建 {c.get('created_days')} 天"
                  f"  24h {c.get('stars_24h')}  ⭐{c.get('stars')}")

    # 规则模式:验证问题1(今日值得关注)
    hl = rule_highlights(cands, cfg)
    print('\n--- 一、今日值得关注(规则模式) ---')
    for h in hl:
        print(f"  - {h['name']}:{h['intro'][:40]}...")
        print(f"      依据:{h['reason']}")
        print(f"      事实:{h['facts']}")

    data = build_report(db, RUN_DATE.isoformat(), cfg, cands, stats=stats,
                        highlights=hl, model_meta={'mode': 'rule', 'degraded': True,
                                                   'status': '规则模式(演示)'})
    print('\n' + data['body'])

    db.close()


if __name__ == '__main__':
    main()
