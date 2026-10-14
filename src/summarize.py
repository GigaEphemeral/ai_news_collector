# -*- coding: utf-8 -*-
"""Ollama 批量摘要(阶段04,可选)。

只对入选 Top N(默认 8)调用局域网 Ollama `Qwen3.5:9B`(github-trend-reference 第五节):
  - 输入精简 JSON:name / description / stars / stars_24h / created_days / topics,
    不传 API 原始 JSON(控制 Token 成本);
  - 关闭 thinking(think=false)+ 给足生成预算(num_predict),输出固定 JSON 数组
    (该模型默认开 thinking,短预算会输出空正文);
  - 输出 3-5 条,经代码校验(项目名与输入匹配 / URL 合法且来自输入 / 简介与依据非空),
    校验失败的条目丢弃;
  - 超时 / 服务不可达 / 非法输出 → 降级:原标题 + 规则标签生成"今日值得关注",
    不阻塞日报、不反复重试(单次调用)。

阶段04修复(AIQI-7,问题2):新增 build_solution_resumes() —— 「解决方案与产品发布」
条目的一行精简摘要。优先级:库中已有 summary → report.fetch_summary=true 时抓正文首段
(复用原项目 content_processor)或调 Ollama 一句话摘要(仅对入选 ≤solutions_max 条,
不反复重试烧 token)→ 仍为空则注明无法产生摘要的理由(如「原文未提供摘要」)。

降级由 summarize_or_fallback() 统一完成:返回 (highlights, meta)。
"""
import json
import logging
import time
from urllib.parse import urlparse

import requests

from .report import rule_highlights  # 降级路径:原标题 + 规则标签

log = logging.getLogger(__name__)

COMPACT_KEYS = ('name', 'description', 'stars', 'stars_24h', 'created_days', 'topics')


# ---------------------------------------------------------------------------
# 输入精简
# ---------------------------------------------------------------------------
def build_compact(items):
    """候选列表 → 精简 JSON(只保留 github-trend-reference 建议的字段)。"""
    compact = []
    for it in items:
        compact.append({
            'name': it.get('name') or it.get('title', ''),
            'description': (it.get('summary') or '')[:120],
            'stars': it.get('stars'),
            'stars_24h': it.get('stars_24h'),
            'created_days': it.get('created_days'),
            'topics': (it.get('topics') or [])[:5],
        })
    return compact


# ---------------------------------------------------------------------------
# 模型调用(单次,不重试)
# ---------------------------------------------------------------------------
def _prompt():
    return (
        '你是 AI 开源项目趋势编辑。\n'
        '根据输入数据,选出最值得关注的项目。\n'
        '优先考虑 Star 增长、新颖性和 AI 实用价值。\n'
        '不要编造数据,不确定的价值判断要谨慎。\n'
        '输出 JSON 数组,每项包含:\n'
        '- "name": 项目名称(必须与输入完全一致)\n'
        '- "url": 原始链接(必须来自输入)\n'
        '- "intro": 一句话介绍(不超过 50 字)\n'
        '- "reason": 热度依据(不超过 80 字)\n'
        '最多输出 5 项;没有值得关注的项目时输出空数组 []。'
    )


def call_ollama(compact, cfg):
    """调用 Ollama /api/chat;单次尝试,失败抛异常(由上层降级)。

    请求体:think=false(关思考)+ options.num_predict 给足生成预算 + format=json。
    """
    oc = cfg.get('ollama', {})
    base = oc.get('base_url', 'http://192.168.0.103:11434')
    payload = {
        'model': oc.get('model', 'Qwen3.5:9B'),
        'messages': [
            {'role': 'system', 'content': '只输出 JSON,不要输出其他内容。'},
            {'role': 'user', 'content': f'{_prompt()}\n输入数据:\n{json.dumps(compact, ensure_ascii=False)}'},
        ],
        'stream': False,
        'think': False,                      # 关闭 thinking
        'format': 'json',
        'options': {
            'temperature': float(oc.get('temperature', 0.2)),
            'num_predict': int(oc.get('num_predict', 2048)),  # 给足生成预算
        },
    }
    r = requests.post(f'{base}/api/chat', json=payload,
                      timeout=float(oc.get('timeout', 180)))
    r.raise_for_status()
    data = r.json()
    content = (data.get('message') or {}).get('content', '')
    if not content:
        raise ValueError('模型返回空正文')
    return content


# ---------------------------------------------------------------------------
# 输出校验(URL / 项目名 / 条数)
# ---------------------------------------------------------------------------
def _find_input(item, inputs):
    """按项目名找输入候选;返回该候选或 None(防模型编造名称)。"""
    name = str(item.get('name', '')).strip()
    if not name:
        return None
    low = name.lower()
    for it in inputs:
        if str(it.get('name', '')).lower() == low:
            return it
    for it in inputs:
        n = str(it.get('name', '')).lower()
        if low and (low in n or n in low):
            return it
    return None


def _valid_url(u):
    try:
        p = urlparse(u)
        return p.scheme in ('http', 'https') and bool(p.netloc)
    except Exception:
        return False


def parse_and_validate(content, inputs, cfg):
    """解析并校验模型输出;返回 (cleaned, errors)。

    cleaned:合法条目列表(项目名匹配输入、URL 取自输入规范化值、简介/依据非空)。
    errors:拒绝原因列表;条目数低于 min_output_items 视为整体校验失败。
    """
    oc = cfg.get('ollama', {})
    min_n = int(oc.get('min_output_items', 3))
    max_n = int(oc.get('max_output_items', 5))
    errors = []

    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return [], ['输出不是合法 JSON']
    # 兼容包装:模型可能输出 {"projects": [...]} / {"items": [...]} 等
    if isinstance(data, dict):
        for key in ('projects', 'items', 'results', 'data', 'highlights', 'list'):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            return [], ['输出不是 JSON 数组']
    if not isinstance(data, list):
        return [], ['输出不是 JSON 数组']

    cleaned = []
    for i, item in enumerate(data, 1):
        if not isinstance(item, dict):
            errors.append(f'第 {i} 项不是对象')
            continue
        src = _find_input(item, inputs)
        if src is None:
            errors.append(f'第 {i} 项项目名未匹配输入: {str(item.get("name", ""))[:30]}')
            continue
        url = src.get('url') or ''
        if not _valid_url(url):
            errors.append(f'第 {i} 项 URL 非法: {url[:30]}')
            continue
        intro = str(item.get('intro', '')).strip()
        reason = str(item.get('reason', '')).strip()
        if not intro or len(intro) > 100:
            errors.append(f'第 {i} 项简介为空或超长')
            continue
        if not reason or len(reason) > 150:
            errors.append(f'第 {i} 项热度依据为空或超长')
            continue
        cleaned.append({
            'name': src['name'], 'url': url, 'intro': intro, 'reason': reason,
            'facts': '',  # 事实串由 report 层补充(库中数据)
        })
    if len(cleaned) < min_n:
        return [], errors + [f'有效条目 {len(cleaned)} 少于最低要求 {min_n}']
    return cleaned[:max_n], errors


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def summarize_or_fallback(items, cfg):
    """对候选列表调用 Ollama;任何失败 → 规则降级(原标题 + 规则标签)。

    返回 (highlights, meta):
      meta: {mode: ollama|rule, degraded: bool, status, detail, model,
             items_used, time_s}
    """
    oc = cfg.get('ollama', {})
    model = oc.get('model', 'Qwen3.5:9B')
    compact = build_compact(items)
    t0 = time.time()

    def meta(mode, degraded, status, detail=None):
        return {'mode': mode, 'degraded': degraded, 'status': status,
                'detail': detail, 'model': model, 'items_used': len(compact),
                'time_s': round(time.time() - t0, 1)}

    try:
        content = call_ollama(compact, cfg)
    except requests.exceptions.Timeout:
        log.warning('[Ollama] 调用超时,降级为规则标签')
        return rule_highlights(items, cfg), meta('rule', True, 'Ollama 超时,降级为规则标签', 'timeout')
    except requests.exceptions.ConnectionError:
        log.warning('[Ollama] 服务不可达,降级为规则标签')
        return rule_highlights(items, cfg), meta('rule', True, 'Ollama 不可达,降级为规则标签', 'unreachable')
    except requests.exceptions.RequestException as e:
        log.warning(f'[Ollama] 请求失败,降级为规则标签: {e.__class__.__name__}')
        return rule_highlights(items, cfg), meta('rule', True, 'Ollama 请求失败,降级为规则标签', e.__class__.__name__)
    except Exception as e:
        log.warning(f'[Ollama] 调用异常,降级为规则标签: {e}')
        return rule_highlights(items, cfg), meta('rule', True, 'Ollama 异常,降级为规则标签', str(e)[:80])

    cleaned, errors = parse_and_validate(content, items, cfg)
    if not cleaned:
        log.warning(f'[Ollama] 输出未通过校验({"; ".join(errors[:3])}),降级为规则标签')
        return rule_highlights(items, cfg), meta('rule', True, 'Ollama 输出未通过校验,降级为规则标签',
                                                 '; '.join(errors[:2]))
    log.info(f'[Ollama] 摘要成功:{len(cleaned)}/{len(compact)} 条'
             f'({"拒绝:" + ";".join(errors[:2]) if errors else ""})')
    return cleaned, meta('ollama', False, f'Ollama 摘要成功({len(cleaned)} 条)')


# ---------------------------------------------------------------------------
# 阶段04修复(AIQI-7,问题2):解决方案与产品发布 一行精简摘要
# ---------------------------------------------------------------------------
def _clamp_summary(s, n):
    s = (s or '').strip().replace('\n', ' ')
    return s if len(s) <= n else s[:n] + '…'


def _fetch_first_paragraph(url, cfg):
    """抓取正文首段(复用原项目 content_processor);失败返回 None。

    只对入选条目执行,单次尝试,不反复重试烧时间/token。
    """
    try:
        from .reuse import ensure_reuse_path
        ensure_reuse_path()
        from content_processor import ContentProcessor
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
    oc = cfg.get('ollama', {})
    base = oc.get('base_url', 'http://192.168.0.103:11434')
    payload_items = [{'url': it['url'], 'title': it['title']} for it in items]
    payload = {
        'model': oc.get('model', 'Qwen3.5:9B'),
        'messages': [
            {'role': 'system', 'content': '只输出 JSON,不要输出其他内容。'},
            {'role': 'user', 'content': _solution_summary_prompt(payload_items)},
        ],
        'stream': False,
        'think': False,
        'format': 'json',
        'options': {
            'temperature': float(oc.get('temperature', 0.2)),
            'num_predict': int(oc.get('num_predict', 1024)),
        },
    }
    r = requests.post(f'{base}/api/chat', json=payload,
                      timeout=float(oc.get('timeout', 180)))
    r.raise_for_status()
    data = r.json()
    content = (data.get('message') or {}).get('content', '')
    if not content:
        raise ValueError('模型返回空正文')
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
