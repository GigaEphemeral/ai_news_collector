#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多日连续运行模拟(阶段03 验收演示,非生产数据)。

用受控合成数据在一个临时 SQLite 库中模拟连续 8 天运行(2026-10-07 → 10-14),
验证趋势引擎能对比昨日 / 近 7 天变化,并覆盖全部五种状态与"窗口不足"标注:

  项目(project_snapshots):
    acme/agentkit    仅 10-14 有快照,创建 13 天前   → 新出现(初步判断)
    acme/ragflow     8 天连续正增长(~6-8%/日)      → 持续升温
    acme/vectorstore 前 6 天 0 增长,10-14 突然 +1000 → 突然增长
    acme/llmserve    20 万 Star,日均 +5(0.0025%)   → 进入成熟期(降权)
    acme/mcp         8000 → 7600,连续负增长         → 热度回落
  主题(topic_daily_stats,由 compute_topic_stats 每日产出):
    agent / rag / inference / newtopic → 升温 / 突增 / 成熟 / 新出现

生产环境里这些数据来自 run.py 每日自动写入;本脚本只演示"多日数据 → 趋势结论"
的完整链路。运行:

    /home/czq/ai_news/code/.venv/bin/python scripts/simulate_multi_day.py

输出:趋势 JSON(状态 + 证据 + 窗口)打印到 stdout,并写入 data/ 下的 JSON 文件。
"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.db import Database
from src.trend import build_trends

RUN_DATE = date(2026, 10, 14)
DAYS = [RUN_DATE - timedelta(days=i) for i in range(7, -1, -1)]  # 10-07..10-14


def d(offset):
    """RUN_DATE 往前 offset 天(0=今天)的 ISO 日期。"""
    return (RUN_DATE - timedelta(days=offset)).isoformat()


def seed_snapshots(db):
    """8 天连续运行会写出的 project_snapshots。"""
    def put(repo, day_iso, stars, created, pushed):
        db.upsert_snapshot(repo=repo, date=day_iso, stars=stars, forks=0,
                           pushed_at=pushed, created_at=created,
                           description='AI agent / RAG / inference tooling',
                           language='Python',
                           topics=['ai-agents', 'llm', 'rag', 'inference'],
                           fetched_at=f'{day_iso}T08:00:00Z')

    # 新出现:仅最后一天入选
    put('acme/agentkit', d(0), 200, '2026-10-01T00:00:00Z', '2026-10-14T07:00:00Z')
    # 持续升温:8 天正增长且加速(60,70,80,90,100,110,120)
    for i, s in enumerate([1000, 1060, 1130, 1210, 1300, 1400, 1510, 1630]):
        put('acme/ragflow', DAYS[i].isoformat(), s, '2026-06-01T00:00:00Z',
            f'{DAYS[i].isoformat()}T08:00:00Z')
    # 突然增长:前 6 天 0 增长,最后一天 +1000
    for i, s in enumerate([5000] * 7 + [6000]):
        put('acme/vectorstore', DAYS[i].isoformat(), s, '2026-01-15T00:00:00Z',
            f'{DAYS[i].isoformat()}T08:00:00Z')
    # 进入成熟期:20 万 Star 微增长
    for i, s in enumerate([200000 + j * 5 for j in range(8)]):
        put('acme/llmserve', DAYS[i].isoformat(), s, '2022-03-01T00:00:00Z',
            f'{DAYS[i].isoformat()}T08:00:00Z')
    # 热度回落:连续负增长
    for i, s in enumerate([8000, 8600, 9000, 8800, 8500, 8200, 7900, 7600]):
        put('acme/mcp', DAYS[i].isoformat(), s, '2024-05-01T00:00:00Z',
            f'{DAYS[i].isoformat()}T08:00:00Z')


def seed_topic_stats(db):
    """模拟 compute_topic_stats 每日产出的 topic_daily_stats。"""
    # agent:1→8 连续升温,多个来源
    for i, c in enumerate(range(1, 9)):
        db.upsert_topic_stat(DAYS[i].isoformat(), 'agent', c,
                             source_count=2 if i >= 2 else 1,
                             representative_item_id=100 + i)
    # rag:前 6 天持平,最后一天突增
    for i in range(8):
        db.upsert_topic_stat(DAYS[i].isoformat(), 'rag', 2 if i < 7 else 8,
                             source_count=1, representative_item_id=200 + i)
    # inference:稳定 4 条/日,成熟期
    for i in range(8):
        db.upsert_topic_stat(DAYS[i].isoformat(), 'inference', 4,
                             source_count=2, representative_item_id=300 + i)
    # newtopic:仅最后一天出现
    db.upsert_topic_stat(d(0), 'newtopic', 2, source_count=1,
                         representative_item_id=400)


def main():
    cfg = load_config()
    tmp = tempfile.mkdtemp(prefix='ai_news_trend_demo_')
    db_path = os.path.join(tmp, 'demo.sqlite3')
    db = Database(db_path)
    seed_snapshots(db)
    seed_topic_stats(db)

    trends = build_trends(db, RUN_DATE.isoformat(), cfg['trend'],
                          cfg['keywords']['ai'])
    db.close()

    print('=' * 70)
    print(f'多日连续运行模拟(8 天,{DAYS[0]} → {RUN_DATE}),趋势结论如下:')
    print('=' * 70)
    print(json.dumps(trends, ensure_ascii=False, indent=2))

    data_dir = os.path.expandvars(cfg['paths']['data_dir'])
    if not os.path.isabs(data_dir):
        data_dir = os.path.join(cfg['_project_root'], data_dir)
    out = os.path.join(data_dir, f'trend_demo_{RUN_DATE.strftime("%Y%m%d")}.json')
    os.makedirs(data_dir, exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(trends, f, ensure_ascii=False, indent=2)
    print(f'\n演示数据(临时库): {db_path}')
    print(f'趋势 JSON 已写入: {out}')


if __name__ == '__main__':
    main()
