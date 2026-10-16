# -*- coding: utf-8 -*-
"""Hacker News 采集器:官方 Firebase API(topstories)。

抓取 topstories 前 N 条,逐条取标题/链接/分数/评论数/时间;
热度+关键词规则过滤在 run.py 中通过 src/filters.rule_filter 完成,每平台 ≤10。
单条失败只计数,不影响其他条目;顶层接口失败返回空列表并计数。
"""
import logging
import time
from datetime import datetime, timezone

import requests


class HackerNewsCollector:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg['api_base']
        self.top_n = int(cfg.get('top_n', 25))
        self.timeout = float(cfg.get('timeout', 30))
        self.retries = int(cfg.get('retries', 2))
        self.backoff = float(cfg.get('backoff_seconds', 2))

    def _get_json(self, url):
        for attempt in range(self.retries + 1):
            try:
                r = requests.get(url, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                if attempt >= self.retries:
                    raise
                wait = self.backoff * (2 ** attempt)
                logging.warning(f'[HN] {url} 第 {attempt + 1} 次失败: {e},{wait}s 后重试')
                time.sleep(wait)

    def fetch(self):
        """返回 (entries, info);info 含 fetched_ids / failed_items。"""
        try:
            ids = self._get_json(f'{self.base}/topstories.json') or []
        except Exception as e:
            logging.error(f'[HN] topstories 失败: {e}')
            return [], {'fetched_ids': 0, 'failed_items': 1, 'top_level_error': str(e)}
        ids = ids[:self.top_n]

        entries, failed = [], 0
        for sid in ids:
            try:
                it = self._get_json(f'{self.base}/item/{sid}.json')
                if not it or it.get('type') != 'story':
                    continue
                title = (it.get('title') or '').strip()
                if not title:
                    continue
                entries.append({
                    'title': title,
                    'url': it.get('url') or f'https://news.ycombinator.com/item?id={sid}',
                    'native_id': str(sid),  # 阶段03:自然键 = hn_id
                    'summary': (it.get('text') or '')[:500],
                    'published_at': datetime.fromtimestamp(
                        it.get('time', 0), tz=timezone.utc
                    ).isoformat(timespec='seconds'),
                    'platform': 'hackernews',
                    'category': 'discussion',
                    '_source_url': 'hackernews:topstories',
                    '_source_name': 'Hacker News',
                    'extra': {'points': it.get('score', 0),
                              'comments': it.get('descendants', 0),
                              'hn_id': sid},
                })
            except Exception as e:
                failed += 1
                logging.warning(f'[HN] item {sid} 失败: {e}')
            time.sleep(0.1)  # 官方 API 无 SLA,温柔限速

        logging.info(f'[HN] topstories 前 {len(ids)} 条,成功 {len(entries)},失败 {failed}')
        return entries, {'fetched_ids': len(ids), 'failed_items': failed}
