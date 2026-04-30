# 0 号 Agent (CEO) 设计文档

0 号 Agent（Agent Zero）是系统的大脑，它的核心作用是"理解、分发与汇总"。所有请求首先发送至它。

## `route_question` (路由)
职责：决定用户的问题是发送给一个现有的专业工作室，还是创建一个全新的临时专家组。
实现：将所有工作室名片 `StudioCard` （摘要信息）注入 Prompt，要求 LLM 返回唯一匹配的 `studio_id` (或 None)。

## `decompose_task` (拆解)
职责：若已匹配到工作室，则限制 0 号必须向工作室的既有专家角色（Sub-agent）下发任务流。如果为 None，则要求 0 号临时创造 2-3 个对口的领域专家。
实现：Json Schema/Format 输出标准的步进动作数组（包括 `step_label`, `agent_role`, `input_brief`）。在此版本，暂简化为顺序列表输出（Sequential），后续可扩展为由 0 号输出图结构边。

## `synthesize_results` (汇总)
职责：吸气结束时（即所有的 Sub-agents 执行完毕），收集所有路径上的子节点的蒸馏摘要 `distilled_summary`，作为上下文传给 0 号 Agent，撰写最终结论。

## `evaluate_promotion` (工作室晋升)
这是一个常驻后台运行的异步任务，它会监测当前用户的临时任务模式。若检测到一个场景频繁发生（如“生成爬虫代码”），它会在看板主动提出建议：“是否建议升格为一个长效的数据采集团队”。（在原型阶段保留接口占位符，不作深层实现）
