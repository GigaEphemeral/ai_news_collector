# -*- coding: utf-8 -*-
"""阶段05 日志稳定性:DailyFileHandler 按日滚动(文件名带日期,不 rename,Windows 安全)
且超过 retention_days 的旧日志会被清理。复用原项目 log_config(只读)。
"""
import glob
import logging
import os
from datetime import datetime as _real_datetime

from src.log import setup_logging


class _Clock:
    """可控时钟:替换 log_config.datetime(datetime 类)的 now()。"""

    def __init__(self, now):
        self._now = now

    def now(self, tz=None):
        return self._now(tz)

    def __getattr__(self, name):
        return getattr(_real_datetime, name)


def test_daily_log_rotates_on_date_change(monkeypatch, tmp_path):
    log_dir = str(tmp_path / 'logs')
    setup_logging(log_dir=log_dir, log_name='mvp_test', retention_days=90)
    log = logging.getLogger('mvp_test_rotation')

    # 第一天:写入
    log.info('day one message')
    files_day1 = glob.glob(os.path.join(log_dir, 'mvp_test_*.log'))
    assert len(files_day1) == 1
    day1 = os.path.basename(files_day1[0])
    assert day1.startswith('mvp_test_')
    assert 'day one message' in open(files_day1[0], encoding='utf-8').read()

    # 拨到第二天:同一 logger 继续写,应落到新文件(按日滚动,不 rename)
    import log_config
    fake = _Clock(lambda tz=None: _real_datetime(2026, 10, 15, 8, 0, 0))
    monkeypatch.setattr(log_config, 'datetime', fake)
    log.info('day two message')
    files_day2 = sorted(glob.glob(os.path.join(log_dir, 'mvp_test_*.log')))
    assert len(files_day2) == 2
    day2_path = os.path.join(log_dir, 'mvp_test_2026-10-15.log')
    assert day2_path in files_day2
    assert 'day two message' in open(day2_path, encoding='utf-8').read()
    assert 'day two message' not in open(files_day1[0], encoding='utf-8').read()
    # 第一天文件仍在(未到保留期)
    assert os.path.exists(files_day1[0])


def test_retention_cleanup_removes_old_logs(monkeypatch, tmp_path):
    import log_config
    log_dir = str(tmp_path / 'logs')
    os.makedirs(log_dir, exist_ok=True)
    # 预置一个 100 天前的旧日志与一个近期的日志
    old = os.path.join(log_dir, 'mvp_test_2026-07-01.log')
    recent = os.path.join(log_dir, 'mvp_test_2026-10-14.log')
    with open(old, 'w', encoding='utf-8') as f:
        f.write('old\n')
    with open(recent, 'w', encoding='utf-8') as f:
        f.write('recent\n')

    handler = log_config.DailyFileHandler(log_dir, 'mvp_test', retention_days=90)
    # 直接触发清理(模拟日期切换时的 _cleanup_old_logs)
    handler._cleanup_old_logs()
    assert not os.path.exists(old), '超过保留期的旧日志应被删除'
    assert os.path.exists(recent), '保留期内的日志不应被删除'
    handler.close()
