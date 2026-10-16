# -*- coding: utf-8 -*-
"""RSS 博客 / Hugging Face Daily Papers 采集器。

复用 vendored rss_fetcher(原 ai-news-brief/src/rss_fetcher.py,MIT,© frankzch,
commit 3b3e182,拷入 src/core/vendor/):
  - HF Daily Papers:直接调用原 RSSFetcher._fetch_hf_daily_papers(逐字复用,
    按 upvotes+stars 双排名保留 top N,只留带 GitHub 仓库的论文)。
  - 普通 RSS:抓取+解析逻辑与原 parse_feed 的 RSS 分支一致(改编),新增超大 feed
    早期截断(OpenAI feed ~785KB/1289 条,在 item/entry 边界截断后再解析)。
  - SOCKS 代理:环境含 ALL_PROXY=socks://127.0.0.1:7897,裸 httpx 不支持 socks
    scheme → 需安装 httpx[socks](socksio);curl_cffi 自身支持 socks。

每个 feed 独立 try/except + 超时 + 退避重试,单 feed 失败不影响其他 feed。
"""
import concurrent.futures
import logging
import time
from calendar import timegm
from datetime import datetime, timezone

import feedparser

from ..core.vendor.rss_fetcher import RSSFetcher  # vendored(阶段02:不再走 sys.path)


def truncate_feed_content(content: bytes, max_entries: int = 30) -> bytes:
    """超大 feed 早期截断:在最后一个完整 <item> 或 <entry> 处截断。

    不超过 max_entries 条时原样返回;feedparser 能容忍缺失闭合标签的 XML。
    """
    if not content:
        return content
    for tag in (b'</item>', b'</entry>'):
        count = 0
        start = 0
        pos = -1
        while True:
            idx = content.find(tag, start)
            if idx == -1:
                break
            count += 1
            pos = idx + len(tag)
            if count >= max_entries:
                return content[:pos]
            start = idx + len(tag)
    return content


class MVPFeedFetcher(RSSFetcher):
    """在复用原 RSSFetcher 基础上:
      - HF Daily Papers 走原 _fetch_hf_daily_papers(逐字复用);
      - 普通 RSS 抓取后早期截断再解析(条目构建逻辑与原 parse_feed 一致)。
    """

    def __init__(self, feeds_config, *, feed_max_entries=30, feed_max_bytes=262144):
        super().__init__(feeds_config)  # 复用原初始化(读取 fetching.network_timeout)
        self.feed_max_entries = feed_max_entries
        self.feed_max_bytes = feed_max_bytes
        self.retries = 2
        self.backoff = 2

    # ---- 抓取(带早期截断 + 退避重试) ----
    def _fetch_with_truncation(self, url, timeout):
        """curl_cffi(TLS 指纹,支持 socks)→ httpx(需 httpx[socks])→ 截断。"""
        content = None
        last_err = None
        for attempt in range(self.retries + 1):
            try:
                try:
                    from curl_cffi import requests as cffi_requests
                    resp = cffi_requests.get(url, impersonate='chrome', timeout=timeout)
                    resp.raise_for_status()
                    content = resp.content
                except Exception as e:
                    last_err = e
                    logging.warning(f'[RSS] curl_cffi 失败 {url}: {e},回退 httpx')
                    content = self._fetch_url(url, {
                        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                                       'Chrome/120.0.0.0 Safari/537.36'),
                        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
                        'Accept-Language': 'en-US,en;q=0.9',
                    })
                if content:
                    break
            except Exception as e:
                last_err = e
                if attempt >= self.retries:
                    break
                wait = self.backoff * (2 ** attempt)
                logging.warning(f'[RSS] {url} 第 {attempt + 1} 次失败: {e},{wait}s 后重试')
                time.sleep(wait)
        if content is None:
            raise RuntimeError(f'feed 抓取失败: {last_err}')

        if len(content) > self.feed_max_bytes:
            truncated = truncate_feed_content(content, self.feed_max_entries)
            logging.info(f'[RSS] {url}: {len(content)} bytes → 早期截断为 '
                         f'{len(truncated)} bytes / 前 {self.feed_max_entries} 条')
            content = truncated
        return content

    # ---- 条目构建(与原 parse_feed 的 RSS 分支一致) ----
    @staticmethod
    def _build_entries(content, feed_config):
        parsed = feedparser.parse(content)
        entries = []
        for entry in parsed.entries:
            published_parsed = entry.get('published_parsed') or entry.get('updated_parsed')
            if published_parsed:
                dt = datetime.fromtimestamp(timegm(published_parsed), tz=timezone.utc)
                published_at = dt.isoformat(timespec='seconds')
            else:
                published_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
            content_val = ''
            if entry.get('content'):
                content_val = entry.content[0].value
            summary = content_val or entry.get('summary', '') or entry.get('description', '')
            entries.append({
                'title': entry.get('title', ''),
                'url': entry.get('link', ''),
                'summary': summary[:500],
                'source': feed_config.get('name', ''),
                'published_at': published_at,
            })
        return entries

    # ---- 单 feed(独立 try/except) ----
    def parse_feed(self, feed_config):
        url = feed_config['url']
        name = feed_config.get('name', url)
        if 'huggingface.co/api/daily_papers' in url:
            # 逐字复用原实现:JSON API + 双排名 + 只留 GitHub 仓库论文
            return self._fetch_hf_daily_papers(feed_config)
        try:
            content = self._fetch_with_truncation(url, self.timeout)
            entries = self._build_entries(content, feed_config)
            logging.info(f'[RSS] {name}: {len(entries)} 条')
            return {'entries': entries, 'error': None, 'feed_config': feed_config}
        except Exception as e:
            logging.error(f'[RSS] {name} 失败: {e}')
            return {'entries': [], 'error': 'rss_fetch_error',
                    'error_detail': str(e), 'feed_config': feed_config}


def fetch_rss_and_hf(cfg):
    """抓取全部 RSS 博客 + HF Daily Papers。

    返回 (results, feed_failures):
      results: {feed_name: [entry,...]}
      feed_failures: {feed_name: error}
    entry 附带 _source_url / _source_name 供 sources 表 upsert。
    """
    feeds = []
    for i, s in enumerate(cfg['rss']['sources']):
        feeds.append({'url': s['url'], 'name': s['name'], 'id': i + 1,
                      'description': s['name'], 'category': s.get('category', 'news')})
    # HF Daily Papers 作为一个"feed"交给原 RSSFetcher 路由
    hf_idx = len(feeds) + 1
    feeds.append({'url': cfg['hf_papers']['url'], 'name': cfg['hf_papers']['name'],
                  'id': hf_idx, 'description': cfg['hf_papers']['name'], 'category': 'papers'})

    fetcher = MVPFeedFetcher(
        feeds,
        feed_max_entries=cfg['rss']['feed_max_entries'],
        feed_max_bytes=cfg['rss']['feed_max_bytes'],
    )
    results, failures = {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fetcher.parse_feed, fc): fc for fc in feeds}
        for fut in concurrent.futures.as_completed(futs):
            fc = futs[fut]
            try:
                res = fut.result()
                if res.get('error'):
                    detail = res.get('error_detail') or ''
                    failures[fc['name']] = f"{res['error']}" + (f": {detail}" if detail else "")
                    logging.warning(f"[RSS] {fc['name']} 失败: {res['error']}")
                    continue
                entries = []
                for e in res.get('entries', []):
                    # 原 _fetch_hf_daily_papers 用 link 键,统一为 url
                    if not e.get('url') and e.get('link'):
                        e['url'] = e['link']
                    e['_source_url'] = fc['url']
                    e['_source_name'] = fc['name']
                    e['category'] = fc.get('category', 'news')
                    e['platform'] = 'hf_papers' if fc['category'] == 'papers' else 'rss'
                    entries.append(e)
                results[fc['name']] = entries
            except Exception as exc:
                failures[fc['name']] = str(exc)
                logging.error(f"[RSS] {fc['name']} 异常: {exc}")
    fetcher.close()
    logging.info(f'[RSS/HF] 完成: {len(results)} feed 成功,{len(failures)} 失败')
    return results, failures
