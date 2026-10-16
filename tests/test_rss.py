# -*- coding: utf-8 -*-
"""RSS 超大 feed 早期截断单元测试。"""
from src.collectors.rss import truncate_feed_content


def _feed(items=10):
    body = ''.join(f'<item><title>t{i}</title><link>https://a.com/{i}</link></item>'
                   for i in range(items))
    return f'<?xml version="1.0"?><rss><channel>{body}</channel></rss>'.encode()


def test_truncate_under_limit_unchanged():
    content = _feed(10)
    out = truncate_feed_content(content, max_entries=30)
    assert out == content
    assert b't9' in out


def test_truncate_over_limit_cuts_at_item_boundary():
    content = _feed(50)
    out = truncate_feed_content(content, max_entries=10)
    assert out.count(b'</item>') == 10
    assert b't9' in out and b't10' not in out
    # 截断结果仍是合法 XML 前缀(最后一个 item 完整)
    assert out.endswith(b'</item>')


def test_truncate_atom_entries():
    body = ''.join(f'<entry><title>e{i}</title></entry>' for i in range(40))
    content = f'<?xml version="1.0"?><feed>{body}</feed>'.encode()
    out = truncate_feed_content(content, max_entries=5)
    assert out.count(b'</entry>') == 5


def test_truncate_empty():
    assert truncate_feed_content(b'', 10) == b''
    assert truncate_feed_content(None, 10) is None
