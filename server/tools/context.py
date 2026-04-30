"""
工具调用上下文。

员工执行工具时由 sub_agent 注入当前 task/sub_task 信息，
沙箱类工具据此绑定到正确的任务目录。
"""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ToolContext:
    task_id: str
    sub_task_id: Optional[str] = None
    studio_id: Optional[str] = None
    agent_role: Optional[str] = None


_CURRENT_TOOL_CONTEXT: ContextVar[ToolContext | None] = ContextVar(
    "current_tool_context",
    default=None,
)


def get_current_tool_context() -> ToolContext:
    context = _CURRENT_TOOL_CONTEXT.get()
    if not context or not context.task_id:
        raise RuntimeError("当前工具调用缺少 task_id，无法绑定任务沙箱")
    return context


def set_current_tool_context(context: ToolContext | None):
    return _CURRENT_TOOL_CONTEXT.set(context)


def reset_current_tool_context(token) -> None:
    _CURRENT_TOOL_CONTEXT.reset(token)
