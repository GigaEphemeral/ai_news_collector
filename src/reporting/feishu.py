# -*- coding: utf-8 -*-
"""飞书自定义机器人发送(阶段04)。

- webhook 只从环境变量 FEISHU_WEBHOOK_URL 读取(.env,定位规则见 src/core/config.py);
  **严禁打印 / 写日志 / 入库**——本模块所有错误信息都会把 webhook 脱敏为
  '<redacted>',调用方拿到的异常文本同样不含 URL。
- [CC 2026-10-16] 幂等跳过逻辑已注释:已 sent 也会重发(便于反复测试);
  需要幂等时恢复 send_report 中被注释的 sent 跳过。
  只有飞书确认成功(code=0)才置 sent。
- 明确失败(HTTP 返回非 0)→ 指数退避重试,有限次(max_retries);
  结果不确定(超时 / 响应无法解析)→ 置 uncertain 立即停止,不无限重发。
- 发送文本必须包含安全关键词(FEISHU_WEBHOOK_KEYWORD / report.required_keyword),
  缺失时拒绝发送。

与日报生成分离:send_report() 只读库中已生成的日报正文,不重新抓取。
"""
import json
import logging
import os
import time
from urllib.parse import urlparse

import requests

from .report import _keyword

log = logging.getLogger(__name__)

REDACTED = '<redacted>'


class FeishuError(Exception):
    """飞书发送失败(文本已脱敏,不含 webhook)。"""


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def load_webhook():
    """从环境变量读取 webhook;缺失抛 FeishuError(不含值)。"""
    url = (os.environ.get('FEISHU_WEBHOOK_URL') or '').strip()
    if not url:
        raise FeishuError('FEISHU_WEBHOOK_URL 未配置,拒绝发送')
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or not p.netloc:
        raise FeishuError('FEISHU_WEBHOOK_URL 不是合法 URL,拒绝发送')
    return url


def _sanitize(text, webhook):
    """把可能夹带 webhook 的异常文本脱敏(连接错误信息常含 URL)。"""
    if not text or not webhook:
        return text
    return str(text).replace(webhook, REDACTED)


def _extract_code(resp):
    """从飞书响应提取业务码;返回 (code, msg)。code None = 无法解析。"""
    try:
        data = resp.json()
    except (ValueError, TypeError):
        return None, '响应无法解析'
    if not isinstance(data, dict):
        return None, '响应不是 JSON 对象'
    code = data.get('code', data.get('StatusCode'))
    msg = data.get('msg') or data.get('statusMessage') or str(data)[:120]
    return code, str(msg)


def send_text(webhook, text, cfg):
    """发送一条文本消息(单次);失败抛 FeishuError(脱敏)。"""
    timeout = float(cfg.get('feishu', {}).get('timeout', 30))
    payload = {'msg_type': 'text', 'content': {'text': text}}
    try:
        resp = requests.post(webhook, json=payload, timeout=timeout)
    except requests.exceptions.Timeout:
        raise FeishuError('飞书请求超时,发送结果不确定') from None
    except requests.exceptions.RequestException as e:
        raise FeishuError(f'飞书请求失败({e.__class__.__name__},webhook 已脱敏)') from None

    code, msg = _extract_code(resp)
    if code is None:
        raise FeishuError(f'飞书响应无法解析,发送结果不确定(HTTP {resp.status_code})')
    if code != 0:
        raise FeishuError(f'飞书返回错误 code={code}: {_sanitize(msg, webhook)[:120]}')


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def send_report(db, report_date, cfg, webhook=None, keyword=None):
    """发送指定日期的日报(幂等)。

    返回 dict:
      {'status': 'sent'|'uncertain'|'failed',   # 幂等跳过已注释,不再返回 skipped_already_sent
       'report_date', 'attempts', 'error'(仅失败), 'sent_at'(仅成功)}

    抛 FeishuError:日报不存在 / 缺安全关键词 / webhook 未配置。
    """
    report_date = str(report_date)
    webhook = webhook or load_webhook()
    kw = keyword or _keyword(cfg)
    fc = cfg.get('feishu', {})
    max_retries = int(fc.get('max_retries', 3))
    backoff = float(fc.get('backoff_seconds', 3))

    report = db.get_daily_report(report_date)
    if report is None:
        raise FeishuError(f'{report_date} 未生成日报(请先运行 run.py --report 生成后再发送)')
    body = report['body'] or ''
    if kw not in body:
        raise FeishuError(f'日报缺少安全关键词「{kw}」,拒绝发送(webhook 校验必需)')
    # [CC 2026-10-16 要求注释] 幂等跳过:已 sent 不重发,会挡住反复测试。
    # 需要幂等时恢复下面三行。
    # if report['sent_status'] == 'sent':
    #     log.info(f'[飞书] {report_date} 日报已成功发送(sent),跳过重复发送')
    #     return {'status': 'skipped_already_sent', 'report_date': report_date,
    #             'sent_at': report['sent_at'], 'attempts': 0}

    last_err = None
    for attempt in range(max_retries):
        try:
            send_text(webhook, body, cfg)
            db.mark_report_sent(report_date)
            log.info(f'[飞书] {report_date} 日报发送成功(第 {attempt + 1} 次尝试)')
            return {'status': 'sent', 'report_date': report_date,
                    'sent_at': db.get_daily_report(report_date)['sent_at'],
                    'attempts': attempt + 1}
        except FeishuError as e:
            last_err = _sanitize(str(e), webhook)
            if '不确定' in str(e):
                # 超时/响应无法解析:结果不确定,不重试,避免重复发送
                db.mark_report_uncertain(report_date, last_err[:300])
                log.warning(f'[飞书] {report_date} 发送结果不确定,停止重发: {last_err}')
                return {'status': 'uncertain', 'report_date': report_date,
                        'error': last_err, 'attempts': attempt + 1}
            db.mark_report_failed(report_date, last_err[:300])
            if attempt < max_retries - 1:
                wait = backoff * (2 ** attempt)
                log.warning(f'[飞书] {report_date} 第 {attempt + 1} 次失败: {last_err},'
                            f' {wait}s 后重试')
                time.sleep(wait)
            else:
                log.error(f'[飞书] {report_date} 重试 {max_retries} 次后仍失败: {last_err}')

    return {'status': 'failed', 'report_date': report_date,
            'error': last_err or 'unknown', 'attempts': max_retries}
