# AI 情报智能体开发方案：Hermes + 飞书 + Pi/Ollama

建议采用先做轻量、可运行的 MVP，再逐步增强趋势分析的路线。由 Windows 10 上的 MultiCA 负责开发和修改代码，Hermes Agent 负责日常调度与执行，已经配置好的飞书机器人负责发送日报；Linux 笔记本上的 Pi Agent 和 Windows 上的 Ollama `qwen3.5:9B` 则作为可选的本地分析能力。

第一阶段不需要做成多智能体协作系统，也不需要先部署向量数据库。核心是：少量高价值情报源、低成本采集、长期历史记录、趋势识别、每天一条飞书日报。

## 一、整体架构

Hermes Agent（Linux）

每日定时触发 · 执行采集流程 · 处理异常 · 调用日报发送

情报采集器

GitHub API、Hacker News API、官方 RSS、Hugging Face

本地情报库

SQLite、历史快照、去重、主题变化、日报发送记录

轻量趋势引擎

新项目 · 持续升温 · 突然增长 · 进入成熟期 · 热度回落

按需调用模型

优先规则过滤；必要时调用 Ollama Qwen 3.5 9B；失败时降级

飞书机器人

每天一条中文日报 · 原文链接 · 趋势证据 · 运行状态

Windows 10 上的 MultiCA 是开发工作台，不是必须持续运行的生产服务。这样可以避免笔记本休眠、Windows 更新或开发环境关闭影响日报。

有一点需要先明确：你的 Ollama 位于 Windows 笔记本，但 Hermes 主要部署在 Linux 上。Linux 能否直接调用它，取决于 Windows 上 Ollama 的监听地址、网络可达性和防火墙配置。如果暂时不通，系统应先使用规则过滤和模板生成日报，而不是让每日任务失败。

## 二、各组件的职责边界

建议不要让 Pi、Hermes、MultiCA 同时承担采集、分析和调度。每个组件只负责一类任务。

| 组件                         | 定位        | 具体职责                       |
| -------------------------- | --------- | -------------------------- |
| MultiCA（Windows）           | 开发工作台     | 管理开发任务、调用 Pi 修改代码、检查测试结果   |
| Pi Agent（Linux）            | 代码开发者     | 编写采集器、数据库、趋势引擎、日报模块，修复测试问题 |
| Hermes Agent（指定一台机器）       | 生产调度者     | 定时触发脚本、检查结果、处理失败、调用飞书机器人   |
| Ollama Qwen3.5:9B（Windows） | 可选的本地分析模型 | 对筛选后的情报做归类、中文摘要和主题合并       |
| SQLite                     | 长期记忆      | 保存情报历史、项目快照、主题变化和日报发送状态    |
| 飞书机器人                      | 最终输出      | 每天发送一条日报，不逐条推送情报           |

生产运行只指定一个 Hermes 实例。 不要让 Windows 和 Linux 上的 Hermes 同时发送同一份日报。

部署位置建议：

- 开发： Windows 10 上使用 MultiCA，交由 Pi 编码。
- 生产： 优先使用 Linux 上的 Hermes，前提是它能稳定运行并且已配置好飞书机器人。
- 模型： 优先尝试通过局域网调用 Windows Ollama；若暂时不可用，日报仍然可以由规则引擎生成。
- 数据： SQLite 与每日任务脚本放在实际运行采集任务的机器上，避免两台机器同时写同一个数据库。

如果现有飞书机器人只配置在 Windows 上，首期就先让 Windows 上对应的 Hermes 执行生产任务，不要为了架构理想化而先迁移机器人配置。

## 三、首期只接入四类情报节点

不追求源数量，先把最有价值的几个节点跑通。

[How to download from GitHub: 3 ways for beginners | Zapier](https://images.openai.com/static-rsc-4/yi_jM58ZCVev5JVcN-2XSW_reKedY5aEzkoswskghS_tp0ckQAsKaHtQKPzSQI6powMzL40Sy9s0StDGJkfHoLxc4d-NsTbGLM9hoDcUjMxqLLF8fmd7UNL0K21xUPjSJgRVTEket7luBSmVQoByjUalrBKURLkRM_oGbjtekbI?purpose=inline)

1\. GitHub：开源项目与工程实践

第一优先级

- GitHub REST API：搜索近期更新的 AI Agent、推理、RAG、模型部署、开发工具项目。
- GitHub Releases API：关注重要项目的新版本和重大功能。
- 首期不依赖 Trending 页面爬取，避免把页面结构变化变成维护负担。
- 按仓库的 stars、forks、最近更新时间及增量快照观察项目动向；不生成综合评分。

[Sniffnet made it to Hacker News front page!](https://images.openai.com/static-rsc-4/RSumMdz0Wm02Q-0uS7lRzwUiOycVGCOQi0MR5g07XNTJOB_PEsvlYgN-UhLsiXA4_fRvxyHJcEQ3eAlsx8nBvMOiW93ZBhSvioFQ_GpQikAZvzOnGf79A_5YELR0tM5EXjXqkFGbkU8QBpbrlDdmfmtzF7rFYUxAwn7UClUF0P0?purpose=inline)

2\. Hacker News：业界讨论与实践反馈

- 优先使用官方 API：[Hacker News API 文档](https://github.com/HackerNews/API)。
- 关注 AI 工程、Agent、推理优化、模型服务、开源基础设施相关讨论。
- 通过讨论热度、评论数量和主题相关性筛选，保留原帖链接。

[How we OCR'ed 30,000 papers using Codex, open OCR models and Jobs](https://images.openai.com/static-rsc-4/kQuMex0ZoPiUG_vWUQnVGuqeXf1NXQ8-nAgQoJpxHjQsJMg2RXktiSaHQy2K7Ojt1UCatzfTakUmCMparXQtUEP6y93cLkHy05hMYHoazSYTmTpnCz41jI7BcQPj_9SDs6Df9AS4cgX_HLVhwn8n5L3pLmBigkwg9ZB_BIsWNRc?purpose=inline)

3\. Hugging Face：论文与模型动态

- 优先用官方公开接口或 RSS 获取论文、模型和项目元数据。
- 重点筛选对 Agent、推理、上下文、模型量化、训练和部署有直接工程价值的成果。
- 首期只读取标题、摘要、作者、时间和链接，不下载论文 PDF 或模型权重。

[@prithivMLmods on Hugging Face: "OpenAI, Google, Hugging Face, and Anthropic have released guides and courses…"](https://images.openai.com/static-rsc-4/LPPxdZ9W5pb75aCFKkdrNuhzLaCxTEV_vYWnMBaF8yTmmxO27Fvhg2VzkCmMtzXQLOVtXOzgqPdn4G660XJLyzzAqiLkss58xSAZKQ3zpcFmPlHJNNdxqyz7_w9VMsssp-ALFWuxI8sOjjp-jDBeTDBSlkHRZHBjbaqY3YHBR3s?purpose=inline)

4\. 官方技术博客 RSS：解决方案与产品发布

- 首期先选 3–5 个与你关注的技术栈直接相关的官方博客。
- 通过 RSS 获取标题、摘要、发布时间和原文链接。
- 只有 RSS 内容不足时，才考虑读取允许公开访问的文章正文。

暂时不接 X、Reddit、YouTube、Bilibili 和大规模 RSS 聚合。这样既降低采集维护成本，也减少模型调用和外部服务账号的风险。

每个平台每天最多保留 Top 10 候选；这只是上限，并不意味着日报必须凑满 10 条。不同平台出现同一项目或新闻时，合并为一条，保留多个来源链接。

## 四、每天的自动运行流程

每日流程应由 Python 项目完成主要工作，Hermes 负责触发和监督，不把整个采集流程都交给 LLM 临场决定。

\#chatgpt-mermaid-\_r_11gf\_{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;fill:rgb(13, 13, 13);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-\_r_11gf\_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_11gf\_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_11gf\_ .error-icon{fill:rgb(243, 243, 243);}#chatgpt-mermaid-\_r_11gf\_ .error-text{fill:rgb(13, 13, 13);stroke:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_11gf\_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-\_r_11gf\_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-\_r_11gf\_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-\_r_11gf\_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-\_r_11gf\_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-\_r_11gf\_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-\_r_11gf\_ .marker{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_11gf\_ .marker.cross{stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_11gf\_ svg{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;}#chatgpt-mermaid-\_r_11gf\_ p{margin:0;}#chatgpt-mermaid-\_r_11gf\_ .label{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_11gf\_ .cluster-label text{fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_11gf\_ .cluster-label span{color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_11gf\_ .cluster-label span p{background-color:transparent;}#chatgpt-mermaid-\_r_11gf\_ .label text,#chatgpt-mermaid-\_r_11gf\_ span{fill:rgb(13, 13, 13);color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_11gf\_ .node rect,#chatgpt-mermaid-\_r_11gf\_ .node circle,#chatgpt-mermaid-\_r_11gf\_ .node ellipse,#chatgpt-mermaid-\_r_11gf\_ .node polygon,#chatgpt-mermaid-\_r_11gf\_ .node path{fill:rgb(222, 234, 251);stroke:rgb(83, 154, 248);stroke-width:1px;}#chatgpt-mermaid-\_r_11gf\_ .rough-node .label text,#chatgpt-mermaid-\_r_11gf\_ .node .label text,#chatgpt-mermaid-\_r_11gf\_ .image-shape .label,#chatgpt-mermaid-\_r_11gf\_ .icon-shape .label{text-anchor:middle;}#chatgpt-mermaid-\_r_11gf\_ .node .katex path{fill:#000;stroke:#000;stroke-width:1px;}#chatgpt-mermaid-\_r_11gf\_ .rough-node .label,#chatgpt-mermaid-\_r_11gf\_ .node .label,#chatgpt-mermaid-\_r_11gf\_ .image-shape .label,#chatgpt-mermaid-\_r_11gf\_ .icon-shape .label{text-align:center;}#chatgpt-mermaid-\_r_11gf\_ .node.clickable{cursor:pointer;}#chatgpt-mermaid-\_r_11gf\_ .root .anchor path{fill:rgb(143, 143, 143)!important;stroke-width:0;stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_11gf\_ .arrowheadPath{fill:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_11gf\_ .edgePath .path{stroke:rgb(143, 143, 143);stroke-width:1px;}#chatgpt-mermaid-\_r_11gf\_ .flowchart-link{stroke:rgb(143, 143, 143);fill:none;}#chatgpt-mermaid-\_r_11gf\_ .edgeLabel{background-color:rgb(252, 252, 252);text-align:center;}#chatgpt-mermaid-\_r_11gf\_ .edgeLabel p{background-color:rgb(252, 252, 252);}#chatgpt-mermaid-\_r_11gf\_ .edgeLabel rect{opacity:0.5;background-color:rgb(252, 252, 252);fill:rgb(252, 252, 252);}#chatgpt-mermaid-\_r_11gf\_ .labelBkg{background-color:rgba(252, 252, 252, 0.5);}#chatgpt-mermaid-\_r_11gf\_ .cluster rect{fill:rgb(243, 243, 243);stroke:rgba(0, 0, 0, 0.1);stroke-width:1px;}#chatgpt-mermaid-\_r_11gf\_ .cluster text{fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_11gf\_ .cluster span{color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_11gf\_ div.mermaidTooltip{position:absolute;text-align:center;max-width:200px;padding:2px;font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:12px;background:rgb(243, 243, 243);border:1px solid rgba(0, 0, 0, 0.1);border-radius:2px;pointer-events:none;z-index:100;}#chatgpt-mermaid-\_r_11gf\_ .flowchartTitleText{text-anchor:middle;font-size:18px;fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_11gf\_ rect.text{fill:none;stroke-width:0;}#chatgpt-mermaid-\_r_11gf\_ .icon-shape,#chatgpt-mermaid-\_r_11gf\_ .image-shape{background-color:rgb(252, 252, 252);text-align:center;}#chatgpt-mermaid-\_r_11gf\_ .icon-shape p,#chatgpt-mermaid-\_r_11gf\_ .image-shape p{background-color:rgb(252, 252, 252);padding:2px;}#chatgpt-mermaid-\_r_11gf\_ .icon-shape .label rect,#chatgpt-mermaid-\_r_11gf\_ .image-shape .label rect{opacity:0.5;background-color:rgb(252, 252, 252);fill:rgb(252, 252, 252);}#chatgpt-mermaid-\_r_11gf\_ .label-icon{display:inline-block;height:1em;overflow:visible;vertical-align:-0.125em;}#chatgpt-mermaid-\_r_11gf\_ .node .label-icon path{fill:currentColor;stroke:revert;stroke-width:revert;}#chatgpt-mermaid-\_r_11gf\_ .node .neo-node{stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_11gf\_ [data-look="neo"].node rect,#chatgpt-mermaid-\_r_11gf\_ [data-look="neo"].cluster rect,#chatgpt-mermaid-\_r_11gf\_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-\_r_11gf\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_11gf\_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-\_r_11gf\_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-\_r_11gf\_-gradient);stroke-width:1px;}#chatgpt-mermaid-\_r_11gf\_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_11gf\_ [data-look="neo"].node .neo-line path{stroke:rgb(83, 154, 248);filter:none;}#chatgpt-mermaid-\_r_11gf\_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-\_r_11gf\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_11gf\_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-\_r_11gf\_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-\_r_11gf\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_11gf\_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-\_r_11gf\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_11gf\_ .node text{font-size:14px;font-weight:600;letter-spacing:normal;fill:rgb(0, 79, 153);}#chatgpt-mermaid-\_r_11gf\_ .edgeLabels text{font-size:13px;font-weight:600;letter-spacing:-0.08px;fill:rgb(0, 79, 153);}#chatgpt-mermaid-\_r_11gf\_ .node tspan[font-weight="normal"],#chatgpt-mermaid-\_r_11gf\_ .edgeLabels tspan[font-weight="normal"]{font-weight:600;}#chatgpt-mermaid-\_r_11gf\_ .edgeLabel .label rect{opacity:1;rx:13px;ry:13px;fill:rgb(245, 250, 255);stroke:rgb(206, 219, 229);stroke-width:1px;}#chatgpt-mermaid-\_r_11gf\_ .node rect,#chatgpt-mermaid-\_r_11gf\_ .node circle,#chatgpt-mermaid-\_r_11gf\_ .node ellipse,#chatgpt-mermaid-\_r_11gf\_ .node polygon,#chatgpt-mermaid-\_r_11gf\_ .node path{fill:rgb(229, 243, 255);stroke:rgba(0, 0, 0, 0.1);stroke-width:1px;}#chatgpt-mermaid-\_r_11gf\_ .node rect{rx:16px;ry:16px;}#chatgpt-mermaid-\_r_11gf\_ .node.mermaid-decision .label-container{fill:rgb(245, 250, 255);stroke:rgb(206, 219, 229);stroke-dasharray:2,2;}#chatgpt-mermaid-\_r_11gf\_ .edgePaths .flowchart-link{stroke:rgb(143, 143, 143);stroke-width:1px;stroke-linecap:round;stroke-linejoin:round;}#chatgpt-mermaid-\_r_11gf\_ .marker{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_11gf\_ :root{--mermaid-font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";}Hermes 定时触发读取采集配置调用 GitHub / HN / HF / RSS统一字段、清洗 URL规则过滤 + URL/内容去重保存 SQLite 原始情报更新项目指标快照计算主题变化和趋势状态是否需要模型摘要？模板生成日报批量调用 Ollama检查长度、链接、日期与重复发送发送飞书日报记录发送状态和运行日志否是

### 采集与过滤顺序

1. 读取每个来源的增量数据，记录来源、获取时间和原始链接。
2. 先按主题关键词、发布时间、项目活跃度等规则过滤。
3. 按规范化 URL、外部 ID、内容哈希去重；必要时再用 SimHash 处理近似重复内容。
4. 将原始情报存入数据库，即使当天没选进日报，也保留必要元数据供趋势判断。
5. 对 GitHub 项目保存每日指标快照，避免只记录当天的 stars 数而无法计算增长。
6. 先生成趋势状态，再决定是否需要模型摘要。
7. 生成日报后检查长度、链接、发送日期和当天是否已经成功发送。
8. 飞书发送成功后记录状态；失败时退避重试，不要无限重发。

日报生成与发送必须分开。 如果飞书临时不可用，不需要重新抓取所有情报；可以从数据库中重新发送已生成的日报。

## 五、数据库与长期记忆

首期用 SQLite 即可，不引入 PostgreSQL、Redis、向量数据库或独立服务。

建议的数据表：

| 表                   | 作用                               |
| ------------------- | -------------------------------- |
| `sources`           | 来源配置、启用状态、最后成功时间、错误记录            |
| `items`             | 情报标题、链接、摘要、发布时间、主题、内容哈希          |
| `item_sources`      | 同一条情报被哪些来源发现                     |
| `project_snapshots` | GitHub 项目的 stars、forks、更新时间等历史快照 |
| `topic_daily_stats` | 每天每个主题的条目数量、来源数量和代表情报            |
| `runs`              | 每次采集任务的开始、结束、错误与处理数量             |
| `daily_reports`     | 日报正文、生成时间、发送状态和重试记录              |
| `items_fts`         | SQLite FTS5 全文检索索引               |

其中，`items` 负责回答“过去发现过什么”，`project_snapshots` 负责回答“项目热度如何变化”，`topic_daily_stats` 负责回答“某个技术方向最近是否升温”。

首期的“长期记忆”主要是结构化历史数据和全文检索，而不是把所有历史内容都塞进模型上下文。后续如果确实需要按语义检索历史方案，再评估本地 embedding 和向量检索；目前不需要为 RAG 增加额外复杂度。

### 趋势引擎：不评分，只输出状态与证据

你已经决定暂时不做评分，因此不生成“综合情报分数”，也不把不同平台的热度强行合并成一个分值。

新出现

最近首次出现的项目、方法或技术方向，历史数据还不足以判断持续性。

持续升温

连续多个观察周期出现，讨论量或项目活跃度持续增加，且不是只靠一个来源支撑。

突然增长

短期内的 stars 增量、讨论活跃度或相关事件数明显高于该项目自身的历史水平。

进入成熟期 / 热度回落

近期新增发布或讨论减少，或项目逐渐从新鲜话题转为稳定的工程工具。应结合证据判断，不能仅凭单日数据下结论。

趋势判断至少区分三个时间尺度：

- 24 小时： 新事件、发布和短期突增。
- 7 天： 主题是否持续升温、是否有多个来源跟进。
- 30 天： 长期方向、成熟项目和逐渐冷却的话题。

前 7 天主要建立基线，不急于给主题下确定性结论；运行 14 天后开始观察周趋势，积累 30 天以上数据后再启用月趋势。

日报中每个趋势结论都应带上证据，例如：

> 趋势：持续升温 — 最近 7 天有多次版本发布，讨论来自 GitHub 和 Hacker News；相较前一周，相关事件增加。当前历史窗口不足 30 天，长期趋势仍待观察。

若证据不够，就写“新出现，尚待观察”，不要用模型编造趋势。

## 六、控制 Token 与计算成本

最重要的成本控制不是换更小的模型，而是尽量让模型少读数据。

| 环节   | 首期策略                        |
| ---- | --------------------------- |
| 采集   | API / RSS 获取元数据，不让模型搜索网页    |
| 初筛   | Python 关键词、日期、字段完整性和项目状态规则  |
| 去重   | URL、ID、哈希、SimHash           |
| 趋势计算 | Python 完成，不调用模型             |
| 摘要   | 只对入选情报批量调用一次或少量调用           |
| 日报编排 | Python 模板生成，模型只负责必要的摘要和主题归纳 |
| 异常处理 | 重试与错误分类由代码完成，不让模型反复诊断       |

### Ollama 的使用建议

你的 Windows 10 笔记本有 32GB 内存、RTX 4060 8GB，已运行 `qwen3.5:9B`。首期把它作为可选的本地摘要服务，不需要为每条情报调用模型。

- 单次请求传入多条已经筛选的标题、摘要和元数据。
- 每条输出控制在约 100–180 个中文字。
- 让模型返回固定 JSON 字段，例如 `topic`、`summary`、`engineering_value`、`trend_evidence`。
- 模型输出必须经过代码校验，尤其是 URL、日期和项目名称。
- 超时或服务不可达时，直接使用原始标题、来源摘要和规则标签生成降级日报。

本地推理通常不按 API Token 计费，但会消耗计算资源、电力和运行时间。对于这个项目，最初可以不依赖云端大模型；如果 Ollama 访问不稳定，也不要为了日报而反复调用模型。

## 七、飞书日报格式

首期每天只发送一条消息。建议使用固定栏目，方便长期比较，也方便后续分析趋势变化。

## AI 情报日报 · 2026-10-14

采集窗口：过去 24 小时 · 历史趋势：过去 7 天

一、今日值得关注

\- 2–3 条最值得了解的技术进展，每条包含结论、实际价值和原文链接。

二、开源项目与工程实践

- 项目名称、解决的问题、适用场景、最近变化。
- 标注新项目或持续升温等状态，并提供 GitHub 链接。

三、解决方案与产品发布

\- 重点说明它解决了什么问题、与现有方案的区别、是否值得试用。

四、趋势观察

\- 主题、趋势状态、过去 7 天的证据和观察限制。

五、采集状态

\- 成功来源数、发现条目数、去重后条目数、模型调用状态、失败来源。

日报应当允许栏目为空，不为填满版面而制造内容。每条情报附原文链接，推测与事实分开写。飞书消息格式可以先用普通文本；确认稳定后再改成富文本卡片。

飞书自定义机器人可以通过 Webhook 接收消息，具体限制以官方文档为准：[飞书自定义机器人文档](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)。Webhook 地址应存放在环境变量或本机配置文件中，不要写进公开仓库、代码示例或日志。

## 八、基于 AI News Brief 开发，还是重新写？

建议先审计，再复用，不要一开始就把整个仓库搬进现有 Agent 框架。

基础项目：[frankzch/ai-news-brief](https://github.com/frankzch/ai-news-brief)。

开发时保留它可能有用的采集器、内容标准化、去重和调度思路；但需要先确认当前代码结构、依赖和运行情况。按我们前面讨论的方向，首期改造重点如下：

| 模块               | 处理方式                          |
| ---------------- | ----------------------------- |
| RSS / API 采集     | 审计后复用，优先保留                    |
| 视频、字幕、视频摘要       | 删除或彻底禁用                       |
| X / Reddit 等扩展来源 | 默认关闭，不作为首期依赖                  |
| 自动浏览器登录与复杂反爬     | 不启用                           |
| 原有重要性评分          | 移除，不保留隐藏评分                    |
| URL 去重、SimHash   | 审计后复用                         |
| 数据库              | 首期采用 SQLite + FTS5            |
| 历史项目快照           | 新增                            |
| 趋势引擎             | 新增                            |
| 飞书日报             | 对接已有 Hermes 机器人配置             |
| 调度               | Hermes 触发 Python 入口，避免两套调度器重叠 |

如果原项目依赖太重、代码耦合严重或当前版本无法稳定运行，就只复用可独立运行的模块，写一个小型 Python 项目更合适。不要为了“复用开源”而承担过多维护成本。

## 九、开发任务拆分：直接交给 MultiCA

建议按 5 个阶段开发，每个阶段都要求 Pi 输出代码、测试结果和变更说明。先验收再进入下一阶段，不要一次性生成完整系统。

## 01

代码审计与最小可运行版本

目标：确认 AI News Brief 能否运行，明确保留、删除和新增的模块。检查依赖、许可、配置、数据库和调度入口。

验收：能在本地完成一次无定时、无飞书发送的采集测试，并输出来源统计。

## 02

接入首批情报节点

目标：GitHub、Hacker News、Hugging Face、少量官方 RSS。统一字段、限量、去重、超时、退避重试。

验收：单次运行结果可复现；某个来源失败不会导致其他来源全部失败。

## 03

历史库与趋势引擎

目标：SQLite、FTS5、项目快照、主题统计、趋势状态。无综合评分，不依赖 LLM 做数值计算。

验收：连续运行后能对比昨日、近 7 天的变化；历史不足时能明确标注。

## 04

日报与飞书

目标：模板日报、可选 Ollama 摘要、发送状态持久化、长度校验和重复发送保护。

验收：手动触发一次能收到日报；重复执行不会重复发送已经成功发送的同一天日报。

## 05

Hermes 定时执行与稳定性测试

目标：让已配置飞书的 Hermes 每天触发脚本，保留日志、失败状态和恢复能力。

验收：连续运行 7 天，检查重复发送、来源错误、日报质量、资源消耗和模型可用性。

### 给 MultiCA / Pi 的第一条开发指令

可以直接复制下面这份任务说明。第一轮只做审计，不要让它直接改造全部项目。

# 项目任务：AI 情报日报智能体（MVP）

## 一、项目目标

基于现有开源项目 [https://github.com/frankzch/ai-news-brief](https://github.com/frankzch/ai-news-brief) 开发一个轻量级 AI 情报日报系统。

系统由 Hermes Agent 定时触发，采集少量高价值 AI 情报，保存长期历史，识别开源项目和技术主题的趋势，每天向已有飞书机器人发送一条中文日报。

Windows 10 是 MultiCA 开发工作台；Linux 笔记本是优先考虑的生产运行环境。Windows 上已有 Ollama qwen3.5:9B，可作为可选本地摘要服务。

## 二、硬性约束

1. 优先复用现有开源代码，但必须先审计仓库结构、许可、依赖和运行方式。
2. 不采集视频，不下载视频，不做字幕或视频总结。
3. 首期只接入 GitHub API、Hacker News API、Hugging Face 的公开接口或 RSS、少量官方博客 RSS。
4. 暂不接入 X、Reddit、YouTube、Bilibili。
5. API 和 RSS 优先；不得绕过验证码、登录限制、访问控制、限流或封禁。
6. 不做重要性评分，不生成综合评分，不使用隐藏评分排序。
7. 保留趋势引擎，使用规则和历史快照识别趋势状态。
8. 每个平台每日最多保留 10 条候选，允许不足 10 条；跨来源去重。
9. 使用 SQLite + FTS5 作为首期历史库，不引入 Redis、Kafka、PostgreSQL、独立向量数据库或复杂多智能体架构。
10. 模型调用必须可选。优先规则过滤、程序计算和模板生成；只有入选情报才允许批量调用 Ollama。
11. 飞书每天只推送一条日报；失败重试、成功记录和重复发送保护必须由代码实现。
12. 不把 Webhook、Token、API Key 写入代码、日志或 Git 仓库。
13. 不得擅自更改现有 Hermes、Pi、MultiCA 的全局配置；新增配置尽量放在独立项目目录。
14. 不要在没有确认前删除原项目文件、迁移现有数据或修改现有机器人配置。

## 三、首轮任务：只审计，不做大规模修改

请完成以下工作：

A. 检查当前工作区、Git 状态、原项目目录、README、LICENSE、依赖清单、配置文件和运行入口。

B. 识别原项目的采集器、内容处理、评分、去重、数据库、调度、视频处理、飞书发送模块。

C. 说明哪些模块可以复用，哪些需要删除或禁用，哪些需要新增。

D. 检查当前代码是否能在目标 Python 环境运行。不要为了通过审计而直接升级大量依赖。

E. 输出建议的目录结构、SQLite 表设计、配置方案、每日流程、异常处理方案和测试计划。

F. 列出尚未验证的外部条件，例如 API 限额、RSS 可用性、Ollama 局域网访问、Hermes 的实际定时调用能力。

G. 输出审计报告和最小改造计划，等待确认后再进入代码修改阶段。

## 四、后续验收原则

每个阶段都必须提供：

- 变更文件清单；
- 实际执行过的测试命令；
- 测试结果和未解决问题；
- 配置项说明；
- 失败时的恢复方法。

禁止把未执行的测试写成已通过，禁止虚构 API 能力、来源数量和成本。

## 十、首期开发前，需要验证的 4 个关键点

1. Hermes 的运行方式。 确认已配置飞书机器人的 Hermes 实例在哪台机器上，以及它实际支持哪种定时任务或定时触发方式。先使用已有能力，不要未经验证就增加另一套调度服务。
2. Windows Ollama 的访问地址。 从 Linux 上测试能否访问 Windows 的 Ollama API；只允许必要的局域网访问，不要将服务直接暴露到公网。
3. GitHub API 凭证。 匿名 API 有限额，建议为采集任务配置权限最小化的只读凭证，并将其放在本地环境变量中。参考官方文档：[GitHub REST API 限流说明](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)。
4. 飞书发送的幂等性。 验证机器人返回结果、失败重试和消息长度限制。发送前检查当天是否已成功推送；若请求超时导致发送结果不确定，不要直接无限重试。

## 我的最终建议

首期就做四件事：GitHub + Hacker News + Hugging Face + 少量官方 RSS，SQLite 历史库，规则型趋势引擎，Hermes 定时发送飞书日报。

不要先做多智能体协同、复杂 RAG、自动浏览器登录或 X 全量采集。等系统稳定运行 7 天后，再依据真实日报质量和运行日志决定是否增加数据源、调整摘要模型或扩展趋势分析。这样更容易控制成本，也方便通过 MultiCA 分阶段开发和验收。
