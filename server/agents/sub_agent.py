import asyncio
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional

from loguru import logger

from agents.context import ContextBuilder
from services.attachments import task_has_attachments
from services.llm_service import llm_service
from tools.context import ToolContext, reset_current_tool_context, set_current_tool_context
from tools.executor import execute_tool
from tools.registry import build_tool_schemas

MAX_REACT_STEPS = 20
_TOOL_RESULT_MAX_CHARS = 12_000
_MAX_NO_TOOL_STREAK = 2
_MAX_CONSECUTIVE_TOOL_FAILURES = 2
_FAILURE_PREFIXES = (
    "[Search failed]",
    "[Browser search failed]",
    "[Tool execution error]",
    "[Tool execution failed]",
    "[Argument error]",
    "[Error]",
    "[Safety blocked]",
    "No relevant results found",
)
_TASK_SANDBOX_HELPERS = [
    "ensure_sandbox",
    "sandbox_list_files",
    "sandbox_read_file",
    "sandbox_write_file",
    "sandbox_run_command",
    "sandbox_start_preview",
]
_TASK_ATTACHMENT_HELPERS = [
    "file_analysis",
    "list_attachments",
    "read_uploaded_file",
    "read_excel_sheet",
    "read_pdf_text",
    "image_metadata",
]


def _truncate_observation(text: str) -> str:
    if len(text) <= _TOOL_RESULT_MAX_CHARS:
        return text
    half = _TOOL_RESULT_MAX_CHARS // 2
    return (
        text[:half]
        + f"\n\n...[Output too long; truncated {len(text) - _TOOL_RESULT_MAX_CHARS} characters]...\n\n"
        + text[-half:]
    )


def _get_response_field(response: Any, field: str) -> Any:
    if isinstance(response, dict):
        value = response.get(field)
    else:
        value = getattr(response, field, None)
    if value is not None:
        return value

    for extra_field in ("provider_specific_fields", "additional_kwargs", "model_extra"):
        if isinstance(response, dict):
            extra = response.get(extra_field)
        else:
            extra = getattr(response, extra_field, None)
        if isinstance(extra, dict) and extra.get(field) is not None:
            return extra.get(field)
    return None


def _build_assistant_tool_message(response: Any) -> Dict[str, Any]:
    tool_calls = _get_response_field(response, "tool_calls") or []
    message: Dict[str, Any] = {
        "role": "assistant",
        "content": _get_response_field(response, "content"),
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ],
    }

    reasoning_content = _get_response_field(response, "reasoning_content")
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    return message


def _tool_name_from_schema(schema: Dict[str, Any]) -> str:
    return str((schema.get("function") or {}).get("name") or "")


def _protocol_tool_schemas(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        schema for schema in tools
        if _tool_name_from_schema(schema) in {"submit_task_deliverable", "report_system_blocker"}
    ]


def _is_failed_tool_result(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return True
    return value.startswith(_FAILURE_PREFIXES) or "TimeoutError" in value


def _structured_observation(tool_name: str, result: str) -> tuple[str, bool]:
    failed = _is_failed_tool_result(result)
    payload = {
        "tool": tool_name,
        "ok": not failed,
        "status": "failed" if failed else "ok",
        "result": _truncate_observation(result),
    }
    if failed:
        payload["instruction"] = (
            "Do not repeat the same failing tool path. Use another available source, "
            "submit a best-effort deliverable, or report a blocker."
        )
    return json.dumps(payload, ensure_ascii=False), failed


class SubAgentExecutor:
    async def run(
        self,
        agent_role: str,
        agent_md_content: str,
        soul_content: str,
        leader_input: str,
        available_tools: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        task_id: Optional[str] = None,
        sub_task_id: Optional[str] = None,
        studio_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        token = None
        if task_id:
            token = set_current_tool_context(
                ToolContext(
                    task_id=task_id,
                    sub_task_id=sub_task_id,
                    studio_id=studio_id,
                    agent_role=agent_role,
                )
            )

        try:
            effective_tools = list(available_tools or [])
            if "web_search" in effective_tools and "browser_search" not in effective_tools:
                effective_tools.append("browser_search")
            if task_id:
                for helper in _TASK_SANDBOX_HELPERS:
                    if helper not in effective_tools:
                        effective_tools.append(helper)
                if task_has_attachments(task_id):
                    for helper in _TASK_ATTACHMENT_HELPERS:
                        if helper not in effective_tools:
                            effective_tools.append(helper)

            bundle_skills_block = await _build_bundle_skills_block(effective_tools)
            system_prompt = ContextBuilder.build_employee(
                agent_role=agent_role,
                agent_md_content=agent_md_content,
                soul_content=soul_content,
                leader_input=leader_input,
                bundle_skills_block=bundle_skills_block,
            )
            tools = await build_tool_schemas(effective_tools)

            history: List[Dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Execute this sub-task. You may use tools to gather information, run code, "
                        "and operate on files. After completing all necessary steps, call "
                        "`submit_task_deliverable` to submit the result. If you hit an unavoidable "
                        "blocker, call `report_system_blocker` and report the reason."
                    ),
                },
            ]

            total_tokens = 0
            no_tool_streak = 0
            consecutive_tool_failures = 0
            force_finalization = False
            finalization_reason = ""
            protocol_tools = _protocol_tool_schemas(tools)

            async def _emit(msg: str) -> None:
                if progress_callback is None:
                    return
                try:
                    await progress_callback(msg)
                except Exception as cb_err:
                    logger.debug(f"[{agent_role}] progress_callback 失败（忽略）: {cb_err}")

            for step in range(MAX_REACT_STEPS):
                logger.debug(f"[{agent_role}] ReAct step {step + 1}/{MAX_REACT_STEPS}")
                current_tools = protocol_tools if force_finalization else tools
                tool_choice = "required" if force_finalization else None
                if force_finalization:
                    await _emit("Finalizing result...")
                    if not history or history[-1].get("content") != finalization_reason:
                        history.append({
                            "role": "user",
                            "content": finalization_reason,
                        })
                else:
                    await _emit(f"Thinking... (step {step + 1})")

                try:
                    response, step_tokens = await llm_service.chat_with_usage(
                        messages=history,
                        role="sub_agent",
                        stream=False,
                        temperature=0.1,
                        tools=current_tools,
                        tool_choice=tool_choice,
                    )
                    total_tokens += step_tokens
                except Exception as llm_err:
                    logger.error(f"[{agent_role}] LLM 调用失败（step {step+1}）: {llm_err}")
                    return {
                        "status": "blocked",
                        "blocker_reason": (
                            f"LLM service is unavailable after retry: {llm_err}\n"
                            "Suggestion: check network connectivity or proxy settings, then retry this step later."
                        ),
                        "tokens": total_tokens,
                    }

                if not (hasattr(response, "tool_calls") and response.tool_calls):
                    plain_text = getattr(response, "content", None) or str(response) or ""
                    if plain_text.strip():
                        logger.info(
                            f"[{agent_role}] Step {step+1}: no tool call; accepting plain text "
                            f"as deliverable, len={len(plain_text)}, tokens={total_tokens}"
                        )
                        await _emit("Submitting deliverable...")
                        return {
                            "status": "completed",
                            "deliverable": plain_text.strip(),
                            "tokens": total_tokens,
                        }

                    no_tool_streak += 1
                    logger.warning(
                        f"[{agent_role}] Step {step+1}: 无工具调用 "
                        f"(streak={no_tool_streak}) 文本前80字: {plain_text[:80]!r}"
                    )

                    history.append({"role": "assistant", "content": plain_text})
                    if no_tool_streak >= _MAX_NO_TOOL_STREAK:
                        history.append({
                            "role": "user",
                            "content": (
                                "You must report the result by calling a tool, not by outputting text only.\n"
                                "- Task complete -> immediately call `submit_task_deliverable` and put the result in `deliverable`\n"
                                "- Unable to complete -> call `report_system_blocker` and explain why\n"
                                "- Need more information -> call the appropriate tool and continue"
                            ),
                        })
                    else:
                        history.append({
                            "role": "user",
                            "content": "Continue. If the task is complete, call `submit_task_deliverable` to submit the result.",
                        })
                    continue

                no_tool_streak = 0
                history.append(_build_assistant_tool_message(response))

                protocol_calls: List[Any] = []
                real_calls: List[Any] = []
                for tc in response.tool_calls:
                    if tc.function.name in ("submit_task_deliverable", "report_system_blocker"):
                        protocol_calls.append(tc)
                    else:
                        real_calls.append(tc)

                if real_calls:
                    tool_summary = ", ".join(tc.function.name for tc in real_calls)
                    await _emit(f"Calling tools: {tool_summary}...")

                    async def _exec(tc):
                        tool_name = tc.function.name
                        try:
                            args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            args = {}
                        logger.info(f"[{agent_role}] 调用工具: {tool_name}({list(args.keys())})")
                        try:
                            result = await execute_tool(tool_name, args)
                        except Exception as e:
                            result = f"[Tool execution error] {type(e).__name__}: {e}"
                        observation, failed = _structured_observation(tool_name, str(result))
                        return tc, observation, failed

                    tool_results = await asyncio.gather(*[_exec(tc) for tc in real_calls])
                    await _emit(f"Processing results from {tool_summary}...")
                    failed_count = 0
                    for tc, observation, failed in tool_results:
                        if failed:
                            failed_count += 1
                        history.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": observation,
                        })
                    if failed_count:
                        consecutive_tool_failures += failed_count
                    else:
                        consecutive_tool_failures = 0
                    if consecutive_tool_failures >= _MAX_CONSECUTIVE_TOOL_FAILURES:
                        force_finalization = True
                        finalization_reason = (
                            "The controller detected repeated tool failures in this sub-task. "
                            "Stop calling information-gathering tools now. Choose exactly one protocol tool:\n"
                            "- `submit_task_deliverable` if you can provide a useful best-effort result from the available context, clearly marking unverified parts.\n"
                            "- `report_system_blocker` if the missing tool results make the task impossible to complete safely.\n"
                        )
                        logger.warning(
                            f"[{agent_role}] 连续工具失败 {consecutive_tool_failures} 次，进入强制收口"
                        )
                        continue

                if protocol_calls:
                    if real_calls:
                        logger.warning(
                            f"[{agent_role}] 同一轮混用协议工具与真实工具，"
                            f"真实工具结果已记录但执行终止于协议工具。"
                        )

                    for tc in protocol_calls:
                        history.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": "protocol acknowledged",
                        })

                    chosen = next(
                        (tc for tc in protocol_calls if tc.function.name == "submit_task_deliverable"),
                        protocol_calls[0],
                    )
                    try:
                        arguments = json.loads(chosen.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}

                    if chosen.function.name == "submit_task_deliverable":
                        deliverable = arguments.get("deliverable", "")
                        logger.info(
                            f"[{agent_role}] 任务完成，deliverable 长度={len(deliverable)}, tokens={total_tokens}"
                        )
                        await _emit("Submitting deliverable...")
                        return {"status": "completed", "deliverable": deliverable, "tokens": total_tokens}

                    reason = arguments.get("reason", "")
                    logger.warning(f"[{agent_role}] 任务阻塞: {reason[:80]}, tokens={total_tokens}")
                    await _emit(f"Reporting blocker: {reason[:60]}")
                    return {"status": "blocked", "blocker_reason": reason, "tokens": total_tokens}

            logger.error(f"[{agent_role}] 超过最大 ReAct 步数 {MAX_REACT_STEPS}，强制阻塞")
            return {
                "status": "blocked",
                "blocker_reason": (
                    f"Execution exceeded the maximum step limit ({MAX_REACT_STEPS} steps). "
                    "Ask the Leader to split the task or provide clearer instructions."
                ),
                "tokens": total_tokens,
            }
        finally:
            if token is not None:
                reset_current_tool_context(token)


async def _build_bundle_skills_block(available_tools: Optional[List[str]]) -> str:
    """Build the available Skill bundle guide from employee skills."""
    try:
        from storage.skill_store import SkillStore  # noqa: PLC0415

        all_skills = await SkillStore().list_all(include_disabled=False)
    except Exception as e:
        logger.warning(f"[sub_agent] 读取 skill 池失败，跳过 bundle 指南: {e}")
        return ""

    allow_set = set(available_tools) if available_tools is not None else None
    bundles = [
        s for s in all_skills
        if s.kind == "bundle" and (allow_set is None or s.slug in allow_set)
    ]
    if not bundles:
        return ""

    lines = ["## Available Skill Bundles (load with `use_skill(slug=...)`, then follow SKILL.md)"]
    for s in bundles:
        summary = ""
        if isinstance(s.config, dict):
            summary = (s.config.get("summary") or "").strip()
        desc = (summary or s.description or "").replace("\n", " ")[:160]
        lines.append(f"- `{s.slug}` — **{s.name}**: {desc}")
    return "\n".join(lines) + "\n"


sub_agent_executor = SubAgentExecutor()
