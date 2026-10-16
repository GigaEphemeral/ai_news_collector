# -*- coding: utf-8 -*-
"""主题日统计(阶段02 从 storage/db.py 拆出,归入 pipeline 领域)。

compute_topic_stats 原为 Database 方法;为保持领域清晰,搬为模块函数,
入参 db 只读库连接 + 调用 db.upsert_topic_stat 写回。
"""
import json


def compute_topic_stats(db, stat_date, keywords, max_topics=3):
    """按日期汇总主题统计(条目数/来源数/代表情报)。

    计数口径:items.first_seen_at 落在 stat_date 当天首次出现的条目。
    主题来源:
      - GitHub:   extra.topics(取前 max_topics,无 topics 归入 opensource)
      - 其他平台: 标题/摘要命中关键词;无命中归入 category/platform。
    代表情报 = 该主题当天互动最高条目(Star/Pointer 最高,平手取最小 id)。
    返回写入的主题数。
    """
    from ..storage.db import match_keywords

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
            topics = (extra.get('topics') or [])[:max_topics] or ['opensource']
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
