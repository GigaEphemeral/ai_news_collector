# -*- coding: utf-8 -*-
"""阶段07 验收测试:agent_readme + 占位符渲染 + 端到端走查。

覆盖:
  - agent_readme.md 存在且含 10 节关键内容
  - render_draft 占位符全替换、无 {{ 残留;{{MODEL_STATUS}} 三文案
  - render_batch_with_retry:非 JSON/条数不匹配 → 重试 1 次 → 整批 fallback
  - check_draft:无残留 → OK;有残留 → FAIL
  - config.yaml ollama.agent 参数(temperature=0.1 / num_predict=512 / num_ctx=4096 / repeat_penalty=1.05)
"""
import json
import os
import tempfile

import yaml

from src.tools.check_draft import check_draft
from src.tools.render_draft import (render_batch_with_retry, render_with_fallback,
                                    STATUS_TEXT)


def _write(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


def _sample_draft_and_report():
    d = tempfile.mkdtemp(prefix='p7_')
    draft = _write(os.path.join(d, 'draft.txt'),
                   '标题\n一、今日值得关注\n{{INTRO_H1}}\n模型状态:{{MODEL_STATUS}}\n')
    report = _write(os.path.join(d, 'cand.json'), json.dumps({
        'items': [{'placeholder': '{{INTRO_H1}}', 'title': 'Repo A',
                   'fallback_intro': 'Repo A 的一句话简介'}]}))
    return d, draft, report


def test_agent_readme_exists_with_10_sections():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, 'agent_readme.md')
    assert os.path.exists(path)
    content = open(path, encoding='utf-8').read()
    # 操作手册结构:步骤 0-8 + 总览/失败处理/汇报格式/调度
    for i in range(0, 9):
        assert f'## {i}.' in content, f'缺少第 {i} 节'
    for key in ('步骤① 采集', '步骤② 生成日报', '步骤④ 发送飞书',
                '失败处理', '汇报格式', '时区与调度'):
        assert key in content, f'缺少关键节 {key}'
    # 关键命令/事实抽查
    assert './.venv/bin/python run.py --since-days 7' in content
    assert '--report' in content and '--send' in content
    assert '/home/czq/ai_news/ai_news_collector' in content
    assert 'AI_NEWS_ENV_PATH=/home/czq/ai_news/.env' in content
    assert 'FEISHU_WEBHOOK_URL' in content
    assert 'code' in content and '0' in content
    assert 'Asia/Shanghai' in content
    assert 'check_draft' in content
    assert 'render_draft' in content


def test_render_fallback_no_residue():
    d, draft, report = _sample_draft_and_report()
    out = os.path.join(d, 'out.txt')
    body, done, total = render_with_fallback(draft, report, out, status='ok')
    assert done == total == 1
    assert '{{' not in body and '}}' not in body
    assert 'Repo A 的一句话简介' in body
    assert 'Agent 渲染成功' in body
    # 三文案
    for status in ('ok', 'partial', 'failed'):
        b, _, _ = render_with_fallback(draft, report, status=status)
        assert STATUS_TEXT[status].split(':')[0].split('（')[0] in b or \
               STATUS_TEXT[status][:10] in b


def test_check_draft_ok_and_fail():
    d, draft, report = _sample_draft_and_report()
    # 未渲染 → 有残留
    ok, residue, missing = check_draft(draft, report)
    assert not ok and residue
    # 渲染后 → 无残留
    out = os.path.join(d, 'rendered.txt')
    render_with_fallback(draft, report, out, status='ok')
    ok2, res2, miss2 = check_draft(out, report)
    assert ok2 and not res2 and not miss2


def test_batch_retry_then_fallback():
    items = [
        {'placeholder': '{{INTRO_H1}}', 'fallback_intro': 'fb1'},
        {'placeholder': '{{INTRO_H2}}', 'fallback_intro': 'fb2'},
    ]
    calls = {'n': 0}

    def bad_model(its):  # 一直失败
        calls['n'] += 1
        raise ValueError('非 JSON')

    out = render_batch_with_retry(items, bad_model, max_retries=1)
    assert calls['n'] == 2  # 初试 + 重试 1 次
    assert out == {'{{INTRO_H1}}': 'fb1', '{{INTRO_H2}}': 'fb2'}  # 整批 fallback

    # 条数不匹配 → 重试 1 次 → fallback
    calls2 = {'n': 0}

    def wrong_count(its):
        calls2['n'] += 1
        return [{'placeholder': '{{INTRO_H1}}', 'intro': 'x'}]  # 少一条

    out2 = render_batch_with_retry(items, wrong_count, max_retries=1)
    assert calls2['n'] == 2
    assert out2['{{INTRO_H2}}'] == 'fb2'


def test_agent_params_in_config():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = yaml.safe_load(open(os.path.join(root, 'config.yaml'), encoding='utf-8'))
    agent = cfg['ollama']['agent']
    assert agent['temperature'] == 0.1
    assert agent['num_predict'] == 512
    assert agent['think'] is False
    assert agent['format'] == 'json'
    assert agent['num_ctx'] == 4096
    assert agent['repeat_penalty'] == 1.05
    assert agent['max_batch'] == 4
