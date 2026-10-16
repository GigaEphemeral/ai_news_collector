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

本模块零第三方依赖;Ollama 调用在 src/summarization/,飞书发送在 src/reporting/feishu.py。
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from ..storage.db import utcnow_iso
from .trend import STATE_ORDER  # 固定展示顺序(非评分排序)
from ..pipeline.tagging import rule_tags

log = logging.getLogger(__name__)

SECTION_TITLES = [
    '一、今日值得关注',
    '二、开源项目与工程实践',
    '三、解决方案与产品发布',
    '四、趋势观察',
    '五、采集状态',
    '六、其他资讯汇总',
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
      created_days / pushed_days / topics / tags(规则标签)/ score(平台内排序指标)
    不做综合评分;排序仅在同一平台内按可观测指标进行(硬性约束 #6)。

    阶段04修复(AIQI-7):GitHub 候选按 report.sort_by(默认 freshness:新生代优先,
    老项目仅当 24h 增量达阈值)排序,而非 star 总数;排序规则在 config.yaml 的
    report: 段可配置(成员修正版)。
    """
    from ..pipeline.ranking import github_sort_key
    cfg_r = cfg.get('report', {})
    run_date = parse_date(run_date) or datetime.now(timezone.utc).date()

    state_map = {}
    if trends:
        for t in trends.get('projects', []):
            state_map[t['repo']] = t['state']

    candidates = []
    for r in db.items_active_on(run_date.isoformat()):
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
            'id': r['id'],  # 阶段04:item id(当天选了谁记 daily_reports)
            'name': title, 'title': title, 'url': url, 'summary': summary,
            'platform': platform, 'category': r['category'] or platform,
            'published_at': r['published_at'] or '',
            'stars': None, 'stars_24h': None, 'created_days': None,
            'topics': [], 'tags': [], 'score': 0.0,
            'extra': extra,  # 阶段06:content_fetcher 取 HN 讨论文字 / HF 热度等
            'content_hash': r['content_hash'],  # 阶段06:GitHub README 缓存键
        }
        if platform == 'github':
            full = extra.get('full_name') or title
            cand['name'] = full
            cand['stars'] = extra.get('stargazers_count')
            cand['stars_24h'] = _snapshot_delta(db, full, run_date)
            created = _parse_iso(extra.get('created_at'))
            if created:
                cand['created_days'] = max((run_date - created.date()).days, 0)
            pushed = _parse_iso(extra.get('pushed_at'))
            if pushed:
                cand['pushed_days'] = max((run_date - pushed.date()).days, 0)
            cand['topics'] = (extra.get('topics') or [])[:5]
            state = state_map.get(full)
            cand['tags'] = rule_tags(cand, cfg, state=state)
            cand['score'] = cand['stars_24h'] if cand['stars_24h'] is not None else (cand['stars'] or 0)
        elif platform == 'hackernews':
            cand['stars'] = extra.get('points')
            cand['tags'] = rule_tags(cand, cfg)
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
            cand['tags'] = rule_tags(cand, cfg)
            # 阶段05:热度分层(消费时规则,不落库)
            #   层1 有真实热度:HF upvotes+stars / 跨源命中 HN 用 points
            #   层2 无信号但 source_count≥2(重复度高)
            #   层3 其余 → 按(新鲜度,关键词权重)
            cand['heat_layer'] = 3
            cand['heat_signal'] = 0
            cand['source_count'] = 1
            cand['keyword_hits'] = _keyword_hits(f"{title} {summary}", cfg)
            if platform == 'hf_papers':
                up = extra.get('upvotes') or 0
                gs = extra.get('github_stars') or 0
                if up or gs:
                    cand['heat_layer'] = 1
                    cand['heat_signal'] = int(up) + int(gs)
            else:  # rss
                try:
                    src_stats = db.item_source_stats(r['id'])
                    cand['source_count'] = src_stats['source_count'] or 1
                    if src_stats['hn_points']:
                        cand['heat_layer'] = 1
                        cand['heat_signal'] = int(src_stats['hn_points'])
                    elif cand['source_count'] >= 2:
                        cand['heat_layer'] = 2
                except Exception:
                    pass
        candidates.append(cand)

    # 平台内排序:GitHub 按 report.sort_by(默认新生代优先)排序;RSS/HF 按热度分层;
    # 全局按平台优先级(github → hackernews → rss/hf)保序。
    from ..pipeline.ranking import rss_hf_heat_sort_key
    order = {'github': 0, 'hackernews': 1, 'rss': 2, 'hf_papers': 3}
    candidates.sort(key=lambda c: ((0, *github_sort_key(c, cfg_r))
                                   if c['platform'] == 'github'
                                   else ((1, -c['score']) if c['platform'] == 'hackernews'
                                         else (order.get(c['platform'], 9), *rss_hf_heat_sort_key(c, cfg)))))
    return candidates


def _keyword_hits(text, cfg):
    """文本命中 AI 关键词数(阶段05 热度分层 层3 用)。"""
    kws = cfg.get('keywords', {}).get('ai', []) or []
    if not text:
        return 0
    low = (text or '').lower()
    return sum(1 for kw in kws if kw and kw in low)


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


def _has_delta_data(candidates):
    """是否有任意 GitHub 候选能算 24h 增量(快照 ≥ 2 天)。"""
    return any(c.get('stars_24h') is not None for c in candidates if c['platform'] == 'github')


def rule_highlights(candidates, cfg):
    """规则降级/非模型模式:原标题 + 规则标签(事实),最多 max_highlights 条。

    阶段04修复(AIQI-7,成员修正版):「新生代优先,兼顾老项目增量」。
      - GitHub:按 report.sort_by 排序(默认 freshness),再按资格过滤:
        新生代(创建 ≤ new_days)全入选;老项目仅当 24h 增量/增长率达阈值入选;
        快照不足(首日)时仅新生代且近期 push 入选并标注「初步判断」;
        宁可少选,不把无增量的老项目顶上来凑数(栏目允许为空)。
      - 非 GitHub:各平台指标最高者补位。
      每条上榜理由来自库中真实字段(ranking.selection_reason),可解释。
    """
    from ..pipeline.ranking import github_eligible, github_sort_key, selection_reason
    cfg_r = cfg.get('report', {})
    max_h = int(cfg_r.get('max_highlights', 3))
    has_delta = _has_delta_data(candidates)

    gh = [c for c in candidates if c['platform'] == 'github']
    gh = [c for c in gh if github_eligible(c, cfg_r, has_delta)]
    gh.sort(key=lambda c: github_sort_key(c, cfg_r))
    gh = gh[:max_h]
    rest = [c for c in candidates if c['platform'] != 'github']
    rest.sort(key=lambda c: c['score'], reverse=True)
    rest = rest[:max(0, max_h - len(gh))]
    chosen = gh + rest

    highlights = []
    for c in chosen[:max_h]:
        intro = _clamp(c['summary'], 60) or f"{c['platform']}:{c['category']}"
        facts = _facts_line(c)
        reason = selection_reason(c, cfg_r, has_delta)
        highlights.append({
            'name': c['name'], 'url': c['url'], 'intro': intro,
            'reason': reason, 'facts': facts,
        })
    return highlights


def _enrich_facts(highlights, candidates, cfg):
    """Ollama 摘要条目缺事实串时补上;上榜理由一律改用库中真实字段(成员要求:
    不用模型编造的模糊话术,上榜理由必须可解释)。"""
    from ..pipeline.ranking import selection_reason
    cfg_r = cfg.get('report', {})
    has_delta = _has_delta_data(candidates)
    by_name = {c['name']: c for c in candidates}
    for h in highlights:
        c = by_name.get(h['name'])
        if c:
            if not h.get('facts'):
                h['facts'] = _facts_line(c)
            h['reason'] = selection_reason(c, cfg_r, has_delta)
    return highlights


# ---------------------------------------------------------------------------
# 栏目构建
# ---------------------------------------------------------------------------
def _section1(highlights):
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
    """二、开源项目与工程实践(GitHub 项目;阶段04修复:按 report.sort_by 新生代优先)。"""
    from ..pipeline.ranking import github_sort_key
    gh = [c for c in candidates if c['platform'] == 'github']
    gh.sort(key=lambda c: github_sort_key(c, cfg_r))
    gh = gh[:int(cfg_r.get('opensource_max', 5))]
    lines = []
    for c in gh:
        state = next((t for t in c['tags'] if t in STATE_ORDER), None)
        tag = f"[{state}]" if state else ''
        facts = _facts_line(c)
        desc = _clamp(c['summary'], 80)
        lines.append(f"- {c['name']} {tag} — {desc} · {facts} · GitHub:{c['url']}")
    return lines


def _section3(candidates, cfg_r, resume_map=None):
    """三、解决方案与产品发布(官方博客 RSS / HF 论文;阶段04修复:每条带精简摘要)。

    resume_map: {url: {'summary': str, 'reason': str}}(见 build_solution_resumes);
    摘要为空时注明无法产生摘要的理由,不显示空白。
    """
    sol = [c for c in candidates if c['platform'] in ('rss', 'hf_papers')]
    sol = sol[:int(cfg_r.get('solutions_max', 5))]
    max_chars = int(cfg_r.get('summary_max_chars', 80))
    lines = []
    for c in sol:
        tag = '、'.join(c['tags']) if c['tags'] else c['category']
        res = (resume_map or {}).get(c['url']) or {}
        summary = res.get('summary') or _clamp(c['summary'], max_chars)
        if summary:
            desc = _clamp(summary, max_chars)
        else:
            desc = res.get('reason') or '原文未提供摘要'
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
    """五、采集状态:成功源数/发现→筛选→去重→入库 分段/模型状态/失败源。

    阶段04修复(AIQI-7,问题3):统计口径如实分段显示(发现 → 规则筛选 → 去重 →
    入库),不再用「去重后」掩盖中间步骤。阶段03(阶段04):去重后条目全量入库,
    不再入库前截断;日报消费仍取 top-N(日报候选数单独标注)。
    """
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

    # 分段统计:发现 → 规则筛选 → 去重 → 入库(全量),日报候选单独标注
    pipeline = (stats or {}).get('pipeline', {}) if stats else {}
    discovered = sum(int(v or 0) for v in pipeline.get('discovered', {}).values()) if pipeline else 0
    after_filter = sum(int(v or 0) for v in pipeline.get('after_filter', {}).values()) if pipeline else 0
    after_dedup = int(pipeline.get('after_dedup', 0)) if pipeline else 0
    stored = sum(int(v or 0) for v in pipeline.get('stored', {}).values()) if pipeline else 0
    if not stored and pipeline:
        stored = after_dedup  # 旧 runs 无 stored 字段时回退
    after_cap = sum(int(v or 0) for v in pipeline.get('after_cap', {}).values()) if pipeline else 0
    if not discovered:  # 兼容旧 runs(无 pipeline)
        fetched = 0
        for p in names:
            s = platforms.get(p, {})
            if isinstance(s, dict):
                fetched += int(s.get('fetched_raw') or s.get('fetched') or 0)
        discovered = fetched
        after_filter = sum(int((platforms.get(p) or {}).get('passed_rules') or 0) for p in names)
        after_dedup = 0
        if stats and stats.get('dedup'):
            after_dedup = int(stats['dedup'].get('after_dedup', 0))
        if not after_dedup:
            try:
                after_dedup = len(db.items_first_seen_on(run_date.isoformat()))
            except Exception:
                after_dedup = 0
        stored = after_dedup
        after_cap = stored

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

    if stored == after_cap:
        # 旧口径(限量=入库)或本轮候选==入库:合并显示
        return [
            f"成功来源:{ok}/{total} · 发现:{discovered} → 筛选:{after_filter} "
            f"→ 去重:{after_dedup} → 入库:{stored}",
            f"模型状态:{model_line}",
            f"失败来源:{fail_text}",
        ]
    return [
        f"成功来源:{ok}/{total} · 发现:{discovered} → 筛选:{after_filter} "
        f"→ 去重:{after_dedup} → 入库:{stored}(日报候选 {after_cap})",
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


def _section6(candidates, cfg_r, selected_urls):
    """六、其他资讯汇总(阶段05):未入选 top-N 的 RSS/HF 条目简单聚合。

    每行:标题 + 来源 + 一句话分类(平台/标签/关键词),不丢弃未入选条目。
    """
    sel = set(selected_urls or [])
    lines = []
    for c in candidates:
        if c['platform'] not in ('rss', 'hf_papers'):
            continue
        if c['url'] in sel:
            continue  # 已入选三、解决方案,不重复
        tag = '、'.join(c['tags']) if c.get('tags') else c['platform']
        lines.append(f"- {c['title'][:80]} [{tag}] · {c['url']}")
    return lines


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


def selected_item_ids(candidates, cfg, highlights=None):
    """当天日报选中的 item id 列表(阶段04:消费时选 top-N 后记录)。

    口径:一、今日值得关注(highlights,按 url 匹配)+ 二、开源项目(前
    opensource_max 个 GitHub)+ 三、解决方案(前 solutions_max 个 RSS/HF)。
    candidates 已按平台内热度/新生代排序。
    """
    cfg_r = cfg.get('report', {})
    by_url = {c['url']: c.get('id') for c in candidates if c.get('id') is not None}
    selected = set()
    for h in (highlights or []):
        if h.get('url') in by_url:
            selected.add(by_url[h['url']])
    n_os = 0
    n_sol = 0
    os_max = int(cfg_r.get('opensource_max', 5))
    sol_max = int(cfg_r.get('solutions_max', 5))
    for c in candidates:
        if c.get('id') is None:
            continue
        if c['platform'] == 'github' and n_os < os_max:
            selected.add(c['id']); n_os += 1
        elif c['platform'] in ('rss', 'hf_papers') and n_sol < sol_max:
            selected.add(c['id']); n_sol += 1
    return sorted(selected)


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
    highlights = _enrich_facts(highlights, candidates, cfg)
    resume_map = _build_resume_map(candidates, cfg)

    # 六、其他资讯汇总:排除已入选三、解决方案的 top-N RSS/HF 条目
    sol_urls = [c['url'] for c in candidates
                if c['platform'] in ('rss', 'hf_papers')][:int(cfg_r.get('solutions_max', 5))]
    sections = {
        1: _section1(highlights),
        2: _section2(candidates, cfg_r),
        3: _section3(candidates, cfg_r, resume_map),
        4: _section4(trends, cfg_r, run_date),
        5: _section5(stats, db, run_date, cfg, model_meta),
        6: _section6(candidates, cfg_r, sol_urls),
    }

    kw = _keyword(cfg)
    title = f"{kw} · AI 情报日报 {run_date.isoformat()}"
    lines = [title, '采集窗口:当日活跃条目(各源年龄窗口内,见 config) · 历史趋势:过去 7 天', '']
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
    """standalone 生成日报时,从 runs/sources 表反推采集统计(分段口径)。"""
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


def _build_resume_map(candidates, cfg):
    """入选「解决方案与产品发布」条目的精简摘要 map(问题2修复,成员修正版)。

    摘要来源优先级:库中已有 summary → report.fetch_summary=true 时抓正文首段
    (复用 content_processor)或调 Ollama 一句话摘要(仅对入选 ≤solutions_max 条,
    不反复重试烧 token)→ 仍为空则注明无法产生摘要的理由。

    返回 {url: {'summary': str, 'reason': str}}。fetch_summary 默认 false:
    只显示库中已有 summary,为空注明理由。
    """
    cfg_r = cfg.get('report', {})
    sol = [c for c in candidates if c['platform'] in ('rss', 'hf_papers')]
    sol = sol[:int(cfg_r.get('solutions_max', 5))]
    if not sol:
        return {}
    try:
        from ..summarization.solution_summarizer import build_solution_resumes
        return build_solution_resumes(sol, cfg)
    except Exception as e:
        log.warning(f'解决方案摘要生成失败,回退到库中 summary: {e}')
        return {}  # 回退:由 _section3 显示库中已有 summary 或注明理由


# ---------------------------------------------------------------------------
# 阶段06 [04]:candidates.json + draft(占位符)
# ---------------------------------------------------------------------------
def build_draft_with_placeholders(candidates, cfg, highlights=None,
                                  stats=None, trends=None):
    """生成带占位符的日报草稿(阶段06)。

    占位符:
      {{INTRO_Hx}} / {{INTRO_Ox}} / {{INTRO_Sx}}  每个唯一、与条目一一对应
      {{MODEL_STATUS}}                            单占位(模型状态文案)
    其余栏目(趋势/采集状态/其他资讯汇总)由脚本生成,agent 不改。

    返回 dict:
      body(草稿文本)/ placeholders(占位符→条目 url 映射)/ sections
    """
    cfg_r = cfg.get('report', {})
    highlights = highlights if highlights is not None else rule_highlights(candidates, cfg)
    highlights = _enrich_facts(highlights, candidates, cfg)
    resume_map = _build_resume_map(candidates, cfg)

    # ---- 1) 今日值得关注(H):每个 highlight 一个占位符 ----
    h_lines = []
    h_map = {}
    for i, h in enumerate(highlights, 1):
        ph = f'{{{{INTRO_H{i}}}}}'
        h_map[ph] = h['url']
        h_lines.append(f'{i}. {h["name"]}')
        h_lines.append(f'   {ph}')
        reason = h.get('reason') or ''
        facts = h.get('facts') or ''
        h_lines.append(f'   依据:{reason} · 事实:{facts} · 原文:{h["url"]}')

    # ---- 2) 开源项目(O):前 opensource_max 个 GitHub,每个一个占位符 ----
    from ..pipeline.ranking import github_sort_key
    gh = [c for c in candidates if c['platform'] == 'github']
    gh.sort(key=lambda c: github_sort_key(c, cfg_r))
    gh = gh[:int(cfg_r.get('opensource_max', 5))]
    o_lines, o_map = [], {}
    for i, c in enumerate(gh, 1):
        ph = f'{{{{INTRO_O{i}}}}}'
        o_map[ph] = c['url']
        state = next((t for t in c['tags'] if t in STATE_ORDER), None)
        tag = f'[{state}]' if state else ''
        facts = _facts_line(c)
        o_lines.append(f'- {c["name"]} {tag} — {ph} · {facts} · GitHub:{c["url"]}')

    # ---- 3) 解决方案(S):前 solutions_max 个 RSS/HF,每个一个占位符 ----
    sol = [c for c in candidates if c['platform'] in ('rss', 'hf_papers')]
    sol = sol[:int(cfg_r.get('solutions_max', 5))]
    s_lines, s_map = [], {}
    for i, c in enumerate(sol, 1):
        ph = f'{{{{INTRO_S{i}}}}}'
        s_map[ph] = c['url']
        tag = '、'.join(c['tags']) if c['tags'] else c['category']
        res = (resume_map or {}).get(c['url']) or {}
        summary = res.get('summary') or _clamp(c['summary'], int(cfg_r.get('summary_max_chars', 80)))
        if not summary:
            summary = res.get('reason') or '原文未提供摘要'
        s_lines.append(f'- {c["title"]} [{tag}] — {ph} · {summary} · {c["url"]}')

    # ---- 4) 其余栏目脚本生成 ----
    t_lines = _section4(trends, cfg_r, parse_date(cfg.get('_date'))
                        if cfg.get('_date') else None) if trends else []
    sec5 = _section5(stats, None, cfg.get('_date'), cfg, None) if stats else []
    sol_urls = [c['url'] for c in sol]
    sec6 = _section6(candidates, cfg_r, sol_urls)

    kw = _keyword(cfg)
    date_str = cfg.get('_date') or datetime.now(timezone.utc).date().isoformat()
    title = f"{kw} · AI 情报日报 {date_str}"
    lines = [title, '采集窗口:当日活跃条目(各源年龄窗口内,见 config) · 历史趋势:过去 7 天', '']
    lines.append('一、今日值得关注')
    lines.extend(h_lines or [NO_HOTSPOT_TEXT])
    lines.append('')
    lines.append('二、开源项目与工程实践')
    lines.extend(o_lines or [EMPTY_SECTION_TEXT])
    lines.append('')
    lines.append('三、解决方案与产品发布')
    lines.extend(s_lines or [EMPTY_SECTION_TEXT])
    lines.append('')
    lines.append('四、趋势观察')
    lines.extend(t_lines or [EMPTY_SECTION_TEXT])
    lines.append('')
    lines.append('五、采集状态')
    lines.extend(sec5 or [EMPTY_SECTION_TEXT])
    lines.append('')
    lines.append('六、其他资讯汇总')
    lines.extend(sec6 or [EMPTY_SECTION_TEXT])
    lines.append('')
    lines.append(f'模型状态:{{{{MODEL_STATUS}}}}')
    body = '\n'.join(lines).rstrip() + '\n'

    placeholders = {}
    placeholders.update(h_map)
    placeholders.update(o_map)
    placeholders.update(s_map)
    placeholders['{{MODEL_STATUS}}'] = ''
    return {
        'body': body,
        'placeholders': placeholders,
        'sections': {'H': h_map, 'O': o_map, 'S': s_map},
    }


def export_candidates_json(candidates, cfg, draft_report_path=None,
                           stats=None, trends=None, highlights=None,
                           content_map=None):
    """导出 candidates.json(阶段06)→ data/ai_news_mvp_candidates_YYYYMMDD.json。

    schema_version=1;items 每条带 placeholder/section/url/title/platform/facts/
    reason/content/content_source/fallback_intro。
    """
    cfg_r = cfg.get('report', {})
    highlights = highlights if highlights is not None else rule_highlights(candidates, cfg)
    highlights = _enrich_facts(highlights, candidates, cfg)
    content_map = content_map or {}
    by_url = {h['url']: h for h in highlights}

    items = []
    ph_counter = {'H': 0, 'O': 0, 'S': 0}
    for c in candidates:
        section = None
        if c['platform'] == 'github':
            section = 'O'
        elif c['platform'] in ('rss', 'hf_papers'):
            section = 'S'
        else:
            section = 'H'
        if section in ('O', 'S'):
            ph_counter[section] += 1
            placeholder = f'{{{{INTRO_{section}{ph_counter[section]}}}}}'
            facts = _facts_line(c)
            reason = selection_reason_for(c, cfg_r)
        else:
            h = by_url.get(c['url'])
            if h is None:
                continue
            ph_counter[section] += 1
            placeholder = f'{{{{INTRO_{section}{ph_counter[section]}}}}}'
            facts = h.get('facts', '')
            reason = h.get('reason', '')
        content = (content_map.get(c['url']) or {}).get('content', '') or (c.get('summary') or '')
        items.append({
            'placeholder': placeholder,
            'section': section,
            'url': c['url'],
            'title': c.get('title', ''),
            'platform': c.get('platform', ''),
            'facts': facts,
            'reason': reason,
            'content': content,
            'content_source': (content_map.get(c['url']) or {}).get('content_source', ''),
            'fallback_intro': (content_map.get(c['url']) or {}).get('fallback_intro', ''),
        })

    kw = _keyword(cfg)
    date_str = cfg.get('_date') or datetime.now(timezone.utc).date().isoformat()
    out = {
        'schema_version': 1,
        'date': date_str,
        'draft_report_path': draft_report_path or '',
        'keyword': kw,
        'stats': stats or {},
        'items': items,
        'trends': trends or {},
    }
    data_dir = cfg.get('_data_dir') or os.path.join(cfg.get('_project_root', ''), 'data')
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f'ai_news_mvp_candidates_{date_str.replace("-", "")}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return path, out


def selection_reason_for(cand, cfg_r):
    """candidates.json 用上榜理由(避免顶层重复 import)。"""
    from ..pipeline.ranking import selection_reason
    return selection_reason(cand, cfg_r)
