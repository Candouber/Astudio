# LLM 集成策略

我们使用 **LiteLLM** 统一代理了上游各个模型供应商。

## 配置与热加载
配置存储在 `~/.canvas/config.yaml` 或项目 `data/config.yaml` 中，包含不同提供商列表与 Key。
系统通过 `LLMService.reload_config()` 实现了无缝热加载。

## 角色分配 (Role-based Model Routing)
- `agent_zero`: 需要强推理能力来统筹规划，通常配置为大模型(如 gpt-4o, claude-3-5-sonnet)。
- `sub_agent`: 性价比高的专业能力模型。
- `distillation`: 只做上下文压缩，用极速轻量化小模型(如 gpt-4o-mini, haiku)可大幅降本。

## 接口特点
`llm_service.chat` 是项目里所有 Agent 发送请求的惟一入口，完全兼容了 SSE (Server-Sent Events) 的 async 生成器，并默认通过 Pydantic Json Schema 控制结构化输出。
