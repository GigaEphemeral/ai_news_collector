# -*- coding: utf-8 -*-
"""日志:复用原项目 log_config 的 DailyFileHandler(按日滚动,Windows 安全)。

注意:原项目 ConfigLoader 的模块级 logging.info() 会触发 Python 隐式
basicConfig()(往 root 挂 stderr handler),导致之后显式 basicConfig 静默失效。
因此这里用 force=True 强制安装我们的 handler,保证日志一定写入文件。
"""
import logging
import os
import sys

from .reuse import ensure_reuse_path

ensure_reuse_path()

from log_config import DailyFileHandler  # noqa: E402  (复用原实现)


def setup_logging(log_dir='logs', log_name='ai_news_mvp', retention_days=90):
    os.makedirs(log_dir, exist_ok=True)
    file_handler = DailyFileHandler(log_dir, log_name, retention_days)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[file_handler, logging.StreamHandler(sys.stdout)],
        force=True,  # 覆盖任何隐式 basicConfig 已挂的 handler
    )
