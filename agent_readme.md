# 每日AI情报 · 定时任务操作手册(Agent 用)

> 你是 Multica 定时任务的执行 agent。任务:按下面步骤运行 Python 脚本完成「采集 → 日报 → 飞书发送」,并核对结果、在本 issue 汇报。
> **必须真实执行命令,禁止虚构结果。** 环境:Linux;所有命令在**项目根目录**下执行(仓库根 = 项目根,即 `run.py` 所在目录)。

## 0. 环境速查

| 项 | 值(均相对项目根) |
|---|---|
| 项目根 | 仓库根目录(含 `run.py`)—— 先 `cd` 到此处 |
| Python | `./.venv/bin/python`(**不要用系统 python3**) |
| 秘密文件 | 项目根**上两级目录**的 `.env`(如 `../../.env`;运行时用 `AI_NEWS_ENV_PATH` 显式指定) |
| 数据目录 | 项目根**上两级目录**的 `data`(默认 `../../data`;见 `config.yaml` 的 `paths.data_dir`) |
| 今日日期 | 用 `date +%Y-%m-%d` 获取(UTC 与东八区注意) |

```bash
cd <项目根>          # 仓库根目录
export AI_NEWS_ENV_PATH=$(cd ../.. && pwd)/.env   # 或按实际 .env 位置指定
DATE=$(date +%Y-%m-%d)
```

## 1. 步骤总览

```
① 采集      run.py --since-days 7
② 生成日报  run.py --report --date $DATE
③ 核对产出  检查 data/ai_news_mvp_run_*.json / candidates / draft
④ 发送飞书  run.py --send --date $DATE(或 ② 时直接 --report --send)
⑤ 汇报     在本 issue 贴结果(来源统计/日报摘要/发送状态/失败原因)
```

> 推荐:一次跑完 `run.py --since-days 7 --report --send`(采集+生成+发送);也可分步跑(见下)。

## 2. 步骤① 采集

```bash
./.venv/bin/python run.py --since-days 7
```

- 期望:输出 `[OK] 完成,4/4 个来源成功(失败:无),发现 N → 筛选 N → 去重 N → 入库 N 条`,退出码 0。
- 核对:`ls data/ai_news_mvp_run_$(date +%Y%m%d).json`,`python3 -c "import json;d=json.load(open('data/ai_news_mvp_run_YYYYMMDD.json'));print(d['platforms'])"`。
- 退出码 1 = 全部来源失败或无条目 → 见第 6 节处理。

## 3. 步骤② 生成日报

```bash
./.venv/bin/python run.py --report --date $DATE
```

- 期望:输出日报正文,`[OK]`,退出码 0;日报写入 SQLite `daily_reports` 表。
- 生成与发送分离:飞书不可达时先 `--report`,恢复后单独 `--send` 重发,不重新采集。
- `--no-summarize`:跳过 Ollama,强制规则模式(不烧 token)。

## 4. 步骤③ 核对产出(Agent 模式产物,可选但推荐)

```bash
ls -la data/ai_news_mvp_candidates_$(date +%Y%m%d).json data/ai_news_mvp_draft_$(date +%Y%m%d).txt
./.venv/bin/python -m src.tools.check_draft data/ai_news_mvp_draft_$(date +%Y%m%d).txt --report data/ai_news_mvp_candidates_$(date +%Y%m%d).json
```

- 期望:`[OK] draft 无 {{ 残留,占位符全部渲染完成`。
- 若需程序化渲染占位符(无模型时):`./.venv/bin/python -m src.tools.render_draft <draft> <candidates.json> -o out.txt`。
- **不访问任何 URL**:简介只用 candidates.json 里的 `content/facts/reason`,不自行抓链接。

## 5. 步骤④ 发送飞书

```bash
./.venv/bin/python run.py --send --date $DATE
```

- 期望:输出发送结果 `{'status': 'sent' | 'skipped_already_sent', ...}`,退出码 0。
- 幂等:当日已 `sent` 会返回 `skipped_already_sent`,不重复发送,属正常。
- 飞书判定:只有飞书确认 `code==0` 才算发送成功;`code!=0` 为失败(重试耗尽后 `status=failed`);超时/响应无法解析 → `status=uncertain`,停止重发(避免重复推送)。
- webhook 全程脱敏,不得打印/外泄。

## 6. 失败处理(逐条对照)

| 现象 | 判断 | 处理 |
|---|---|---|
| 退出码 1,`[FAIL] 所有来源失败` | 断网/全源挂 | 查看数据目录(`paths.data_dir`,相对项目根)下 `ai_news_mvp_run_*.json` 的 `platforms[*].error` 与 `sources.last_error`;网络恢复后重跑,幂等无害 |
| 部分来源失败,退出码 0 | 单源问题 | 其余源照常,汇报失败源;GitHub 403/429 是限流,跳过该查询,不重试硬闯 |
| `--report` 退出 1 | 日报生成失败 | 看异常;Ollama 不可达会自动降级规则模式,不应失败;必要时 `--no-summarize` |
| `--send` 退出 1,status=failed | 飞书返回错误/重试耗尽 | 检查 `.env` 的 `FEISHU_WEBHOOK_URL`/关键词;`daily_reports.last_error` 已脱敏;恢复后 `--send --date` 重发 |
| `--send` status=uncertain | 超时/响应无法解析 | 停止重发(避免重复推送),人工确认后可手动重发 |
| check_draft 有 `{{` 残留 | 占位符未渲染完 | 用 render_draft 补渲染或回退;条数不匹配重试 1 次 |
| 想重置库 | 数据异常 | 删 `paths.data_dir` 下的 `ai_news_mvp.sqlite3` 后重跑(自动建表) |

## 7. 汇报格式(在本 issue 贴评论)

```text
✅ 每日AI情报日报 <DATE>
采集:4/4 来源成功,发现 N → 筛选 N → 去重 N → 入库 N 条(或列出失败源)
日报:已生成并写入 daily_reports;模型状态:<Ollama 摘要/规则模式>
发送:sent(或 skipped_already_sent / failed:<原因>)
产物:<paths.data_dir>/ai_news_mvp_run_YYYYMMDD.json / candidates / draft
```

- 结果必须来自真实命令输出,不得虚构。
- webhook/token 严禁出现在任何评论、日志、代码里。

## 8. 时区与调度提示

- Multica autopilot 定时触发必须显式 `--timezone Asia/Shanghai`,否则默认 UTC 会偏 8 小时。
- 建议节奏:采集 21:30,日报发送 21:45(先采后发);实测单次采集约 60–70s。
- 重跑安全:程序幂等,重复执行不产生重复发送/重复数据。
