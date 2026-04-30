"""
工具执行器 (Tool Executor) — 兼容层

历史上 `sub_agent_executor` 与各 router 都从这里直接引用 `execute_tool` /
`get_tool_schemas` / `ALL_TOOL_SCHEMAS`。真正的注册中心已经挪到
`tools.registry`，本模块只保留对外符号的兼容导出。

新代码请直接 `from tools.registry import ...` 使用：
  - `build_tool_schemas(slugs)`       async，支持 builtin + http，sub_agent 运行期走这条
  - `list_available_slugs()`          当前 skill 池启用 + 有实现的 slug
  - `describe_available_skills()`     给 LLM prompt 用的 [{slug,name,description}]
"""
from tools.registry import (
    PROTOCOL_TOOL_NAMES,
    REPORT_BLOCKER_SCHEMA,
    SUBMIT_DELIVERABLE_SCHEMA,
    all_builtin_schemas,
    build_tool_schemas,
    builtin_slugs,
    describe_available_skills,
    execute_tool,
    get_enabled_tool_schemas,
    get_tool_schemas,
    list_available_slugs,
)

# 兼容旧字段：等价于 registry.all_builtin_schemas() + 协议工具
ALL_TOOL_SCHEMAS = [*all_builtin_schemas(), SUBMIT_DELIVERABLE_SCHEMA, REPORT_BLOCKER_SCHEMA]

# 仅协议工具，供"简单任务"场景复用
PROTOCOL_ONLY_SCHEMAS = [SUBMIT_DELIVERABLE_SCHEMA, REPORT_BLOCKER_SCHEMA]

__all__ = [
    "ALL_TOOL_SCHEMAS",
    "PROTOCOL_ONLY_SCHEMAS",
    "PROTOCOL_TOOL_NAMES",
    "REPORT_BLOCKER_SCHEMA",
    "SUBMIT_DELIVERABLE_SCHEMA",
    "build_tool_schemas",
    "builtin_slugs",
    "describe_available_skills",
    "execute_tool",
    "get_enabled_tool_schemas",
    "get_tool_schemas",
    "list_available_slugs",
]
