# -*- coding: utf-8 -*-
"""阶段06 验收测试:[04] 脚本抓详情 + candidates/draft 输出。

覆盖:
  - build_draft_with_placeholders:占位符 {{INTRO_Hx/Ox/Sx}}/{{MODEL_STATUS}} 唯一、一一对应
  - export_candidates_json:schema_version/date/draft_report_path/keyword/stats/items/trends
  - GitHub 详情来自 README、按 content_hash 缓存 7 天不重抓、无 clone(不抓 git 目录)
  - fetch_contents 并发回退(RSS 失败回退 summary;HN 回退标题)
"""
import json
import os
import re
import tempfile
from datetime import datetime, timezone

from src.storage.db import Database
from src.pipeline.dedup import canonical_key as _ck
from src.reporting.report import (build_draft_with_placeholders, collect_candidates,
                                  export_candidates_json, rule_highlights)
from src.summarization.content_fetcher import fetch_contents, _cached, _cache_put


def _make_db():
    d = tempfile.mkdtemp(prefix='ai_news_mvp_test_')
    return Database(os.path.join(d, 'test.sqlite3')), d


def _cfg(data_dir=None):
    return {
        'report': {'highlights_cap': 8, 'max_highlights': 3, 'opensource_max': 5,
                   'solutions_max': 5, 'new_days': 30, 'summary_max_chars': 80,
                   'max_chars': 6000, 'required_keyword': '每日ai情报'},
        'trend': {'windows': {'24h': 2, '7d': 7, '30d': 30},
                  'project': {'young_days': 30}, 'topic': {}},
        'keywords': {'ai': ['ai', 'llm', 'agent', 'rag']},
        '_data_dir': data_dir,
        '_project_root': os.getcwd(),
    }


def _seed_mixed(db, run_date):
    now = f'{run_date}T09:00:00+00:00'
    gh = db.upsert_source('github:search', 'GitHub', 'github')
    hn = db.upsert_source('hackernews:topstories', 'HN', 'hackernews')
    rss = db.upsert_source('https://a.com/feed', 'Blog A', 'rss')
    hf = db.upsert_source('https://huggingface.co/api/daily_papers', 'HF', 'hf_papers')
    db.upsert_item(platform='github', title='acme/agentkit',
                   url='https://github.com/acme/agentkit', summary='AI agent framework',
                   published_at=None, content_hash=1, canonical_key=_ck('acme/agentkit'),
                   category='opensource',
                   extra={'full_name': 'acme/agentkit', 'stargazers_count': 100,
                          'created_at': '2026-09-01T00:00:00Z', 'topics': ['ai', 'agents'],
                          'language': 'Python'},
                   source_id=gh, first_seen_at=now, native_id='acme/agentkit')
    db.upsert_item(platform='hackernews', title='Agentic coding on the rise',
                   url='https://news.ycombinator.com/item?id=555', summary='',
                   published_at=None, content_hash=2, canonical_key=_ck('Agentic coding on the rise'),
                   category='discussion', extra={'points': 300, 'text': 'a long discussion body'},
                   source_id=hn, first_seen_at=now, native_id='555')
    db.upsert_item(platform='rss', title='OpenAI reasoning model',
                   url='https://a.com/blog/x', summary='official blog post',
                   published_at=None, content_hash=3, canonical_key=_ck('OpenAI reasoning model'),
                   category='news', extra={}, source_id=rss,
                   source_url='https://a.com/feed', first_seen_at=now, native_id='f:1:g1')
    db.upsert_item(platform='hf_papers', title='RAG paper', url='https://huggingface.co/papers/2401.1',
                   summary='rag paper summary', published_at=None, content_hash=4,
                   canonical_key=_ck('RAG paper'), category='papers',
                   extra={'arxiv_id': '2401.1', 'upvotes': 50, 'github_stars': 200,
                          'is_hf_papers': True},
                   source_id=hf, first_seen_at=now, native_id='2401.1')


def test_draft_placeholders_unique_and_1to1():
    db, d = _make_db()
    run_date = datetime.now(timezone.utc).date().isoformat()
    _seed_mixed(db, run_date)
    cands = collect_candidates(db, run_date, _cfg())
    hl = rule_highlights(cands, _cfg())
    draft = build_draft_with_placeholders(cands, _cfg(), highlights=hl)
    phs = re.findall(r'\{\{INTRO_[HOS]\d+\}\}|\{\{MODEL_STATUS\}\}', draft['body'])
    assert '{{MODEL_STATUS}}' in phs
    intros = [p for p in phs if p != '{{MODEL_STATUS}}']
    # 每个占位符唯一(不重复出现)
    assert len(intros) == len(set(intros)), '占位符必须唯一'
    # 每个占位符映射到恰好一个条目(url 非空且来自候选)
    urls = {c['url'] for c in cands}
    for ph, url in draft['placeholders'].items():
        if ph == '{{MODEL_STATUS}}':
            continue
        assert url in urls, f'{ph} 必须对应一个候选条目'
    # 占位符数与映射条目一一对应(同一 url 可出现在多个 section,但占位符各自唯一)
    assert len(draft['placeholders']) - 1 == len(intros)
    db.close()


def test_export_candidates_json_schema():
    db, d = _make_db()
    data_dir = tempfile.mkdtemp(prefix='cand_out_')
    run_date = datetime.now(timezone.utc).date().isoformat()
    _seed_mixed(db, run_date)
    cfg = _cfg(data_dir)
    cfg['_date'] = run_date
    cands = collect_candidates(db, run_date, cfg)
    hl = rule_highlights(cands, cfg)
    path, out = export_candidates_json(cands, cfg, draft_report_path='/tmp/draft.txt',
                                       highlights=hl)
    assert os.path.exists(path)
    assert out['schema_version'] == 1
    assert out['date'] == run_date
    assert out['draft_report_path'] == '/tmp/draft.txt'
    assert out['keyword'] == '每日ai情报'
    assert isinstance(out['items'], list) and len(out['items']) > 0
    keys = {'placeholder', 'section', 'url', 'title', 'platform', 'facts',
            'reason', 'content', 'content_source', 'fallback_intro'}
    assert keys <= set(out['items'][0].keys())
    phs = [it['placeholder'] for it in out['items']]
    assert len(phs) == len(set(phs)), 'candidates 占位符必须唯一'
    db.close()


def test_github_readme_cache_no_refetch(monkeypatch):
    """GitHub 详情来自 README;content_hash 命中缓存后不再请求(7 天内)。"""
    db, d = _make_db()
    data_dir = tempfile.mkdtemp(prefix='cache_')
    run_date = datetime.now(timezone.utc).date().isoformat()
    _seed_mixed(db, run_date)
    cfg = _cfg(data_dir)
    cands = collect_candidates(db, run_date, cfg)
    gh = [c for c in cands if c['platform'] == 'github'][0]
    calls = {'n': 0}

    def fake_get(url, **kw):
        calls['n'] += 1
        class R:
            status_code = 200
            text = '# AgentKit\n\nA powerful agent framework.' * 5
            def raise_for_status(self):
                pass
        return R()

    monkeypatch.setattr('src.summarization.content_fetcher.requests.get', fake_get)
    m1 = fetch_contents([gh], cfg, workers=1, timeout=5)
    assert m1[gh['url']]['content_source'] == 'GitHub README'
    assert 'AgentKit' in m1[gh['url']]['content']
    first_calls = calls['n']
    # 再次抓:content_hash 缓存命中 → 不再请求网络
    m2 = fetch_contents([gh], cfg, workers=1, timeout=5)
    assert calls['n'] == first_calls
    assert m2[gh['url']]['content'] == m1[gh['url']]['content']
    db.close()


def test_github_readme_fallback_and_no_clone(monkeypatch):
    """README 全失败 → 兜底 description+topics+language,标 fallback;无 clone。"""
    db, d = _make_db()
    data_dir = tempfile.mkdtemp(prefix='fb_')
    run_date = datetime.now(timezone.utc).date().isoformat()
    _seed_mixed(db, run_date)
    cfg = _cfg(data_dir)
    cands = collect_candidates(db, run_date, cfg)
    gh = [c for c in cands if c['platform'] == 'github'][0]

    def boom(url, **kw):
        raise __import__('requests').exceptions.ConnectionError('offline')

    monkeypatch.setattr('src.summarization.content_fetcher.requests.get', boom)
    m = fetch_contents([gh], cfg, workers=1, timeout=5)
    assert m[gh['url']]['content_source'].startswith('fallback')
    assert 'AI agent framework' in m[gh['url']]['content']
    # 无 clone:不调用 git 命令(本模块无 subprocess/git 调用)
    import subprocess
    assert not hasattr(__import__('src.summarization.content_fetcher',
                                  fromlist=['x']), 'subprocess')
    db.close()


def test_rss_failure_falls_back_to_summary(monkeypatch):
    db, d = _make_db()
    data_dir = tempfile.mkdtemp(prefix='rssfb_')
    run_date = datetime.now(timezone.utc).date().isoformat()
    _seed_mixed(db, run_date)
    cfg = _cfg(data_dir)
    cands = collect_candidates(db, run_date, cfg)
    rss = [c for c in cands if c['platform'] == 'rss'][0]

    def boom(url, **kw):
        raise __import__('requests').exceptions.Timeout('slow')

    monkeypatch.setattr('src.summarization.content_fetcher.ContentProcessor.fetch_and_extract',
                        lambda *a, **k: (_ for _ in ()).throw(__import__('requests').exceptions.Timeout()))
    monkeypatch.setattr('src.summarization.content_fetcher.requests.get', boom)
    m = fetch_contents([rss], cfg, workers=1, timeout=5)
    assert m[rss['url']]['content_source'] == '回退 summary'
    assert 'official blog post' in m[rss['url']]['content']
    db.close()


def test_hn_content_uses_discussion_text():
    db, d = _make_db()
    data_dir = tempfile.mkdtemp(prefix='hn_')
    run_date = datetime.now(timezone.utc).date().isoformat()
    _seed_mixed(db, run_date)
    cfg = _cfg(data_dir)
    cands = collect_candidates(db, run_date, cfg)
    hn = [c for c in cands if c['platform'] == 'hackernews'][0]
    m = fetch_contents([hn], cfg, workers=1, timeout=5)
    assert m[hn['url']]['content_source'] == '讨论文字'
    assert 'long discussion body' in m[hn['url']]['content']
    db.close()
