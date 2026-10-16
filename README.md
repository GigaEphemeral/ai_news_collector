# ai-news-mvp · AI 情报采集器

轻量 AI 情报采集项目:从 **GitHub Search API / Hacker News 官方 API / 官方博客 RSS / Hugging Face Daily Papers** 四类来源采集 AI 相关情报,经规则筛选、去重、限量后写入 **SQLite 全表 + FTS5**,提供**规则趋势引擎**(24h/7d/30d 五态,纯规则不评分不调 LLM)与**模板日报 + 可选 Ollama 摘要 + 飞书幂等发送**。单次运行入口 `run.py`,无自建调度,由平台 autopilot 每日触发。

## 快速开始

以下命令假设你已位于项目根的上层目录(本项目部署时该目录名为 `code`);虚拟环境既可放在项目根(`.venv`),也可放在项目根上一级(部署环境为 `../.venv`)。

```bash
# 一次性准备(假设已位于 code/ 下)
cd ai-news-mvp
python3 -m venv .venv                 # 或直接复用上一级已有的虚拟环境
./.venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env   # 填入真实值(见"配置")
```

```bash
# 日常运行(部署环境虚拟环境在上一级时,把 ./.venv 换成 ../.venv)
./.venv/bin/python run.py --since-days 7          # 1) 仅采集
./.venv/bin/python run.py --report                # 2) 采集 + 生成日报(可选 Ollama 摘要,失败自动降级)
./.venv/bin/python run.py --send                  # 3) 从库中发送当日日报(幂等,不重新采集)
./.venv/bin/python run.py --report --send         # 4) 采集 + 生成 + 发送一次
./.venv/bin/python run.py --report --no-summarize # 5) 跳过 Ollama,强制规则模式
```

- `.env` 读取顺序:`AI_NEWS_ENV_PATH` 环境变量 → 项目根上两级目录的 `.env`(`../../.env`) → 项目根 `.env`。程序自动加载,无需手动 source。
- 数据库/日志默认写入 `../../data`、`../../logs`(项目根上两级),可用 `AI_NEWS_DATA_DIR` / `AI_NEWS_LOG_DIR` / `AI_NEWS_DB_PATH` / `--db` 覆盖。
- `--date YYYY-MM-DD` 指定运行日期(默认今天 UTC;快照/主题统计/趋势/日报都归到该日期)。
- **生成与发送分离**:`--report` 只生成并写入 `daily_reports`;`--send` 只读库发送。飞书不可用时先 `--report` 生成,恢复后直接 `--send`,不重新抓取。
- **幂等**:`daily_reports.sent_status='sent'` 的日报不会被重复发送,也不会被重新生成覆盖。
- 单源失败不影响其他源;全部失败或无条目时退出码 1,否则 0。

## 目录结构

```
ai-news-mvp/
  run.py                    # 单次运行入口:采集 + [--report] 日报 + [--send] 飞书发送
  config.yaml               # 全部非秘密配置(源/关键词/限额/超时/趋势阈值/report/ollama/feishu)
  .env.example              # 只列变量名,不存真值(FEISHU_WEBHOOK_URL 等秘密只在 .env)
  requirements.txt          # 轻量依赖清单
  scripts/
    simulate_multi_day.py   # 多日连续运行模拟(受控合成数据,演示 8 天趋势对比)
    demo_report_aiqi7.py    # 日报生成演示(合成数据,验证排序/摘要/分段统计)
  src/
    core/                 # 配置/日志/vendored 复用(src/core/vendor/)
      config.py           # config.yaml + .env 加载
      log.py              # 按日滚动日志(DailyFileHandler,vendored log_config)
      vendor/             # vendored ai-news-brief(MIT,© frankzch,commit 3b3e182)
        rss_fetcher.py    # 原 RSSFetcher(HF daily papers / 多策略抓取)
        lsh_dedup.py      # SimHash 去重
        log_config.py     # DailyFileHandler(按日滚动)
        fetch_stats.py    # 抓取统计(当前未直接使用)
        content_processor.py / config_loader.py   # 上述模块的依赖
    collectors/           # 采集器
      github.py           # GitHub Search API(3 组查询模板,日期动态计算)
      hackernews.py       # HN 官方 Firebase API(topstories + 规则过滤)
      rss.py              # RSS 博客 / HF Daily Papers(vendored rss_fetcher + 早期截断)
    pipeline/             # 规则管线
      filters.py          # 规则过滤(字段完整/时间窗口/关键词,无评分)
      dedup.py            # URL 归一化 + 标题 SimHash + 标题/摘要相似度(阈值可配置)
      ranking.py          # GitHub 候选排序与上榜理由(新生代优先,纯规则不评分)
      tagging.py          # 候选规则标签(平台/热度/趋势状态)
      topic_stats.py      # 主题日统计(compute_topic_stats)
    storage/              # 存储
      schema.py           # SQLite 表结构 + FTS5(ensure_schema)
      db.py               # Database 类(快照/主题统计/运行记录/日报状态)
    summarization/        # 摘要
      ollama_client.py    # Ollama /api/chat 客户端 + 输入精简
      news_summarizer.py  # 「今日值得关注」摘要 + 降级
      solution_summarizer.py  # 「解决方案」一行摘要(fetch_summary)
    reporting/            # 日报
      trend.py            # 规则趋势引擎(五态 + 24h/7d/30d + 证据)
      report.py           # 日报模板(固定栏目/关键词/长度校验/规则降级选择/解决方案摘要)
      feishu.py           # 飞书发送(webhook 脱敏/幂等/退避重试/结果不确定停止)
  tests/                    # pytest 单元测试(119 个)
```

## 数据来源

| 来源 | 采集方式 | 说明 |
|---|---|---|
| GitHub Search API | 3 组查询模板(新项目 `topic:ai created:>{d} stars:>=20` / 新兴 Agent `topic:agents pushed:>{d} stars:>=50` / 活跃 LLM `topic:llm pushed:>{d} stars:>=100`),`{d}` 为程序动态计算的日期,每组 ≤10 条 | 只存轻量字段(快照),不抓 README/源码/评论;跨组按 `full_name` 去重 |
| Hacker News | 官方 Firebase API:`topstories` 前 25 条 + 规则过滤(≥100 分、年龄 2h–3d) | 热度阈值与年龄窗口可配置 |
| 官方博客 RSS | OpenAI / Hugging Face / Google DeepMind / NVIDIA Developer 四个 feed,`feedparser`(超大 feed 早期截断,如 OpenAI ~785KB 截断为前 30 条) | 源列表可配置 |
| Hugging Face Daily Papers | HF Papers API,按(upvotes+stars)保留前 5 篇 | 数量由 `fetching.hf_daily_top_n` 决定 |

采集走官方 API/RSS,不绕过验证码/登录/限流/封禁;不采集视频;不接入 X/Reddit/YouTube/Bilibili。

## 处理流程

```
各源采集 → 规则筛选(字段完整/时间窗/关键词/热度,不限量)
        → 跨源去重(URL 归一化 + 标题 SimHash + 标题/摘要相似度)
        → 每平台限量(per_platform_cap,默认 10)
        → 趋势计算(24h/7d/30d 五态,纯规则)→ 日报生成(模板 + 可选 Ollama 摘要)→ 飞书发送(幂等)
```

- **筛选**(`src/filters.py`):字段完整、时间窗口、AI 关键词命中,不做评分。
- **去重**(`src/dedup.py`):① URL 归一化(去跟踪参数/默认端口/www);② 标题 SimHash(汉明距离 ≤ `dedup.simhash_threshold`,默认 4);③ 标题或摘要 token 集合 Jaccard 相似度 ≥ `dedup.similarity_threshold`(默认 0.8)判为重复。
- **入库归并**(阶段03,`src/storage/db.py`):`items` 唯一键 `UNIQUE(platform, native_id)`(自然键:GitHub `full_name` / HN `hn_id` / RSS `(source_id, guid)` 无 guid 回退归一化 url / HF `arxiv_id` 回退 url);入库先按自然键精确匹配,未命中再按内容指纹 `canonical_key`(标题归一化 SimHash)近 7 天窗口模糊匹配(阈值 4),命中则合并——新来源挂 `item_sources`,标题/摘要择优,`first_seen_at` 保留最早,不拒绝;近似标题(指纹距离 > 4)不误并。
- **限量发生在去重之后**:采集器内部不再限量,先统一跨源去重再按每平台上限截断,避免跨源重复内容各自占掉限量名额。
- **每平台上限** `per_platform_cap: 10`:只是上限,允许不足 10 条。
- **趋势**(`src/trend.py`):对项目(基于快照)与主题(基于日统计)输出五态,见下节。
- **摘要**(`src/summarization/`):可选,只对入选「今日值得关注」的 Top 8 候选调 Ollama;失败自动降级为规则标签。
- **日报**(`src/report.py`):固定五栏目模板 + 长度/关键词校验 + 幂等发送。

## 配置

全部非秘密配置在 `config.yaml`,秘密(`GITHUB_PAT` / `FEISHU_WEBHOOK_URL` / `FEISHU_WEBHOOK_KEYWORD`)只放 `.env`。以下为所有可配置项;除标注"环境变量覆盖"的外,均可直接改 `config.yaml` 后重启生效。

### paths / app / fetching

| 配置项 | 默认 | 作用 | 可配置 |
|---|---|---|---|
| `app.name` | `ai-news-mvp` | 应用名(日志/库名前缀) | 是 |
| `app.since_days_default` | 7 | `run.py` 默认采集窗口(`--since-days` 可覆盖) | 是 |
| `paths.data_dir` | `../../data` | 数据目录(项目根上两级);`AI_NEWS_DATA_DIR` 可覆盖 | 是 |
| `paths.log_dir` | `../../logs` | 日志目录;`AI_NEWS_LOG_DIR` 可覆盖 | 是 |
| `paths.db_file` | `ai_news_mvp.sqlite3` | SQLite 文件名;`AI_NEWS_DB_PATH` / `--db` 可覆盖 | 是 |
| `fetching.network_timeout` | 90 | 复用 rss_fetcher 的网络超时(秒) | 是 |
| `fetching.hf_daily_top_n` | 5 | HF Daily Papers 按(upvotes+stars)排名保留前 N 篇 | 是 |

### github / hackernews / rss / hf_papers

| 配置项 | 默认 | 作用 |
|---|---|---|
| `github.api_base` | `https://api.github.com` | GitHub API 地址 |
| `github.per_page` | 10 | 每组查询每页上限 |
| `github.per_platform_cap` | 10 | 每平台候选上限(去重后截断) |
| `github.sort` / `github.order` | `stars` / `desc` | 搜索排序 |
| `github.timeout` | 30 | 单请求超时(秒) |
| `github.retries` | 2 | 5xx/网络错误退避重试次数(403/429 不硬闯) |
| `github.backoff_seconds` | 2 | 退避基数(指数:2s,4s) |
| `github.request_interval_seconds` | 1.2 | 查询间隔(search API 30/min) |
| `github.queries` | 3 组模板 | 查询模板列表,`{d}` 为动态日期 |
| `hackernews.api_base` | Firebase v0 | HN 官方 API 地址 |
| `hackernews.top_n` | 25 | topstories 取前 N 条 |
| `hackernews.min_points` | 100 | 热度阈值(score) |
| `hackernews.min_age_hours` | 2 | 过滤过新帖子(热度未聚集) |
| `hackernews.max_age_days` | 3 | 帖子最大年龄 |
| `hackernews.per_platform_cap` | 10 | HN 每平台候选上限 |
| `hackernews.timeout` | 30 | 单请求超时(秒) |
| `hackernews.retries` | 2 | 失败退避重试次数 |
| `hackernews.backoff_seconds` | 2 | 退避基数 |
| `rss.max_age_days` | 7 | 普通博客条目最大年龄 |
| `rss.per_platform_cap` | 10 | RSS 平台(全部博客合并)候选上限 |
| `rss.feed_max_entries` | 30 | 超大 feed 早期截断:最多保留前 N 条 |
| `rss.feed_max_bytes` | 262144 | 超过该字节数触发截断(OpenAI feed ~785KB) |
| `rss.timeout` | 60 | 单请求超时(秒) |
| `rss.retries` | 2 | 失败退避重试次数 |
| `rss.backoff_seconds` | 2 | 退避基数 |
| `rss.sources` | 4 个官方博客 | feed 列表(name/url/category),可增删 |
| `hf_papers.url` | HF API 地址 | HF 数据源地址 |
| `hf_papers.name` | `HF Daily Papers` | 数据源名称 |
| `hf_papers.per_platform_cap` | 10 | HF 候选上限 |
| `hf_papers.max_age_days` | 14 | HF 按周批,放宽年龄窗口 |

### dedup / limits / keywords / topics

| 配置项 | 默认 | 作用 |
|---|---|---|
| `dedup.simhash_threshold` | 4 | 标题 SimHash 汉明距离阈值(越小越严格) |
| `dedup.similarity_threshold` | 0.8 | 标题/摘要 Jaccard 相似度 ≥ 该值判重(设为 ≤0 关闭) |
| `limits.per_platform_cap` | 10 | 每平台每日候选上限(硬性约束) |
| `keywords.ai` | 24 个词 | 规则初筛关键词(仅 HN/RSS/HF 标题+摘要命中判断,不做评分) |
| `topics.max_per_item` | 3 | 每条目最多取 N 个主题(GitHub 取 extra.topics 前 N) |

### trend(规则趋势引擎阈值)

| 配置项 | 默认 | 作用 |
|---|---|---|
| `trend.windows.24h` | 2 | 24h 窗口所需最小历史天数(不足标"窗口不足") |
| `trend.windows.7d` | 7 | 7d 窗口所需最小历史天数 |
| `trend.windows.30d` | 30 | 30d 窗口所需最小历史天数 |
| `trend.project.young_days` | 30 | 创建 ≤ 30 天 → 新项目(新鲜度证据) |
| `trend.project.sudden_growth_ratio` | 3.0 | 24h 增量 ≥ 前 7d 日均 × 3 → 突然增长 |
| `trend.project.warming_min_days` | 3 | 连续 N 天正增长且趋势不减 → 持续升温 |
| `trend.project.warming_min_growth_pct` | 0.5 | 累计增长率 < 该值(%)→ 幅度不足标"尚待观察" |
| `trend.project.maturity_stars` | 50000 | Star 总数 ≥ 该值 → 成熟期候选 |
| `trend.project.maturity_growth_pct` | 0.05 | 近 7 天日均增长率 ≤ 该值(%)→ 稳定/成熟 |
| `trend.project.decline_negative_days` | 2 | 连续 N 天负增长 → 热度回落 |
| `trend.project.stagnation_days` | 7 | 连续 N 日入选且近 N 日增量≈0 → 降权 |
| `trend.project.stagnation_max_delta_pct` | 0.05 | 近 N 天总增量 ≤ 该比例(%)视为无新变化 |
| `trend.topic.sudden_growth_ratio` | 2.0 | 当日条目数 ≥ 前 N 日均值 × 2 → 突然增长 |
| `trend.topic.warming_min_days` | 3 | 连续 N 天增量且 ≥1 天多来源 → 持续升温 |
| `trend.topic.maturity_min_count` | 3 | 近 7 天日均条目数 ≥ 该值 → 成熟期候选 |
| `trend.topic.decline_negative_days` | 2 | 连续 N 天条目数递减 → 热度回落 |

### report(日报)

| 配置项 | 默认 | 作用 |
|---|---|---|
| `report.required_keyword` | `每日ai情报` | 飞书安全关键词(发送文本必须包含;`FEISHU_WEBHOOK_KEYWORD` 可覆盖) |
| `report.max_chars` | 6000 | 日报文本长度上限(超长自动裁剪) |
| `report.highlights_cap` | 8 | 入选 Top N(交给 Ollama 或规则挑选) |
| `report.max_highlights` | 3 | 「今日值得关注」最多条数 |
| `report.opensource_max` | 5 | 「开源项目与工程实践」最多条数 |
| `report.solutions_max` | 5 | 「解决方案与产品发布」最多条数 |
| `report.trends_projects_max` | 3 | 「趋势观察」项目最多条数 |
| `report.trends_topics_max` | 3 | 「趋势观察」主题最多条数 |
| `report.sort_by` | `freshness` | 排序策略:`freshness`(新生代优先,兼顾老项目增量)/ `growth`(24h 增量优先)/ `stars`(star 总数) |
| `report.new_days` | 30 | 创建 ≤ 该天数 → 新生代(与 `trend.project.young_days` 一致) |
| `report.old_repo_min_delta_24h` | 50 | 老项目上榜「今日值得关注」的最低 24h Star 增量 |
| `report.old_repo_min_growth_pct` | 0.5 | 或 24h 增长率 ≥ 该值(%)(满足其一即可) |
| `report.recent_push_days` | 7 | 快照不足(首日)时,新生代须近期 push 窗口(天) |
| `report.fetch_summary` | false | `true` 时对入选「解决方案与产品发布」抓正文首段/调 Ollama 生成一行摘要;`false` 只显示库中已有 summary,为空注明理由 |
| `report.summary_max_chars` | 80 | 「解决方案与产品发布」一行摘要最大长度 |

### ollama / feishu

| 配置项 | 默认 | 作用 |
|---|---|---|
| `ollama.base_url` | `http://192.168.0.103:11434` | Ollama 服务地址(局域网) |
| `ollama.model` | `Qwen3.5:9B` | 摘要模型 |
| `ollama.enabled` | true | `false` 时强制规则模式,不再尝试调用模型 |
| `ollama.timeout` | 180 | 单次调用超时(秒) |
| `ollama.think` | false | 关闭 thinking |
| `ollama.num_predict` | 2048 | 生成预算上限 |
| `ollama.temperature` | 0.2 | 采样温度 |
| `ollama.min_output_items` | 3 | 模型有效输出下限(低于则整体降级) |
| `ollama.max_output_items` | 5 | 模型输出条数上限 |
| `feishu.timeout` | 30 | 单次 POST 超时(秒) |
| `feishu.max_retries` | 3 | 明确失败时的指数退避重试次数 |
| `feishu.backoff_seconds` | 3 | 退避基数(3s,6s) |

**排序只做规则分层**(新生代层/老项目增量层/其余),不生成综合评分或隐藏分值,不把不同平台热度合并。

## 依赖与约束

- **复用来源**:复用原项目 [`frankzch/ai-news-brief`](https://github.com/frankzch/ai-news-brief)(MIT,commit `3b3e182`)的 `rss_fetcher`(HF daily papers / parse_feed)、`lsh_dedup`、`log_config`、`content_processor` 等,已 **vendoring** 到 `src/core/vendor/`(逐字拷贝,仅改包内相对 import,保留版权头与 LICENSE);仓库自包含,不依赖外部 `ai-news-brief` 目录。
- **Python 版本**:开发环境实测 3.14.4;无锁死版本,`requirements.txt` 为下限约束。
- **依赖清单**(全部轻量,无重依赖):`requests`、`httpx[socks]`、`feedparser`、`curl_cffi`、`simhash`、`pyyaml`、`python-dotenv`、`beautifulsoup4`、`trafilatura`。
- **约束**:不安装 torch / playwright / psycopg2 / PostgreSQL;不引入 Redis/Kafka/向量库/复杂多智能体架构;模型调用可选(默认规则优先)。

## 数据库与表结构

SQLite 全表 + FTS5 全文索引(连接时 `CREATE TABLE IF NOT EXISTS` 自动补表,旧库自动迁移并对存量 items 执行一次 FTS rebuild)。

| 表 | 作用 |
|---|---|
| `sources` | 来源配置/启用/最后成功/错误(`last_error`) |
| `items` | 情报条目(唯一键 `UNIQUE(platform, native_id)`;url 为普通列;`canonical_key` 标题指纹供跨源归并) |
| `item_sources` | 同一事件多来源映射 |
| `project_snapshots` | GitHub 项目每日快照(`repo, date` 主键:stars/forks/pushed_at/created_at/description/language/topics≤5/fetched_at) |
| `topic_daily_stats` | 每天每主题条目数/来源数/代表情报 |
| `runs` | 每次运行开始/结束/状态(ok/partial/failed)/错误/趋势 |
| `daily_reports` | 日报正文/生成时间/发送状态(sent/failed/uncertain/pending)/retry_count/last_error |
| `items_fts` | FTS5 external-content 全文索引 + 触发器(items_ai/ad/au) |

FTS 查询:`MATCH 'llm OR agent'` 语法,按 bm25 排序。

## 趋势引擎(src/trend.py,纯规则、不评分、不调 LLM)

对**项目**(基于 `project_snapshots`)与**主题**(基于 `topic_daily_stats`)各输出五态:

| 状态 | 项目规则 | 主题规则 |
|---|---|---|
| 新出现 | 仅 1 个快照日 / 昨日断档 / 变化不足以定论 | 仅 1 天统计 / 昨日断档 |
| 持续升温 | 连续 ≥3 天正增长、趋势不减、窗口累计增长率 ≥ 0.5% | 连续 ≥3 天增量且 ≥1 天多来源 |
| 突然增长 | 24h 增量 ≥ 前序日均增量 × 3 | 当日增量 ≥ 前序日均增量 × 2 |
| 进入成熟期 | Star ≥ 5 万且 7 日均增长 ≤ 0.05%;或连续 ≥7 日入选且近 7 日增量≈0 | 7 天日均 ≥ 3 条且近 3 天持平 |
| 热度回落 | 连续 ≥2 天负增长;或近期均值低于前期且当日无正增长 | 连续 ≥2 天递减;或近期低于前期 |

- **三个时间尺度**:24h(需昨日+今日两个连续快照日)、7d(需 7 个观察日)、30d(需 30 个观察日);不足的窗口明确输出 `窗口不足(仅 N 天,需 M 天)`,不臆造。
- **首次运行只建基线**:库中无更早日快照时全部标 `初步判断` + `系统首日运行` 说明。
- **证据强制**:每个结论带来源(表名)、数字、时间窗;证据不足写"新出现,尚待观察",禁止编造。
- **重复降权**:连续 ≥7 日入选且近 7 日 Star 增量≈0(±≤0.05%)标 `downgraded: true`;真实涨跌不算降权。
- 阈值全部在 `config.yaml` 的 `trend:` 段;输出顺序为固定状态分组(展示用,非评分排序)。

## 日报与飞书

### 日报模板(src/report.py)

固定五栏目(栏目允许为空、不凑数,每条附原文链接,推测与事实分开):

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

- **「今日值得关注」排序**(纯规则、不评分):新生代(创建 ≤ `report.new_days` 天)优先;老项目仅当 24h Star 增量 ≥ `report.old_repo_min_delta_24h` 或 24h 增长率 ≥ `report.old_repo_min_growth_pct` 才允许上榜;快照不足 2 天(首日)时仅新生代且近期 push(≤ `report.recent_push_days`)入选并标注「初步判断」;上榜理由基于库中真实字段(「今日新出现」「新生代(创建 N 天)」「24h Star +N(前一日 M)」等),Ollama 摘要路径同样由代码覆盖为库中事实。
- **「解决方案与产品发布」一行摘要**:每条带一行精简摘要(≤ `report.summary_max_chars` 字);来源优先级:库中已有 summary → 为空且 `report.fetch_summary: true` 时抓正文首段或调 Ollama 生成一句话摘要 → 仍为空注明理由(如「原文未提供摘要」);`fetch_summary` 默认 `false`(不额外烧 token)。
- **长度与关键词校验**:`validate_report()` 检查「每日ai情报」关键词与 `report.max_chars`(6000),超长自动裁剪。
- **生成与发送分离**:`build_report()` 只写 `daily_reports`;`run.py --send` 从库重发,不重新抓取。

### 可选 Ollama 摘要(src/summarization/news_summarizer.py)

- 只对入选 Top `report.highlights_cap`(8)调用 `ollama.base_url` 的 `ollama.model`(Qwen3.5:9B);
- 输入精简 JSON:`name/description/stars/stars_24h/created_days/topics`(description ≤120 字符、topics ≤5),不传 API 原始 JSON;
- **关闭 thinking(`think: false`)并给足生成预算(`num_predict: 2048`)**,`format: json` 强制 JSON 输出;
- 输出 3-5 条,经代码校验:项目名必须与输入匹配、URL 取输入规范化值(防编造链接)、简介/依据非空;
- 超时/不可达/输出非法 → **降级**:原标题 + 规则标签,不阻塞日报、不反复重试(单次调用)。

### 飞书发送(src/reporting/feishu.py)

- webhook 只从 `.env` 的 `FEISHU_WEBHOOK_URL` 读取;**严禁打印/外泄**:所有日志、异常文本、`daily_reports.last_error` 都把 webhook 脱敏为 `<redacted>`;
- **幂等**:发送前查 `daily_reports.sent_status == 'sent'` → 跳过;只有飞书确认成功(`code=0`)才置 `sent`;
- 明确失败(HTTP 返回非 0)→ 指数退避重试,有限次(`feishu.max_retries: 3`);结果不确定(超时/响应无法解析)→ 置 `uncertain` 立即停止,不无限重发;
- 发送文本必须含安全关键词,缺失拒绝发送。

## 测试

```bash
./.venv/bin/python -m pytest tests/ -q   # 全量测试,当前 119 个
```

覆盖:趋势五态、快照连续两日对比与幂等、FTS5 查询与触发器同步、旧库迁移 rebuild、item_sources 跨源映射、runs 记录、主题统计、日报关键词/长度/栏目顺序/链接/空栏目不凑数/规则降级选择、Ollama 输入精简/输出校验/降级路径/think+num_predict 请求参数、飞书幂等/退避重试/结果不确定/缺日报与缺关键词拒绝/webhook 脱敏、run.py 断网与单源失败不崩溃、日志按日滚动与保留期清理、排序与相似度去重。

多日连续运行演示(合成数据,8 天趋势):`./.venv/bin/python scripts/simulate_multi_day.py`。

## 定时与自动化

本项目**无自建调度**,每日触发方式二选一(或并用):

### 平台 autopilot(推荐)

```bash
# 1) 建 autopilot(采集环节)
multica autopilot create \
  --title "每日AI情报-采集" \
  --description "<采集任务提示词>" \
  --agent <agent名或ID> \
  --mode create_issue \
  --issue-title-template "每日AI情报 采集 {{date}}" \
  --project <project-id>          # 可选,归属项目

# 2) 加定时触发 —— 必须显式 --timezone,否则默认 UTC(会偏 8 小时)
multica autopilot trigger-add <autopilot-id> \
  --kind schedule --cron "30 21 * * *" --timezone Asia/Shanghai --label "每日 21:30 采集"

# 3) 查看/验证(trigger 的 next_run_at 应为东八区目标时刻)
multica autopilot list
multica autopilot get <autopilot-id>
multica autopilot trigger-list <autopilot-id>
multica autopilot runs <autopilot-id>       # 执行历史

# 4) 改时间:trigger-delete 旧触发 + trigger-add 新触发(update 只能改 agent/描述/状态)
multica autopilot trigger-delete <autopilot-id> <trigger-id>
multica autopilot trigger-add <autopilot-id> --kind schedule --cron "0 8 * * *" --timezone Asia/Shanghai
```

autopilot 每日触发的实际命令为 `run.py --since-days 7`(采集)与 `run.py --report --send`(日报发送);`.env` 由 `src/core/config.py` 自动加载,无需手动 source。程序幂等,重复跑安全。

本项目已配置两个 autopilot(2026-10-14 创建,配置正确,次日生效,无需重复创建):

| 名称 | 调度(cron,Asia/Shanghai) | 作用 |
|---|---|---|
| 每日AI情报-采集 | `30 21 * * *`(每天 21:30) | 采集四源 → 写 SQLite/JSON → 汇报统计 |
| 每日AI情报-日报发送 | `45 21 * * *`(每天 21:45) | 幂等生成+发送飞书日报 |

顺序合理(先采后发);验证生效看它们的 `runs` 历史与 issue 即可。

### 本地 cron 替代

```bash
crontab -e
# 每天 08:00 采集+生成+发送全链路
0 8 * * * cd <项目根> && <项目根>/.venv/bin/python run.py --since-days 7 --report --send >> <项目根上两级>/logs/cron.log 2>&1
# 或拆分两步:21:30 只采集,21:45 生成并发送(与平台 autopilot 相同的节奏)
30 21 * * * cd <项目根> && <项目根>/.venv/bin/python run.py --since-days 7 >> <项目根上两级>/logs/cron.log 2>&1
45 21 * * * cd <项目根> && <项目根>/.venv/bin/python run.py --report --send --date $(date +\%Y-\%m-\%d) >> <项目根上两级>/logs/cron.log 2>&1
```

(把 `<项目根>` 换成实际路径;示例中的 `.venv` 在项目根内,部署环境下它在项目根上一级,相应写 `<项目根>/.venv` 为 `<项目根>/../.venv`。日志文件按日滚动,保留 90 天。)

### 时区与时刻建议

- **时区警告**:autopilot 创建必须显式 `--timezone Asia/Shanghai`,否则默认 UTC(东八区下午 4 点)。
- **实测采集耗时**:约 60–70s(2026-10-14 实测单次 68s,见「开发与运行统计」);定时时刻建议采集在 08:00,日报发送按「实测耗时 + 10 分钟冗余」推导。

### 运行产物路径(相对项目根)

- 运行统计 JSON:`../../data/ai_news_mvp_run_YYYYMMDD.json`(来源统计/趋势结论/日报生成与发送结果,每次运行覆盖当日文件)。
- 日志:`../../logs/ai_news_mvp_YYYY-MM-DD.log`,按日滚动(文件名带日期,不 rename,Windows 安全),保留 90 天(`retention_days`,超期自动清理)。
- SQLite:`../../data/ai_news_mvp.sqlite3`;`runs` 表记录每次运行,`sources.last_error` 记录按来源的最后错误,`daily_reports` 记录日报正文与发送状态。

### 退出码与 runs 状态

- 退出码 0:至少一个来源成功且有去重后条目(即使部分源失败);1:全部来源失败 / 未产出条目 / 日报生成或发送失败。
- `runs.status`:`ok`(全部来源 ok)、`partial`(部分来源失败)、`failed`(全部失败或无条目或 DB 错误)。
- 健康度判定 `_platform_level`(run.py):顶层异常、GitHub 全部查询失败、HN 0 条目且有失败、RSS 全部 feed 失败、HF feed 失败且 0 条目 → `failed`;部分失败 → `partial`;其余 → `ok`。错误按来源写进 `runs.errors` 与 `sources.last_error`。

## 故障恢复

- **断网/全源失败**:运行不崩溃,退出 1,`runs.status=failed`,每源错误入 `runs.errors` 与 `sources.last_error`;网络恢复后直接重跑 `run.py`(快照/主题统计按主键幂等,重复无害)。
- **单源失败**:该源错误记录,其余源照常,`runs.status=partial`,退出 0。
- **Ollama 不可达**:日报自动降级为规则标签(标题+规则),不阻塞、不重试,`model_meta.degraded=true` 记录在输出 JSON。
- **飞书不可达**:日报已存在 `daily_reports`(sent_status=failed),恢复后 `run.py --send --date YYYY-MM-DD` 从库重发,不重新采集;已 `sent` 不会重复发送。
- **发送结果不确定(超时)**:置 `uncertain` 停止重发,避免重复推送;人工确认后可重新 `--send`。
- **限流**:GitHub 403/429 不硬闯,记录并跳过该查询;HN 单条失败只计数。
- **vendored 路径**:复用模块已在 `src/core/vendor/`(MIT © frankzch),仓库自包含,不依赖外部目录。
- **想要重置库**:删除 `../../data/ai_news_mvp.sqlite3` 后重跑即可(自动建表)。

## 开发与运行统计(复盘用)

> 本节数字来自真实运行日志(`logs/`)、运行统计 JSON(`data/ai_news_mvp_run_*.json`)与代码参数;**环境相关,仅供参考**。未精确统计的项均标注口径。

### 开发环境与工具

- **Python 版本**:3.14.4(开发环境虚拟环境实测;无锁死版本,`requirements.txt` 为下限约束)。
- **关键依赖**:`requests`、`httpx[socks]`、`feedparser`、`curl_cffi`、`simhash`、`pyyaml`、`python-dotenv`、`beautifulsoup4`、`trafilatura`。
- **复用来源**:[frankzch/ai-news-brief](https://github.com/frankzch/ai-news-brief)(MIT,commit `3b3e182`)的 `rss_fetcher`、`lsh_dedup`、`log_config`、`content_processor` 已 vendoring 到 `src/core/vendor/`(阶段02 起仓库自包含)。
- **开发 agent 模型**:deepseek-v4-flash(平台编码 agent)。

### 模型使用

- **本地模型**:Ollama `Qwen3.5:9B`(`ollama.base_url` 指向局域网 Windows 开发台上的 Ollama)。
- **参数**:`think: false`(关闭 thinking,该模型默认开 thinking)、`num_predict: 2048`(给足生成预算)、`format: json`(强制 JSON 输出)、`temperature: 0.2`。
- **调用场景**:仅对入选「今日值得关注」的 Top `report.highlights_cap`(8)候选做一次性批量摘要;单次尝试,不重试。
- **失败降级**:超时 / 服务不可达 / 输出非法(项目名不匹配、URL 非法、条数不足)→ 自动降级为规则标签(原标题 + 规则依据),不阻塞日报、不烧 token。当日日志实测记录过「摘要成功:5/8 条」与「服务不可达,降级为规则标签」两种路径。

### 典型耗时(2026-10-14 实测,环境相关,仅供参考)

- **完整采集 ≈ 68s**:GitHub 12.6s + HN 41.6s + RSS 13.5s + HF 约 2s(来源为当日运行统计 JSON 的 `time_s` 字段与运行日志;总时长含入库/快照/统计)。
- **摘要环节视 Ollama 状态**:服务可达时一次成功调用(输出 5/8 条)约数秒~数十秒;不可达时立即降级,不增加耗时。
- **定时建议**:采集 08:00,发送按「实测耗时 + 10 分钟冗余」推导。

### Token 用量

- **精确数字未统计**:运行日志与代码不记录每次调用的 token 明细,以下为**估算口径**,非实测值。
- **每次主摘要调用输入**:Top 8 精简 JSON(`name/description/stars/stars_24h/created_days/topics`,description 截断 ≤120 字符、topics ≤5),约 8 条 × 150–200 字符 ≈ 1.2–1.6K 字符(中英混合,估算约 0.8–1.5K tokens)。
- **输出上限**:`num_predict: 2048`;模型输出 3–5 条,每条 intro ≤50 字 + reason ≤80 字,实际输出远小于上限。
- **每轮日报估算**:1 次主摘要调用(输入约 1.5K 字符 + 输出 ≤2K tokens)+ 提示词;「解决方案」摘要仅在 `report.fetch_summary: true` 时触发(默认 false,不调)。估算单轮 Ollama 摘要合计约 3–6K tokens,未精确计量。
- **规则模式**(`ollama.enabled: false` 或 Ollama 不可达降级):token 用量为 0。
