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


# ---------------------------------------------------------------------------
# AIQI-7 问题3:标题/摘要相似度 ≥ 阈值(默认 0.8,八成)判为重复
# ---------------------------------------------------------------------------
def test_deduper_similarity_title_dup():
    d = Deduper(threshold=4, similarity_threshold=0.8)
    t1 = 'Gemini 4 Argon is a new model from Google DeepMind focused on safety'
    t2 = 'Gemini 4 Argon is a new model from Google DeepMind focused on safety and alignment'
    assert d.accept('https://a.com/1', t1, 'Some summary text')
    # 标题 token 集合 Jaccard ≈ 0.87 ≥ 0.8 且 SimHash 未命中 → 判重复(URL 不同也重复)
    assert not d.accept('https://a.com/2', t2, 'Different summary')
    assert d.similarity_dups == 1


def test_deduper_similarity_summary_dup():
    d = Deduper(threshold=4, similarity_threshold=0.8)
    s1 = 'Google DeepMind releases Gemini 4 Argon a new model focused on safety'
    s2 = 'Google DeepMind releases Gemini 4 Argon, a brand new model focused on safety'
    assert d.accept('https://a.com/1', 'Title one completely unrelated', s1)
    # 标题不同但摘要高度相似(≥0.8)→ 判重复
    assert not d.accept('https://a.com/2', 'Title two completely unrelated', s2)
    assert d.similarity_dups == 1


def test_deduper_similarity_below_threshold_passes():
    d = Deduper(threshold=4, similarity_threshold=0.8)
    assert d.accept('https://a.com/1', 'Agent framework for reasoning', 'one')
    assert d.accept('https://a.com/2', 'RAG patterns in production at scale', 'two')
    assert d.url_dups == 0 and d.simhash_dups == 0 and d.similarity_dups == 0


def test_deduper_similarity_disabled_when_zero():
    d = Deduper(threshold=4, similarity_threshold=0.0)
    assert d.accept('https://a.com/1', 'Title A distinct enough', 'Summary text one')
    assert d.accept('https://a.com/2', 'Title B distinct enough', 'Summary text one')  # 阈值关闭,仅按标题
    assert d.similarity_dups == 0


def test_deduper_similarity_chinese_duplicate():
    d = Deduper(threshold=4, similarity_threshold=0.8)
    t1 = '谷歌发布新一代大语言模型智能体框架'
    t2 = '谷歌发布新一代大语言模型智能体框架V2'
    assert d.accept('https://a.com/1', t1, '')
    assert not d.accept('https://a.com/2', t2, '')  # 中文二元组相似
    assert d.similarity_dups == 1


def test_jaccard_similarity_basic():
    from src.dedup import jaccard_similarity, _tokens
    a, b = _tokens('hello world'), _tokens('hello world again')
    assert abs(jaccard_similarity(a, b) - 2 / 3) < 1e-9
    assert jaccard_similarity(set(), set()) == 0.0
