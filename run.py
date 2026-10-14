#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 情报 MVP · 阶段04 单次运行入口(无调度;飞书/LLM 可选)。

用法:
    python run.py [--since-days N] [--date YYYY-MM-DD] [--config PATH]
                  [--db PATH] [--no-db]
                  [--report] [--send] [--no-summarize]

流程:
    读 config.yaml + .env → 分源采集(GitHub / HN / RSS / HF,
    每源独立 try/except + 超时 + 退避重试,单源失败不影响其他源)
    → 规则过滤(字段完整 / 时间窗口 / 关键词,每平台 ≤10)
    → 跨源去重(URL 归一化 + 标题 SimHash)
    → 写 SQLite 全表(sources / items / item_sources / project_snapshots /
       topic_daily_stats / runs / daily_reports + items_fts)
    → 趋势引擎(纯规则 24h/7d/30d,历史不足标注"窗口不足",首日只建基线)
    → [--report] 生成日报(可选 Ollama 摘要,失败自动降级)并存入 daily_reports
    → [--send] 发送日报(幂等:当日已 sent 则跳过;只读库,不重新抓取)
    → 输出来源统计 + 趋势结论。

--report 与 --send 分离:飞书不可用时,后续可直接 `run.py --send --date X`
从数据库重发已生成的日报,不重新采集。

退出码:0=正常运行(即使部分源失败);1=全部来源失败/未产出条目/生成或发送失败。
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
    ap = argparse.ArgumentParser(description='AIQI-5 阶段04 单次采集+日报(可选飞书)')
    ap.add_argument('--since-days', type=int, default=None,
                    help='GitHub 采集窗口天数(默认取 config.yaml app.since_days_default)')
    ap.add_argument('--date', default=None,
                    help='运行日期 YYYY-MM-DD(默认今天 UTC;Hermes 可指定报表日期)')
    ap.add_argument('--config', default=None, help='config.yaml 路径(默认项目根)')
    ap.add_argument('--db', default=None, help='SQLite 路径(默认取 config.yaml paths)')
    ap.add_argument('--no-db', action='store_true', help='跳过数据库写入(仅统计输出)')
    ap.add_argument('--report', action='store_true',
                    help='采集后生成日报并存入 daily_reports(不发送;失败可用 --send 重发)')
    ap.add_argument('--send', action='store_true',
                    help='发送 --date 对应日报(只读库,不采集;当日已成功发送则跳过)')
    ap.add_argument('--no-summarize', action='store_true',
                    help='跳过 Ollama 摘要,用原标题+规则标签生成"今日值得关注"')
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
        except Exception as e:
            import logging
            logging.getLogger().error(f'数据库初始化失败: {e}')

    # ---------- 0) 纯发送模式:只读库,不采集(日报生成与发送分离) ----------
    if args.send and not args.report:
        if db is None:
            print('[FAIL] --send 需要 SQLite(去掉 --no-db 或指定 --db)')
            sys.exit(1)
        try:
            from src.feishu import send_report
            result = send_report(db, run_date, cfg)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            ok = result['status'] in ('sent', 'skipped_already_sent')
            print(f"\n[{'OK' if ok else 'FAIL'}] 发送结果: {result['status']}")
            sys.exit(0 if ok else 1)
        except Exception as e:
            print(f'[FAIL] 发送失败: {e}')
            sys.exit(1)
        finally:
            if db:
                db.close()

    if db is not None and not args.no_db:
        try:
            run_id = db.start_run(run_date, started_at=started_at)
        except Exception as e:
            import logging
            logging.getLogger().error(f'数据库初始化失败,继续只做统计输出: {e}')

    now_iso = started_at
    stats = {'date': run_date, 'since_days': since_days, 'platforms': {}, 'dedup': {}, 'db': {}}
    per_platform = {}

    # ---------- 1) GitHub(3 组 Search 查询,内部逐查询 try/except+重试;跨组按 full_name 去重) ----------
    # 阶段04修复(AIQI-7):采集器不再内部限量,限量在跨源去重之后执行。
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

    # ---------- 2) Hacker News(官方 API,热度+关键词规则过滤;不限量) ----------
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
        # 阶段04修复:不再在此限量,限量在跨源去重之后执行
        stats['platforms']['hackernews'] = {
            'fetched': hn_info['fetched_ids'], 'failed_items': hn_info['failed_items'],
            'top_level_error': hn_info.get('top_level_error'),
            'passed_rules': len(selected), 'dropped': dropped,
            'cap': cfg['hackernews']['per_platform_cap'], 'time_s': round(time.time() - t0, 1),
        }
        per_platform['hackernews'] = selected
    except Exception as e:
        stats['platforms']['hackernews'] = {'error': str(e), 'time_s': round(time.time() - t0, 1)}
        per_platform['hackernews'] = []

    # ---------- 3) RSS 博客 + HF Daily Papers(每 feed 独立 try/except;不限量) ----------
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
        hf_passed, hf_drop = rule_filter(hf_all, 'hf_papers', hf_cutoff, keywords=cfg['keywords']['ai'])
        # 阶段04修复:不再在此限量,限量在跨源去重之后执行
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

    # ---------- 4) 处理顺序(阶段04修复 AIQI-7):规则筛选 → 跨源去重 → 每平台限量 ----------
    # 4a) 跨源去重:URL 归一化 + 标题 SimHash + 标题/摘要相似度(阈值可配置)
    deduper = Deduper(threshold=cfg['dedup']['simhash_threshold'],
                      similarity_threshold=float(cfg.get('dedup', {}).get('similarity_threshold', 0.8)))
    deduped = []  # 去重后的统一条目(未限量)
    for platform in ('github', 'hackernews', 'rss', 'hf_papers'):
        for e in per_platform[platform]:
            if deduper.accept(url=e['url'], title=e['title'], summary=e.get('summary', '')):
                deduped.append({
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
        'after_dedup': len(deduped),
        'url_dups': deduper.url_dups, 'simhash_dups': deduper.simhash_dups,
        'similarity_dups': deduper.similarity_dups,
    }

    # 4b) 每平台限量(跨源去重之后):GitHub 按 report.sort_by(新生代优先)排序后取上限
    caps = {
        'github': int(cfg['github']['per_platform_cap']),
        'hackernews': int(cfg['hackernews']['per_platform_cap']),
        'rss': int(cfg['rss']['per_platform_cap']),
        'hf_papers': int(cfg['hf_papers']['per_platform_cap']),
    }
    after_cap = {p: 0 for p in caps}
    stored_items = []
    for platform in ('github', 'hackernews', 'rss', 'hf_papers'):
        items = [it for it in deduped if it['platform'] == platform]
        if platform == 'github' and items:
            from src.ranking import github_entry_sort_key
            cfg_r = cfg.get('report', {})
            items.sort(key=lambda it: github_entry_sort_key(it, cfg_r, run_date, db))
        # 非 GitHub 平台保持各自采集器/规则过滤后的顺序(热度/时间),只截断
        items = items[:caps[platform]]
        after_cap[platform] = len(items)
        stored_items.extend(items)
    stats['pipeline'] = {
        'discovered': {p: int((stats['platforms'].get(p) or {}).get('fetched_raw')
                              or (stats['platforms'].get(p) or {}).get('fetched') or 0)
                       for p in caps},
        'after_filter': {p: len(per_platform[p]) for p in caps},
        'after_dedup': len(deduped),
        'after_cap': after_cap,
    }

    # ---------- 5) 写 SQLite 全表 + 快照 + 主题统计 + 趋势 ----------
    db_stats = {'items_inserted': 0, 'items_updated': 0, 'sources_upserted': 0}
    trends = None
    report_data = None
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
        # 阶段04:日报生成(--report,可选 Ollama 摘要,失败自动降级)与发送(--send)
        if args.report:
            try:
                report_data = build_and_save_report(
                    db, run_date, cfg, stats, trends, no_summarize=args.no_summarize)
                stats['report'] = report_data
            except Exception as e:
                stats['report'] = {'error': str(e)}
                print(f'[FAIL] 日报生成失败: {e}')
        if args.send and args.report:
            try:
                from src.feishu import send_report
                send_result = send_report(db, run_date, cfg)
                stats['report']['send'] = send_result
                print(f"[飞书] 发送结果: {json.dumps(send_result, ensure_ascii=False)}")
            except Exception as e:
                stats.setdefault('report', {})['send_error'] = str(e)
                print(f'[FAIL] 日报发送失败: {e}')
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

    # 阶段04:打印日报正文
    if report_data:
        print(f"\n===== 日报({run_date}) =====\n{report_data['body']}")

    ok_platforms = [p for p, s in stats['platforms'].items() if _platform_level(s) == 'ok']
    failed_platforms = [p for p, s in stats['platforms'].items() if _platform_level(s) == 'failed']
    if not ok_platforms or stats['dedup']['after_dedup'] == 0:
        print("\n[FAIL] 所有来源失败或未产出条目")
        if failed_platforms:
            print(f"[FAIL] 失败来源: {', '.join(failed_platforms)}")
        sys.exit(1)
    if args.report and not report_data:
        print("\n[FAIL] 日报生成失败(见上方错误)")
        sys.exit(1)
    if args.send and args.report:
        sr = (report_data or {}).get('send') or {}
        if sr.get('status') not in ('sent', 'skipped_already_sent'):
            print(f"\n[FAIL] 日报发送失败: {sr.get('status') or 'unknown'}")
            sys.exit(1)
    print(f"\n[OK] 完成,{len(ok_platforms)}/4 个来源成功"
          f"(失败:{', '.join(failed_platforms) if failed_platforms else '无'})"
          f",发现 {sum(stats['pipeline']['discovered'].values())} → "
          f"筛选 {sum(stats['pipeline']['after_filter'].values())} → "
          f"去重 {stats['pipeline']['after_dedup']} → "
          f"限量 {sum(stats['pipeline']['after_cap'].values())} 条")


def build_and_save_report(db, run_date, cfg, stats, trends, no_summarize=False):
    """阶段04:采集结果 + 趋势 → 日报;可选 Ollama 摘要(失败自动降级);存入 daily_reports。

    返回 build_report() 的 dict(含 body / validation / highlights / model_meta)。
    """
    from src.report import build_report, collect_candidates, rule_highlights
    from src.summarize import summarize_or_fallback

    candidates = collect_candidates(db, run_date, cfg, trends)
    if no_summarize or not cfg.get('ollama', {}).get('enabled', True):
        highlights = rule_highlights(candidates, cfg)
        model_meta = {'mode': 'rule', 'degraded': False, 'status': '未启用(规则模式)'}
    else:
        # 只把入选 Top N 交给模型(硬性约束:模型调用必须可选且只对入选情报)
        cap = int(cfg.get('report', {}).get('highlights_cap', 8))
        model_input = candidates[:cap]
        highlights, model_meta = summarize_or_fallback(model_input, cfg)
    data = build_report(db, run_date, cfg, candidates, stats=stats, trends=trends,
                        highlights=highlights, model_meta=model_meta)
    saved = db.save_daily_report(run_date, data['body'])
    data['saved_to_db'] = saved
    issues = data.get('validation') or []
    if issues:
        print(f"[WARN] 日报校验未完全通过: {'; '.join(issues)}")
    return data


def _platform_level(stat):
    """平台级健康度:'ok' / 'partial' / 'failed'。

    判定依据(阶段05:断网/单源失败时 runs 状态与退出码必须如实反映):
      - 顶层异常(error / top_level_error)→ failed
      - GitHub:全部查询失败 → failed;部分查询失败 → partial
      - HN:一个条目都没抓到且有失败 → failed;有条目级失败 → partial
      - RSS:全部 feed 失败且 0 条目 → failed;部分 feed 失败 → partial
      - HF:feed 失败且 0 条目 → failed
      - 其余 → ok(即使 0 条目,来源本身工作正常)
    """
    if not isinstance(stat, dict) or stat.get('error'):
        return 'failed'
    if stat.get('top_level_error'):
        return 'failed'
    if stat.get('queries_failed') and not stat.get('queries_ok'):
        return 'failed'
    if stat.get('fetched', 0) == 0 and (stat.get('failed_items')
                                        or stat.get('feeds_failed')
                                        or stat.get('feed_failed')):
        return 'failed'
    if (stat.get('queries_failed') or stat.get('failed_items')
            or stat.get('feeds_failed') or stat.get('feed_failed')):
        return 'partial'
    return 'ok'


def _finish_run(db, run_id, stats, forced_error=None):
    """把运行状态/错误写回 runs 表(状态按平台级健康度判定)。"""
    if run_id is None:
        return
    levels = {p: _platform_level(s) for p, s in stats['platforms'].items()}
    n_items = stats.get('dedup', {}).get('after_dedup', 0)
    n_ok = len([l for l in levels.values() if l == 'ok'])
    if forced_error:
        status = 'failed'
    elif n_ok == 0 or n_items == 0:
        status = 'failed'
    elif n_ok < 4 or any(l != 'ok' for l in levels.values()):
        status = 'partial'
    else:
        status = 'ok'
    errors = {}
    for p, s in stats['platforms'].items():
        lvl = levels[p]
        if lvl == 'failed':
            if 'error' in s:
                errors[p] = str(s['error'])[:500]
            elif p == 'github' and s.get('queries_failed'):
                errors[p] = f"{s['queries_failed']} 组查询失败" + (', 限流' if s.get('rate_limited') else '')
            elif s.get('top_level_error'):
                errors[p] = str(s['top_level_error'])[:500]
        if lvl in ('partial', 'failed') and (s.get('feeds_failed') or s.get('feed_failed')):
            if s.get('feeds_failed'):
                names = list(s['feeds_failed'].keys())
                errors.setdefault(p, f"{len(names)} 个 RSS feed 失败")
                errors[p] = f"{len(names)} 个 RSS feed 失败: " + '; '.join(
                    f"{n}:{str(e)[:60]}" for n, e in list(s['feeds_failed'].items())[:3])
            elif s.get('feed_failed'):
                errors[p] = f"HF Daily Papers 失败: {str(s['feed_failed'])[:200]}"
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
    elif hn.get('top_level_error'):
        errs['hackernews:topstories'] = str(hn['top_level_error'])[:500]
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
