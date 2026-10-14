# -*- coding: utf-8 -*-
"""Ollama 摘要测试(阶段04):输入精简/输出校验/降级路径/请求参数。全部不依赖真实网络。"""
import json
import sys

import pytest
import requests

from conftest import trend_cfg

from src.report import collect_candidates
from src.summarize import (build_compact, call_ollama, parse_and_validate,
                           summarize_or_fallback)


def mini_cfg():
    return {
        'report': {'highlights_cap': 8, 'max_highlights': 3},
        'ollama': {
            'base_url': 'http://ollama.test:11434', 'model': 'Qwen3.5:9B',
            'enabled': True, 'timeout': 5, 'think': False, 'num_predict': 2048,
            'temperature': 0.2, 'min_output_items': 3, 'max_output_items': 5,
        },
        'trend': trend_cfg(),
        'keywords': {'ai': ['ai', 'llm']},
    }


def _items():
    return [
        {'name': 'acme/one', 'title': 'acme/one', 'url': 'https://github.com/acme/one',
         'summary': 'AI framework', 'platform': 'github', 'category': 'opensource',
         'stars': 100, 'stars_24h': 20, 'created_days': 5, 'topics': ['ai'],
         'tags': ['新出现'], 'score': 20},
        {'name': 'acme/two', 'title': 'acme/two', 'url': 'https://github.com/acme/two',
         'summary': 'RAG tool', 'platform': 'github', 'category': 'opensource',
         'stars': 200, 'stars_24h': None, 'created_days': 10, 'topics': ['rag'],
         'tags': ['持续升温'], 'score': 200},
        {'name': 'acme/three', 'title': 'acme/three', 'url': 'https://github.com/acme/three',
         'summary': 'Agent runtime', 'platform': 'github', 'category': 'opensource',
         'stars': 50, 'stars_24h': 5, 'created_days': 2, 'topics': ['agents'],
         'tags': ['新项目'], 'score': 5},
        {'name': 'acme/four', 'title': 'acme/four', 'url': 'https://github.com/acme/four',
         'summary': 'Inference engine', 'platform': 'github', 'category': 'opensource',
         'stars': 80, 'stars_24h': None, 'created_days': 20, 'topics': ['inference'],
         'tags': ['新出现'], 'score': 80},
        {'name': 'acme/five', 'title': 'acme/five', 'url': 'https://github.com/acme/five',
         'summary': 'Model serving', 'platform': 'github', 'category': 'opensource',
         'stars': 300, 'stars_24h': 90, 'created_days': 8, 'topics': ['serving'],
         'tags': ['突然增长'], 'score': 90},
    ]


def _ok_payload(n=3):
    items = [{'name': f'acme/{w}', 'url': f'https://github.com/acme/{w}',
              'intro': f'{w} 简介', 'reason': f'{w} 热度依据'}
             for w in ('one', 'two', 'three')[:n]]
    return json.dumps(items)


class _FakeResp:
    def __init__(self, content, status=200):
        self.content = content
        self.status_code = status

    def json(self):
        return {'message': {'content': self.content}}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f'HTTP {self.status_code}')


def test_build_compact_only_allowed_keys(monkeypatch):
    monkeypatch.setenv('FEISHU_WEBHOOK_KEYWORD', '每日ai情报')
    compact = build_compact(_items())
    assert len(compact) == 5
    for c in compact:
        assert set(c.keys()) == {'name', 'description', 'stars', 'stars_24h',
                                 'created_days', 'topics'}


def test_call_ollama_payload_has_think_and_budget(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured['url'] = url
        captured['json'] = json
        captured['timeout'] = timeout
        return _FakeResp(_ok_payload())

    monkeypatch.setattr(requests, 'post', fake_post)
    content = call_ollama(build_compact(_items()), mini_cfg())
    assert captured['json']['think'] is False
    assert captured['json']['format'] == 'json'
    assert captured['json']['options']['num_predict'] >= 512
    assert captured['url'].startswith('http://ollama.test:11434')
    assert json.loads(content)


def test_parse_validate_plain_array():
    items = _items()
    cleaned, errors = parse_and_validate(_ok_payload(3), items, mini_cfg())
    assert len(cleaned) == 3 and not errors
    # URL 取自输入规范化值(防模型编造链接)
    assert all(h['url'].startswith('https://github.com/acme/') for h in cleaned)


def test_parse_validate_wrapped_object():
    items = _items()
    wrapped = json.dumps({'projects': json.loads(_ok_payload(3))})
    cleaned, errors = parse_and_validate(wrapped, items, mini_cfg())
    assert len(cleaned) == 3 and not errors


def test_parse_validate_drops_fabricated_name():
    items = _items()
    data = [{'name': 'evil/fabricated', 'url': 'https://evil.example/x',
             'intro': '编造', 'reason': '编造'}]
    cleaned, errors = parse_and_validate(json.dumps(data), items, mini_cfg())
    assert cleaned == []
    assert any('未匹配输入' in e for e in errors)


def test_parse_validate_too_few_items_fails():
    items = _items()
    cleaned, errors = parse_and_validate(_ok_payload(1), items, mini_cfg())
    assert cleaned == [] and any('少于最低要求' in e for e in errors)


def test_parse_validate_invalid_json():
    items = _items()
    cleaned, errors = parse_and_validate('not json at all', items, mini_cfg())
    assert cleaned == [] and '合法 JSON' in errors[0]


def test_degraded_on_connection_error(monkeypatch):
    def boom(url, json=None, timeout=None):
        raise requests.exceptions.ConnectionError('unreachable')
    monkeypatch.setattr(requests, 'post', boom)
    hl, meta = summarize_or_fallback(_items(), mini_cfg())
    assert meta['degraded'] is True
    assert '降级' in meta['status']
    assert hl  # 降级后仍有原标题+规则标签
    for h in hl:
        assert h['url'].startswith('http') and h['reason']  # AIQI-7:上榜理由改为库中真实字段,不再用模糊话术


def test_degraded_on_timeout(monkeypatch):
    def boom(url, json=None, timeout=None):
        raise requests.exceptions.Timeout('timeout')
    monkeypatch.setattr(requests, 'post', boom)
    hl, meta = summarize_or_fallback(_items(), mini_cfg())
    assert meta['degraded'] is True and '超时' in meta['status']


def test_degraded_on_invalid_output(monkeypatch):
    monkeypatch.setattr(requests, 'post',
                        lambda url, json=None, timeout=None: _FakeResp('garbage'))
    hl, meta = summarize_or_fallback(_items(), mini_cfg())
    assert meta['degraded'] is True
    assert '未通过校验' in meta['status']


def test_success_path(monkeypatch):
    monkeypatch.setattr(requests, 'post',
                        lambda url, json=None, timeout=None: _FakeResp(_ok_payload(3)))
    hl, meta = summarize_or_fallback(_items(), mini_cfg())
    assert meta['degraded'] is False
    assert meta['mode'] == 'ollama'
    assert len(hl) == 3
    assert all(h['name'].startswith('acme/') for h in hl)
