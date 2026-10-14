#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 情报 MVP · 阶段03 单次运行入口(无调度、无飞书、无 LLM、无评分)。

用法:
    python run.py [--since-days N] [--date YYYY-MM-DD] [--config PATH]
                  [--db PATH] [--no-db]

流程:
    读 config.yaml + .env → 分源采集(GitHub / HN / RSS / HF,
    每源独立 try/except + 超时 + 退避重试,单源失败不影响其他源)
    → 规则过滤(字段完整 / 时间窗口 / 关键词,每平台 ≤10)
    → 跨源去重(URL 归一化 + 标题 SimHash)
    → 写 SQLite 全表(sources / items / item_sources / project_snapshots /
       topic_daily_stats / runs / daily_reports + items_fts)
    → 趋势引擎(纯规则 24h/7d/30d,历史不足标注"窗口不足",首日只建基线)
    → 输出来源统计 + 趋势结论。

退出码:0=正常运行(即使部分源失败);1=全部来源失败或未产出任何条目。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import load_config
from src.log import setup_logging
from src.db import Database, utcnow_iso
from src.github import GitHubCollector
from src.hackernews import HackerNewsCollector
from src.rss import fetch_rss_and_hf
from src.filters import rule_filter
from src.dedup import Deduper
from src.trend import build_trends


def parse_args():
    ap = argparse.ArgumentParser(description='AIQI-4 阶段03 单次采集(无调度/飞书/LLM/评分)')
    ap.add_argument('--since-days', type=int, default=None,
                    help='GitHub 采集窗口天数(默认取 config.yaml app.since_days_default)')
    ap.add_argument('--date', default=None,
                    help='运行日期 YYYY-MM-DD(默认今天 UTC;Hermes 可指定报表日期)')
    ap.add_argument('--config', default=None, help='config.yaml 路径(默认项目根)')
    ap.add_argument('--db', default=None, help='SQLite 路径(默认取 config.yaml paths)')
    ap.add_argument('--no-db', action='store_true', help='跳过数据库写入(仅统计输出)')
    return ap.parse_args()


def main():
    args = parse_args()
    cfg = load_config(args.config)

    log_dir = os.path.expandvars(cfg['paths']['log_dir'])
    data_dir = os.path.expandvars(cfg['paths']['data_dir'])
    if not os.path.isabs(log_dir):
        log_dir = os.path.join(cfg['_project_root'], log_dir)
    if not os.path.isabs(data_dir):
        data_dir = os.path.join(cfg['_project_root'], data_dir)
    os.makedirs(log_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)

    setup_logging(log_dir=log_dir, log_name='ai_news_mvp', retention_days=90)

    since_days = args.since_days if args.since_days is not None else cfg['app'].get('since_days_default', 7)
    run_date = args.date or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    gh_pat = os.environ.get('GITHUB_PAT', '')
    if not gh_pat:
        import logging
        logging.getLogger().warning('GITHUB_PAT 未在 .env 中配置,GitHub 将走匿名配额(search 10/min)')

    db = None
    run_id = None
    started_at = utcnow_iso()
    if not args.no_db:
        db_path = args.db or os.path.expandvars(cfg['paths']['db_file'])
        if not os.path.isabs(db_path):
            db_path = os.path.join(data_dir, db_path)
        try:
            db = Database(db_path)
            run_id = db.start_run(run_date, started_at=started_at)
        except Exception as e:
            import logging
            logging.getLogger().error(f'数据库初始化失败,继续只做统计输出: {e}')

    now_iso = started_at
    stats = {'date': run_date, 'since_days': since_days, 'platforms': {}, 'dedup': {}, 'db': {}}
    per_platform = {}

    # ---------- 1) GitHub(3 组 Search 查询,内部逐查询 try/except+重试) ----------
    t0 = time.time()
    try:
        gh = GitHubCollector(cfg['github'], gh_pat)
        gh_items, gh_info = gh.fetch(since_days)
        stats['platforms']['github'] = {
            'fetched_raw': gh_info['raw_items'], 'queries_ok': gh_info['queries_done'],
            'queries_failed': gh_info['queries_failed'], 'rate_limited': gh_info['rate_limited'],
            'passed_rules': len(gh_items), 'cap': gh_info['cap'],
            'time_s': round(time.time() - t0, 1),
        }
        per_platform['github'] = gh_items
    except Exception as e:
        stats['platforms']['github'] = {'error': str(e), 'time_s': round(time.time() - t0, 1)}
        per_platform['github'] = []

    # ---------- 2) Hacker News(官方 API,热度+关键词规则过滤) ----------
    t0 = time.time()
    try:
        hn = HackerNewsCollector(cfg['hackernews'])
        hn_items, hn_info = hn.fetch()
        cutoff = datetime.now(timezone.utc) - timedelta(days=cfg['hackernews']['max_age_days'])
        passed, dropped = rule_filter(
            hn_items, 'hackernews', cutoff,
            keywords=cfg['keywords']['ai'],
            min_age_hours=cfg['hackernews']['min_age_hours'],
        )
        selected = []
        for e in passed:
            if (e.get('extra') or {}).get('points', 0) >= cfg['hackernews']['min_points']:
                selected.append(e)
            else:
                dropped['low_points'] = dropped.get('low_points', 0) + 1
        selected = selected[:cfg['hackernews']['per_platform_cap']]
        stats['platforms']['hackernews'] = {
            'fetched': hn_info['fetched_ids'], 'failed_items': hn_info['failed_items'],
            'passed_rules': len(selected), 'dropped': dropped,
            'cap': cfg['hackernews']['per_platform_cap'], 'time_s': round(time.time() - t0, 1),
        }
        per_platform['hackernews'] = selected
    except Exception as e:
        stats['platforms']['hackernews'] = {'error': str(e), 'time_s': round(time.time() - t0, 1)}
        per_platform['hackernews'] = []

    # ---------- 3) RSS 博客 + HF Daily Papers(每 feed 独立 try/except) ----------
    t0 = time.time()
    try:
        rss_results, feed_failures = fetch_rss_and_hf(cfg)
        rss_all, hf_all = [], []
        for name, entries in rss_results.items():
            if name == cfg['hf_papers']['name']:
                hf_all = entries
            else:
                rss_all.extend(entries)
        rss_cutoff = datetime.now(timezone.utc) - timedelta(days=cfg['rss']['max_age_days'])
        hf_cutoff = datetime.now(timezone.utc) - timedelta(days=cfg['hf_papers']['max_age_days'])
        rss_passed, rss_drop = rule_filter(rss_all, 'rss', rss_cutoff, keywords=cfg['keywords']['ai'])
        rss_passed = rss_passed[:cfg['rss']['per_platform_cap']]
        hf_passed, hf_drop = rule_filter(hf_all, 'hf_papers', hf_cutoff, keywords=cfg['keywords']['ai'])
        hf_passed = hf_passed[:cfg['hf_papers']['per_platform_cap']]
        hf_name = cfg['hf_papers']['name']
        stats['platforms']['rss'] = {
            'fetched': len(rss_all), 'passed_rules': len(rss_passed), 'dropped': rss_drop,
            'feeds_ok': len([n for n in rss_results if n != hf_name]),
            'feeds_failed': {n: e for n, e in feed_failures.items() if n != hf_name},
            'cap': cfg['rss']['per_platform_cap'], 'time_s': round(time.time() - t0, 1),
        }
        stats['platforms']['hf_papers'] = {
            'fetched': len(hf_all), 'passed_rules': len(hf_passed), 'dropped': hf_drop,
            'feed_failed': feed_failures.get(hf_name),
            'cap': cfg['hf_papers']['per_platform_cap'],
        }
        per_platform['rss'] = rss_passed
        per_platform['hf_papers'] = hf_passed
    except Exception as e:
        stats['platforms']['rss'] = {'error': str(e), 'time_s': round(time.time() - t0, 1)}
        stats['platforms']['hf_papers'] = {'error': str(e)}
        per_platform['rss'], per_platform['hf_papers'] = [], []

    # ---------- 4) 跨源去重:URL 归一化 + 标题 SimHash ----------
    deduper = Deduper(threshold=cfg['dedup']['simhash_threshold'])
    stored_items = []  # 去重后的统一条目
    for platform in ('github', 'hackernews', 'rss', 'hf_papers'):
        for e in per_platform[platform]:
            if deduper.accept(url=e['url'], title=e['title']):
                stored_items.append({
                    'platform': platform,
                    'title': e['title'], 'url': e['url'],
                    'summary': e.get('summary', ''),
                    'published_at': e.get('published_at', ''),
                    'content_hash': deduper.last_hash,
                    'category': e.get('category', platform),
                    'extra': e.get('extra', {}),
                    'source_url': e.get('_source_url', f"{platform}:default"),
                    'source_name': e.get('_source_name', platform),
                })
    stats['dedup'] = {
        'input': sum(len(v) for v in per_platform.values()),
        'after_dedup': len(stored_items),
        'url_dups': deduper.url_dups, 'simhash_dups': deduper.simhash_dups,
    }

    # ---------- 5) 写 SQLite 全表 + 快照 + 主题统计 + 趋势 ----------
    db_stats = {'items_inserted': 0, 'items_updated': 0, 'sources_upserted': 0}
    trends = None
    if db is not None:
        try:
            # 1) 所有已配置来源(含 0 条目):sources 表记录 last_fetch_at / last_error
            src_defs = [
                ('github:search', 'GitHub Search API', 'github'),
                ('hackernews:topstories', 'Hacker News', 'hackernews'),
            ] + [(s['url'], s['name'], 'rss') for s in cfg['rss']['sources']] + [
                (cfg['hf_papers']['url'], cfg['hf_papers']['name'], 'hf_papers'),
            ]
            src_errors = _collect_source_errors(stats, cfg)
            for url, name, cat in src_defs:
                db.upsert_source(url, name, cat, last_fetch_at=now_iso,
                                 last_error=src_errors.get(url))
                db_stats['sources_upserted'] += 1
            # 2) 去重后条目 + item_sources 跨源映射
            for it in stored_items:
                src_id = db.get_source_id(it['source_url'])
                action, _ = db.upsert_item(
                    platform=it['platform'], title=it['title'], url=it['url'],
                    summary=it['summary'], published_at=it['published_at'],
                    content_hash=it['content_hash'], category=it['category'],
                    extra=it['extra'], source_id=src_id, source_url=it['source_url'],
                )
                if action == 'inserted':
                    db_stats['items_inserted'] += 1
                else:
                    db_stats['items_updated'] += 1
            # 3) project_snapshots:GitHub 采集结果按日写入(repo+date 主键)
            snap_written = 0
            for it in per_platform['github']:
                ex = it.get('extra') or {}
                if not ex.get('full_name'):
                    continue
                db.upsert_snapshot(
                    repo=ex['full_name'], date=run_date,
                    stars=ex.get('stargazers_count'), forks=ex.get('forks_count'),
                    pushed_at=ex.get('pushed_at'), created_at=ex.get('created_at'),
                    description=ex.get('description'), language=ex.get('language'),
                    topics=ex.get('topics') or [], fetched_at=ex.get('fetched_at'),
                )
                snap_written += 1
            db_stats['snapshots_written'] = snap_written
            # 4) topic_daily_stats:当天首次出现条目按主题汇总
            db_stats['topics_written'] = db.compute_topic_stats(
                run_date, cfg['keywords']['ai'],
                max_topics=cfg.get('topics', {}).get('max_per_item', 3),
            )
            # 5) 趋势引擎(纯规则)
            trends = build_trends(db, run_date, cfg['trend'], cfg['keywords']['ai'])
            db_stats['trend_projects'] = len(trends['projects'])
            db_stats['trend_topics'] = len(trends['topics'])
            db_stats['total_items'] = db.count_items()
            db_stats['total_sources'] = db.count_sources()
            db_stats['db_path'] = db_path
            stats['db'] = db_stats
            stats['trends'] = trends
            # 6) 结束运行记录
            _finish_run(db, run_id, stats)
        except Exception as e:
            stats['db'] = {'error': str(e)}
            _finish_run(db, run_id, stats, forced_error=str(e))
        finally:
            db.close()

    # ---------- 6) 输出 ----------
    out_path = os.path.join(data_dir, f"ai_news_mvp_run_{run_date.replace('-', '')}.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"\n===== 采集统计(ai-news-mvp 阶段03)=====\n{json.dumps(stats, ensure_ascii=False, indent=2)}")
    print(f"\n结果 JSON: {out_path}")

    # 按平台输出明细
    for platform, entries in per_platform.items():
        print(f"\n--- {platform}({len(entries)} 条) ---")
        for e in entries:
            extra = e.get('extra') or {}
            meta = f"⭐{extra.get('stars')} 🍴{extra.get('forks')}" if extra.get('stars') is not None else \
                   (f"⬆{extra.get('points')} 💬{extra.get('comments')}" if extra.get('points') is not None else '')
            print(f"  {e['title'][:90]} | {e['url']} {meta}")

    # 趋势结论(证据带出处)
    if trends:
        print(f"\n===== 趋势结论({run_date}) =====")
        for t in trends['projects']:
            tag = ' · 降权' if t['downgraded'] else ''
            conf = f" [{t['confidence']}]" if t['confidence'] else ''
            print(f"[项目·{t['state']}{conf}{tag}] {t['repo']}")
            for ev in t['evidence']:
                print(f"    - {ev}")
        for t in trends['topics']:
            conf = f" [{t['confidence']}]" if t['confidence'] else ''
            print(f"[主题·{t['state']}{conf}] {t['topic']}")
            for ev in t['evidence']:
                print(f"    - {ev}")

    ok_platforms = [p for p, s in stats['platforms'].items() if 'error' not in s]
    if not ok_platforms or stats['dedup']['after_dedup'] == 0:
        print("\n[FAIL] 所有来源失败或未产出条目")
        sys.exit(1)
    print(f"\n[OK] 完成,{len(ok_platforms)}/4 个来源成功,共 {stats['dedup']['after_dedup']} 条去重后条目")


def _finish_run(db, run_id, stats, forced_error=None):
    """把运行状态/错误写回 runs 表。"""
    if run_id is None:
        return
    ok_platforms = [p for p, s in stats['platforms'].items() if 'error' not in s]
    n_items = stats.get('dedup', {}).get('after_dedup', 0)
    if forced_error:
        status = 'failed'
    elif not ok_platforms or n_items == 0:
        status = 'failed'
    elif len(ok_platforms) < 4:
        status = 'partial'
    else:
        status = 'ok'
    errors = {}
    for p, s in stats['platforms'].items():
        if 'error' in s:
            errors[p] = str(s['error'])[:500]
        elif p == 'github' and s.get('queries_failed'):
            errors[p] = f"{s['queries_failed']} 组查询失败" + (', 限流' if s.get('rate_limited') else '')
    if forced_error:
        errors['db'] = forced_error[:500]
    db.finish_run(
        run_id, status,
        per_source_stats={p: {k: v for k, v in s.items() if k != 'error'}
                          for p, s in stats['platforms'].items()},
        errors=errors,
        items_inserted=stats.get('db', {}).get('items_inserted', 0),
        items_updated=stats.get('db', {}).get('items_updated', 0),
        trends=stats.get('trends'),
    )


def _collect_source_errors(stats, cfg):
    """把各平台/feed 的失败映射到 sources.url,供 last_error 记录。"""
    errs = {}
    gh = stats['platforms'].get('github', {})
    if gh.get('error'):
        errs['github:search'] = gh['error'][:500]
    elif gh.get('queries_failed'):
        errs['github:search'] = f"{gh['queries_failed']} 组查询失败" + (', 限流' if gh.get('rate_limited') else '')
    hn = stats['platforms'].get('hackernews', {})
    if hn.get('error'):
        errs['hackernews:topstories'] = hn['error'][:500]
    elif hn.get('failed_items'):
        errs['hackernews:topstories'] = f"{hn['failed_items']} 条 item 抓取失败"
    for name, e in stats['platforms'].get('rss', {}).get('feeds_failed', {}).items():
        for s in cfg['rss']['sources']:
            if s['name'] == name:
                errs[s['url']] = str(e)[:500]
    hf_err = stats['platforms'].get('hf_papers', {}).get('feed_failed')
    if hf_err:
        errs[cfg['hf_papers']['url']] = str(hf_err)[:500]
    return errs


if __name__ == '__main__':
    main()
