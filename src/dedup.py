# -*- coding: utf-8 -*-
"""去重:URL 归一化 + 标题 SimHash(复用原项目 lsh_dedup 的 compute_simhash/is_similar)。"""
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .reuse import ensure_reuse_path

ensure_reuse_path()

from lsh_dedup import compute_simhash, is_similar  # noqa: E402


def normalize_url(url):
    """URL 归一化:小写 host、去 www、去默认端口、去跟踪参数(utm_*/ref/fbclid/gclid/mc_)、
    去尾斜杠。用于 URL 级去重。"""
    try:
        raw = (url or '').strip()
        if not raw:
            return ''
        if '://' not in raw:
            raw = 'https://' + raw
        parsed = urlparse(raw)
        host = (parsed.netloc or '').lower()
        if host.startswith('www.'):
            host = host[4:]
        scheme = (parsed.scheme or 'https').lower()
        if scheme == 'http' and parsed.port == 80:
            host = host.replace(':80', '')
        if scheme == 'https' and parsed.port == 443:
            host = host.replace(':443', '')
        qs = parse_qs(parsed.query, keep_blank_values=True)
        qs = {k: v for k, v in qs.items()
              if not k.lower().startswith(('utm_', 'ref', 'fbclid', 'gclid', 'mc_'))}
        query = urlencode(qs, doseq=True) if qs else ''
        path = parsed.path.rstrip('/') or '/'
        return urlunparse((scheme, host, path, parsed.params, query, ''))
    except Exception:
        return (url or '').strip().lower()


class Deduper:
    """跨源去重:URL 精确(归一化后)+ 标题 SimHash 近似。线程不安全,单线程用。"""

    def __init__(self, threshold=4):
        self.threshold = threshold
        self.seen_urls = set()
        self.seen_hashes = []
        self.url_dups = 0
        self.simhash_dups = 0
        self.last_hash = 0

    def accept(self, url, title):
        """返回 True 表示该条目首次出现(可通过);否则计数并返回 False。"""
        norm = normalize_url(url)
        if not norm or norm in self.seen_urls:
            self.url_dups += 1
            return False
        th = compute_simhash(title or '')
        for h in self.seen_hashes:
            if is_similar(th, h, threshold=self.threshold):
                self.simhash_dups += 1
                return False
        self.seen_urls.add(norm)
        if th:
            self.seen_hashes.append(th)
        self.last_hash = th
        return True
