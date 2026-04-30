"""
ToolRegistry — 把「Skill 池元信息」与「Python 工具实现」合并为唯一权威注册中心。

两种 skill 源头：
  1. `builtin`：slug 必须出现在 `_BUILTIN_IMPL` 中，绑定一个 async Python 实现 + schema。
  2. `http`  ：slug 的 schema 和执行方式都来自 `skill_pool.config`（HttpSkillConfig）。

三条典型调用链：
  - LLM 要看 function 列表 →       `await build_tool_schemas(slugs)`
  - LLM 真的调了某个 function →    `await execute_tool(slug, args)`
  - Leader/HR 规划期需要能力表 →   `await describe_available_skills()`
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

from tools.attachments import (
    FILE_ANALYSIS_SCHEMA,
    IMAGE_METADATA_SCHEMA,
    LIST_ATTACHMENTS_SCHEMA,
    READ_EXCEL_SHEET_SCHEMA,
    READ_PDF_TEXT_SCHEMA,
    READ_UPLOADED_FILE_SCHEMA,
    file_analysis,
    image_metadata,
    list_attachments,
    read_excel_sheet,
    read_pdf_text,
    read_uploaded_file,
)
from tools.browser_search import SCHEMA as BROWSER_SEARCH_SCHEMA
from tools.browser_search import browser_search
from tools.code_runner import SCHEMA as EXECUTE_CODE_SCHEMA
from tools.code_runner import execute_code
from tools.file_ops import (
    LIST_FILES_SCHEMA,
    READ_FILE_SCHEMA,
    WRITE_FILE_SCHEMA,
    list_files,
    read_file,
    write_file,
)
from tools.sandbox_ops import (
    ENSURE_SANDBOX_SCHEMA,
    SANDBOX_LIST_FILES_SCHEMA,
    SANDBOX_READ_FILE_SCHEMA,
    SANDBOX_RUN_COMMAND_SCHEMA,
    SANDBOX_START_PREVIEW_SCHEMA,
    SANDBOX_WRITE_FILE_SCHEMA,
    ensure_sandbox,
    sandbox_list_files,
    sandbox_read_file,
    sandbox_run_command,
    sandbox_start_preview,
    sandbox_write_file,
)
from tools.scheduler import SCHEMA as SCHEDULE_TASK_SCHEMA
from tools.scheduler import schedule_task
from tools.skill_hub import (
    FIND_SKILL_SCHEMA,
    SKILL_CREATOR_SCHEMA,
    USE_SKILL_SCHEMA,
    find_skill,
    skill_creator,
    use_skill,
)
from tools.web_search import SCHEMA as WEB_SEARCH_SCHEMA
from tools.web_search import web_search

# ── 协议工具（员工上报结果用，始终可用，不来自 skill 池）──────────────────────
SUBMIT_DELIVERABLE_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_task_deliverable",
        "description": "Call this tool when you have successfully completed all necessary steps and are ready to submit the final result.",
        "parameters": {
            "type": "object",
            "properties": {
                "deliverable": {
                    "type": "string",
                    "description": "Complete final deliverable, including conclusions, code, reports, or other results",
                },
            },
            "required": ["deliverable"],
        },
    },
}

REPORT_BLOCKER_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "report_system_blocker",
        "description": "Call this tool to report an unavoidable blocker to the Leader, such as insufficient permissions, missing information, or unavailable external services.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Detailed blocker reason and what the Leader must provide or change so execution can continue",
                },
            },
            "required": ["reason"],
        },
    },
}

PROTOCOL_TOOL_NAMES = {"submit_task_deliverable", "report_system_blocker"}

# ── 内置 Python 实现 ───────────────────────────────────────────────────────────
# slug 必须与 skill_store.DEFAULT_SKILLS 里的 slug 对齐。
_BUILTIN_IMPL: Dict[str, Tuple[Callable, Dict[str, Any]]] = {
    "file_analysis": (file_analysis, FILE_ANALYSIS_SCHEMA),
    "list_attachments": (list_attachments, LIST_ATTACHMENTS_SCHEMA),
    "read_uploaded_file": (read_uploaded_file, READ_UPLOADED_FILE_SCHEMA),
    "read_excel_sheet": (read_excel_sheet, READ_EXCEL_SHEET_SCHEMA),
    "read_pdf_text": (read_pdf_text, READ_PDF_TEXT_SCHEMA),
    "image_metadata": (image_metadata, IMAGE_METADATA_SCHEMA),
    "web_search":    (web_search,    WEB_SEARCH_SCHEMA),
    "browser_search": (browser_search, BROWSER_SEARCH_SCHEMA),
    "execute_code":  (execute_code,  EXECUTE_CODE_SCHEMA),
    "read_file":     (read_file,     READ_FILE_SCHEMA),
    "write_file":    (write_file,    WRITE_FILE_SCHEMA),
    "list_files":    (list_files,    LIST_FILES_SCHEMA),
    "ensure_sandbox": (ensure_sandbox, ENSURE_SANDBOX_SCHEMA),
    "sandbox_list_files": (sandbox_list_files, SANDBOX_LIST_FILES_SCHEMA),
    "sandbox_read_file": (sandbox_read_file, SANDBOX_READ_FILE_SCHEMA),
    "sandbox_write_file": (sandbox_write_file, SANDBOX_WRITE_FILE_SCHEMA),
    "sandbox_run_command": (sandbox_run_command, SANDBOX_RUN_COMMAND_SCHEMA),
    "sandbox_start_preview": (sandbox_start_preview, SANDBOX_START_PREVIEW_SCHEMA),
    "schedule_task": (schedule_task, SCHEDULE_TASK_SCHEMA),
    # Skill 元技能：管理 / 加载 / 搜索 / 创建 Skill 包
    "use_skill":      (use_skill,      USE_SKILL_SCHEMA),
    "find_skill":     (find_skill,     FIND_SKILL_SCHEMA),
    "skill_creator":  (skill_creator,  SKILL_CREATOR_SCHEMA),
}


def builtin_slugs() -> List[str]:
    """当前 Python 层实际有实现的内置 slug 列表。"""
    return list(_BUILTIN_IMPL.keys())


def all_builtin_schemas() -> List[Dict[str, Any]]:
    """全部内置工具 schema（不含协议工具）。"""
    return [schema for _, schema in _BUILTIN_IMPL.values()]


# ── Skill 池层辅助函数 ─────────────────────────────────────────────────────────
async def _load_skill(slug: str):
    """按 slug 读取 skill_pool 条目（延迟导入以避免循环）。"""
    from storage.skill_store import SkillStore  # noqa: PLC0415
    return await SkillStore().get(slug)


def _is_slug_runnable(skill) -> bool:
    """slug 是否可以被 agent 真正使用。
    - builtin：必须在 `_BUILTIN_IMPL` 里有实现。
    - bundle ：必须 enabled 且 config 里有 local_dir（SKILL.md 实际存在的情况下，
               use_skill 运行时还会再 double-check 文件是否存在）。
    - 其他旧 kind（例如早期 P1 的 http）：一律视为不可执行，avoid LLM 看到残影。"""
    if not skill or not skill.enabled:
        return False
    if skill.kind == "builtin":
        return skill.slug in _BUILTIN_IMPL
    if skill.kind == "bundle":
        cfg = skill.config or {}
        return bool(cfg.get("local_dir"))
    return False


# ── 对外：查询 ─────────────────────────────────────────────────────────────────
async def list_available_slugs() -> List[str]:
    """Skill 池中所有"启用 + 可执行"的 slug（builtin 有实现的，加上任意 enabled 的 http）。"""
    try:
        from storage.skill_store import SkillStore  # noqa: PLC0415
        skills = await SkillStore().list_all(include_disabled=False)
    except Exception as e:
        logger.warning(f"[ToolRegistry] 读取 skill 池失败，退化到内置集合: {e}")
        return list(_BUILTIN_IMPL.keys())
    return [s.slug for s in skills if _is_slug_runnable(s)]


async def describe_available_skills() -> List[Dict[str, str]]:
    """给 Leader / HR 规划时用：返回 [{slug, name, description, kind}]。
    - builtin 直接作为 function 可用。
    - bundle 通过 `use_skill(slug=...)` 加载后按 SKILL.md 指南执行；这里在 description
      后面加一个标注让模型知道。"""
    try:
        from storage.skill_store import SkillStore  # noqa: PLC0415
        skills = await SkillStore().list_all(include_disabled=False)
    except Exception as e:
        logger.warning(f"[ToolRegistry] 读取 skill 池失败，退化到 DEFAULT_SKILLS: {e}")
        from storage.skill_store import DEFAULT_SKILLS  # noqa: PLC0415
        return [
            {"slug": s["slug"], "name": s.get("name", s["slug"]),
             "description": s.get("description", ""), "kind": "builtin"}
            for s in DEFAULT_SKILLS if s["slug"] in _BUILTIN_IMPL
        ]
    out: List[Dict[str, str]] = []
    for s in skills:
        if not _is_slug_runnable(s):
            continue
        desc = s.description or ""
        if s.kind == "bundle":
            desc = f"{desc} (Skill bundle; first call use_skill(slug='{s.slug}') to load SKILL.md)".strip()
        out.append({
            "slug": s.slug, "name": s.name or s.slug,
            "description": desc, "kind": s.kind,
        })
    return out


# ── 对外：拼给 LLM 的 tools 列表 ───────────────────────────────────────────────
async def build_tool_schemas(tool_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    **运行期唯一入口**：给一组 slug（或 None 表全量），返回 OpenAI tools 列表。

    处理规则：
      - builtin slug → 直接附加它的 schema
      - bundle slug  → 不单独暴露为 function（模型调用方式是 `use_skill(slug=...)`），
                       但检测到就记录一下，最后如果用户原本没显式带上 use_skill，
                       自动把它补进来，避免"有 bundle 但调不了"。
      - 其他/未知 slug → 静默跳过
      - 始终附带 submit_task_deliverable / report_system_blocker
    """
    if tool_names is None:
        chosen_slugs = await list_available_slugs()
    else:
        chosen_slugs = [s for s in tool_names if s not in PROTOCOL_TOOL_NAMES]

    schemas: List[Dict[str, Any]] = []
    seen: set[str] = set()
    has_bundle = False
    bundle_runtime_helpers = (
        "use_skill",
        "ensure_sandbox",
        "sandbox_list_files",
        "sandbox_read_file",
        "sandbox_write_file",
        "sandbox_run_command",
        "sandbox_start_preview",
    )
    file_analysis_helpers = (
        "list_attachments",
        "read_uploaded_file",
        "read_excel_sheet",
        "read_pdf_text",
        "image_metadata",
    )

    for slug in chosen_slugs:
        if slug in seen:
            continue
        if slug in _BUILTIN_IMPL:
            schemas.append(_BUILTIN_IMPL[slug][1])
            seen.add(slug)
            if slug == "file_analysis":
                for helper in file_analysis_helpers:
                    if helper in seen:
                        continue
                    schemas.append(_BUILTIN_IMPL[helper][1])
                    seen.add(helper)
            continue
        try:
            skill = await _load_skill(slug)
        except Exception as e:
            logger.warning(f"[ToolRegistry] 解析 slug={slug} 失败: {e}")
            continue
        if not _is_slug_runnable(skill):
            logger.debug(f"[ToolRegistry] slug={slug} 未启用或无实现，跳过")
            continue
        if skill.kind == "bundle":
            has_bundle = True

    # 如果员工被配置了 bundle skill，但没带运行期辅助工具，统一兜底补齐：
    # use_skill 负责加载 SKILL.md；sandbox_* 提供 bundle skill 常见的终端/文件/预览能力。
    if has_bundle:
        for helper in bundle_runtime_helpers:
            if helper in seen:
                continue
            schemas.append(_BUILTIN_IMPL[helper][1])
            seen.add(helper)

    return [*schemas, SUBMIT_DELIVERABLE_SCHEMA, REPORT_BLOCKER_SCHEMA]


# ── 兼容旧入口：仅基于内置实现的同步版 ─────────────────────────────────────────
def get_tool_schemas(tool_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """旧版同步接口：只看内置实现 + 协议工具。新代码请用 `build_tool_schemas`。"""
    if tool_names is None:
        base = all_builtin_schemas()
    else:
        base = []
        for slug in tool_names:
            if slug in PROTOCOL_TOOL_NAMES:
                continue
            impl = _BUILTIN_IMPL.get(slug)
            if impl is not None:
                base.append(impl[1])
    return [*base, SUBMIT_DELIVERABLE_SCHEMA, REPORT_BLOCKER_SCHEMA]


async def get_enabled_tool_schemas(tool_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """等同于 `build_tool_schemas`，保留旧名字兼容更早引用。"""
    return await build_tool_schemas(tool_names)


# ── 对外：执行 ─────────────────────────────────────────────────────────────────
async def execute_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    """执行真实工具并返回观察结果字符串。
    协议工具不经过这里（由 sub_agent 直接处理）。

    bundle 类型 skill 不会直接被 function-call —— 模型应该调 `use_skill(slug=...)`
    来加载它。万一 LLM 手滑直接调了 bundle slug，在这里给它一个清晰的提示。"""
    impl = _BUILTIN_IMPL.get(tool_name)
    if impl is not None:
        fn, _schema = impl
        try:
            result = await fn(**arguments)
            logger.debug(f"Tool [{tool_name}] (builtin) executed, len={len(str(result))}")
            return str(result)
        except TypeError as e:
            logger.error(f"Tool [{tool_name}] argument error: {e}")
            return f"[Argument error] Tool {tool_name} was called with invalid arguments: {e}"
        except Exception as e:
            logger.error(f"Tool [{tool_name}] execution error: {e}")
            return f"[Tool execution failed] {tool_name}: {e}"

    try:
        skill = await _load_skill(tool_name)
    except Exception as e:
        logger.error(f"[ToolRegistry] 查询 skill={tool_name} 失败: {e}")
        return f"[Error] Failed to read skill configuration: {tool_name}"

    if not _is_slug_runnable(skill):
        return f"[Error] Unknown tool: {tool_name} (not in Skill pool or disabled)"

    if skill.kind == "bundle":
        return (
            f"[Hint] slug={tool_name} is a Skill bundle and cannot be function-called directly. "
            f"Call use_skill(slug='{tool_name}') instead to load its SKILL.md and follow the guide."
        )

    return f"[Error] Unknown tool: {tool_name} (kind={skill.kind}, currently unsupported)"
