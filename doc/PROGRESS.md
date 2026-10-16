# PROGRESS（待办）

> 规则：本文件只列待办事项；每个阶段完成且测试验收通过后，**删除**对应条目（不保留 done 历史）。
> 验收标准见 issue AIQI-18 描述中各阶段「验收」小节。

- [ ] 阶段 4：[03] 全量入库 + 消费时选 top-N（放宽每源保底上限、去重后全量入库、`daily_reports` 记当天选中、`topic_daily_stats` 按日期窗口全量统计）
- [ ] 阶段 5：RSS/HF 热度分层 + 「其他资讯汇总」段（HF `extra` 补采、RSS 三层热度规则、脚本侧聚合未入选条目）
- [ ] 阶段 6：[04] 脚本抓详情 + candidates/draft 输出（`summarize.fetch_contents` 并发 4、GitHub README 详情 7 天缓存、`build_draft_with_placeholders`、`export_candidates_json`）
- [ ] 阶段 7：[05] agent_readme.md + 占位符渲染 + 端到端走查（agent 参数、失败兜底、`src.tools.check_draft` 自检、README Agent 模式入口）
