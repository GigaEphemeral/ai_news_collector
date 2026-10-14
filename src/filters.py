# -*- coding: utf-8 -*-
"""规则过滤(纯 Python,不做任何评分):
  - 字段完整性(link / title)
  - 发布时间窗口(可选,配合 min_age_hours 过滤过新条目)
  - AI 关键词初筛(仅 HN / RSS / HF;GitHub 按 topic 已保证相关性)
"""
from datetime import datetime, timezone


def ai_relevant(title, summary='', keywords=None):
    """标题/摘要命中任一 AI 关键词即认为相关(大小写不敏感)。"""
    text = f'{title or ""} {summary or ""}'.lower()
    return any(kw in text for kw in (keywords or []))


def _parse_dt(pub):
    """解析 ISO 时间;失败返回 None。"""
    if not pub:
        return None
    try:
        s = str(pub)[:19]
        return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def rule_filter(entries, platform, cutoff=None, keywords=None, min_age_hours=0):
    """规则过滤,返回 (passed, dropped)。

    dropped 计数:no_link / no_title / old / too_new / not_ai
    """
    passed, dropped = [], {'no_link': 0, 'no_title': 0, 'old': 0, 'too_new': 0, 'not_ai': 0}
    now = datetime.now(timezone.utc)
    for e in entries:
        url = (e.get('url') or '').strip()
        title = (e.get('title') or '').strip()
        if not url:
            dropped['no_link'] += 1
            continue
        if not title:
            dropped['no_title'] += 1
            continue
        pub_dt = _parse_dt(e.get('published_at'))
        if pub_dt:
            if cutoff and pub_dt < cutoff:
                dropped['old'] += 1
                continue
            if min_age_hours and (now - pub_dt).total_seconds() < min_age_hours * 3600:
                dropped['too_new'] += 1
                continue
        if platform in ('hackernews', 'rss', 'hf_papers') and not ai_relevant(
                title, e.get('summary', ''), keywords):
            dropped['not_ai'] += 1
            continue
        passed.append(e)
    return passed, dropped
