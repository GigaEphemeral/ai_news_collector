# -*- coding: utf-8 -*-
"""project_snapshots 测试:按日写入、PK 幂等、连续两日对比、首日基线判定。"""
import tempfile
import os

from src.storage.db import Database


def _make_db():
    d = tempfile.mkdtemp(prefix='ai_news_mvp_test_')
    return Database(os.path.join(d, 'test.sqlite3'))


def test_snapshot_pk_upsert_idempotent():
    db = _make_db()
    db.upsert_snapshot('owner/proj', '2026-10-13', stars=1000, forks=50,
                       pushed_at='2026-10-13T08:00:00Z', created_at='2026-09-01T00:00:00Z')
    # 同日重复写入 → 幂等更新,不产生第二行
    db.upsert_snapshot('owner/proj', '2026-10-13', stars=1050, forks=55,
                       pushed_at='2026-10-13T09:00:00Z', created_at='2026-09-01T00:00:00Z')
    rows = db.snapshot_rows('owner/proj')
    assert len(rows) == 1
    assert rows[0]['stars'] == 1050
    db.close()


def test_two_day_comparison_delta():
    db = _make_db()
    db.upsert_snapshot('owner/proj', '2026-10-13', stars=1000, forks=50,
                       pushed_at='2026-10-13T08:00:00Z')
    db.upsert_snapshot('owner/proj', '2026-10-14', stars=1250, forks=60,
                       pushed_at='2026-10-14T08:00:00Z')
    rows = db.snapshot_rows('owner/proj')
    assert len(rows) == 2
    d0, d1 = rows[0], rows[1]
    delta = d1['stars'] - d0['stars']
    growth = delta / d0['stars'] * 100
    assert delta == 250
    assert round(growth, 1) == 25.0
    db.close()


def test_first_snapshot_day_detection():
    db = _make_db()
    assert not db.has_snapshot_before('2026-10-14')
    db.upsert_snapshot('owner/proj', '2026-10-14', stars=100, forks=1,
                       pushed_at='2026-10-14T08:00:00Z')
    assert not db.has_snapshot_before('2026-10-14')  # 无更早日期 → 首日
    db.upsert_snapshot('owner/proj', '2026-10-15', stars=110, forks=1,
                       pushed_at='2026-10-15T08:00:00Z')
    assert db.has_snapshot_before('2026-10-15')  # 10-14 < 10-15 → 非首日
    assert db.snapshot_repos_on('2026-10-15') == ['owner/proj']
    db.close()
