# -*- coding: utf-8 -*-
"""阶段05 稳定性集成测试:run.py 在断网/单源失败/Ollama 不可达/飞书不可达时均不崩溃,
错误按来源记录进 runs,sources.last_error 准确,退出码与状态如实反映,日志按日滚动。

测试策略:全部不依赖真实网络——要么用桩替代采集器返回确定性数据,
要么把 requests / curl_cffi / httpx 的网络入口打成连接失败,驱动真实采集器代码
(含重试/退避/降级)走完整 run.py 主流程。每个测试用独立的临时 config/data/log。
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import requests
import yaml

import run as run_module
from conftest import trend_cfg
from src.github import GitHubCollector
from src.hackernews import HackerNewsCollector

RUN_DATE = datetime.now(timezone.utc).strftime('%Y-%m-%d')


# ---------------------------------------------------------------------------
# 测试配置:基于真实 config.yaml,路径指到临时目录,重试/退避/超时缩小
# ---------------------------------------------------------------------------
def make_cfg(tmp_path, *, feeds=False):
    cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'config.yaml'), 'r', encoding='utf-8'))
    cfg['paths']['data_dir'] = str(tmp_path / 'data')
    cfg['paths']['log_dir'] = str(tmp_path / 'logs')
    cfg['paths']['db_file'] = str(tmp_path / 'data' / 'test.sqlite3')
    # 缩小重试/退避/超时,让失败路径快速且确定
    cfg['github']['retries'] = 1
    cfg['github']['backoff_seconds'] = 0.01
    cfg['github']['request_interval_seconds'] = 0.0
    cfg['hackernews']['retries'] = 1
    cfg['hackernews']['backoff_seconds'] = 0.01
    cfg['rss']['timeout'] = 5
    cfg['feishu']['max_retries'] = 2
    cfg['feishu']['backoff_seconds'] = 0.01
    cfg['ollama']['timeout'] = 5
    cfg['trend'] = trend_cfg()
    if not feeds:
        cfg['rss']['sources'] = []  # 桩模式下不需要真实 feed 列表
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                    encoding='utf-8')
    return str(path)


def _argv(cfg_path, *args):
    """构建 argv,强制带 --config 隔离到临时配置(绝不碰真实 config/DB)。"""
    return ['run.py', '--config', cfg_path] + list(args)


def _run(monkeypatch, argv):
    """进程内调用 run.main();成功(无 SystemExit)返回 0,失败返回 SystemExit 码。

    任何未捕获异常会原样抛出——这正是"不崩溃"的断言手段。
    """
    code = 0
    monkeypatch.setattr(sys, 'argv', argv)
    try:
        run_module.main()
    except SystemExit as e:
        code = e.code if e.code is not None else 0
    return code


# ---------------------------------------------------------------------------
# 确定性桩数据(模拟各来源成功)
# ---------------------------------------------------------------------------
def _stub_gh_fetch(self, since_days):
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    return [
        {'title': 'acme/agentkit', 'url': 'https://github.com/acme/agentkit',
         'summary': 'AI agent framework for llm serving', 'published_at': '2026-10-10T00:00:00+00:00',
         'platform': 'github', 'category': 'opensource', '_source_url': 'github:search',
         '_source_name': 'GitHub Search API',
         'extra': {'full_name': 'acme/agentkit', 'html_url': 'https://github.com/acme/agentkit',
                   'stargazers_count': 1000, 'forks_count': 100,
                   'created_at': '2026-09-01T00:00:00Z', 'pushed_at': '2026-10-14T00:00:00Z',
                   'description': 'AI agent framework', 'language': 'Python',
                   'topics': ['ai', 'agents'], 'fetched_at': now}},
    ], {'raw_items': 1, 'queries_done': 3, 'queries_failed': 0,
        'rate_limited': False, 'cap': 10, 'per_query': []}


def _stub_hn_fetch(self):
    now = datetime.now(timezone.utc)
    entries = []
    for i, (title, pts, age_h) in enumerate([
            ('New LLM agent framework released', 200, 5),
            ('Gemini 4 benchmark results published', 150, 6)]):
        entries.append({
            'title': title,
            'url': f'https://news.ycombinator.com/item?id={i + 100}',
            'summary': 'discussion about llm and agents',
            'published_at': (now - timedelta(hours=age_h)).isoformat(timespec='seconds'),
            'platform': 'hackernews', 'category': 'discussion',
            '_source_url': 'hackernews:topstories', '_source_name': 'Hacker News',
            'extra': {'points': pts, 'comments': 10, 'hn_id': i + 100},
        })
    return entries, {'fetched_ids': 2, 'failed_items': 0}


def _stub_rss_hf(cfg):
    now = datetime.now(timezone.utc)
    rss_entries = [{
        'title': 'OpenAI releases new reasoning model', 'url': 'https://openai.com/blog/x',
        'summary': 'a new llm reasoning model for agentic coding',
        'published_at': (now - timedelta(hours=3)).isoformat(timespec='seconds'),
        'platform': 'rss', 'category': 'news', '_source_url': 'https://openai.com/news/rss.xml',
        '_source_name': 'OpenAI Blog'}]
    hf_entries = [{
        'title': 'A new RAG framework paper', 'url': 'https://huggingface.co/papers/123',
        'summary': 'rag and llm inference paper',
        'published_at': (now - timedelta(hours=4)).isoformat(timespec='seconds'),
        'platform': 'hf_papers', 'category': 'papers',
        '_source_url': 'https://huggingface.co/api/daily_papers',
        '_source_name': 'HF Daily Papers'}]
    return {'OpenAI Blog': rss_entries, 'HF Daily Papers': hf_entries}, {}


def _stub_hn_rss(monkeypatch):
    """桩 HN 与 RSS/HF(确定性成功)。注意 run.py 在 import 时已绑定
    fetch_rss_and_hf,必须替换 run_module 上的引用,不能只改 src.rss 模块属性。"""
    monkeypatch.setattr(HackerNewsCollector, 'fetch', _stub_hn_fetch)
    monkeypatch.setattr(run_module, 'fetch_rss_and_hf', _stub_rss_hf)


def _stub_all_ok(monkeypatch):
    """全部来源成功(确定性桩)。"""
    monkeypatch.setattr(GitHubCollector, 'fetch', _stub_gh_fetch)
    _stub_hn_rss(monkeypatch)


def _offline(monkeypatch):
    """模拟断网:requests / curl_cffi / httpx 全部连接失败;退避 sleep 清零。"""
    def conn_err(*a, **k):
        raise requests.exceptions.ConnectionError('network unreachable (test)')

    def httpx_err(self, *a, **k):
        raise httpx.ConnectError('network unreachable (test)')

    monkeypatch.setattr(requests, 'get', conn_err)
    monkeypatch.setattr(requests, 'post', conn_err)
    import curl_cffi.requests
    monkeypatch.setattr(curl_cffi.requests, 'get', conn_err)
    monkeypatch.setattr(httpx.Client, 'get', httpx_err)
    monkeypatch.setattr(time, 'sleep', lambda s: None)  # 退避不真睡


def _read_out_json(cfg_path, run_date):
    tmp = os.path.dirname(cfg_path)
    path = os.path.join(tmp, 'data', f'ai_news_mvp_run_{run_date.replace("-", "")}.json')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _run_db(cfg_path):
    from src.db import Database
    tmp = os.path.dirname(cfg_path)
    return Database(os.path.join(tmp, 'data', 'test.sqlite3'))


# ---------------------------------------------------------------------------
# 1) 断网:全部来源失败,不崩溃,状态/错误/退出码如实
# ---------------------------------------------------------------------------
class TestOffline:
    def test_all_sources_fail_without_crash(self, monkeypatch, tmp_path):
        cfg_path = make_cfg(tmp_path, feeds=True)  # 保留真实 feed 列表
        _offline(monkeypatch)
        monkeypatch.setenv('FEISHU_WEBHOOK_URL', 'https://open.feishu.cn/hook/test-offline')
        monkeypatch.setenv('GITHUB_PAT', '')

        code = _run(monkeypatch, _argv(cfg_path, '--since-days', '1', '--date', RUN_DATE))
        assert code == 1, '断网时退出码应为 1(全部来源失败)'

        out = _read_out_json(cfg_path, RUN_DATE)
        for p in ('github', 'hackernews', 'rss', 'hf_papers'):
            assert p in out['platforms'], p
            assert _platform_level(out['platforms'][p]) == 'failed', f'{p} 应判为 failed'

        db = _run_db(cfg_path)
        run_row = db.conn.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()
        assert run_row['status'] == 'failed'
        errs = json.loads(run_row['errors'] or '{}')
        for key in ('github', 'hackernews', 'rss', 'hf_papers'):
            assert errs.get(key), f'runs.errors 缺少 {key}: {errs}'
        # sources.last_error 按来源记录
        src_errs = {r['url']: r['last_error'] for r in db.failed_sources()}
        assert 'github:search' in src_errs
        assert 'hackernews:topstories' in src_errs
        # 真实 feed 列表时,每个 feed 也有 last_error
        from src.config import load_config
        cfg = load_config(cfg_path)
        for s in cfg['rss']['sources']:
            assert s['url'] in src_errs, f'{s["name"]} 应记录 last_error'
        assert cfg['hf_papers']['url'] in src_errs
        db.close()
        assert out['dedup']['after_dedup'] == 0


# ---------------------------------------------------------------------------
# 2) 单源失败:GitHub 全挂,其他来源照常,运行 partial,退出 0
# ---------------------------------------------------------------------------
class TestSingleSourceFailure:
    def test_github_down_others_ok(self, monkeypatch, tmp_path):
        cfg_path = make_cfg(tmp_path)
        _stub_hn_rss(monkeypatch)                     # 只桩 HN/RSS/HF,GitHub 走真实采集器
        def gh_search(self, params):
            raise requests.exceptions.ConnectionError('github down (test)')
        monkeypatch.setattr(GitHubCollector, '_search', gh_search)  # 只打 GitHub
        monkeypatch.setenv('FEISHU_WEBHOOK_URL', 'https://open.feishu.cn/hook/test-single')
        monkeypatch.setenv('GITHUB_PAT', '')

        code = _run(monkeypatch, _argv(cfg_path, '--since-days', '1', '--date', RUN_DATE))
        assert code == 0, '单源失败不应整体失败(退出 0)'

        out = _read_out_json(cfg_path, RUN_DATE)
        gh = out['platforms']['github']
        assert gh['queries_failed'] == 3 and gh['queries_ok'] == 0
        assert _platform_level(gh) == 'failed'
        assert _platform_level(out['platforms']['hackernews']) == 'ok'
        assert out['dedup']['after_dedup'] >= 3  # HN 2 条 + RSS 1 条 + HF 1 条(去重后≥3)

        db = _run_db(cfg_path)
        run_row = db.conn.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()
        assert run_row['status'] == 'partial'
        errs = json.loads(run_row['errors'] or '{}')
        assert '查询失败' in errs.get('github', '')
        # 其他来源的条目已入库
        n_items = db.count_items()
        assert n_items >= 3
        src_errs = {r['url']: r['last_error'] for r in db.failed_sources()}
        assert 'github:search' in src_errs
        db.close()


# ---------------------------------------------------------------------------
# 3) Ollama 不可达:日报仍生成,规则降级,退出 0
# ---------------------------------------------------------------------------
class TestOllamaDown:
    def test_report_falls_back_to_rules(self, monkeypatch, tmp_path):
        cfg_path = make_cfg(tmp_path)
        _stub_all_ok(monkeypatch)
        def conn_err(*a, **k):
            raise requests.exceptions.ConnectionError('ollama unreachable (test)')
        monkeypatch.setattr(requests, 'post', conn_err)   # 只有 summarize 用 post
        monkeypatch.setenv('FEISHU_WEBHOOK_URL', 'https://open.feishu.cn/hook/test-ollama')
        monkeypatch.setenv('FEISHU_WEBHOOK_KEYWORD', '每日ai情报')

        code = _run(monkeypatch, _argv(cfg_path, '--date', RUN_DATE, '--report'))
        assert code == 0

        out = _read_out_json(cfg_path, RUN_DATE)
        meta = out['report']['model_meta']
        assert meta['mode'] == 'rule' and meta['degraded'] is True

        db = _run_db(cfg_path)
        row = db.get_daily_report(RUN_DATE)
        assert row is not None and row['sent_status'] == 'pending'
        assert '每日ai情报' in row['body']          # 安全关键词仍在
        assert row['body'].startswith('每日ai情报')
        db.close()


# ---------------------------------------------------------------------------
# 4) 飞书不可达:发送失败记录进 daily_reports,正文保留可恢复,退出 1
# ---------------------------------------------------------------------------
class TestFeishuDown:
    def test_send_fails_recorded_and_recoverable(self, monkeypatch, tmp_path):
        cfg_path = make_cfg(tmp_path)
        _stub_all_ok(monkeypatch)
        monkeypatch.setenv('FEISHU_WEBHOOK_URL', 'https://open.feishu.cn/hook/test-feishu')
        monkeypatch.setenv('FEISHU_WEBHOOK_KEYWORD', '每日ai情报')

        # 第一步:采集 + 生成日报(不发送)
        code = _run(monkeypatch, _argv(cfg_path, '--date', RUN_DATE, '--report'))
        assert code == 0
        db = _run_db(cfg_path)
        row = db.get_daily_report(RUN_DATE)
        assert row is not None and row['sent_status'] == 'pending'
        body = row['body']
        db.close()

        # 第二步:飞书不可达时 --send → failed,不崩溃,正文保留
        def conn_err(*a, **k):
            raise requests.exceptions.ConnectionError('feishu unreachable (test)')
        monkeypatch.setattr(requests, 'post', conn_err)
        code = _run(monkeypatch, _argv(cfg_path, '--date', RUN_DATE, '--send'))
        assert code == 1
        db = _run_db(cfg_path)
        row = db.get_daily_report(RUN_DATE)
        assert row['sent_status'] == 'failed'
        assert row['retry_count'] == 2                 # max_retries=2 次尝试
        assert 'hook/test-feishu' not in (row['last_error'] or '')   # webhook 脱敏
        assert row['body'] == body                     # 正文未被清空,可恢复
        db.close()

        # 第三步:飞书恢复后 --send 从库重发,不重新采集
        def ok_post(url, json=None, timeout=None):
            class _R:
                status_code = 200
                def json(self):
                    return {'code': 0, 'msg': 'ok'}
            return _R()
        monkeypatch.setattr(requests, 'post', ok_post)
        code = _run(monkeypatch, _argv(cfg_path, '--date', RUN_DATE, '--send'))
        assert code == 0
        db = _run_db(cfg_path)
        assert db.get_daily_report(RUN_DATE)['sent_status'] == 'sent'
        db.close()

    def test_send_idempotent_no_duplicate(self, monkeypatch, tmp_path):
        """阶段05 验收:重复执行不会重复发送已成功发送的同一天日报。"""
        cfg_path = make_cfg(tmp_path)
        _stub_all_ok(monkeypatch)
        monkeypatch.setenv('FEISHU_WEBHOOK_URL', 'https://open.feishu.cn/hook/test-idem')
        monkeypatch.setenv('FEISHU_WEBHOOK_KEYWORD', '每日ai情报')

        # 生成日报并成功发送一次(只统计飞书 webhook 调用,忽略 Ollama 的 post)
        calls = {'n': 0}
        def ok_post(url, json=None, timeout=None):
            if 'feishu' in url:
                calls['n'] += 1
            class _R:
                status_code = 200
                def json(self):
                    return {'code': 0, 'msg': 'ok'}
            return _R()
        monkeypatch.setattr(requests, 'post', ok_post)
        code = _run(monkeypatch, _argv(cfg_path, '--date', RUN_DATE, '--report', '--send'))
        assert code == 0
        assert calls['n'] == 1

        # 再次 --send:必须跳过,不再调用 webhook
        code = _run(monkeypatch, _argv(cfg_path, '--date', RUN_DATE, '--send'))
        assert code == 0
        assert calls['n'] == 1, '已 sent 的日报重复 --send 不应再调用 webhook'
        db = _run_db(cfg_path)
        assert db.get_daily_report(RUN_DATE)['sent_status'] == 'sent'
        db.close()


# ---------------------------------------------------------------------------
# 5) _platform_level 健康度判定单元测试
# ---------------------------------------------------------------------------
def _platform_level(stat):
    return run_module._platform_level(stat)


class TestPlatformLevel:
    def test_error_key_failed(self):
        assert _platform_level({'error': 'boom'}) == 'failed'

    def test_hn_top_level_error_failed(self):
        assert _platform_level({'fetched': 0, 'failed_items': 1,
                                'top_level_error': 'net down'}) == 'failed'

    def test_hn_partial_failed_items(self):
        assert _platform_level({'fetched': 24, 'failed_items': 1}) == 'partial'

    def test_github_all_queries_failed(self):
        assert _platform_level({'queries_failed': 3, 'queries_ok': 0,
                                'fetched_raw': 0}) == 'failed'

    def test_github_partial_queries(self):
        assert _platform_level({'queries_failed': 1, 'queries_ok': 2}) == 'partial'

    def test_rss_all_feeds_failed(self):
        assert _platform_level({'fetched': 0, 'feeds_failed': {'a': 'e', 'b': 'e'},
                                'feeds_ok': 0}) == 'failed'

    def test_rss_some_feeds_failed(self):
        assert _platform_level({'fetched': 10, 'feeds_failed': {'a': 'e'},
                                'feeds_ok': 4}) == 'partial'

    def test_hf_feed_failed(self):
        assert _platform_level({'fetched': 0, 'feed_failed': 'err'}) == 'failed'

    def test_ok_empty_source(self):
        # 来源工作正常但当天无条目 → ok(不是失败)
        assert _platform_level({'fetched': 0, 'passed_rules': 0}) == 'ok'

    def test_ok_normal(self):
        assert _platform_level({'fetched': 25, 'passed_rules': 3}) == 'ok'
