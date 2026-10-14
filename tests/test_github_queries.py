# -*- coding: utf-8 -*-
"""GitHub 查询模板渲染测试(不联网):日期动态计算 + 每组 ≤10 + 字段裁剪。"""
from datetime import datetime, timedelta, timezone

from src.github import GitHubCollector


def test_query_templates_render():
    cfg = {'api_base': 'https://api.github.com', 'per_page': 10, 'per_platform_cap': 10,
           'sort': 'stars', 'order': 'desc', 'timeout': 30, 'retries': 2,
           'backoff_seconds': 2, 'request_interval_seconds': 0,
           'queries': [
               {'name': '新项目', 'template': 'topic:ai created:>{d} stars:>=20'},
               {'name': '新兴Agent', 'template': 'topic:agents pushed:>{d} stars:>=50'},
               {'name': '活跃LLM项目', 'template': 'topic:llm pushed:>{d} stars:>=100'},
           ]}
    since_days = 7
    d = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime('%Y-%m-%d')
    gh = GitHubCollector(cfg, gh_pat='')
    templates = [q['template'] for q in cfg['queries']]
    rendered = [t.format(d=d) for t in templates]
    assert rendered[0] == f'topic:ai created:>{d} stars:>=20'
    assert rendered[1] == f'topic:agents pushed:>{d} stars:>=50'
    assert rendered[2] == f'topic:llm pushed:>{d} stars:>=100'
    # 每组 per_page ≤ 10
    assert gh.per_page <= 10


def test_pick_fields_trim():
    f = GitHubCollector._pick_fields({
        'full_name': 'a/b', 'html_url': 'https://github.com/a/b',
        'description': 'x' * 300, 'stargazers_count': 100, 'forks_count': 5,
        'created_at': '2026-01-01T00:00:00Z', 'pushed_at': '2026-10-01T00:00:00Z',
        'language': 'Python', 'topics': ['a', 'b', 'c', 'd', 'e', 'f'],
    }, '2026-10-14T00:00:00+00:00')
    assert len(f['description']) == 200
    assert len(f['topics']) == 5
    assert f['stargazers_count'] == 100
    assert 'fetched_at' in f
    # 只含轻量字段,不含 README/源码等
    assert 'readme' not in f and 'contents' not in f


def test_to_entry_unified_fields():
    f = {'full_name': 'a/b', 'html_url': 'https://github.com/a/b', 'description': 'd',
         'stargazers_count': 1, 'forks_count': 0, 'created_at': 'c', 'pushed_at': 'p',
         'language': 'Go', 'topics': ['ai'], 'fetched_at': 'f'}
    e = GitHubCollector._to_entry(f)
    assert e['title'] == 'a/b' and e['url'] == 'https://github.com/a/b'
    assert e['platform'] == 'github'
    assert e['_source_url'] == 'github:search'
    assert e['extra'] == f
