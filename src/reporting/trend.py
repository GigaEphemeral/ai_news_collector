# -*- coding: utf-8 -*-
"""规则趋势引擎(阶段03):纯规则、不评分、不调 LLM。

对两类对象输出状态与证据:
  1. 项目趋势(project_snapshots:Star/Fork/更新时间每日快照)
  2. 主题趋势(topic_daily_stats:每日条目数/来源数)

状态机(五态,方案文档第五节):
  新出现     最近首次出现,历史不足以判断持续性 → 常标"初步判断"/"尚待观察"
  持续升温   连续多个观察周期活跃度增加(项目:连续正增长;主题:连续增量+多来源)
  突然增长   短期增量显著高于该项目/主题自身历史水平
  进入成熟期 高存量但增长趋缓(稳定工具);或连续多日无新变化
  热度回落   近期连续下降

时间尺度:24h / 7d / 30d。各窗口历史天数不足时明确标注"窗口不足",不臆造。
证据规则:每个结论必须带来源、数字、时间窗;证据不足写"新出现,尚待观察"。
重复降权:连续多日入选且近 N 日无新变化(Star 增量≈0)的仓库标 downgraded。
首次运行只建基线,次日才开始计算 24h 增量;首日/首周趋势须标注"初步判断"。

全部阈值在 config.yaml 的 trend 段配置;本模块零第三方依赖。
"""
import logging
from datetime import date, datetime, timedelta

from ..storage.db import match_keywords

# 五态常量(输出文案)
STATE_NEW = '新出现'
STATE_WARMING = '持续升温'
STATE_SURGE = '突然增长'
STATE_MATURE = '进入成熟期'
STATE_DECLINE = '热度回落'

WINDOW_24H, WINDOW_7D, WINDOW_30D = '24h', '7d', '30d'
ALL_WINDOWS = (WINDOW_24H, WINDOW_7D, WINDOW_30D)


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
def parse_date(s):
    """YYYY-MM-DD → date;失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _fmt_pct(x):
    """百分比格式化:+12.3% / -0.0% / None。"""
    if x is None:
        return None
    return f'{x:+.2f}%'


def _growth_pct(delta, base):
    if base is None or base <= 0:
        return None
    return delta / base * 100


def _window_status(needed, have, what):
    """历史不足的窗口统一标注:窗口不足(原因)。"""
    if have >= needed:
        return {'status': 'ok', 'needed': needed, 'have': have}
    return {'status': '窗口不足', 'needed': needed, 'have': have,
            'detail': f'{what}仅 {have} 天,需 {needed} 天'}


# ---------------------------------------------------------------------------
# 项目趋势(基于 project_snapshots)
# ---------------------------------------------------------------------------
def repo_trend(repo, snap_rows, run_date, cfg, ai_keywords):
    """单仓库趋势结论。snap_rows:按 date 升序的 project_snapshots 行。

    返回 dict:
      repo / state / confidence / downgraded / windows / evidence / numbers
    """
    wcfg = cfg.get('windows', {})
    need24 = int(wcfg.get(WINDOW_24H, 2))
    need7 = int(wcfg.get(WINDOW_7D, 7))
    need30 = int(wcfg.get(WINDOW_30D, 30))
    pcfg = cfg.get('project', {})

    days = [parse_date(r['date']) for r in snap_rows]
    days = [d for d in days if d]
    if not days:
        return {'repo': repo, 'state': STATE_NEW, 'confidence': '初步判断',
                'downgraded': False, 'windows': {}, 'evidence': ['新出现:无可用快照,尚待观察'], 'numbers': {}}
    n = len(days)
    today = run_date
    d_prev24 = today - timedelta(days=1)

    stars = {d: (r['stars'] or 0) for d, r in zip(days, snap_rows)}
    dates_set = set(days)

    # ---- 三个时间窗口 ----
    win24 = _window_status(need24, 2 if (today in dates_set and d_prev24 in dates_set) else
                           (1 if today in dates_set else 0), '24h 窗口')
    have7 = len([d for d in days if today - timedelta(days=6) <= d <= today])
    have30 = len([d for d in days if today - timedelta(days=29) <= d <= today])
    win7 = _window_status(need7, have7, '7d 窗口')
    win30 = _window_status(need30, have30, '30d 窗口')

    # 24h 增量
    delta24 = growth24 = None
    if win24['status'] == 'ok':
        s_prev, s_curr = stars[d_prev24], stars[today]
        delta24 = s_curr - s_prev
        growth24 = _growth_pct(delta24, s_prev)

    # 近 7 日逐日增量(用于均值/突增判断)
    deltas = []
    for i in range(1, len(days)):
        if days[i] - days[i - 1] == timedelta(days=1):
            deltas.append(stars[days[i]] - stars[days[i - 1]])

    # ---- 证据 ----
    evidence = []
    numbers = {'stars_today': stars.get(today), 'delta_24h': delta24,
               'growth_24h_pct': growth24, 'snapshot_days': n}
    if win24['status'] == 'ok':
        evidence.append(
            f'24h:Star {stars[d_prev24]} → {stars[today]},增量 {delta24:+d}'
            f'({_fmt_pct(growth24)}),窗口 {d_prev24.isoformat()} → {today.isoformat()}(project_snapshots)')
    else:
        evidence.append(f'24h:{win24["detail"]},暂无法计算 24h 增量')

    if win7['status'] == 'ok':
        seg = deltas[-6:]
        avg = sum(seg) / len(seg) if seg else 0
        numbers['avg_daily_delta_7d'] = round(avg, 2)
        evidence.append(
            f'7d:{have7} 个观察日,日均增量 {avg:+.2f},近 7 天累计 {sum(seg):+d}')
    else:
        evidence.append(f'7d:{win7["detail"]}')
    if win30['status'] != 'ok':
        evidence.append(f'30d:{win30["detail"]}')

    # 新鲜度
    last_row = snap_rows[-1]
    created = parse_date(last_row['created_at'])
    if created:
        age_days = (today - created).days
        numbers['age_days'] = age_days
        if age_days <= int(pcfg.get('young_days', 30)):
            evidence.append(f'新鲜度:新项目,创建于 {created.isoformat()}({age_days} 天前)')
        else:
            evidence.append(f'新鲜度:创建于 {created.isoformat()}({age_days} 天前,非新项目)')
    pushed = parse_date(last_row['pushed_at'])
    if pushed and pushed >= today - timedelta(days=1):
        evidence.append(f'活跃:最近 push {last_row["pushed_at"][:16]}(24h 内)')

    # AI 相关性(topics/description)
    topics = []
    try:
        import json
        topics = json.loads(last_row['topics'] or '[]')
    except (ValueError, TypeError):
        pass
    rel = match_keywords(' '.join(topics + [last_row['description'] or '']), ai_keywords)
    if rel:
        evidence.append(f'AI 相关性:topics=[{", ".join(topics[:5]) or "无"}] 命中 {", ".join(rel[:3])}')
    else:
        evidence.append('AI 相关性:topics/description 未命中关键词,相关性弱')

    # ---- 重复降权:连续多日入选且近 N 日增量≈0 ----
    stagnation_days = int(pcfg.get('stagnation_days', 7))
    stagnation_max_pct = float(pcfg.get('stagnation_max_delta_pct', 0.05))
    downgraded = False
    if n >= stagnation_days:
        tail = deltas[-(stagnation_days - 1):]
        total_tail = sum(tail)
        base = stars.get(today)
        tail_pct = _growth_pct(total_tail, base)
        # 降权仅针对"无新变化":近 N 日增量≈0(正负都在阈值内);真实涨跌不算降权
        if total_tail == 0 or (tail_pct is not None and abs(tail_pct) <= stagnation_max_pct):
            downgraded = True
            evidence.append(
                f'降权:连续 {stagnation_days} 日入选,近 {stagnation_days} 日 Star 增量 '
                f'{total_tail:+d}({_fmt_pct(tail_pct)}),无新变化')

    # ---- 状态判定 ----
    state, confidence = STATE_NEW, None
    young_days = int(pcfg.get('young_days', 30))
    if created and (today - created).days <= young_days:
        confidence = '初步判断'

    if n == 1:
        state = STATE_NEW
        if confidence is None:
            confidence = '初步判断'
        evidence.insert(0, '新出现:首次入选(仅 1 个快照日),历史不足,尚待观察')
    elif win24['status'] != 'ok':
        # 有历史但昨日缺口,不能算 24h 增量
        state = STATE_NEW
        evidence.insert(0, '新出现:昨日无快照,24h 增量无法计算,尚待观察')
    else:
        state = _project_state(delta24, growth24, deltas, stars.get(today, 0),
                               n, pcfg)
        if n < need7:
            confidence = '初步判断'  # 历史窗口不足时不下确定性结论
        elif state in (STATE_WARMING, STATE_SURGE) and n < int(pcfg.get('warming_min_days', 3)) + 1:
            confidence = '初步判断'

    # 新出现(n≥2 但变化不足以定论)时,如实说明原因,不编造趋势
    if state == STATE_NEW and n >= 2 and delta24 is not None:
        if delta24 > 0:
            evidence.insert(0, f'新出现:24h 增量 {delta24:+d}({_fmt_pct(growth24)}),'
                               '幅度/历史不足,尚待观察')
        elif delta24 == 0:
            evidence.insert(0, '新出现:近 24h 增量 0,变化不足以定论,尚待观察')
        elif delta24 < 0:
            evidence.insert(0, f'新出现:24h 增量 {delta24:+d},单日下降不足以定论,尚待观察')

    return {
        'repo': repo, 'state': state, 'confidence': confidence,
        'downgraded': downgraded,
        'windows': {WINDOW_24H: win24, WINDOW_7D: win7, WINDOW_30D: win30},
        'evidence': evidence, 'numbers': numbers,
    }


def _project_state(delta24, growth24, deltas, stars_today, n, pcfg):
    """五态判定(前提:24h 窗口可用)。"""
    sudden_ratio = float(pcfg.get('sudden_growth_ratio', 3.0))
    warming_min = int(pcfg.get('warming_min_days', 3))
    warming_growth = float(pcfg.get('warming_min_growth_pct', 0.5))
    maturity_stars = int(pcfg.get('maturity_stars', 50000))
    maturity_pct = float(pcfg.get('maturity_growth_pct', 0.05))
    decline_neg = int(pcfg.get('decline_negative_days', 2))
    stagnation_days = int(pcfg.get('stagnation_days', 7))

    last3 = deltas[-3:] if deltas else []
    prev3 = deltas[-6:-3] if len(deltas) >= 6 else deltas[:-3]

    # 1) 突然增长:24h 增量显著高于自身历史日均(需 ≥1 个前序日可对比)
    if delta24 > 0 and len(deltas) >= 2:
        prev = deltas[:-1]
        avg_prev = sum(prev) / len(prev) if prev else 0
        if avg_prev <= 0 or delta24 >= sudden_ratio * avg_prev:
            return STATE_SURGE

    # 2) 持续升温:连续 warming_min 天正增长、趋势不减、且单日增长率达最低阈值
    if len(last3) >= warming_min and all(d > 0 for d in last3[-warming_min:]):
        window_days = last3[-warming_min:]
        base = stars_today - sum(window_days)  # 窗口起点 Star 数
        win_growth = _growth_pct(sum(window_days), base)  # 窗口期累计增长率
        if win_growth is not None and win_growth >= warming_growth:
            if not prev3 or sum(last3) >= sum(prev3):
                return STATE_WARMING

    # 3) 热度回落:连续 decline_neg 天负增长
    if len(deltas) >= decline_neg and all(d < 0 for d in deltas[-decline_neg:]):
        return STATE_DECLINE

    # 4) 进入成熟期:高存量 + 增长趋缓 / 长期无变化(含重复入选无新变化)
    if n >= stagnation_days:
        tail = deltas[-(stagnation_days - 1):]
        if tail and (sum(tail) == 0 or
                     (_growth_pct(sum(tail), stars_today) or 99) <= maturity_pct):
            return STATE_MATURE
    if stars_today >= maturity_stars and n >= 7:
        avg7 = sum(deltas[-6:]) / 6 if len(deltas) >= 6 else 0
        if _growth_pct(avg7, stars_today) is None or \
                abs(_growth_pct(avg7, stars_today) or 0) <= maturity_pct:
            return STATE_MATURE

    # 5) 温和回落:近期均值低于前期均值且当日无正增长(单日负增长不单独定论)
    if prev3 and last3 and sum(last3) < sum(prev3) and delta24 <= 0:
        return STATE_DECLINE

    # 6) 正增长但幅度不足/历史太短 → 如实标"新出现,尚待观察"
    if delta24 > 0:
        if growth24 is not None and growth24 < warming_growth:
            return STATE_NEW
        return STATE_WARMING
    if n >= 7:
        return STATE_MATURE
    return STATE_NEW


# ---------------------------------------------------------------------------
# 主题趋势(基于 topic_daily_stats)
# ---------------------------------------------------------------------------
def topic_trend(topic, stat_rows, run_date, cfg):
    """单主题趋势结论。stat_rows:按 stat_date 升序的 topic_daily_stats 行。"""
    wcfg = cfg.get('windows', {})
    need24 = int(wcfg.get(WINDOW_24H, 2))
    need7 = int(wcfg.get(WINDOW_7D, 7))
    need30 = int(wcfg.get(WINDOW_30D, 30))
    tcfg = cfg.get('topic', {})

    days = [parse_date(r['stat_date']) for r in stat_rows]
    days = [d for d in days if d]
    if not days:
        return {'topic': topic, 'state': STATE_NEW, 'confidence': '初步判断',
                'windows': {}, 'evidence': ['新出现:无统计数据,尚待观察'], 'numbers': {}}
    n = len(days)
    today = run_date
    d_prev24 = today - timedelta(days=1)
    cnt = {d: (r['count'] or 0) for d, r in zip(days, stat_rows)}
    src = {d: (r['source_count'] or 0) for d, r in zip(days, stat_rows)}
    dates_set = set(days)

    win24 = _window_status(need24, 2 if (today in dates_set and d_prev24 in dates_set) else
                           (1 if today in dates_set else 0), '24h 窗口')
    have7 = len([d for d in days if today - timedelta(days=6) <= d <= today])
    have30 = len([d for d in days if today - timedelta(days=29) <= d <= today])
    win7 = _window_status(need7, have7, '7d 窗口')
    win30 = _window_status(need30, have30, '30d 窗口')

    delta24 = None
    if win24['status'] == 'ok':
        delta24 = cnt[today] - cnt[d_prev24]

    evidence = []
    numbers = {'count_today': cnt.get(today), 'delta_24h': delta24,
               'stat_days': n}
    if win24['status'] == 'ok':
        evidence.append(
            f'24h:条目 {cnt[d_prev24]} → {cnt[today]},增量 {delta24:+d},'
            f'窗口 {d_prev24.isoformat()} → {today.isoformat()}(topic_daily_stats)')
    else:
        evidence.append(f'24h:{win24["detail"]},暂无法计算 24h 增量')
    if win7['status'] == 'ok':
        seg = [cnt[d] for d in days if today - timedelta(days=6) <= d <= today]
        evidence.append(f'7d:{len(seg)} 个观察日,日均条目 {sum(seg) / len(seg):.1f}')
    else:
        evidence.append(f'7d:{win7["detail"]}')
    if win30['status'] != 'ok':
        evidence.append(f'30d:{win30["detail"]}')

    # 状态判定
    state, confidence = STATE_NEW, None
    if n == 1:
        state = STATE_NEW
        confidence = '初步判断'
        evidence.insert(0, '新出现:首次统计(仅 1 天),历史不足,尚待观察')
    elif win24['status'] != 'ok':
        state = STATE_NEW
        evidence.insert(0, '新出现:昨日无统计,24h 增量无法计算,尚待观察')
    else:
        state = _topic_state(cnt, src, days, today, delta24, n, tcfg)
        if n < need7:
            confidence = '初步判断'

    if state == STATE_NEW and n >= 2 and delta24 == 0:
        evidence.insert(0, '新出现:近 24h 条目数无变化,尚待观察')
    if state == STATE_NEW and not any('尚待观察' in e for e in evidence):
        evidence.insert(0, '新出现,尚待观察')

    return {
        'topic': topic, 'state': state, 'confidence': confidence,
        'windows': {WINDOW_24H: win24, WINDOW_7D: win7, WINDOW_30D: win30},
        'evidence': evidence, 'numbers': numbers,
    }


def _topic_state(cnt, src, days, today, delta24, n, tcfg):
    sudden_ratio = float(tcfg.get('sudden_growth_ratio', 2.0))
    warming_min = int(tcfg.get('warming_min_days', 3))
    maturity_min = int(tcfg.get('maturity_min_count', 3))
    decline_neg = int(tcfg.get('decline_negative_days', 2))

    series = [cnt[d] for d in days]
    deltas = [series[i] - series[i - 1] for i in range(1, len(series))]
    # 突然增长:当日增量 ≥ 前序日均增量 × ratio(需 ≥1 个前序增量可对比)
    if delta24 is not None and delta24 > 0 and len(deltas) >= 2:
        prev_inc = deltas[:-1]
        avg_inc = sum(prev_inc) / len(prev_inc) if prev_inc else 0
        if avg_inc <= 0 or delta24 >= sudden_ratio * avg_inc:
            return STATE_SURGE
    # 持续升温:连续增量 + 至少一天多来源
    last_deltas = deltas[-warming_min:]
    multi_source = any(src[d] >= 2 for d in days[-warming_min:])
    if len(last_deltas) >= warming_min and all(d > 0 for d in last_deltas) and multi_source:
        return STATE_WARMING
    # 热度回落:连续递减
    if len(series) >= decline_neg and all(
            series[i] < series[i - 1] for i in range(len(series) - decline_neg, len(series))):
        return STATE_DECLINE
    # 进入成熟期:7 天日均 ≥ 阈值且近 3 天无增长
    seg7 = series[-7:] if len(series) >= 7 else series
    if len(seg7) >= 7 and sum(seg7) / 7 >= maturity_min and series[-3:] == [series[-1]] * 3:
        return STATE_MATURE
    # 温和回落
    if len(series) >= 6:
        if sum(series[-3:]) < sum(series[-6:-3]) and delta24 is not None and delta24 <= 0:
            return STATE_DECLINE
    if delta24 is not None and delta24 > 0:
        return STATE_WARMING
    if n >= 7:
        return STATE_MATURE
    return STATE_NEW


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------
# 输出展示顺序(固定,非评分排序)
STATE_ORDER = [STATE_NEW, STATE_WARMING, STATE_SURGE, STATE_MATURE, STATE_DECLINE]


def build_trends(db, run_date, cfg, ai_keywords, limit_projects=20, limit_topics=20):
    """汇总全部项目/主题趋势。返回 {date, first_snapshot_day, projects, topics}。

    first_snapshot_day:本运行前库中没有任何更早日期的快照 → 首日只建基线。
    """
    run_date = parse_date(run_date) or date.today()
    first_day = not db.has_snapshot_before(run_date.isoformat())
    first_topic_day = not db.topic_stats_exist_before(run_date.isoformat())

    projects = []
    for repo in db.snapshot_repos_on(run_date.isoformat()):
        rows = db.snapshot_rows(repo)
        t = repo_trend(repo, rows, run_date, cfg, ai_keywords)
        if first_day:
            t['confidence'] = '初步判断'
            if not any('首日运行' in e for e in t['evidence']):
                t['evidence'].insert(0, '系统首日运行:仅建立基线,次日才开始计算 24h 增量')
        projects.append(t)
        if len(projects) >= limit_projects:
            break
    projects.sort(key=lambda t: (STATE_ORDER.index(t['state']), t['repo']))

    topics = []
    for topic in db.topics_with_stat_on(run_date.isoformat()):
        rows = db.topic_stat_rows(topic)
        t = topic_trend(topic, rows, run_date, cfg)
        if first_topic_day:
            t['confidence'] = '初步判断'
        # 阶段05修复:主题趋势附代表条目(标题+url)作为来源证据,避免"只有计数无出处"
        rep_id = rows[-1]['representative_item_id'] if rows else None
        title, url = db.item_title_url(rep_id)
        if title:
            t['evidence'].append(f'代表条目:{title}({url or "无链接"})')
            t['representative'] = {'title': title, 'url': url}
        topics.append(t)
        if len(topics) >= limit_topics:
            break
    topics.sort(key=lambda t: (STATE_ORDER.index(t['state']), t['topic']))

    return {
        'date': run_date.isoformat(),
        'first_snapshot_day': first_day,
        'windows_required': {WINDOW_24H: int(cfg['windows'].get(WINDOW_24H, 2)),
                             WINDOW_7D: int(cfg['windows'].get(WINDOW_7D, 7)),
                             WINDOW_30D: int(cfg['windows'].get(WINDOW_30D, 30))},
        'projects': projects,
        'topics': topics,
    }
