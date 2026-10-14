# ai-news-mvp · AI 情报采集器(阶段03:历史库与趋势引擎)

轻量情报采集项目:接入 **GitHub Search API / Hacker News 官方 API / 官方博客 RSS / Hugging Face Daily Papers** 四类来源,统一字段、限量、去重,写入 **SQLite 全表 + FTS5**,提供**规则趋势引擎**(24h/7d/30d,五态,纯规则不评分不调 LLM),单次运行入口 `run.py`(无自建调度、无飞书、无 LLM)。

> 阶段02:仅 sources/items 两表采集存储;阶段03 补齐全部表、项目快照、主题统计、趋势引擎。阶段04(日报/飞书)、阶段05(Hermes 定时)后续。

## 目录结构

```
ai-news-mvp/
  run.py                    # 单次运行入口:python run.py [--since-days N] [--date YYYY-MM-DD]
  config.yaml               # 源/关键词/限额/超时/趋势阈值(非秘密)
  .env.example              # 只列变量名,不存真值
  requirements.txt          # 轻量依赖清单(阶段03 无新增)
  scripts/
    simulate_multi_day.py   # 多日连续运行模拟(受控合成数据,演示 8 天趋势对比)
  src/
    config.py               # config.yaml + .env 加载
    github.py               # GitHub Search API(3 组查询模板,日期动态计算)
    hackernews.py           # HN 官方 Firebase API(topstories + 规则过滤)
    rss.py                  # 复用原项目 rss_fetcher;超大 feed 早期截断
    filters.py              # 规则过滤(字段完整/时间窗口/关键词,无评分)
    dedup.py                # URL 归一化 + 标题 SimHash(复用原项目 lsh_dedup)
    db.py                   # SQLite 全表 + FTS5 + 快照/主题统计/运行记录
    trend.py                # 规则趋势引擎(五态 + 24h/7d/30d + 证据)
    log.py / reuse.py       # 日志与复用路径挂载
  tests/                    # pytest 单元测试(含趋势规则/快照对比/FTS5)
```

## 运行

```bash
cd /home/czq/ai_news/code/ai-news-mvp
/home/czq/ai_news/code/.venv/bin/python run.py --since-days 7
```

- `.env` 读取顺序:`AI_NEWS_ENV_PATH` → `/home/czq/ai_news/.env` → 项目根 `.env`。
- 数据库/日志默认写入 `../../data`、`../../logs`(即 `/home/czq/ai_news/data`、`/home/czq/ai_news/logs`),可用 `AI_NEWS_DATA_DIR` / `AI_NEWS_LOG_DIR` / `--db` 覆盖。
- `--date YYYY-MM-DD` 指定运行日期(默认今天 UTC;Hermes 可指定报表日期;快照/主题统计/趋势都归到该日期)。
- 单源失败不影响其他源;全部失败或无条目时退出码 1,否则 0。
- 重复运行同一 URL 的条目不会重复插入(只更新 `last_seen_at`),快照按 (repo, date) 幂等,可安全重复执行。

## SQLite 全表(阶段03)

| 表 | 作用 | 说明 |
|---|---|---|
| `sources` | 来源配置/启用/最后成功/错误 | 阶段02 已有 |
| `items` | 情报条目(url UNIQUE + 内容哈希去重) | 阶段02 已有 |
| `item_sources` | 同一事件多来源映射 | 阶段03 新增 |
| `project_snapshots` | GitHub 项目每日快照(repo+date 主键) | **star_snapshots 思路并入此表** |
| `topic_daily_stats` | 每天每主题条目数/来源数/代表情报 | 阶段03 新增 |
| `runs` | 每次运行开始/结束/状态/错误/趋势 | 阶段03 新增 |
| `daily_reports` | 日报与发送状态(阶段04 使用) | 阶段03 建表 |
| `items_fts` | FTS5 external-content 全文索引 + 触发器(items_ai/ad/au) | 审计实测可用 |

- 阶段02 旧库自动迁移:连接时 `CREATE TABLE IF NOT EXISTS` 补新表,并对存量 items 执行一次 FTS `rebuild`,旧数据立即可检索。
- FTS 查询:`MATCH 'llm OR agent'` 语法,按 bm25 排序,测试与真实运行均验证可用。

## 项目快照(project_snapshots)

GitHub 采集后按运行日写入:`repo, date, stars, forks, pushed_at, created_at, description, language, topics(≤5), fetched_at`,主键 `(repo, date)`。首次运行只建基线;连续运行后即可算 24h 增量、7d/30d 均值。字段裁剪与趋势参考一致(description≤200、topics≤5、不抓 README)。

## 主题统计(topic_daily_stats)

每日按 `date(first_seen_at)=运行日` 汇总:
- GitHub 条目 → `extra.topics` 原样入库(无 topics 归入 `opensource`);
- 其他平台 → 标题/摘要命中 AI 关键词;无命中归入 category/platform;
- 计数:条目数、来源数(去重平台数)、代表情报(当日该主题互动最高条目)。

## 趋势引擎(src/trend.py,纯规则、不评分、不调 LLM)

对**项目**(基于快照)与**主题**(基于日统计)各输出五态:

| 状态 | 项目规则(project_snapshots) | 主题规则(topic_daily_stats) |
|---|---|---|
| 新出现 | 仅 1 个快照日 / 昨日断档 / 变化不足以定论 | 仅 1 天统计 / 昨日断档 |
| 持续升温 | 连续 ≥3 天正增长、趋势不减、窗口累计增长率 ≥ 0.5% | 连续 ≥3 天增量且 ≥1 天多来源 |
| 突然增长 | 24h 增量 ≥ 前序日均增量 × 3 | 当日增量 ≥ 前序日均增量 × 2 |
| 进入成熟期 | Star ≥ 5 万且 7 日均增长 ≤ 0.05%;或连续 ≥7 日入选且近 7 日增量≈0 | 7 天日均 ≥ 3 条且近 3 天持平 |
| 热度回落 | 连续 ≥2 天负增长;或近期均值低于前期且当日无正增长 | 连续 ≥2 天递减;或近期低于前期 |

- **三个时间尺度**:24h(需连续昨日+今日)、7d(需 7 个观察日)、30d(需 30 个观察日);不足的窗口明确输出 `窗口不足(仅 N 天,需 M 天)`,不臆造。
- **首次运行只建基线**:库中无更早日快照时全部标 `初步判断` + `系统首日运行` 说明,次日才开始计算 24h 增量。
- **证据强制**:每个结论带来源(表名)、数字、时间窗;证据不足写"新出现,尚待观察",禁止编造。
- **重复降权**:连续 ≥7 日入选且近 7 日 Star 增量≈0(正负均 ≤0.05%)标 `downgraded: true` 并附原因;真实涨跌不算降权。
- 阈值全部在 `config.yaml` 的 `trend:` 段,可调。输出顺序为固定状态分组(展示用,非评分排序)。

## 多日连续运行演示(验收:连续运行可对比昨日/近 7 天)

```bash
/home/czq/ai_news/code/.venv/bin/python scripts/simulate_multi_day.py
```

用受控合成数据模拟连续 8 天运行(临时库),演示五态全覆盖、昨日/7 天对比、"窗口不足"标注与降权。真实运行请每日执行 `run.py`(Hermes 触发,阶段05),快照与统计逐日累积。

## 复用说明

按审计报告复用原项目 [`frankzch/ai-news-brief`](https://github.com/frankzch/ai-news-brief)(MIT,commit `3b3e182`,只读不改动):`rss_fetcher`(HF daily papers / parse_feed)、`lsh_dedup`、`log_config`。原项目 src 定位:默认 `/home/czq/ai_news/code/ai-news-brief/src`,可用 `AI_NEWS_BRIEF_SRC` 覆盖。

## 依赖

阶段03 **无新增依赖**。仅轻量:requests, httpx[socks], feedparser, curl_cffi, simhash, pyyaml, python-dotenv, beautifulsoup4, trafilatura。**不装** torch / playwright / psycopg2 / PostgreSQL / 向量库。

## GitHub 采集查询(3 组,日期动态计算,每组 ≤10)

| 组 | 模板 |
|---|---|
| 新项目 | `topic:ai created:>{d} stars:>=20` |
| 新兴 Agent | `topic:agents pushed:>{d} stars:>=50` |
| 活跃 LLM 项目 | `topic:llm pushed:>{d} stars:>=100` |

只保存轻量字段(见"项目快照"),不抓 README/源码/评论。跨组按 `full_name` 去重,按 stars 降序取前 10。

## 测试

```bash
cd /home/czq/ai_news/code/ai-news-mvp
/home/czq/ai_news/code/.venv/bin/python -m pytest tests/ -v
```

覆盖:趋势规则五态单元测试(新/升温/突增/回落/成熟)、快照连续两日对比与幂等、FTS5 查询与触发器同步、旧库迁移 rebuild、item_sources 跨源映射、runs 记录、主题统计。从 git 仓库副本运行时需设 `AI_NEWS_BRIEF_SRC=/home/czq/ai_news/code/ai-news-brief/src`。

## 配置项说明(全部)

见 `config.yaml` 内注释;秘密(`GITHUB_PAT`)只放 `.env`,不写入代码/日志/仓库。新增 `trend:`(阈值)与 `topics.max_per_item` 段,阈值含义见文件内注释。

## 失败恢复

- 来源级隔离:GitHub/HN/RSS/HF 各自独立 try/except + 超时 + 指数退避重试(2 次);单源失败不影响其他源。
- 限流处理:GitHub 403/429 不硬闯,记录并跳过该查询;HN 单条失败只计数。
- 重复运行安全:URL 冲突只更新 `last_seen_at`;快照/主题统计按主键幂等;想重置库删除 `data/ai_news_mvp.sqlite3` 即可。
- 运行记录:`runs` 表记录每次运行状态(ok/partial/failed)、来源统计、错误、趋势输出。
- 复用路径缺失:设 `AI_NEWS_BRIEF_SRC` 指向原项目 src。
