# 上下文蒸馏 (Context Distillation) 机制

由于在空间画布中可能有极其庞大的历史分支记录，并且存在多个 Sub-agent 同时发散的问题，我们不能简单地通过历史 Message 列表做拼接。
相反我们采取“节点蒸馏”的方案。

## 蒸馏流程
当某一个节点由 `sub_agent` 处理完毕由 `running` 转入 `completed` 状态时，会立即触发 `ContextDistiller`:
1. 获取 Node `#id` 的 `input` (Task Instruction) + `output` (Agent Response)
2. 送入轻量模型 (例如 GPT 4-o Mini / Claude Haiku) 进行提炼，生成约 150字的 `distilled_summary`
3. 更新数据库对应记录。

## 0 号 Agent 的数据视图
在整个任务最终汇总 (Synthesize) 时，0 号并不直接访问上文任何 10 万字的具体细节推导。取而代之的是，它拿到各模块的 `distilled_summary` 摘要合集：
`"Step [UI Draft] processed by [Designer]: <150 word summary>"`

### Soul 沉淀 (原型后扩展)
当一轮大任务完结，系统会触发长期的 Soul 更新。这也是同样的原理，把本次的多个节点提炼物，进一步作为新的 Memory 追加进该工作室（及其内部 Sub-agents）的 `soul.md` 文件中。
