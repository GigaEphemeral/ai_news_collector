# -*- coding: utf-8 -*-
"""去重:URL 归一化 + 标题 SimHash + 标题/摘要相似度(阶段04修复 AIQI-7)。

问题3 修复(成员修正版:规则筛选 → 去重 → 限量,并增加相似度去重):
  - URL 归一化(原有)
  - 标题 SimHash 汉明距离阈值(原有)
  - 标题或摘要 token 集合 Jaccard 相似度 ≥ dedup.similarity_threshold(默认 0.8,八成)
    → 判为重复(新增;阈值可配置,README 已注明)

SimHash 实现来自 vendored lsh_dedup(MIT, © frankzch);阶段02 起包内相对 import。
"""
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from ..core.vendor.lsh_dedup import compute_simhash, is_similar

_CJK_RE = re.compile(r'[\u4e00-\u9fff]')
_WORD_RE = re.compile(r'[a-z0-9]+')
_TITLE_CLEAN_RE = re.compile(r'[^\w\u4e00-\u9fff]+', re.UNICODE)


def normalize_title(title):
    """标题归一化:小写、去标点、压缩空白(用于内容指纹 canonical_key)。"""
    t = (title or '').lower()
    t = _TITLE_CLEAN_RE.sub(' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def canonical_key(title):
    """内容指纹:归一化标题的 SimHash(阶段03 跨源模糊合并用)。"""
    return compute_simhash(normalize_title(title))


def _tokens(text):
    """标题/摘要 → token 集合:英文按词,中文按字符二元组。

    用于 Jaccard 相似度去重(英文标题、中文标题、摘要都能稳定比较)。
    """
    text = (text or '').lower()
    words = set(_WORD_RE.findall(text))
    cjk = _CJK_RE.findall(text)
    bigrams = {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    return words | bigrams


def jaccard_similarity(a, b):
    """两 token 集合的 Jaccard 相似度;任一为空返回 0.0。"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


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
    """跨源去重:URL 精确(归一化后)+ 标题 SimHash 近似 + 标题/摘要相似度。

    相似度阈值 similarity_threshold(默认 0.8):标题或摘要的 token 集合
    Jaccard 相似度 ≥ 阈值 → 判为重复。设 ≤ 0 关闭。线程不安全,单线程用。
    """

    def __init__(self, threshold=4, similarity_threshold=0.8):
        self.threshold = threshold
        self.similarity_threshold = similarity_threshold
        self.seen_urls = set()
        self.seen_hashes = []
        self.seen_titles = []    # token 集合
        self.seen_summaries = []  # token 集合
        self.url_dups = 0
        self.simhash_dups = 0
        self.similarity_dups = 0
        self.last_hash = 0

    def accept(self, url, title, summary=''):
        """返回 True 表示该条目首次出现(可通过);否则计数并返回 False。

        顺序:URL → SimHash → 相似度(标题或摘要)。summary 为空时跳过摘要相似度。
        """
        norm = normalize_url(url)
        if not norm or norm in self.seen_urls:
            self.url_dups += 1
            return False
        th = compute_simhash(title or '')
        for h in self.seen_hashes:
            if is_similar(th, h, threshold=self.threshold):
                self.simhash_dups += 1
                return False
        if self.similarity_threshold and self.similarity_threshold > 0:
            t_tok = _tokens(title)
            s_tok = _tokens(summary)
            for i, seen_t in enumerate(self.seen_titles):
                if jaccard_similarity(t_tok, seen_t) >= self.similarity_threshold:
                    self.similarity_dups += 1
                    return False
                if s_tok and self.seen_summaries[i] and \
                        jaccard_similarity(s_tok, self.seen_summaries[i]) >= self.similarity_threshold:
                    self.similarity_dups += 1
                    return False
            self.seen_titles.append(t_tok)
            self.seen_summaries.append(s_tok)
        self.seen_urls.add(norm)
        if th:
            self.seen_hashes.append(th)
        self.last_hash = th
        return True
