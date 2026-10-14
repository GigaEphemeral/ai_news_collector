# -*- coding: utf-8 -*-
"""去重单元测试:URL 归一化 + SimHash。"""
from src.dedup import Deduper, normalize_url


def test_normalize_url_strips_tracking():
    assert normalize_url('https://example.com/a?utm_source=x&ref=y&id=1') == \
        'https://example.com/a?id=1'
    assert normalize_url('https://www.example.com/a/') == 'https://example.com/a'
    assert normalize_url('HTTP://Example.com:80/a') == 'http://example.com/a'
    assert normalize_url('https://example.com:443/a') == 'https://example.com/a'


def test_normalize_url_bare():
    assert normalize_url('example.com/A') == 'https://example.com/A'


def test_deduper_url_dup():
    d = Deduper(threshold=4)
    assert d.accept('https://example.com/a?utm_medium=x', 'Title A')
    assert not d.accept('https://example.com/a', 'Title A')  # URL 归一化后重复
    assert d.url_dups == 1


def test_deduper_simhash_dup():
    d = Deduper(threshold=4)
    title = 'Gemini 4 Argon is a new model from Google DeepMind'
    assert d.accept('https://a.com/1', title)
    assert not d.accept('https://a.com/2', title + ' !!!')  # 近似重复
    assert d.simhash_dups == 1


def test_deduper_distinct_titles_pass():
    d = Deduper(threshold=4)
    assert d.accept('https://a.com/1', 'Completely different topic one')
    assert d.accept('https://a.com/2', 'Another totally distinct topic two')
    assert d.url_dups == 0 and d.simhash_dups == 0
