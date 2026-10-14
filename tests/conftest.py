# -*- coding: utf-8 -*-
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.db import Database


@pytest.fixture
def db():
    """每个测试一个临时 SQLite 库。"""
    d = tempfile.mkdtemp(prefix='ai_news_mvp_test_')
    database = Database(os.path.join(d, 'test.sqlite3'))
    yield database
    database.close()


def trend_cfg():
    """与 config.yaml 的 trend 段一致的测试阈值。"""
    return {
        'windows': {'24h': 2, '7d': 7, '30d': 30},
        'project': {
            'young_days': 30,
            'sudden_growth_ratio': 3.0,
            'warming_min_days': 3,
            'warming_min_growth_pct': 0.5,
            'maturity_stars': 50000,
            'maturity_growth_pct': 0.05,
            'decline_negative_days': 2,
            'stagnation_days': 7,
            'stagnation_max_delta_pct': 0.05,
        },
        'topic': {
            'sudden_growth_ratio': 2.0,
            'warming_min_days': 3,
            'maturity_min_count': 3,
            'decline_negative_days': 2,
        },
    }
