# -*- coding: utf-8 -*-
"""主题日统计(阶段02 从 storage/db.py 拆出,归入 pipeline 领域)。

compute_topic_stats 原为 Database 方法;为保持领域清晰,搬为模块函数,
入参 db 只读库连接 + 调用 db.upsert_topic_stat 写回。
"""
import json
import re


def _tag_matches(tag, keywords):
    """仓库标签是否命中 AI 关键词(阶段05修复)。

    标签是短词,用子串匹配会产生误判(如 'email' 含 'ai');短关键词(长度 2,
    如 ai)要求词边界,长关键词允许子串(兼容 'llms'→'llm'、'fine-tuning'→'fine-tun')。
    """
    tag = (tag or '').lower()
    if not tag:
        return False
    for kw in (keywords or []):
        if not kw:
            continue
        if len(kw) <= 2:
            if re.search(r'(?<![a-z0-9])' + re.escape(kw) + r'(?![a-z0-9])', tag):
                return True
        elif kw in tag:
            return True
    return False


def compute_topic_stats(db, stat_date, keywords, max_topics=3):
    """按日期汇总主题统计(条目数/来源数/代表情报)。

    计数口径:items.first_seen_at 落在 stat_date 当天首次出现的条目。
    主题来源:
      - GitHub:   extra.topics 仅保留命中 AI 关键词者(阶段05修复:不再把
                  acme/9router/backend 等非 AI 仓库标签当作趋势主题),取前
                  max_topics,无命中归入 opensource
      - 其他平台: 标题/摘要命中关键词;无命中归入 category/platform。
    代表情报 = 该主题当天互动最高条目(Star/Pointer 最高,平手取最小 id)。
    返回写入的主题数。
    """
    from ..storage.db import match_keywords

    # 重建当日统计:先清掉旧行,避免历史运行残留的旧主题(如已被关键词过滤掉的
    # backend/databases 等)继续出现在趋势里
    db.conn.execute('DELETE FROM topic_daily_stats WHERE stat_date=?', (stat_date,))
    rows = db.conn.execute(
        'SELECT * FROM items WHERE date(first_seen_at)=?',
        (stat_date,)).fetchall()
    agg = {}
    for r in rows:
        extra = {}
        try:
            extra = json.loads(r['extra'] or '{}')
        except (ValueError, TypeError):
            pass
        if r['platform'] == 'github':
            raw = (extra.get('topics') or [])
            # 只保留命中 AI 关键词的仓库标签,避免非 AI 标签(acme/backend/email 等)
            # 污染趋势主题;无命中归入 opensource
            topics = [t for t in raw if _tag_matches(t, keywords)]
            topics = topics[:max_topics] or ['opensource']
        else:
            topics = match_keywords(f"{r['title']} {r['summary'] or ''}", keywords)
            topics = topics[:max_topics] or [r['category'] or r['platform']]
        score = extra.get('stars') or extra.get('points') or 0
        for t in topics:
            a = agg.setdefault(t, {'count': 0, 'sources': set(), 'score': -1, 'rep': None})
            a['count'] += 1
            a['sources'].add(r['platform'])
            if score > a['score'] or (score == a['score'] and
                                      (a['rep'] is None or r['id'] < a['rep'])):
                a['score'], a['rep'] = score, r['id']
    for t, a in agg.items():
        db.upsert_topic_stat(stat_date, t, a['count'], len(a['sources']), a['rep'])
    return len(agg)
