# -*- coding: utf-8 -*-
"""GitHub 候选排序与上榜理由(阶段04修复 AIQI-7):纯规则、不评分、不调 LLM。

成员反馈问题1(「今日值得关注」时效性差)修复,按成员修正版实现:
  - 「新生代优先,兼顾老项目增量」:优先选创建时间近(新生代)或近期有真实增长的项目;
    老项目只要当日/近期有显著增量(24h Star 增量或增长率达阈值)也允许上榜,不一刀切排除;
  - 首日/快照不足(无法算 24h 增量)时:仅新生代且近期 push 的项目入选,并标注「初步判断」;
    宁可少选,不把无增量的老项目顶上来凑数(硬性约束:栏目允许为空);
  - 上榜理由可解释:每条给出具体理由(今日新出现 / 近 7 天持续更新 / 24h Star +N(前一日 M) /
    创建 N 天),基于库中真实字段,不用模型编造的模糊话术。

排序策略与阈值全部来自 config.yaml 的 report: 段:
  sort_by: freshness(默认)| growth | stars
  new_days / old_repo_min_delta_24h / old_repo_min_growth_pct / recent_push_days

不生成任何综合评分或隐藏分值;只做规则分层排序(硬性约束 #6)。
本模块零第三方依赖;供 src/report.py 与 run.py 共用。
"""
from datetime import datetime, timedelta, timezone

DEFAULT_SORT_BY = 'freshness'
DEFAULT_NEW_DAYS = 30
DEFAULT_MIN_DELTA_24H = 50
DEFAULT_MIN_GROWTH_PCT = 0.5
DEFAULT_RECENT_PUSH_DAYS = 7


def parse_date(s):
    """YYYY-MM-DD → date;失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _days_since(iso, run_date):
    """ISO 时间距 run_date 的天数;无法解析返回 None。"""
    dt = _parse_iso(iso)
    if dt is None:
        return None
    return max((run_date - dt.date()).days, 0)


def _delta_ok(delta, stars, cfg_r):
    """老项目 24h 增量是否达上榜阈值(增量 ≥ min_delta 或增长率 ≥ min_pct)。"""
    min_delta = int(cfg_r.get('old_repo_min_delta_24h', DEFAULT_MIN_DELTA_24H))
    min_pct = float(cfg_r.get('old_repo_min_growth_pct', DEFAULT_MIN_GROWTH_PCT))
    if delta is None:
        return False
    if delta >= min_delta:
        return True
    return stars > 0 and delta / stars * 100 >= min_pct


def github_sort_key(cand, cfg_r):
    """GitHub 候选排序键(纯规则分层,非综合评分)。

    cand 需含:created_days / stars_24h / stars / pushed_days(均可 None)。
    sort_by:
      - freshness(默认):(新生代层, 增量, 创建天数, star 总数)
          层0=新生代(创建 ≤ new_days);层1=老项目且增量达阈值;层2=其余
      - growth:有 24h 增量的在前,按增量降序;无增量数据垫底
      - stars:按 star 总数降序(旧行为,仅当明确需要时选用)
    返回可比较元组(数值越小越靠前)。
    """
    sort_by = cfg_r.get('sort_by', DEFAULT_SORT_BY)
    new_days = int(cfg_r.get('new_days', DEFAULT_NEW_DAYS))
    created = cand.get('created_days')
    delta = cand.get('stars_24h')
    stars = cand.get('stars') or 0
    if sort_by == 'stars':
        return (0, 0, -stars, -(created or 0))
    if sort_by == 'growth':
        if delta is None:
            return (1, 0, -(created or 0), -stars)
        return (0, -delta, -(created or 0), -stars)
    # freshness(默认)
    is_new = created is not None and created <= new_days
    if is_new:
        return (0, -(delta or 0), -(created or 0), -stars)
    if _delta_ok(delta, stars, cfg_r):
        return (1, -(delta or 0), -(created or 0), -stars)
    return (2, 0, -(created or 0), -stars)


def github_eligible(cand, cfg_r, has_delta_data):
    """「今日值得关注」的 GitHub 候选资格(硬性约束:宁缺毋滥,不凑数)。

    has_delta_data: 本次是否有任何仓库能算 24h 增量(快照 ≥ 2 天)。
      - 有增量数据:新生代 或 老项目增量达阈值 → 入选
      - 无增量数据(首日/快照不足):仅新生代且近期 push → 入选,并标注「初步判断」
    """
    new_days = int(cfg_r.get('new_days', DEFAULT_NEW_DAYS))
    recent_push = int(cfg_r.get('recent_push_days', DEFAULT_RECENT_PUSH_DAYS))
    created = cand.get('created_days')
    if created is None:
        return False
    is_new = created <= new_days
    if not has_delta_data:
        pushed = cand.get('pushed_days')
        return is_new and (pushed is None or pushed <= recent_push)
    if is_new:
        return True
    return _delta_ok(cand.get('stars_24h'), cand.get('stars') or 0, cfg_r)


def github_entry_sort_key(entry, cfg_r, run_date, db=None):
    """GitHub 采集条目(entry.extra)→ 排序键(供 run.py 跨源去重后限量前排序)。

    24h 增量 = 本次采集 stargazers - 库中昨日快照(今日快照尚未写入);
    无昨日快照或 db 不可用 → delta=None(视为无增量数据)。
    """
    ex = entry.get('extra') or {}
    run_date = parse_date(run_date) or datetime.now(timezone.utc).date()
    stars = ex.get('stargazers_count')
    created_days = _days_since(ex.get('created_at'), run_date)
    pushed_days = _days_since(ex.get('pushed_at'), run_date)
    delta = None
    if db is not None and ex.get('full_name') and stars is not None:
        try:
            d_prev = (run_date - timedelta(days=1)).isoformat()
            for r in db.snapshot_rows(ex['full_name']):
                if r['date'] == d_prev and r['stars'] is not None:
                    delta = stars - r['stars']
                    break
        except Exception:
            delta = None
    return github_sort_key({'created_days': created_days, 'stars_24h': delta,
                            'stars': stars, 'pushed_days': pushed_days}, cfg_r)


def rss_hf_heat_sort_key(cand, cfg):
    """RSS/HF 候选热度分层排序键(阶段05:消费时规则,不落库)。

    三层:
      层1 有真实热度:HF upvotes+stars / 跨源命中 HN 用 points → 按信号降序
      层2 无信号但 source_count≥2(重复度高)→ 按重复度 + 新鲜度
      层3 其余 → 按(新鲜度, 关键词权重)
    返回可比较元组(数值越小越靠前);signal 越大越靠前 → 取负。
    """
    layer = cand.get('heat_layer', 3)
    signal = cand.get('heat_signal') or 0
    source_count = cand.get('source_count') or 1
    freshness = cand.get('score') or 0  # 已算:发布距当日 0 点的秒数(越小越新)
    kw = cand.get('keyword_hits') or 0
    if layer == 1:
        return (1, -signal, freshness)
    if layer == 2:
        return (2, -source_count, freshness)
    return (3, freshness, -kw)


def selection_reason(cand, cfg_r, has_delta_data=False):
    """可解释上榜理由:基于库中真实字段,不用模型编造的模糊话术(问题1修复3)。

    示例:「今日新出现」「新生代(创建 3 天)」「24h Star +250(前一日 0)」
        「近 7 天持续更新」「老项目 24h 增量 +250 达阈值上榜」「初步判断(历史不足)」
    """
    platform = cand.get('platform')
    if platform == 'hackernews':
        pts = cand.get('stars')
        return f'HN {pts} 分' if pts is not None else 'HN 热度条目'
    if platform in ('rss', 'hf_papers'):
        created = cand.get('created_days')
        if created is None:
            return '今日发布'
        return '今日发布' if created == 0 else f'发布 {created} 天前'

    new_days = int(cfg_r.get('new_days', DEFAULT_NEW_DAYS))
    parts = []
    created = cand.get('created_days')
    delta = cand.get('stars_24h')
    stars = cand.get('stars')
    pushed = cand.get('pushed_days')

    if created is not None:
        if created == 0:
            parts.append('今日新出现')
        elif created <= new_days:
            parts.append(f'新生代(创建 {created} 天)')
        else:
            parts.append(f'创建 {created} 天')
    if delta is not None:
        if delta > 0:
            prev = (stars - delta) if stars is not None and delta is not None else None
            prev_txt = f'(前一日 {prev})' if prev is not None and prev >= 0 else ''
            parts.append(f'24h Star {delta:+d}{prev_txt}')
        elif delta == 0:
            parts.append('24h 增量 0')
        else:
            parts.append(f'24h Star {delta:+d}(回落)')
    if pushed is not None:
        if pushed <= 1:
            parts.append('近 24h 有更新')
        elif pushed <= 7:
            parts.append('近 7 天持续更新')
    if created is not None and created > new_days and delta is not None and delta > 0:
        parts.append('老项目增量达阈值上榜')
    if not has_delta_data:
        parts.append('初步判断(历史不足,按创建时间+近期更新初选)')
    return '、'.join(parts) if parts else '规则筛选入选'
