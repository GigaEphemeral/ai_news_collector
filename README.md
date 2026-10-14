# ai-news-mvp · AI 情报采集器(阶段04:日报与飞书)

轻量情报采集项目:接入 **GitHub Search API / Hacker News 官方 API / 官方博客 RSS / Hugging Face Daily Papers** 四类来源,统一字段、限量、去重,写入 **SQLite 全表 + FTS5**,提供**规则趋势引擎**(24h/7d/30d,五态,纯规则不评分不调 LLM)与**模板日报 + 可选 Ollama 摘要 + 飞书幂等发送**(生成与发送分离)。单次运行入口 `run.py`(无自建调度)。

> 阶段02:仅 sources/items 两表采集存储;阶段03 补齐全部表、项目快照、主题统计、趋势引擎;阶段04 日报模板/可选 Ollama 摘要/飞书发送(幂等、重试、防泄漏);阶段05(Hermes 定时)后续。

## 目录结构

```
ai-news-mvp/
  run.py                    # 单次运行入口:采集 + [--report] 日报 + [--send] 飞书发送
  config.yaml               # 源/关键词/限额/超时/趋势阈值/report/ollama/feishu(非秘密)
  .env.example              # 只列变量名,不存真值(FEISHU_WEBHOOK_URL 等秘密只在 .env)
  requirements.txt          # 轻量依赖清单(阶段04 无新增)
  scripts/
    simulate_multi_day.py   # 多日连续运行模拟(受控合成数据,演示 8 天趋势对比)
  src/
    config.py               # config.yaml + .env 加载
    github.py               # GitHub Search API(3 组查询模板,日期动态计算)
    hackernews.py           # HN 官方 Firebase API(topstories + 规则过滤)
    rss.py                  # 复用原项目 rss_fetcher;超大 feed 早期截断
    filters.py              # 规则过滤(字段完整/时间窗口/关键词,无评分)
    dedup.py                # URL 归一化 + 标题 SimHash(复用原项目 lsh_dedup)
    db.py                   # SQLite 全表 + FTS5 + 快照/主题统计/运行记录/日报状态
    trend.py                # 规则趋势引擎(五态 + 24h/7d/30d + 证据)
    report.py               # 日报模板(固定栏目/关键词/长度校验/规则降级选择) [阶段04]
    summarize.py            # 可选 Ollama 摘要(精简 JSON/关 thinking/输出校验/降级) [阶段04]
    feishu.py               # 飞书发送(webhook 脱敏/幂等/退避重试/结果不确定停止) [阶段04]
    log.py / reuse.py       # 日志与复用路径挂载
  tests/                    # pytest 单元测试(80 个:趋势/快照/FTS5/日报/Ollama/飞书)
```

## 运行

```bash
cd /home/czq/ai_news/code/ai-news-mvp
# 1) 仅采集
/home/czq/ai_news/code/.venv/bin/python run.py --since-days 7
# 2) 采集 + 生成日报(可选 Ollama 摘要,失败自动降级),不发送
/home/czq/ai_news/code/.venv/bin/python run.py --report
# 3) 从库中发送当日日报(幂等:已成功发送则跳过;不重新采集)
/home/czq/ai_news/code/.venv/bin/python run.py --send
# 4) 采集 + 生成 + 发送一次(手动触发验收)
/home/czq/ai_news/code/.venv/bin/python run.py --report --send
# 5) 跳过 Ollama,强制规则模式
/home/czq/ai_news/code/.venv/bin/python run.py --report --no-summarize
```

- `.env` 读取顺序:`AI_NEWS_ENV_PATH` → `/home/czq/ai_news/.env` → 项目根 `.env`。
- 秘密只放 `.env`:`FEISHU_WEBHOOK_URL`(飞书自定义机器人 webhook)、`FEISHU_WEBHOOK_KEYWORD`(安全关键词,默认「每日ai情报」)、`GITHUB_PAT`。**严禁写入代码/日志/仓库**;webhook 在任何日志/错误信息/数据库记录中都被脱敏为 `<redacted>`。
- 数据库/日志默认写入 `../../data`、`../../logs`(即 `/home/czq/ai_news/data`、`/home/czq/ai_news/logs`),可用 `AI_NEWS_DATA_DIR` / `AI_NEWS_LOG_DIR` / `--db` 覆盖。
- `--date YYYY-MM-DD` 指定运行日期(默认今天 UTC;Hermes 可指定报表日期;快照/主题统计/趋势/日报都归到该日期)。
- **日报生成与发送分离**:`--report` 只生成并写入 `daily_reports`;`--send` 只读库发送。飞书不可用时先 `--report` 生成,恢复后直接 `--send`,不重新抓取。
- **幂等**:`daily_reports.sent_status='sent'` 的日报不会被重复发送,也不会被重新生成覆盖。
- 单源失败不影响其他源;全部失败或无条目时退出码 1,否则 0。

## SQLite 全表(阶段03+04)

| 表 | 作用 | 说明 |
|---|---|---|
| `sources` | 来源配置/启用/最后成功/错误 | 阶段02 已有 |
| `items` | 情报条目(url UNIQUE + 内容哈希去重) | 阶段02 已有 |
| `item_sources` | 同一事件多来源映射 | 阶段03 新增 |
| `project_snapshots` | GitHub 项目每日快照(repo+date 主键) | **star_snapshots 思路并入此表** |
| `topic_daily_stats` | 每天每主题条目数/来源数/代表情报 | 阶段03 新增 |
| `runs` | 每次运行开始/结束/状态/错误/趋势 | 阶段03 新增 |
| `daily_reports` | 日报正文/生成时间/发送状态(sent/failed/uncertain/pending)/retry_count/last_error | 阶段04 使用(阶段03 建表) |
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

## 日报与飞书(阶段04)

### 日报模板(src/report.py)

固定栏目(方案第七节),栏目允许为空、不凑数,每条附原文链接,推测与事实分开:

```
每日ai情报 · AI 情报日报 2026-10-14        ← 标题含安全关键词(webhook 校验必需)
采集窗口:过去 24 小时 · 历史趋势:过去 7 天
一、今日值得关注   (2-3 条:Ollama 摘要或规则标签降级;无热点输出"今日没有符合筛选条件的新热点")
二、开源项目与工程实践 (GitHub 项目 + 趋势状态标签 + ⭐/24h 增量 + 链接)
三、解决方案与产品发布 (官方博客 RSS / HF 论文 + 链接)
四、趋势观察       (项目/主题 + 状态 + 7d 证据;证据=事实来自库,
                    "初步判断,历史不足"=观察限制,明确标注)
五、采集状态       (成功来源数/发现条目/去重后/模型状态/失败源)
```

- 长度与关键词校验:`validate_report()` 检查「每日ai情报」关键词与 `report.max_chars`(6000),超长自动裁剪(关键词在标题首行不受影响)。
- 生成与发送分离:`build_report()` 只写 `daily_reports`;`run.py --send` 从库重发,不重新抓取。

### 可选 Ollama 摘要(src/summarize.py)

- 只对入选 Top `highlights_cap`(8)调用 `http://192.168.0.103:11434` 的 `Qwen3.5:9B`;
- 输入精简 JSON:`name/description/stars/stars_24h/created_days/topics`(github-trend-reference 第五节),不传 API 原始 JSON;
- **关闭 thinking(`think: false`)并给足生成预算(`num_predict: 2048`)**,`format: json` 强制 JSON 输出;
- 输出 3-5 条,经代码校验:项目名必须与输入匹配、URL 取输入规范化值(防编造链接)、简介/依据非空;
- 超时/不可达/输出非法 → **降级**:原标题 + 规则标签,不阻塞日报、不反复重试(单次调用)。

### 飞书发送(src/feishu.py)

- webhook 只从 `.env` 的 `FEISHU_WEBHOOK_URL` 读取;**严禁打印/外泄**:所有日志、异常文本、`daily_reports.last_error` 都把 webhook 脱敏为 `<redacted>`;
- **幂等**:发送前查 `daily_reports.sent_status == 'sent'` → 跳过;只有飞书确认成功(`code=0`)才置 `sent`;
- 明确失败(HTTP 返回非 0)→ 指数退避重试,有限次(`feishu.max_retries: 3`);结果不确定(超时/响应无法解析)→ 置 `uncertain` 立即停止,不无限重发;
- 发送文本必须含安全关键词,缺失拒绝发送。

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

覆盖:趋势规则五态单元测试(新/升温/突增/回落/成熟)、快照连续两日对比与幂等、FTS5 查询与触发器同步、旧库迁移 rebuild、item_sources 跨源映射、runs 记录、主题统计;**阶段04**:日报关键词/长度/五栏目顺序/链接/空栏目不凑数/规则降级选择、Ollama 输入精简/输出校验(项目名匹配/URL 规范化/条数下限)/降级路径(连接错误/超时/非法输出)/think+num_predict 请求参数、飞书幂等(已 sent 跳过)/成功置 sent/退避重试后成功/重试耗尽置 failed/超时置 uncertain 不重试/缺日报与缺关键词拒绝/webhook 不出现在日志与库。从 git 仓库副本运行时需设 `AI_NEWS_BRIEF_SRC=/home/czq/ai_news/code/ai-news-brief/src`。

## 配置项说明(全部)

见 `config.yaml` 内注释;秘密(`GITHUB_PAT` / `FEISHU_WEBHOOK_URL` / `FEISHU_WEBHOOK_KEYWORD`)只放 `.env`,不写入代码/日志/仓库。新增 `trend:`(阈值)、`topics.max_per_item`、`report:`(关键词/长度/各栏目条数上限)、`ollama:`(地址/模型/think/num_predict/输出条数)、`feishu:`(超时/重试/退避)段,含义见文件内注释。

## 失败恢复

- 来源级隔离:GitHub/HN/RSS/HF 各自独立 try/except + 超时 + 指数退避重试(2 次);单源失败不影响其他源。
- 限流处理:GitHub 403/429 不硬闯,记录并跳过该查询;HN 单条失败只计数。
- **Ollama 不可达/超时/输出非法 → 日报自动降级**(规则标签),不阻塞、不反复重试。
- **飞书不可用 → 日报已在 `daily_reports`**,恢复后 `run.py --send --date YYYY-MM-DD` 从库重发,不重新采集;已 `sent` 的日报不会被重复发送。
- **发送结果不确定(超时) → 置 `uncertain` 停止重发**,避免同一条消息被重复推送;人工确认后可重新 `--send`。
- 重复运行安全:URL 冲突只更新 `last_seen_at`;快照/主题统计按主键幂等;想重置库删除 `data/ai_news_mvp.sqlite3` 即可。
- 运行记录:`runs` 表记录每次运行状态(ok/partial/failed)、来源统计、错误、趋势输出。
- 复用路径缺失:设 `AI_NEWS_BRIEF_SRC` 指向原项目 src。
