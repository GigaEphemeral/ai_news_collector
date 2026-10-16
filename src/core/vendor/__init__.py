# -*- coding: utf-8 -*-
"""vendored ai-news-brief 只读模块(MIT, © frankzch, commit 3b3e182)。

- rss_fetcher.py       原 RSSFetcher(含 HF daily papers / YouTube / 多策略抓取)
- lsh_dedup.py         SimHash 去重
- log_config.py        DailyFileHandler(按日滚动日志)
- fetch_stats.py       抓取统计(本项目当前未直接使用,保留以供复用)
- config_loader.py     ConfigLoader 单例(rss_fetcher / content_processor 依赖)
- content_processor.py 正文提取(解决方案摘要抓正文首段用)

除把模块内 `from config_loader import ...` 改为包内相对 import 外,逐字保留。
"""
