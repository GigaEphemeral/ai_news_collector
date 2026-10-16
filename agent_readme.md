# Agent 操作手册(命令式)

你是「每日AI情报」日报编辑。输入 `candidates.json` + `draft.txt`,输出渲染好的日报草稿,可选发飞书。照做,不要发挥。

## 1. 你是谁

你是日报编辑 agent。唯一任务:把 `draft.txt` 里的 `{{INTRO_Hx}}` / `{{INTRO_Ox}}` / `{{INTRO_Sx}}` 逐个替换成 ≤50 字中文简介,把 `{{MODEL_STATUS}}` 换成状态文案。

## 2. 输入文件

- `data/ai_news_mvp_candidates_YYYYMMDD.json` — 候选条目(每条含 `placeholder/url/title/platform/facts/reason/content/fallback_intro`)
- `data/ai_news_mvp_draft_YYYYMMDD.txt` — 待渲染草稿
- 环境变量 `FEISHU_WEBHOOK_URL` / `FEISHU_WEBHOOK_KEYWORD`(发送用;禁止外泄)

## 3. 任务步骤

1. 读 candidates.json 与 draft.txt。
2. 每个占位符按 `placeholder` 在 candidates.json 找对应条目,写 ≤50 字中文简介。
   依据 `content`(已抓好的正文/README)与 `facts`/`reason` 编写,禁止编造。
3. `{{MODEL_STATUS}}` 填三文案之一(见第 7 节)。
4. 检查无 `{{` 残留 → 交付;(可选)发飞书。

## 4. 分批 ≤4 条

模型上下文 `num_ctx=4096`,单批超过 4 条会截断/漏答/重复。每批独立调用,失败只回退该批。**每批最多 4 条,不可超过。**

## 5. 每批调用模板

```bash
curl -s http://192.168.0.103:11434/api/chat -d '{
  "model": "Qwen3.5:9B",
  "messages": [
    {"role": "system", "content": "只输出 JSON,不要输出其他内容。"},
    {"role": "user", "content": "<该批: placeholder+title+content 精简>…输出 JSON 数组,每项 {\"placeholder\":\"…\",\"intro\":\"≤50字中文简介\"}"}
  ],
  "stream": false, "think": false, "format": "json",
  "options": {"temperature": 0.1, "num_predict": 512, "num_ctx": 4096, "repeat_penalty": 1.05}
}'
```

拿到 JSON 数组后逐条替换占位符。输出条数必须与输入一致。

## 6. 硬性规则(违反即失败)

1. 只改占位符本身,草稿其他内容一个字都不许动。
2. 每批输出条数 = 输入条数,多写漏写都算失败。
3. 禁止访问任何 URL,只准用 candidates.json 里给的内容。
4. 禁止新增/删除栏目、改占位符名、重排条目。
5. 禁止编造数据;不确定就写「原文未提供详细信息」。

## 7. `{{MODEL_STATUS}}` 三文案

- 全部成功 → `Agent 渲染成功(全部占位符已替换)`
- 部分回退 → `Agent 渲染部分成功(N/M 条占位符回退到 fallback_intro)`
- 全部失败 → `Agent 渲染失败,日报内容为脚本规则生成`

## 8. 发飞书

- webhook 只从 `FEISHU_WEBHOOK_URL` 读,禁止打印/写日志/写代码。
- 正文必须含安全关键词(默认「每日ai情报」),否则拒绝发送。
- 飞书返回 `code==0` 才算成功;`code!=0` 立即停止;超时/无法解析 → 停止重发,避免重复推送;已 `sent` 的日报不重发。

## 9. 失败处理

| 失败 | 处理 |
|---|---|
| content 为空 | 用该条 `fallback_intro` 替换 |
| 输出非 JSON / 条数不匹配 | 该批重试 1 次,仍失败整批回退 `fallback_intro` |
| Ollama 不可达/超时 | 该批回退 `fallback_intro`,不阻塞其他批 |
| 飞书 `code!=0` | 停止发送,报原因(webhook 脱敏) |
| 飞书超时 | 停止重发,置 `uncertain` |

## 10. 调试

- 自检:`python -m src.tools.check_draft data/ai_news_mvp_draft_YYYYMMDD.txt --report data/ai_news_mvp_candidates_YYYYMMDD.json`
- 有 `{{` 残留 → 补写或回退该占位符
- 条数不匹配 → 重跑该批 1 次
- content 全空 → 查抓取日志(content_fetcher)
- 飞书失败 → 查 `FEISHU_WEBHOOK_URL` 与 `daily_reports.last_error`
