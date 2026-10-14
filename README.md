# ai-news-mvp · AI 情报采集器(阶段02:接入首批情报节点)

轻量情报采集项目:接入 **GitHub Search API / Hacker News 官方 API / 官方博客 RSS / Hugging Face Daily Papers** 四类来源,统一字段、限量、去重,写入 SQLite(`sources` / `items` 两张表),提供单次运行入口 `run.py`(无自建调度、无飞书、无 LLM)。

> 本阶段(阶段02)仅采集与存储;SQLite 全表 + FTS5 + 趋势引擎、日报/飞书、Hermes 定时分别在阶段 03/04/05。

## 目录结构

```
ai-news-mvp/
  run.py                # 单次运行入口:python run.py [--since-days N]
  config.yaml           # 源/关键词/限额/超时(非秘密)
  .env.example          # 只列变量名,不存真值
  requirements.txt      # 轻量依赖清单
  src/
    config.py           # config.yaml + .env 加载
    github.py           # GitHub Search API(3 组查询模板,日期动态计算)
    hackernews.py       # HN 官方 Firebase API(topstories + 规则过滤)
    rss.py              # 复用原项目 rss_fetcher;OpenAI 等超大 feed 早期截断
    filters.py          # 规则过滤(字段完整/时间窗口/关键词,无评分)
    dedup.py            # URL 归一化 + 标题 SimHash(复用原项目 lsh_dedup)
    db.py               # SQLite(sources / items)
    log.py / reuse.py   # 日志与复用路径挂载
  tests/                # pytest 单元测试
```

## 运行

```bash
cd /home/czq/ai_news/code/ai-news-mvp
/home/czq/ai_news/code/.venv/bin/python run.py --since-days 7
```

- `.env` 读取顺序:`AI_NEWS_ENV_PATH` → `/home/czq/ai_news/.env` → 项目根 `.env`。
- 数据库/日志默认写入 `../../data`、`../../logs`(即 `/home/czq/ai_news/data`、`/home/czq/ai_news/logs`),可用 `AI_NEWS_DATA_DIR` / `AI_NEWS_LOG_DIR` / `--db` 覆盖。
- 单源失败不影响其他源;全部失败或无条目时退出码 1,否则 0。
- 重复运行同一 URL 的条目不会重复插入(只更新 `last_seen_at`),可安全重复执行。

## 复用说明

本阶段按审计报告复用原项目 [`frankzch/ai-news-brief`](https://github.com/frankzch/ai-news-brief)(MIT,commit `3b3e182`,只读不改动):

| 复用模块 | 使用方式 |
|---|---|
| `rss_fetcher.RSSFetcher._fetch_hf_daily_papers` | 逐字复用(HF 双排名 + 只留 GitHub 仓库论文) |
| `rss_fetcher.RSSFetcher`(抓取/解析逻辑) | 改编于 `parse_feed`,新增超大 feed 早期截断 |
| `lsh_dedup`(compute_simhash / is_similar) | 逐字复用,标题 SimHash 去重 |
| `log_config.setup_logging` | 逐字复用,按日滚动日志 |

原项目 src 目录定位:默认 `/home/czq/ai_news/code/ai-news-brief/src`,可用 `AI_NEWS_BRIEF_SRC` 覆盖。缺少时会给出明确报错。

## 依赖

仅轻量:requests, httpx[socks], feedparser, curl_cffi, simhash, pyyaml, python-dotenv, beautifulsoup4, trafilatura。**不装** torch / playwright / psycopg2 / PostgreSQL。

`socks` 代理兼容:`ALL_PROXY=socks://127.0.0.1:7897` 需要 `httpx[socks]`(socksio)支持 socks scheme;curl_cffi 自身支持 socks。

## GitHub 采集查询(3 组,日期动态计算,每组 ≤10)

| 组 | 模板 |
|---|---|
| 新项目 | `topic:ai created:>{d} stars:>=20` |
| 新兴 Agent | `topic:agents pushed:>{d} stars:>=50` |
| 活跃 LLM 项目 | `topic:llm pushed:>{d} stars:>=100` |

只保存轻量字段:`full_name / html_url / description(≤200 字) / stargazers_count / forks_count / created_at / pushed_at / language / topics(≤5) / fetched_at`,不抓 README/源码/评论。跨组按 `full_name` 去重,按 stars 降序取前 10。

## 测试

```bash
/home/czq/ai_news/code/.venv/bin/python -m pytest tests/ -v
```

## 配置项说明(全部)

见 `config.yaml` 内注释;秘密(`GITHUB_PAT`)只放 `.env`,不写入代码/日志/仓库。

## 失败恢复

- 来源级隔离:GitHub/HN/RSS/HF 各自独立 try/except + 超时 + 指数退避重试(2 次);单源失败不影响其他源。
- 限流处理:GitHub 403/429 不硬闯,记录并跳过该查询;HN 单条失败只计数。
- 重复运行安全:URL 冲突只更新 `last_seen_at`;想重置库删除 `data/ai_news_mvp.sqlite3` 即可。
- 复用路径缺失:设 `AI_NEWS_BRIEF_SRC` 指向原项目 src。
