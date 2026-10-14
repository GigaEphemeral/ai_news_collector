# -*- coding: utf-8 -*-
"""复用原项目 ai-news-brief 的只读模块(rss_fetcher / lsh_dedup / fetch_stats / log_config)。

原项目目录: /home/czq/ai_news/code/ai-news-brief(阶段01 审计确认可复用,MIT 许可)。
本模块只把它的 src/ 挂到 sys.path,不改动原项目任何文件。
可用环境变量 AI_NEWS_BRIEF_SRC 覆盖原项目 src 路径(例如从仓库副本运行时)。
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # code/ai-news-mvp


def brief_src_path():
    """定位原项目 ai-news-brief/src 目录。"""
    env = os.environ.get('AI_NEWS_BRIEF_SRC')
    if env and os.path.isdir(env):
        return env
    # 项目根上一级是 code/,原项目在其同级
    candidates = [
        os.path.join(os.path.dirname(PROJECT_ROOT), 'ai-news-brief', 'src'),
        os.path.join(PROJECT_ROOT, '..', 'ai-news-brief', 'src'),
        os.path.join(PROJECT_ROOT, 'third_party', 'ai-news-brief', 'src'),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return os.path.abspath(c)
    return None


def ensure_reuse_path():
    """把原项目 src 挂到 sys.path;找不到时报清晰错误。"""
    p = brief_src_path()
    if p is None:
        raise RuntimeError(
            '未找到原项目 ai-news-brief/src(复用 rss_fetcher/lsh_dedup 需要)。'
            '请确认 /home/czq/ai_news/code/ai-news-brief 存在,'
            '或通过环境变量 AI_NEWS_BRIEF_SRC 指定其 src 目录。'
        )
    if p not in sys.path:
        sys.path.insert(0, p)
    return p
