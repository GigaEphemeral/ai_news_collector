# -*- coding: utf-8 -*-
"""「今日值得关注」摘要(阶段04,可选;阶段02 从 summarize.py 拆出)。

只对入选 Top N(默认 8)调用局域网 Ollama `Qwen3.5:9B`(github-trend-reference 第五节):
  - 输入精简 JSON(build_compact,见 ollama_client):name / description / stars /
    stars_24h / created_days / topics,不传 API 原始 JSON(控制 Token 成本);
  - 关闭 thinking(think=false)+ 给足生成预算(num_predict),输出固定 JSON 数组
    (该模型默认开 thinking,短预算会输出空正文);
  - 输出 3-5 条,经代码校验(项目名与输入匹配 / URL 合法且来自输入 / 简介与依据非空),
    校验失败的条目丢弃;
  - 超时 / 服务不可达 / 非法输出 → 降级:原标题 + 规则标签生成"今日值得关注",
    不阻塞日报、不反复重试(单次调用)。

降级由 summarize_or_fallback() 统一完成:返回 (highlights, meta)。
"""
import json
import logging
import time
from urllib.parse import urlparse

from .ollama_client import build_compact, call_ollama
from ..reporting.report import rule_highlights  # 降级路径:原标题 + 规则标签

log = logging.getLogger(__name__)


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


def call_news_ollama(compact, cfg):
    """新闻摘要专用:输入精简 → 组装消息 → 调 Ollama(单次,失败抛异常)。"""
    messages = [
        {'role': 'system', 'content': '只输出 JSON,不要输出其他内容。'},
        {'role': 'user', 'content': f'{_prompt()}\n输入数据:\n{json.dumps(compact, ensure_ascii=False)}'},
    ]
    return call_ollama(messages, cfg, num_predict=int(cfg.get('ollama', {}).get('num_predict', 2048)))


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
    import requests
    oc = cfg.get('ollama', {})
    model = oc.get('model', 'Qwen3.5:9B')
    compact = build_compact(items)
    t0 = time.time()

    def meta(mode, degraded, status, detail=None):
        return {'mode': mode, 'degraded': degraded, 'status': status,
                'detail': detail, 'model': model, 'items_used': len(compact),
                'time_s': round(time.time() - t0, 1)}

    try:
        content = call_news_ollama(compact, cfg)
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
