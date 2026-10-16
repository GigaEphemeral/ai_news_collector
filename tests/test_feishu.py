# -*- coding: utf-8 -*-
"""飞书发送测试(阶段04):幂等/成功/退避重试/结果不确定停止/webhook 不外泄。不依赖真实网络。"""
import os
import sys

import pytest
import requests

from src.storage.db import Database
from src.reporting.feishu import FeishuError, load_webhook, send_report

RUN_DATE = '2026-10-14'
WEBHOOK = 'https://open.feishu.cn/open-apis/bot/v2/hook/test-only'


def mini_cfg():
    return {
        'report': {'required_keyword': '每日ai情报'},
        'feishu': {'timeout': 5, 'max_retries': 3, 'backoff_seconds': 0.01},
    }


def _make_db():
    import tempfile
    d = tempfile.mkdtemp(prefix='ai_news_mvp_test_feishu_')
    return Database(os.path.join(d, 'test.sqlite3'))


def _seed_pending(db, body=None):
    db.save_daily_report(RUN_DATE, body or ('每日ai情报 日报正文\n一、今日值得关注\n(无)'))


def _seed_sent(db):
    _seed_pending(db)
    db.mark_report_sent(RUN_DATE)


class _FakeResp:
    def __init__(self, code, status=200):
        self._code = code
        self.status_code = status

    def json(self):
        return {'code': self._code, 'msg': 'ok'}


def _fake_post(responses):
    """按序返回响应的 requests.post 桩;记录调用次数。"""
    calls = {'n': 0}

    def fake(url, json=None, timeout=None):
        calls['n'] += 1
        resp = responses[min(calls['n'] - 1, len(responses) - 1)]
        if isinstance(resp, Exception):
            raise resp
        return resp

    return fake, calls


def test_load_webhook_missing_env(monkeypatch):
    monkeypatch.delenv('FEISHU_WEBHOOK_URL', raising=False)
    with pytest.raises(FeishuError, match='未配置'):
        load_webhook()


def test_load_webhook_from_env(monkeypatch):
    monkeypatch.setenv('FEISHU_WEBHOOK_URL', WEBHOOK)
    assert load_webhook() == WEBHOOK


def test_idempotent_skip_commented_so_resend_works(monkeypatch):
    """[CC 2026-10-16] 幂等跳过逻辑已注释:即使日报已 sent,再次 send_report 仍会
    重新调用 webhook(便于反复测试)。需要幂等时恢复 feishu.py 中被注释的三行。"""
    db = _make_db()
    _seed_sent(db)
    fake, calls = _fake_post([_FakeResp(0)])
    monkeypatch.setattr(requests, 'post', fake)
    monkeypatch.setenv('FEISHU_WEBHOOK_URL', WEBHOOK)
    result = send_report(db, RUN_DATE, mini_cfg(), webhook=WEBHOOK)
    assert result['status'] == 'sent'   # 注释后:已 sent 也会重发
    assert calls['n'] == 1              # webhook 被再次调用
    db.close()


def test_success_sets_sent(monkeypatch):
    db = _make_db()
    _seed_pending(db)
    fake, calls = _fake_post([_FakeResp(0)])
    monkeypatch.setattr(requests, 'post', fake)
    result = send_report(db, RUN_DATE, mini_cfg(), webhook=WEBHOOK)
    assert result['status'] == 'sent'
    assert calls['n'] == 1
    assert db.get_daily_report(RUN_DATE)['sent_status'] == 'sent'
    assert db.get_daily_report(RUN_DATE)['sent_at'] is not None
    db.close()


def test_retry_then_success(monkeypatch):
    db = _make_db()
    _seed_pending(db)
    fake, calls = _fake_post([_FakeResp(999), _FakeResp(999), _FakeResp(0)])
    monkeypatch.setattr(requests, 'post', fake)
    result = send_report(db, RUN_DATE, mini_cfg(), webhook=WEBHOOK)
    assert result['status'] == 'sent'
    assert calls['n'] == 3
    assert db.get_daily_report(RUN_DATE)['sent_status'] == 'sent'
    assert db.get_daily_report(RUN_DATE)['retry_count'] == 2  # 记录前两次失败尝试
    db.close()


def test_exhausted_failure_marks_failed(monkeypatch):
    db = _make_db()
    _seed_pending(db)
    fake, calls = _fake_post([_FakeResp(999)] * 5)
    monkeypatch.setattr(requests, 'post', fake)
    result = send_report(db, RUN_DATE, mini_cfg(), webhook=WEBHOOK)
    assert result['status'] == 'failed'
    assert calls['n'] == mini_cfg()['feishu']['max_retries']
    row = db.get_daily_report(RUN_DATE)
    assert row['sent_status'] == 'failed'
    assert row['retry_count'] == mini_cfg()['feishu']['max_retries']
    db.close()


def test_timeout_uncertain_no_retry(monkeypatch):
    db = _make_db()
    _seed_pending(db)
    fake, calls = _fake_post([requests.exceptions.Timeout('timeout')])
    monkeypatch.setattr(requests, 'post', fake)
    result = send_report(db, RUN_DATE, mini_cfg(), webhook=WEBHOOK)
    assert result['status'] == 'uncertain'
    assert calls['n'] == 1  # 结果不确定,不重试,避免重复发送
    assert db.get_daily_report(RUN_DATE)['sent_status'] == 'uncertain'
    db.close()


def test_missing_report_raises(monkeypatch):
    db = _make_db()  # 未生成日报
    monkeypatch.setenv('FEISHU_WEBHOOK_URL', WEBHOOK)
    with pytest.raises(FeishuError, match='未生成日报'):
        send_report(db, RUN_DATE, mini_cfg(), webhook=WEBHOOK)
    db.close()


def test_missing_keyword_refused(monkeypatch):
    db = _make_db()
    _seed_pending(db, body='没有关键词的日报正文')
    fake, calls = _fake_post([_FakeResp(0)])
    monkeypatch.setattr(requests, 'post', fake)
    with pytest.raises(FeishuError, match='关键词'):
        send_report(db, RUN_DATE, mini_cfg(), webhook=WEBHOOK)
    assert calls['n'] == 0
    db.close()


def test_webhook_never_leaked(caplog, monkeypatch):
    import logging
    caplog.set_level(logging.WARNING)
    db = _make_db()
    _seed_pending(db)

    fake, _ = _fake_post([requests.exceptions.ConnectionError(f'failed to reach {WEBHOOK}')])
    monkeypatch.setattr(requests, 'post', fake)
    result = send_report(db, RUN_DATE, mini_cfg(), webhook=WEBHOOK)
    assert result['status'] == 'failed'
    text = '\n'.join(r.getMessage() for r in caplog.records) + '\n' + \
           str(result.get('error', '')) + '\n' + \
           str(db.get_daily_report(RUN_DATE)['last_error'] or '')
    assert WEBHOOK not in text          # webhook 不出现在日志/错误/库中
    db.close()
