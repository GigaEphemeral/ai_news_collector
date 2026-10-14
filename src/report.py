# -*- coding: utf-8 -*-
"""日报模板生成(阶段04):固定栏目、允许空栏目、每条附原文链接、长度与关键词校验。

生成与发送分离:本模块只负责"从库 + 本次统计 + 趋势 → 日报文本并写入 daily_reports",
不发送;发送由 src/feishu.py 单独执行,失败可从库中重发,不重新抓取。

固定栏目(方案第七节 / ainews.md):
  - 标题:每日ai情报 · AI 情报日报 <date>(含 webhook 安全关键词,发送必需)
  - 一、今日值得关注(2-3 条,来自 summarize.py 或规则降级)
  - 二、开源项目与工程实践(GitHub 项目 + 状态标签 + 链接)
  - 三、解决方案与产品发布(官方博客 RSS / HF 论文)
  - 四、趋势观察(主题/项目 + 状态 + 7 天证据 + 观察限制)
  - 五、采集状态(成功源数/发现条目/去重后/模型状态/失败源)

原则:栏目允许为空,不为填满版面凑数;每条情报附原文链接;推测与事实分开写
(证据=事实,来自库;状态标签/观察限制=判断,明确标注)。

本模块零第三方依赖;Ollama 调用在 src/summarize.py,飞书发送在 src/feishu.py。
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from .db import utcnow_iso
from .trend import STATE_ORDER  # 固定展示顺序(非评分排序)

log = logging.getLogger(__name__)

SECTION_TITLES = [
    '一、今日值得关注',
    '二、开源项目与工程实践',
    '三、解决方案与产品发布',
    '四、趋势观察',
    '五、采集状态',
]

NO_HOTSPOT_TEXT = '今日没有符合筛选条件的新热点'
EMPTY_SECTION_TEXT = '(无符合条件内容)'


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _clamp(s, n):
    s = (s or '').strip().replace('\n', ' ')
    return s if len(s) <= n else s[:n] + '…'


def _fmt_pct(x):
    return f'{x:+.2f}%' if x is not None else None


def _keyword(cfg):
    """安全关键词:env 优先,回退 config report.required_keyword,再回退默认值。"""
    import os
    return (os.environ.get('FEISHU_WEBHOOK_KEYWORD') or ''
            ).strip() or cfg.get('report', {}).get('required_keyword', '每日ai情报')


# ---------------------------------------------------------------------------
# 候选条目(今日入库条目 → 统一候选,含紧凑字段与热度指标)
# ---------------------------------------------------------------------------
def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _snapshot_delta(db, repo, run_date):
    """GitHub 仓库 24h Star 增量:昨日→今日快照差值;缺任一日返回 None。"""
    try:
        rows = db.snapshot_rows(repo)
    except Exception:
        return None
    prev = curr = None
    d_prev = (run_date - timedelta(days=1)).isoformat()
    d_curr = run_date.isoformat()
    for r in rows:
        if r['date'] == d_prev:
            prev = r['stars']
        elif r['date'] == d_curr:
            curr = r['stars']
    if prev is None or curr is None:
        return None
    return curr - prev


def collect_candidates(db, run_date, cfg, trends=None):
    """今日去重后条目 → 统一候选列表(≤ highlights_cap)。

    每个候选携带:
      name / title / url / summary / platform / category / published_at
      stars(GitHub stargazers、HN points)/ stars_24h(GitHub 快照差值)
      created_days / topics / tags(规则标签)/ score(平台内排序指标)
    不做综合评分;排序仅在同一平台内按可观测指标进行(硬性约束 #6)。
    """
    cfg_r = cfg.get('report', {})
    young_days = int(cfg.get('trend', {}).get('project', {}).get('young_days', 30))
    run_date = parse_date(run_date) or datetime.now(timezone.utc).date()

    state_map = {}
    if trends:
        for t in trends.get('projects', []):
            state_map[t['repo']] = t['state']

    candidates = []
    for r in db.items_first_seen_on(run_date.isoformat()):
        extra = {}
        try:
            extra = json.loads(r['extra'] or '{}')
        except (ValueError, TypeError):
            pass
        platform = r['platform']
        title = (r['title'] or '').strip()
        url = (r['url'] or '').strip()
        summary = (r['summary'] or '').strip()
        if not title or not url:
            continue
        cand = {
            'name': title, 'title': title, 'url': url, 'summary': summary,
            'platform': platform, 'category': r['category'] or platform,
            'published_at': r['published_at'] or '',
            'stars': None, 'stars_24h': None, 'created_days': None,
            'topics': [], 'tags': [], 'score': 0.0,
        }
        if platform == 'github':
            full = extra.get('full_name') or title
            cand['name'] = full
            cand['stars'] = extra.get('stargazers_count')
            cand['stars_24h'] = _snapshot_delta(db, full, run_date)
            created = _parse_iso(extra.get('created_at'))
            if created:
                cand['created_days'] = max((run_date - created.date()).days, 0)
            cand['topics'] = (extra.get('topics') or [])[:5]
            state = state_map.get(full)
            if state:
                cand['tags'].append(state)
            if cand['created_days'] is not None and cand['created_days'] <= young_days:
                cand['tags'].append('新项目')
            if not cand['tags']:
                cand['tags'].append('GitHub 项目')
            cand['score'] = cand['stars_24h'] if cand['stars_24h'] is not None else (cand['stars'] or 0)
        elif platform == 'hackernews':
            cand['stars'] = extra.get('points')
            if cand['stars']:
                cand['tags'].append(f"HN {cand['stars']} 分")
            cand['score'] = cand['stars'] or 0
            pub = _parse_iso(r['published_at'])
            if pub:
                cand['created_days'] = max((run_date - pub.date()).days, 0)
        else:  # rss / hf_papers
            pub = _parse_iso(r['published_at'])
            if pub:
                cand['created_days'] = max((run_date - pub.date()).days, 0)
                cand['score'] = (pub - datetime(run_date.year, run_date.month, run_date.day,
                                                tzinfo=timezone.utc)).total_seconds()
            if platform == 'hf_papers':
                cand['tags'].append('HF 论文')
            else:
                cand['tags'].append('官方博客')
        candidates.append(cand)

    # 平台内按指标降序;全局按平台优先级(github → hackernews → rss/hf)保序
    order = {'github': 0, 'hackernews': 1, 'rss': 2, 'hf_papers': 3}
    candidates.sort(key=lambda c: (order.get(c['platform'], 9), -c['score']))
    return candidates


def _facts_line(cand):
    """事实字符串(全部来自库中可观测数据,不含模型判断)。"""
    parts = []
    if cand.get('stars') is not None and cand['platform'] == 'github':
        parts.append(f"⭐{cand['stars']}")
    if cand.get('stars_24h') is not None:
        parts.append(f"24h {cand['stars_24h']:+d}")
    if cand.get('stars') is not None and cand['platform'] == 'hackernews':
        parts.append(f"HN {cand['stars']} 分")
    if cand.get('created_days') is not None and cand['platform'] == 'github':
        parts.append(f"创建 {cand['created_days']} 天")
    return ' · '.join(parts) or '规则筛选入选'


def rule_highlights(candidates, cfg):
    """规则降级/非模型模式:原标题 + 规则标签(事实),最多 max_highlights 条。

    选择策略(纯规则,非评分):优先带趋势状态的 GitHub 项目(新出现/持续升温/
    突然增长/热度回落),再补 1 条 HN/RSS/HF 指标最高者;凑不满不硬凑。
    """
    cfg_r = cfg.get('report', {})
    max_h = int(cfg_r.get('max_highlights', 3))
    gh = [c for c in candidates if c['platform'] == 'github' and c.get('tags')]
    gh.sort(key=lambda c: c['score'], reverse=True)
    gh = gh[:max(0, max_h - 1)]
    rest = [c for c in candidates if c['platform'] != 'github']
    rest.sort(key=lambda c: c['score'], reverse=True)
    rest = rest[:1] if max_h - len(gh) > 0 else []
    chosen = gh + rest

    highlights = []
    for c in chosen[:max_h]:
        intro = _clamp(c['summary'], 60) or f"{c['platform']}:{c['category']}"
        facts = _facts_line(c)
        tags = '、'.join(c['tags']) or '规则筛选入选'
        highlights.append({
            'name': c['name'], 'url': c['url'], 'intro': intro,
            'reason': f'规则标签:{tags}', 'facts': facts,
        })
    return highlights


def _enrich_facts(highlights, candidates):
    """Ollama 摘要条目缺事实串时,从候选数据(库中可观测指标)补上。"""
    by_name = {c['name']: c for c in candidates}
    for h in highlights:
        if not h.get('facts'):
            c = by_name.get(h['name'])
            if c:
                h['facts'] = _facts_line(c)
    return highlights


# ---------------------------------------------------------------------------
# 栏目构建
# ---------------------------------------------------------------------------
def _section1(highlights):
    """一、今日值得关注。"""
    if not highlights:
        return [NO_HOTSPOT_TEXT]
    lines = []
    for i, h in enumerate(highlights, 1):
        lines.append(f"{i}. {h['name']}")
        if h.get('intro'):
            lines.append(f"   {h['intro']}")
        reason = h.get('reason') or ''
        facts = h.get('facts') or ''
        lines.append(f"   依据:{reason} · 事实:{facts} · 原文:{h['url']}")
    return lines


def _section2(candidates, cfg_r):
    """二、开源项目与工程实践(GitHub 项目)。"""
    gh = [c for c in candidates if c['platform'] == 'github']
    gh.sort(key=lambda c: (STATE_ORDER.index(c['tags'][0]) if c.get('tags') and c['tags'][0] in STATE_ORDER else 99, -c['score']))
    gh = gh[:int(cfg_r.get('opensource_max', 5))]
    lines = []
    for c in gh:
        state = next((t for t in c['tags'] if t in STATE_ORDER), None)
        tag = f"[{state}]" if state else ''
        facts = _facts_line(c)
        desc = _clamp(c['summary'], 80)
        lines.append(f"- {c['name']} {tag} — {desc} · {facts} · GitHub:{c['url']}")
    return lines


def _section3(candidates, cfg_r):
    """三、解决方案与产品发布(官方博客 RSS / HF 论文)。"""
    sol = [c for c in candidates if c['platform'] in ('rss', 'hf_papers')]
    sol = sol[:int(cfg_r.get('solutions_max', 5))]
    lines = []
    for c in sol:
        tag = '、'.join(c['tags']) if c['tags'] else c['category']
        desc = _clamp(c['summary'], 70)
        lines.append(f"- {c['title']} [{tag}] — {desc} · {c['url']}")
    return lines


def _section4(trends, cfg_r, run_date):
    """四、趋势观察:项目/主题 + 状态 + 证据(事实)+ 观察限制(判断)。"""
    lines = []
    n_proj = int(cfg_r.get('trends_projects_max', 3))
    n_topic = int(cfg_r.get('trends_topics_max', 3))
    if not trends:
        return lines
    for t in trends.get('projects', [])[:n_proj]:
        evidence = _pick_evidence(t.get('evidence', []))
        lines.append(f"- [项目] {t['repo']} · {t['state']}"
                     f"{'(初步判断,历史不足)' if t.get('confidence') == '初步判断' else ''}"
                     f" — {evidence}")
    for t in trends.get('topics', [])[:n_topic]:
        evidence = _pick_evidence(t.get('evidence', []))
        lines.append(f"- [主题] {t['topic']} · {t['state']}"
                     f"{'(初步判断,历史不足)' if t.get('confidence') == '初步判断' else ''}"
                     f" — {evidence}")
    return lines


def _pick_evidence(evidence):
    """取最能说明 7d/24h 变化的一条事实证据;缺省取首条。"""
    if not evidence:
        return '无可用证据'
    chosen = None
    for pref in ('7d:', '24h:'):
        for e in evidence:
            if pref in e:
                chosen = e
                break
        if chosen:
            break
    chosen = chosen or evidence[0]
    # 趋势证据行自带窗口前缀(如 "7d:7d 窗口仅 1 天"),去掉冗余的重复前缀
    import re as _re
    chosen = _re.sub(r'^(7d|24h):\s*(?:7d|24h)', r'\1', chosen)
    return _clamp(chosen, 120)


def _section5(stats, db, run_date, cfg, model_meta):
    """五、采集状态:成功源数/发现条目/去重后/模型状态/失败源。"""
    platforms = stats.get('platforms', {}) if stats else {}
    names = ('github', 'hackernews', 'rss', 'hf_papers')
    failed_platforms = set()
    for p in names:
        s = platforms.get(p)
        if isinstance(s, dict) and s.get('error'):
            failed_platforms.add(p)
    if stats:
        failed_platforms |= set(stats.get('_run_errors', {}).keys())
    total = len(names)
    ok = total - len([p for p in failed_platforms if p in names])

    fetched = 0
    for p in names:
        s = platforms.get(p, {})
        if isinstance(s, dict):
            fetched += int(s.get('fetched_raw') or s.get('fetched') or 0)
    dedup = 0
    if stats and stats.get('dedup'):
        dedup = int(stats['dedup'].get('after_dedup', 0))
    if not dedup:
        try:
            dedup = len(db.items_first_seen_on(run_date.isoformat()))
        except Exception:
            dedup = 0

    model = model_meta or {'status': '未调用', 'mode': 'rule'}
    model_line = model.get('status', '未调用')
    if model.get('detail'):
        model_line += f"({_clamp(model['detail'], 60)})"

    fail_names = []
    for k in sorted(failed_platforms):
        fail_names.append(_platform_display_name(k, cfg))
    rss_stat = platforms.get('rss', {})
    if isinstance(rss_stat, dict):
        for name in (rss_stat.get('feeds_failed') or {}).keys():
            if name not in fail_names:
                fail_names.append(name)
    try:
        for srow in db.failed_sources():
            n = srow['name'] or srow['url']
            if n not in fail_names:
                fail_names.append(n)
    except Exception:
        pass
    fail_text = '、'.join(fail_names) if fail_names else '无'

    return [
        f"成功来源:{ok}/{total} · 发现条目:{fetched} · 去重后:{dedup}",
        f"模型状态:{model_line}",
        f"失败来源:{fail_text}",
    ]


def _platform_display_name(key, cfg):
    m = {
        'github': 'GitHub', 'hackernews': 'Hacker News', 'hf_papers': 'HF Daily Papers',
    }
    if key in m:
        return m[key]
    for s in cfg.get('rss', {}).get('sources', []):
        if s['url'] == key or s['name'] == key:
            return s['name']
    return key


# ---------------------------------------------------------------------------
# 汇总与校验
# ---------------------------------------------------------------------------
def validate_report(body, cfg, keyword=None):
    """长度/关键词校验;返回问题列表(空 = 通过)。"""
    kw = keyword or _keyword(cfg)
    max_chars = int(cfg.get('report', {}).get('max_chars', 6000))
    issues = []
    if kw not in body:
        issues.append(f'缺少安全关键词「{kw}」')
    if len(body) > max_chars:
        issues.append(f'长度 {len(body)} 超过上限 {max_chars}')
    return issues


def _fit(body, max_chars):
    """超长时截断并在末尾加标记;关键词在标题首行,不受截断影响。"""
    if len(body) <= max_chars:
        return body
    return body[: max_chars - 20].rstrip() + '\n…(内容超长已截断)'


def build_report(db, run_date, cfg, candidates, stats=None, trends=None,
                 highlights=None, model_meta=None):
    """构建日报文本(不发送)。

    返回 dict:
      date / body / sections(各栏目行列表)/ highlights / model_meta /
      validation(问题列表)/ keyword
    """
    run_date = parse_date(run_date) or datetime.now(timezone.utc).date()
    if trends is None:
        try:
            trends = _build_trends(db, run_date, cfg)
        except Exception as e:
            log.warning(f'趋势计算失败,趋势观察栏目留空: {e}')
            trends = None
    if stats is None:
        stats = _derive_stats(db, run_date)

    cfg_r = cfg.get('report', {})
    highlights = highlights if highlights is not None else rule_highlights(candidates, cfg)
    highlights = _enrich_facts(highlights, candidates)

    sections = {
        1: _section1(highlights),
        2: _section2(candidates, cfg_r),
        3: _section3(candidates, cfg_r),
        4: _section4(trends, cfg_r, run_date),
        5: _section5(stats, db, run_date, cfg, model_meta),
    }

    kw = _keyword(cfg)
    title = f"{kw} · AI 情报日报 {run_date.isoformat()}"
    lines = [title, '采集窗口:过去 24 小时 · 历史趋势:过去 7 天', '']
    for i, sec_title in enumerate(SECTION_TITLES, 1):
        lines.append(sec_title)
        body_lines = sections[i] or [EMPTY_SECTION_TEXT]
        lines.extend(body_lines)
        lines.append('')
    body = '\n'.join(lines).rstrip() + '\n'

    max_chars = int(cfg_r.get('max_chars', 6000))
    body = _fit(body, max_chars)
    return {
        'date': run_date.isoformat(),
        'body': body,
        'sections': {i: sections[i] for i in sections},
        'highlights': highlights,
        'model_meta': model_meta or {'mode': 'rule', 'degraded': False, 'status': '未调用'},
        'validation': validate_report(body, cfg, keyword=kw),
        'keyword': kw,
        'generated_at': utcnow_iso(),
    }


def _build_trends(db, run_date, cfg):
    from .trend import build_trends
    return build_trends(db, run_date.isoformat(), cfg.get('trend', {}),
                        cfg.get('keywords', {}).get('ai', []))


def _derive_stats(db, run_date):
    """standalone 生成日报时,从 runs/sources 表反推采集统计。"""
    stats = {'platforms': {}, 'dedup': {'after_dedup': 0}, '_run_errors': {}}
    run = db.latest_run_for_date(run_date.isoformat())
    if run:
        try:
            stats['platforms'] = json.loads(run['per_source_stats'] or '{}')
            stats['_run_errors'] = json.loads(run['errors'] or '{}')
        except (ValueError, TypeError):
            pass
    try:
        stats['dedup']['after_dedup'] = len(db.items_first_seen_on(run_date.isoformat()))
    except Exception:
        pass
    return stats
