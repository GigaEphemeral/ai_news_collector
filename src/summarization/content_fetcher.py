# -*- coding: utf-8 -*-
"""候选内容抓取(阶段06 [04]):并发抓详情,供 candidates.json / draft 使用。

策略:
  - 并发 workers=4,单条超时 timeout_s(默认 15s),整体不做超过预算的等待;
  - github → README(API raw,不 clone;回退 raw.githubusercontent.com HEAD/main/master,
    再回退 description+topics+language 并标 fallback);按 content_hash 缓存 7 天不重抓;
  - rss → content_processor 正文首段 → 回退 summary(截 800);
  - hf  → summary(截 500);
  - hn  → 讨论文字(text)→ 回退标题(截 500)。

返回 {url: {'content': str, 'content_source': str, 'fallback_intro': str}}。
所有网络失败都不抛异常,回退到库中已有摘要/标题,保证主流程不中断。
"""
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from ..core.vendor.content_processor import ContentProcessor

log = logging.getLogger(__name__)

GITHUB_README_URL = 'https://api.github.com/repos/{owner}/{repo}/readme'
GITHUB_RAW_URL = 'https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md'
GITHUB_RAW_BRANCHES = ('HEAD', 'main', 'master')


def _cache_path(cfg):
    data_dir = cfg.get('_data_dir') or os.path.join(cfg.get('_project_root', ''), 'data')
    return os.path.join(data_dir, 'content_cache.json')


def _load_cache(cfg):
    path = _cache_path(cfg)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(cfg, cache):
    path = _cache_path(cfg)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError as e:
        log.warning(f'[详情] 缓存写入失败: {e}')


def _cached(key, cfg, ttl_days=7):
    """读 7 天内的内容缓存;命中返回 (content, source),未命中返回 None。"""
    cache = _load_cache(cfg)
    ent = cache.get(str(key))
    if not ent:
        return None
    fetched = ent.get('fetched_at', '')
    if fetched:
        try:
            from datetime import datetime, timezone
            ts = datetime.fromisoformat(fetched)
            if (datetime.now(timezone.utc) - ts).days >= ttl_days:
                return None
        except (ValueError, TypeError):
            pass
    return ent.get('content'), ent.get('source')


def _cache_put(key, content, source, cfg):
    cache = _load_cache(cfg)
    cache[str(key)] = {
        'content': content, 'source': source,
        'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime()),
    }
    _save_cache(cfg, cache)


# ---------------------------------------------------------------------------
# GitHub README(不 clone)
# ---------------------------------------------------------------------------
def fetch_github_readme(owner, repo, cfg, timeout=15):
    """抓 GitHub README 纯文本;不 clone。

    顺序:API /repos/{o}/{r}/readme + Accept raw → raw.githubusercontent.com
    HEAD/main/master → 返回 None(由调用方回退 description+topics)。
    """
    headers = {'User-Agent': 'ai-news-mvp-stage06'}
    gh_pat = os.environ.get('GITHUB_PAT', '')
    if gh_pat:
        headers['Authorization'] = f'Bearer {gh_pat}'
    try:
        r = requests.get(GITHUB_README_URL.format(owner=owner, repo=repo),
                         headers={**headers, 'Accept': 'application/vnd.github.raw'},
                         timeout=timeout)
        if r.status_code == 200 and r.text.strip():
            return r.text
    except requests.exceptions.RequestException as e:
        log.debug(f'[详情] GitHub API readme 失败 {owner}/{repo}: {e.__class__.__name__}')
    for branch in GITHUB_RAW_BRANCHES:
        try:
            r = requests.get(GITHUB_RAW_URL.format(owner=owner, repo=repo, branch=branch),
                             headers=headers, timeout=timeout)
            if r.status_code == 200 and r.text.strip():
                return r.text
        except requests.exceptions.RequestException:
            continue
    return None


def _github_content(cand, cfg):
    """GitHub 候选详情:README(截 800)→ 兜底 description+topics+language(fallback)。"""
    url = cand.get('url') or ''
    parts = [p for p in url.split('/') if p]
    if len(parts) < 2:
        return cand.get('summary', ''), '库中 summary', cand.get('summary', '')
    owner, repo = parts[-2], parts[-1]
    content_hash = cand.get('content_hash')
    if content_hash:
        hit = _cached(content_hash, cfg)
        if hit:
            return hit[0], hit[1], ''
    readme = None
    try:
        readme = fetch_github_readme(owner, repo, cfg)
    except Exception as e:
        log.debug(f'[详情] GitHub readme 异常 {owner}/{repo}: {e}')
    if readme:
        text = readme[:800]
        if content_hash:
            _cache_put(content_hash, text, 'GitHub README', cfg)
        return text, 'GitHub README', ''
    # 兜底:description + topics + language(标 fallback)
    ex = cand.get('extra') or {}
    bits = [cand.get('summary') or ex.get('description') or '']
    topics = ex.get('topics') or []
    if topics:
        bits.append('topics: ' + ', '.join(topics[:5]))
    if ex.get('language'):
        bits.append('language: ' + str(ex['language']))
    text = ' '.join(b for b in bits if b)[:800] or (cand.get('title') or '')
    return text, 'fallback: description+topics', text


def _rss_content(cand, cfg, timeout=15):
    """RSS 候选详情:content_processor 正文首段 → 回退 summary(截 800)。"""
    url = cand.get('url') or ''
    try:
        text = ContentProcessor.fetch_and_extract(url, timeout=timeout, max_retries=1)
        if text:
            paras = [p.strip() for p in text.split('\n\n') if p.strip()]
            if not paras:
                paras = [p.strip() for p in text.split('\n') if p.strip()]
            if paras:
                return max(paras, key=len)[:800], 'content_processor 首段', ''
    except Exception as e:
        log.debug(f'[详情] RSS 首段抓取失败 {url}: {e}')
    s = (cand.get('summary') or cand.get('title') or '')[:800]
    return s, '回退 summary', s


def _hf_content(cand):
    s = (cand.get('summary') or cand.get('title') or '')[:500]
    return s, '库中 summary', s


def _hn_content(cand):
    ex = cand.get('extra') or {}
    text = (ex.get('text') or cand.get('summary') or '')[:500]
    if not text.strip():
        text = (cand.get('title') or '')[:500]
        return text, '回退标题', text
    return text, '讨论文字', text


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def fetch_contents(candidates, cfg, workers=4, timeout=15):
    """并发抓候选详情。返回 {url: {content, content_source, fallback_intro}}。

    单条超时 timeout 秒;整体最多 workers 波次,不额外等待。任何失败回退,
    不抛异常。
    """
    results = {}

    def _one(cand):
        platform = cand.get('platform')
        try:
            if platform == 'github':
                c, src, fb = _github_content(cand, cfg)
            elif platform == 'rss':
                c, src, fb = _rss_content(cand, cfg, timeout)
            elif platform == 'hf_papers':
                c, src, fb = _hf_content(cand)
            elif platform == 'hackernews':
                c, src, fb = _hn_content(cand)
            else:
                c, src, fb = (cand.get('summary') or cand.get('title') or ''), '库中 summary', ''
            return cand.get('url'), {'content': c, 'content_source': src,
                                     'fallback_intro': fb or (cand.get('summary') or '')[:200]}
        except Exception as e:
            log.warning(f'[详情] 抓取异常 {cand.get("url")}: {e}')
            return cand.get('url'), {'content': (cand.get('summary') or cand.get('title') or ''),
                                     'content_source': '库中 summary',
                                     'fallback_intro': (cand.get('summary') or '')[:200]}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, c): c for c in candidates}
        for fut in as_completed(futs):
            url, data = fut.result()
            results[url] = data
    return results
