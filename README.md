# ai-news-mvp · AI 情报采集器(阶段05:定时执行与稳定性)

轻量情报采集项目:接入 **GitHub Search API / Hacker News 官方 API / 官方博客 RSS / Hugging Face Daily Papers** 四类来源,统一字段、限量、去重,写入 **SQLite 全表 + FTS5**,提供**规则趋势引擎**(24h/7d/30d,五态,纯规则不评分不调 LLM)与**模板日报 + 可选 Ollama 摘要 + 飞书幂等发送**(生成与发送分离)。单次运行入口 `run.py`(无自建调度,由平台 autopilot 每日触发)。

> 阶段02:仅 sources/items 两表采集存储;阶段03 补齐全部表、项目快照、主题统计、趋势引擎;阶段04 日报模板/可选 Ollama 摘要/飞书发送(幂等、重试、防泄漏);阶段05 稳定性收尾(断网/单源失败/Ollama 不可达/飞书不可达均不崩溃、错误按来源进 runs、日志按日滚动)与定时触发验证(autopilot 每日跑 `python run.py`)。阶段04修复(AIQI-7):「今日值得关注」新生代优先 + 老项目增量达阈值才上榜 + 上榜理由可解释;解决方案与产品发布每条带一行精简摘要(`report.fetch_summary` 可配置);处理顺序改为 筛选→去重→限量 并增加标题/摘要相似度去重(阈值 0.8);采集状态分段显示。

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
    dedup.py                # URL 归一化 + 标题 SimHash + 标题/摘要相似度(阈值可配置) [AIQI-7]
    ranking.py              # GitHub 候选排序与上榜理由(新生代优先,纯规则不评分) [AIQI-7]
    db.py                   # SQLite 全表 + FTS5 + 快照/主题统计/运行记录/日报状态
    trend.py                # 规则趋势引擎(五态 + 24h/7d/30d + 证据)
    report.py               # 日报模板(固定栏目/关键词/长度校验/规则降级选择/解决方案摘要) [阶段04+AIQI-7]
    summarize.py            # 可选 Ollama 摘要 + 解决方案一行摘要(fetch_summary) [阶段04+AIQI-7]
    feishu.py               # 飞书发送(webhook 脱敏/幂等/退避重试/结果不确定停止) [阶段04]
    log.py / reuse.py       # 日志与复用路径挂载
  tests/                    # pytest 单元测试(119 个:趋势/快照/FTS5/日报/Ollama/飞书/稳定性/日志滚动/排序/相似度去重)
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
一、今日值得关注   (2-3 条:新生代优先 + 老项目增量达阈值;上榜理由基于库中真实字段可解释;
                    无热点输出"今日没有符合筛选条件的新热点",不凑数)
二、开源项目与工程实践 (GitHub 项目 + 趋势状态标签 + ⭐/24h 增量 + 链接;按 report.sort_by 排序)
三、解决方案与产品发布 (官方博客 RSS / HF 论文 + 一行精简摘要 + 链接)
四、趋势观察       (项目/主题 + 状态 + 7d 证据;证据=事实来自库,
                    "初步判断,历史不足"=观察限制,明确标注)
五、采集状态       (成功来源数/发现→筛选→去重→限量 分段/模型状态/失败源)
```

- 长度与关键词校验:`validate_report()` 检查「每日ai情报」关键词与 `report.max_chars`(6000),超长自动裁剪(关键词在标题首行不受影响)。
- 生成与发送分离:`build_report()` 只写 `daily_reports`;`run.py --send` 从库重发,不重新抓取。

### 「今日值得关注」排序与上榜理由(阶段04修复 AIQI-7,成员修正版)

成员反馈:首条日报该栏目选了 ECC(创建 269 天)、firecrawl(912 天)、ollama(1206 天)、AutoGPT(1308 天)等老项目,时效性差;上榜理由也是模型编造的模糊话术。修复(纯规则、不评分、不调 LLM,阈值全部在 `config.yaml` 的 `report:` 段):

- **排序策略 `report.sort_by`**:`freshness`(默认,新生代优先)| `growth`(24h 增量优先)| `stars`(star 总数,旧行为)。
- **新生代优先,兼顾老项目增量**:创建 ≤ `report.new_days`(30)天的项目排最前;老项目只要 24h Star 增量 ≥ `report.old_repo_min_delta_24h`(50)或 24h 增长率 ≥ `report.old_repo_min_growth_pct`(0.5%)也允许上榜,不一刀切排除(成员明确:老项目有价值,但更要关注异军突起的新生代)。
- **首日/快照不足降级**:快照不足 2 天无法算 24h 增量时,仅新生代且近期 push(≤ `report.recent_push_days`,7 天)入选,并标注「初步判断」;宁可少选,不把无增量的老项目顶上来凑数(栏目允许为空)。
- **上榜理由可解释**:每条给出具体理由(「今日新出现」「新生代(创建 N 天)」「24h Star +N(前一日 M)」「近 7 天持续更新」「老项目增量达阈值上榜」「初步判断(历史不足)」),全部基于库中真实字段;Ollama 摘要路径的上榜理由同样由代码覆盖为库中事实,不用模型编造的模糊话术。

### 解决方案与产品发布 一行精简摘要(阶段04修复 AIQI-7,成员修正版)

成员反馈:该栏目多条 summary 为空,一眼看不出内容。修复:

- 每条带一行精简摘要(≤ `report.summary_max_chars`,80 字),让人不看链接就懂内容;
- **摘要来源优先级**:库中已有 summary → 为空且 `report.fetch_summary: true` 时抓正文首段(复用原项目 `content_processor`)或调 Ollama 生成一句话摘要(只对入选 ≤ `solutions_max` 条,单次尝试不反复重试烧 token)→ 仍为空则注明理由(如「原文未提供摘要」),不显示空白;
- **`report.fetch_summary` 默认 `false`**(成员自己用不开启,不额外烧 token):只显示库中已有 summary,为空就注明理由;开源给他人使用时可设为 `true` 自动抓取/生成。

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

只保存轻量字段(见"项目快照"),不抓 README/源码/评论。跨组按 `full_name` 去重。

## 排序与筛选规则(阶段04修复 AIQI-7)

### 处理顺序(成员修正版:规则筛选 → 去重 → 限量)

```
各源采集 → 规则筛选(字段完整/时间窗/关键词/热度,不限量)
        → 跨源去重(URL 归一化 + 标题 SimHash + 标题/摘要相似度)
        → 每平台限量(per_platform_cap,默认 10)
```

- **限量发生在去重之后**:采集器内部不再限量(GitHub 跨组 full_name 去重后全量进入跨源去重),先统一去重再按每平台上限截断,避免跨源重复内容各自占掉限量名额、挤掉独特条目。
- **去重机制**:① URL 归一化(去跟踪参数/默认端口/www);② 标题 SimHash(汉明距离阈值 `dedup.simhash_threshold: 4`);③ **标题或摘要 token 集合 Jaccard 相似度 ≥ `dedup.similarity_threshold`(默认 0.8,八成)判为重复**(新增;成员明确规则并已注明在 README)。
- **统计口径如实分段显示**:日报「五、采集状态」与运行输出都显示 发现 → 筛选 → 去重 → 限量 各步数字,不再用「去重后」掩盖中间步骤。

### 每平台上限

GitHub / HN / RSS(全部博客合并)/ HF 各自 `per_platform_cap: 10`,只是上限,允许不足 10 条(硬性约束 #8)。

### 排序策略(可配置,`config.yaml` 的 `report:` 段)

| 配置项 | 默认 | 说明 |
|---|---|---|
| `report.sort_by` | `freshness` | `freshness`(新生代优先,兼顾老项目增量)/ `growth`(24h 增量优先)/ `stars`(star 总数,旧行为) |
| `report.new_days` | 30 | 创建 ≤ 该天数 → 新生代(与 `trend.project.young_days` 一致) |
| `report.old_repo_min_delta_24h` | 50 | 老项目上榜「今日值得关注」的最低 24h Star 增量 |
| `report.old_repo_min_growth_pct` | 0.5 | 或 24h 增长率 ≥ 该值(%)(满足其一即可) |
| `report.recent_push_days` | 7 | 快照不足(首日)时,新生代须近期 push 窗口(天) |
| `report.fetch_summary` | false | `true` 时对入选「解决方案与产品发布」抓正文首段/调 Ollama 生成一行摘要;`false`(默认)只显示库中已有 summary,为空注明理由 |
| `report.summary_max_chars` | 80 | 解决方案一行摘要最大长度 |
| `dedup.similarity_threshold` | 0.8 | 标题/摘要相似度阈值(设为 ≤0 关闭) |

排序只做规则分层(新生代层/老项目增量层/其余),不生成综合评分或隐藏分值,不把不同平台热度合并(硬性约束 #6)。

## 测试

```bash
cd /home/czq/ai_news/code/ai-news-mvp
/home/czq/ai_news/code/.venv/bin/python -m pytest tests/ -v
```

覆盖:趋势规则五态单元测试(新/升温/突增/回落/成熟)、快照连续两日对比与幂等、FTS5 查询与触发器同步、旧库迁移 rebuild、item_sources 跨源映射、runs 记录、主题统计;**阶段04**:日报关键词/长度/五栏目顺序/链接/空栏目不凑数/规则降级选择、Ollama 输入精简/输出校验(项目名匹配/URL 规范化/条数下限)/降级路径(连接错误/超时/非法输出)/think+num_predict 请求参数、飞书幂等(已 sent 跳过)/成功置 sent/退避重试后成功/重试耗尽置 failed/超时置 uncertain 不重试/缺日报与缺关键词拒绝/webhook 不出现在日志与库。**阶段05**:run.py 断网全源失败不崩溃(退出 1、runs.failed、每源 last_error)、单源失败其余照常(退出 0、runs.partial)、Ollama 不可达日报规则降级、飞书不可达发送 failed 正文保留可重发、重复 --send 幂等不再调用 webhook、_platform_level 健康度判定、DailyFileHandler 按日滚动与保留期清理。**阶段04修复(AIQI-7)**:新生代优先排序/老项目增量阈值/首日降级不凑数/上榜理由可解释(ranking)、标题与摘要相似度 ≥0.8 判重(dedup)、处理顺序 筛选→去重→限量(采集器不限量、限量后入库条数)、跨源相似度去重、解决方案摘要 fetch_summary 生效与空摘要注明理由、采集状态分段显示。从 git 仓库副本运行时需设 `AI_NEWS_BRIEF_SRC=/home/czq/ai_news/code/ai-news-brief/src`。

## 阶段05:稳定性与定时执行

### 触发方式(无自建调度)

由平台 autopilot 每日触发单次运行:`cd /home/czq/ai_news/code/ai-news-mvp && /home/czq/ai_news/code/.venv/bin/python run.py --since-days 7`。.env 由 `src/config.py` 自动加载(`AI_NEWS_ENV_PATH` → `/home/czq/ai_news/.env` → 项目根 `.env`),无需手动 source。采集+生成日报+发送为 `run.py --since-days 7 --report --send`。

- **实测采集耗时**:约 50–60s(GitHub ~10s、HN ~37s、RSS/HF ~4s,2026-10-14 实测)。定时时刻建议采集在 08:00、日报发送按「实测耗时 + 10 分钟冗余」推导(如 08:15–08:20)。
- **时区**:autopilot 创建必须显式 `--timezone Asia/Shanghai`,否则默认 UTC(东八区下午 4 点)。
- **幂等/不重复发送**:`daily_reports.sent_status='sent'` 的日报,重复 `--send` 直接跳过(不调用 webhook),可安全每日重复触发。

### 运行日志路径

- 运行统计 JSON:`/home/czq/ai_news/data/ai_news_mvp_run_YYYYMMDD.json`(来源统计/趋势结论/日报生成与发送结果,每次运行覆盖当日文件)。
- 日志:`/home/czq/ai_news/logs/ai_news_mvp_YYYY-MM-DD.log`,按日滚动(文件名带日期,不 rename,Windows 安全),保留 90 天(`retention_days`,超期自动清理)。
- SQLite:`/home/czq/ai_news/data/ai_news_mvp.sqlite3`;`runs` 表记录每次运行(状态/来源统计/错误/趋势),`sources.last_error` 记录按来源的最后错误,`daily_reports` 记录日报正文与发送状态。

### 退出码与 runs 状态语义

- 退出码 0:至少一个来源成功且有去重后条目(即使部分源失败);1:全部来源失败 / 未产出条目 / 日报生成或发送失败。
- `runs.status`:`ok`(全部来源 ok)、`partial`(部分来源失败,如 GitHub 全挂其余正常)、`failed`(全部失败或无条目或 DB 错误)。
- 平台健康度判定 `_platform_level`(run.py):顶层异常、GitHub 全部查询失败、HN 0 条目且有失败、RSS 全部 feed 失败、HF feed 失败且 0 条目 → `failed`;部分失败 → `partial`;其余 → `ok`。错误按来源写进 `runs.errors` 与 `sources.last_error`。

### 故障恢复

- **断网/全源失败**:运行不崩溃,退出 1,`runs.status=failed`,每源错误入 `runs.errors` 与 `sources.last_error`;网络恢复后直接重跑 `run.py`(快照/主题统计按主键幂等,重复无害)。
- **单源失败**:该源错误记录,其余源照常,`runs.status=partial`,退出 0。
- **Ollama 不可达**:日报自动降级为规则标签(标题+规则),不阻塞、不重试,`model_meta.degraded=true` 记录在输出 JSON。
- **飞书不可达**:日报已存在 `daily_reports`(sent_status=failed),恢复后 `run.py --send --date YYYY-MM-DD` 从库重发,不重新采集;已 `sent` 不会重复发送。
- **发送结果不确定(超时)**:置 `uncertain` 停止重发,避免重复推送;人工确认后可重新 `--send`。
- **想要重置库**:删除 `data/ai_news_mvp.sqlite3` 后重跑即可(自动建表)。

### 配置项说明(阶段05 相关)

- `paths.log_dir`(默认 `../../logs` = `/home/czq/ai_news/logs`)、`paths.data_dir`、`paths.db_file`;可用 `AI_NEWS_LOG_DIR` / `AI_NEWS_DATA_DIR` / `AI_NEWS_DB_PATH` 覆盖。
- `rss.feed_max_entries` / `feed_max_bytes`:超大 feed 早期截断(OpenAI feed ~785KB)。
- `github.retries` / `backoff_seconds` / `request_interval_seconds`:查询级重试与限速(search 30/min)。
- `ollama.enabled`:`false` 时强制规则模式,不再尝试调用模型。
- `feishu.max_retries` / `backoff_seconds` / `timeout`:明确失败退避重试次数;结果不确定不重试。

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
