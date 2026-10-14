# -*- coding: utf-8 -*-
"""GitHub 采集器:官方 Search API(只读 GITHUB_PAT)。

按趋势参考实现 3 组查询模板,日期程序动态计算,每组 ≤ per_page(10):
  - 新项目:      topic:ai     created:>{d} stars:>=20
  - 新兴 Agent:  topic:agents pushed:>{d} stars:>=50
  - 活跃 LLM:    topic:llm    pushed:>{d} stars:>=100

只保存轻量字段(full_name/html_url/description≤200/stargazers/forks/created_at/
pushed_at/language/topics≤5/fetched_at),不抓 README/源码/评论。
每组查询独立 try/except + 退避重试;403/429 不硬闯,单组失败不影响其他组。
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import requests


class RateLimitedError(Exception):
    """GitHub search API 限流(403/429),不再重试该查询。"""


class GitHubCollector:
    def __init__(self, cfg, gh_pat):
        self.cfg = cfg
        self.headers = {
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'ai-news-mvp-stage02',
            'X-GitHub-Api-Version': '2022-11-28',
        }
        if gh_pat:
            self.headers['Authorization'] = f'Bearer {gh_pat}'
        self.per_page = int(cfg.get('per_page', 10))
        self.cap = int(cfg.get('per_platform_cap', 10))
        self.timeout = float(cfg.get('timeout', 30))
        self.retries = int(cfg.get('retries', 2))
        self.backoff = float(cfg.get('backoff_seconds', 2))
        self.interval = float(cfg.get('request_interval_seconds', 1.2))

    # ---------- 查询执行(带退避重试) ----------
    def _search(self, params):
        """单次 search 请求;5xx/网络错误抛异常由调用方重试,403/429 抛 RateLimitedError。"""
        r = requests.get(
            f"{self.cfg['api_base']}/search/repositories",
            params=params, headers=self.headers, timeout=self.timeout,
        )
        if r.status_code in (403, 429):
            raise RateLimitedError(f'HTTP {r.status_code}: {r.text[:120]}')
        r.raise_for_status()
        return r.json().get('items', [])

    def _search_with_retry(self, params, query):
        """对 5xx/网络错误做指数退避重试;限流不重试。"""
        for attempt in range(self.retries + 1):
            try:
                return self._search(params)
            except RateLimitedError:
                raise
            except Exception as e:
                if attempt >= self.retries:
                    raise
                wait = self.backoff * (2 ** attempt)
                logging.warning(f'[GitHub] 查询 "{query}" 第 {attempt + 1} 次失败: {e},'
                                f' {wait}s 后重试')
                time.sleep(wait)
        return []

    # ---------- 字段裁剪 ----------
    @staticmethod
    def _pick_fields(it, fetched_at):
        """只保留轻量字段;description ≤200 字,topics ≤5。"""
        description = (it.get('description') or '').strip()
        if len(description) > 200:
            description = description[:200]
        return {
            'full_name': it.get('full_name', ''),
            'html_url': it.get('html_url', ''),
            'description': description,
            'stargazers_count': it.get('stargazers_count'),
            'forks_count': it.get('forks_count'),
            'created_at': it.get('created_at'),
            'pushed_at': it.get('pushed_at'),
            'language': it.get('language'),
            'topics': (it.get('topics') or [])[:5],
            'fetched_at': fetched_at,
        }

    # ---------- 主入口 ----------
    def fetch(self, since_days):
        """执行 3 组查询,按 full_name 跨组去重,返回 (unified_items, info)。"""
        d = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime('%Y-%m-%d')
        fetched_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        seen = {}      # full_name -> 轻量字段 dict
        done = failed = 0
        rate_limited = False
        per_query = []

        for q in self.cfg.get('queries', []):
            query = q['template'].format(d=d)
            params = {'q': query, 'sort': self.cfg.get('sort', 'stars'),
                      'order': self.cfg.get('order', 'desc'), 'per_page': self.per_page}
            try:
                items = self._search_with_retry(params, query)
                done += 1
                per_query.append({'name': q['name'], 'query': query, 'ok': True, 'items': len(items)})
            except RateLimitedError as e:
                rate_limited = True
                failed += 1
                per_query.append({'name': q['name'], 'query': query, 'ok': False, 'error': str(e)})
                logging.warning(f'[GitHub] 查询 "{query}" 限流跳过: {e}')
                continue
            except Exception as e:
                failed += 1
                per_query.append({'name': q['name'], 'query': query, 'ok': False, 'error': str(e)})
                logging.warning(f'[GitHub] 查询 "{query}" 失败: {e}')
                continue

            for it in items:
                full = it.get('full_name', '')
                if not full or full in seen:
                    continue
                seen[full] = self._pick_fields(it, fetched_at)
            time.sleep(self.interval)  # search 30/min,留余量

        # 统一条目:按 stars 降序,跨组按 full_name 去重。
        # 阶段04修复(AIQI-7):不再在采集器内部限量 —— 处理顺序改为
        # 「规则筛选 → 跨源去重 → 每平台限量」,限量在 run.py 跨源去重之后执行。
        ordered = sorted(seen.values(), key=lambda x: x['stargazers_count'] or 0, reverse=True)
        entries = [self._to_entry(f) for f in ordered]
        logging.info(f'[GitHub] {done} 查询成功 / {failed} 失败,'
                     f'跨组去重 {len(seen)} 个仓库(限量将在跨源去重后执行,上限 {self.cap})')
        return entries, {
            'raw_items': len(seen), 'queries_done': done, 'queries_failed': failed,
            'rate_limited': rate_limited, 'cap': self.cap, 'per_query': per_query,
        }

    @staticmethod
    def _to_entry(f):
        """统一条目(供去重/入库);GitHub 轻量字段整体放入 extra。"""
        return {
            'title': f['full_name'],
            'url': f['html_url'],
            'summary': f['description'],
            'published_at': f['pushed_at'] or f['created_at'],
            'platform': 'github',
            'category': 'opensource',
            '_source_url': 'github:search',
            '_source_name': 'GitHub Search API',
            'extra': f,
        }
