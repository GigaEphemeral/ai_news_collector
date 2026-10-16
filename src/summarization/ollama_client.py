# -*- coding: utf-8 -*-
"""Ollama 客户端(阶段04 拆分,阶段02 按领域归入 summarization)。

只负责:输入精简(build_compact)+ 对局域网 Ollama `/api/chat` 的单次调用
(call_ollama,think=false + format=json + 给足 num_predict)。不包含业务判断,
业务逻辑在 news_summarizer / solution_summarizer。
"""
import json

import requests

COMPACT_KEYS = ('name', 'description', 'stars', 'stars_24h', 'created_days', 'topics')


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


def call_ollama(messages, cfg, num_predict=2048):
    """调用 Ollama /api/chat;单次尝试,失败抛异常(由上层降级)。

    messages: 完整消息列表(含 system 提示与用户输入)。
    请求体:think=false(关思考)+ options.num_predict 给足生成预算 + format=json。
    返回模型输出正文(字符串);空正文抛 ValueError。
    """
    oc = cfg.get('ollama', {})
    base = oc.get('base_url', 'http://192.168.0.103:11434')
    payload = {
        'model': oc.get('model', 'Qwen3.5:9B'),
        'messages': messages,
        'stream': False,
        'think': False,                      # 关闭 thinking
        'format': 'json',
        'options': {
            'temperature': float(oc.get('temperature', 0.2)),
            'num_predict': int(oc.get('num_predict', num_predict)),  # 给足生成预算
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
