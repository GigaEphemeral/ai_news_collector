# -*- coding: utf-8 -*-
"""规则过滤单元测试。"""
from src.pipeline.filters import ai_relevant, rule_filter

KEYWORDS = ['ai', 'llm', 'agent', 'rag']


def test_ai_relevant_hit_and_miss():
    assert ai_relevant('LLM release notes', '', KEYWORDS)
    assert ai_relevant('', 'A new RAG framework', KEYWORDS)
    assert not ai_relevant('New pizza recipe', '', KEYWORDS)


def test_rule_filter_missing_link_and_title():
    entries = [
        {'title': 'no link', 'url': '', 'summary': 'x'},
        {'title': '', 'url': 'https://a.com/1', 'summary': 'x'},
        {'title': 'AI Agent framework', 'url': 'https://a.com/2', 'summary': ''},
    ]
    passed, dropped = rule_filter(entries, 'rss', keywords=KEYWORDS)
    assert len(passed) == 1
    assert dropped['no_link'] == 1
    assert dropped['no_title'] == 1


def test_rule_filter_age_window():
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    entries = [
        {'title': 'AI old', 'url': 'https://a.com/old', 'published_at': old},
        {'title': 'AI fresh', 'url': 'https://a.com/fresh', 'published_at': fresh},
    ]
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    passed, dropped = rule_filter(entries, 'rss', cutoff=cutoff, keywords=KEYWORDS)
    assert len(passed) == 1 and passed[0]['title'] == 'AI fresh'
    assert dropped['old'] == 1


def test_rule_filter_too_new():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    entries = [
        {'title': 'AI just now', 'url': 'https://a.com/now', 'published_at': now.isoformat()},
    ]
    passed, dropped = rule_filter(entries, 'hackernews', keywords=KEYWORDS, min_age_hours=2)
    assert len(passed) == 0
    assert dropped['too_new'] == 1


def test_rule_filter_keyword_gate():
    entries = [{'title': 'How to bake bread', 'url': 'https://a.com/bread'}]
    passed, dropped = rule_filter(entries, 'rss', keywords=KEYWORDS)
    assert len(passed) == 0
    assert dropped['not_ai'] == 1


def test_rule_filter_github_no_keyword_gate():
    # GitHub 按 topic 已保证相关性,不走关键词门
    entries = [{'title': 'Some repo', 'url': 'https://github.com/a/b'}]
    passed, _ = rule_filter(entries, 'github', keywords=KEYWORDS)
    assert len(passed) == 1
