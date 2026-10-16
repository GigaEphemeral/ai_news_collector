# -*- coding: utf-8 -*-
"""「解决方案与产品发布」一行精简摘要(阶段04修复 AIQI-7,问题2;阶段02 拆分)。

优先级:库中已有 summary → report.fetch_summary=true 时抓正文首段
(复用 vendored content_processor)或调 Ollama 一句话摘要(仅对入选 ≤solutions_max 条,
不反复重试烧 token)→ 仍为空则注明无法产生摘要的理由(如「原文未提供摘要」)。
"""
import json
import logging

from .ollama_client import call_ollama
from ..core.vendor.content_processor import ContentProcessor

log = logging.getLogger(__name__)


def _clamp_summary(s, n):
    s = (s or '').strip().replace('\n', ' ')
    return s if len(s) <= n else s[:n] + '…'


def _fetch_first_paragraph(url, cfg):
    """抓取正文首段(复用 vendored content_processor);失败返回 None。

    只对入选条目执行,单次尝试,不反复重试烧时间/token。
    """
    try:
        timeout = float(cfg.get('fetching', {}).get('network_timeout', 90))
        text = ContentProcessor.fetch_and_extract(url, timeout=timeout, max_retries=1)
        if not text:
            return None
        # 取第一段(按空行切分,取最长段更稳定)
        paras = [p.strip() for p in text.split('\n\n') if p.strip()]
        if not paras:
            paras = [p.strip() for p in text.split('\n') if p.strip()]
        if not paras:
            return None
        return max(paras, key=len)
    except Exception as e:
        log.warning(f'[摘要] 正文首段抓取失败 {url}: {e}')
        return None


def _solution_summary_prompt(items):
    return (
        '为以下每条内容生成一句 ≤80 字的中文精简摘要(resume),让人不看链接就懂内容。\n'
        '不要编造内容,内容不明确时如实写「原文未提供详细信息」。\n'
        '输出 JSON 数组,每项包含:\n'
        '- "url": 必须与输入完全一致\n'
        '- "summary": 一句话精简摘要(≤80 字)\n'
        f'输入:\n{json.dumps(items, ensure_ascii=False)}'
    )


def _ollama_solution_summaries(items, cfg):
    """对入选条目调 Ollama 生成一句话摘要(单次批量,不重试)。

    返回 {url: summary};任何失败抛异常,由调用方回退并注明理由。
    """
    payload_items = [{'url': it['url'], 'title': it['title']} for it in items]
    messages = [
        {'role': 'system', 'content': '只输出 JSON,不要输出其他内容。'},
        {'role': 'user', 'content': _solution_summary_prompt(payload_items)},
    ]
    content = call_ollama(messages, cfg, num_predict=int(
        cfg.get('ollama', {}).get('num_predict', 1024)))
    parsed = json.loads(content)
    if isinstance(parsed, dict):
        for key in ('items', 'results', 'data', 'list', 'summaries'):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    result = {}
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get('url', '')).strip()
        summary = str(item.get('summary', '')).strip()
        if url and summary:
            result[url] = summary
    return result


def build_solution_resumes(cands, cfg):
    """入选「解决方案与产品发布」条目的精简摘要(问题2修复,成员修正版)。

    摘要来源优先级:
      1. 库中已有 summary(截断到 report.summary_max_chars);
      2. 为空且 report.fetch_summary=true → 抓正文首段(复用 content_processor)
         或调 Ollama 一句话摘要(仅对入选 ≤solutions_max 条,单次尝试不重试);
      3. 仍为空 → 注明无法产生摘要的理由(如「原文未提供摘要」),不显示空白。

    report.fetch_summary 默认 false:只显示库中已有 summary,为空注明理由
    (成员自己用不开启;开源给他人可开启,README 已说明)。

    返回 {url: {'summary': str, 'reason': str}}。
    """
    cfg_r = cfg.get('report', {})
    max_chars = int(cfg_r.get('summary_max_chars', 80))
    fetch_summary = bool(cfg_r.get('fetch_summary', False))
    out = {}
    missing = []  # summary 为空且需抓取/生成的条目
    for c in cands:
        s = (c.get('summary') or '').strip()
        if s:
            out[c['url']] = {'summary': _clamp_summary(s, max_chars), 'reason': ''}
        elif fetch_summary:
            missing.append(c)
            out[c['url']] = {'summary': '', 'reason': ''}
        else:
            out[c['url']] = {'summary': '', 'reason': '原文未提供摘要(fetch_summary 未开启,可配置开启)'}

    # 只对缺失摘要的入选条目执行,且仅当 fetch_summary=true(默认 false 不额外烧 token)
    if missing:
        # 1) 抓正文首段(复用 content_processor;单次尝试)
        still_missing = []
        for c in missing:
            para = _fetch_first_paragraph(c['url'], cfg)
            if para:
                out[c['url']] = {'summary': _clamp_summary(para, max_chars), 'reason': '正文首段'}
            else:
                still_missing.append(c)
        # 2) 调 Ollama 一句话摘要(单次批量;失败/不可用则注明理由)
        if still_missing and cfg.get('ollama', {}).get('enabled', True):
            try:
                generated = _ollama_solution_summaries(still_missing, cfg)
                for c in still_missing:
                    if generated.get(c['url']):
                        out[c['url']] = {'summary': _clamp_summary(generated[c['url']], max_chars),
                                         'reason': 'Ollama 生成'}
            except Exception as e:
                log.warning(f'[摘要] Ollama 解决方案摘要失败: {e.__class__.__name__}: {str(e)[:120]}')
        for c in still_missing:
            if not out[c['url']]['summary']:
                out[c['url']]['reason'] = '原文未提供摘要,正文抓取与模型生成均失败'
    return out
