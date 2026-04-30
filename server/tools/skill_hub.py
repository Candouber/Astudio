"""
Skill 元技能 —— 让 agent 能"管理 Skill 包"。

三个内置：
  - `use_skill(slug)`：加载本地已安装的 bundle skill 的 SKILL.md，注入到下一轮 Observation。
  - `find_skill(query, provider?, limit?)`：调 ClawHub 搜索 API，返回候选。
  - `skill_creator(slug, name, goal, category?)`：让 LLM 生成 SKILL.md 并落地注册。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from services.llm_service import llm_service
from services.skill_import import (
    CLAWHUB_API_BASE,
    SkillImportError,
    register_local_bundle,
)
from storage.skill_store import SkillStore

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_ROOT = _PROJECT_ROOT / "data"


def _resolve_skill_local_dir(local_dir_rel: str) -> Path:
    """兼容多种 local_dir 存储格式，返回真实 skill 目录。

    历史数据里 `local_dir` 可能是：
    - `workspace/skills/foo`   （相对 data/）
    - `data/workspace/skills/foo`（相对项目根）
    - 绝对路径
    """
    raw = (local_dir_rel or "").strip()
    if not raw:
        return _PROJECT_ROOT

    path = Path(raw)
    if path.is_absolute():
        return path

    project_relative = (_PROJECT_ROOT / path).resolve()
    data_relative = (_DATA_ROOT / path).resolve()

    if project_relative.exists():
        return project_relative
    if data_relative.exists():
        return data_relative

    # 默认优先走 data 相对路径，因为当前导入器写入的是 workspace/skills/...
    if raw.startswith("workspace/"):
        return data_relative
    return project_relative

# ── SKILL schemas (OpenAI function-calling) ───────────────────────────────
USE_SKILL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "use_skill",
        "description": (
            "Load a locally installed Skill bundle (kind=bundle) and return the full SKILL.md content as this turn's Observation. "
            "Then continue by following SKILL.md. Use read_file / list_files / execute_code to access resource files in the skill directory; "
            "if the Skill needs terminal or browser automation, use sandbox_run_command inside the task sandbox."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Slug in the Skill pool, for example acme__data-analysis or local__my-skill",
                },
            },
            "required": ["slug"],
        },
    },
}

FIND_SKILL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "find_skill",
        "description": (
            "Search ClawHub / SkillHub community for suitable Skill bundles by natural language, returning candidate slug / name / description / install URL. "
            "After finding a candidate, return the URL to the user so they can install it via URL import on the Skill Pool page."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Capability to search for, for example 'analyze CSV' or 'generate PPT'"},
                "provider": {
                    "type": "string",
                    "enum": ["clawhub", "skillhub_cn"],
                    "description": "Source platform, default clawhub",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Maximum returned items, up to 10",
                },
            },
            "required": ["query"],
        },
    },
}

SKILL_CREATOR_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "skill_creator",
        "description": (
            "Use an LLM to generate a new Skill bundle (SKILL.md) from a natural-language goal, save it under local data/workspace/skills/, "
            "and automatically register it as a bundle skill. Suitable when no existing skill is available and a custom SOP should be packaged."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Slug for the new skill. Must be unique and contain only letters, digits, underscores, or hyphens",
                    "pattern": "^[a-zA-Z0-9_\\-]+$",
                },
                "name": {"type": "string", "description": "Display name"},
                "goal": {
                    "type": "string",
                    "description": "What problem this skill should solve. Be specific about inputs, processing steps, and outputs",
                },
                "category": {"type": "string", "description": "Category, default Custom"},
            },
            "required": ["slug", "name", "goal"],
        },
    },
}


# ── 实现 ────────────────────────────────────────────────────────────────
async def use_skill(slug: str) -> str:
    store = SkillStore()
    skill = await store.get(slug)
    if not skill:
        return f"[Error] slug={slug} was not found in the Skill pool."
    if not skill.enabled:
        return f"[Error] skill={slug} is currently disabled."
    if skill.kind != "bundle":
        return f"[Error] slug={slug} is not a bundle skill (kind={skill.kind}) and cannot be loaded with use_skill."

    config = skill.config or {}
    local_dir_rel = config.get("local_dir")
    if not local_dir_rel:
        return f"[Error] skill={slug} is missing local_dir configuration."

    local_dir = _resolve_skill_local_dir(local_dir_rel)
    skill_md_path = local_dir / "SKILL.md"
    if not skill_md_path.exists():
        return f"[Error] SKILL.md not found: {skill_md_path}"

    try:
        md = skill_md_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"[Error] Failed to read SKILL.md: {e}"

    files = config.get("files") or []
    resource_hint = ""
    if files:
        resource_hint = (
            f"\n\n### Skill Resource Directory\nDirectory: `{local_dir_rel}`\n"
            f"Use read_file/list_files/execute_code to access the following files. "
            f"If terminal/CLI access is needed, call sandbox_run_command in the task sandbox:\n"
            + "\n".join(f"- {f}" for f in files)
        )

    return (
        f"Loaded Skill [{skill.name}] (slug={slug}).\n"
        f"Continue strictly according to the SKILL.md guide below:\n\n"
        f"---SKILL.md START---\n{md}\n---SKILL.md END---"
        f"{resource_hint}"
    )


async def find_skill(query: str, provider: str = "clawhub", limit: int = 5) -> str:
    query = (query or "").strip()
    if len(query) < 2:
        return "[Error] query must contain at least 2 characters."
    limit = max(1, min(int(limit or 5), 10))

    if provider == "skillhub_cn":
        return (
            "[Hint] skillhub.cn search API is not integrated yet. "
            "Use provider=clawhub instead, or manually choose a URL from https://skillhub.cn/skills/find-skills and return it."
        )

    url = f"{CLAWHUB_API_BASE}/search"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params={"q": query, "limit": limit})
    except httpx.HTTPError as e:
        logger.warning(f"[find_skill] ClawHub 调用失败: {e}")
        return f"[Error] Cannot connect to ClawHub search: {e}"

    if resp.status_code >= 400:
        return f"[Error] ClawHub search returned {resp.status_code}: {resp.text[:200]}"

    try:
        data = resp.json()
    except ValueError:
        return "[Error] ClawHub search response is not JSON."

    skills = data.get("skills") or []
    if not skills:
        return f"No Skill matching '{query}' was found. You can create one with skill_creator."

    lines = [f"Found {len(skills)} candidate Skills for '{query}' (paste the URL into the Skill Pool page to import):"]
    for item in skills:
        slug = item.get("slug", "")
        name = item.get("name", slug)
        desc = (item.get("description") or "").strip()
        installs = item.get("totalInstalls", 0)
        source = item.get("sourceIdentifier", "")
        install_url = f"https://clawhub.ai/u/{source.split('/')[0]}/skills/{slug}" if source else ""
        lines.append(
            f"- **{name}** (`{slug}`) downloads={installs}\n  {desc}"
            + (f"\n  Install URL: {install_url}" if install_url else "")
        )
    return "\n".join(lines)


# ── skill_creator：用 LLM 生成 SKILL.md ──────────────────────────────────
_SKILL_MD_SYSTEM = """You are a Claude Skill author. The user will provide a goal description.
Produce a Markdown document that follows the SKILL.md conventions:

1. The top must include YAML frontmatter with at least `name`, `description`, `license`, and `when_to_use`.
2. Organize the body using these sections: Overview / When to use this / Workflow / Inputs / Outputs / Guardrails.
3. Write Workflow as numbered steps. Each step must explain what to do and which tool to use. Runtime tools available to the agent include:
   read_file, write_file, list_files, execute_code, web_search,
   ensure_sandbox, sandbox_list_files, sandbox_read_file, sandbox_write_file,
   sandbox_run_command, sandbox_start_preview. You may assume a sandbox Python environment when necessary.
4. Do not reference nonexistent external scripts or dependencies. If a script is truly needed, include the complete implementation for scripts/<file>.py.

**Output only the SKILL.md text itself**. Do not add explanations before or after it. Do not wrap it in a code fence.
"""


async def skill_creator(slug: str, name: str, goal: str, category: str = "Custom") -> str:
    slug = (slug or "").strip()
    name = (name or "").strip() or slug
    goal = (goal or "").strip()
    if not slug or not goal:
        return "[Error] slug / goal cannot be empty."

    store = SkillStore()
    if await store.get(slug):
        return f"[Error] slug={slug} already exists. Choose another name."

    user_prompt = (
        f"Goal: {goal}\n\n"
        f"Skill display name: {name}\nSkill slug: {slug}\n"
        "Generate SKILL.md."
    )
    messages = [
        {"role": "system", "content": _SKILL_MD_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    try:
        skill_md = await llm_service.chat(messages=messages, temperature=0.3)
    except Exception as e:
        logger.warning(f"[skill_creator] LLM 生成失败: {e}")
        return f"[Error] Failed to generate SKILL.md: {e}"

    if not skill_md or not skill_md.strip():
        return "[Error] LLM returned empty content. Adjust the goal and retry."

    skill_md = _strip_codefence(skill_md.strip())

    try:
        skill = await register_local_bundle(
            slug=slug,
            name=name,
            description=goal[:180],
            category=category,
            skill_md=skill_md,
        )
    except SkillImportError as e:
        return f"[Error] Registration failed: {e}"

    return (
        f"Generated and registered skill={skill.slug} (name={skill.name}).\n"
        f"Storage path: {skill.config.get('local_dir')}\n\n"
        f"SKILL.md preview (first 500 characters):\n{skill_md[:500]}"
    )


def _strip_codefence(text: str) -> str:
    """LLM 有时候会把 markdown 裹进 ``` 代码块，剥掉外层围栏。"""
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return s


def _ensure_json_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


__all__ = [
    "USE_SKILL_SCHEMA",
    "FIND_SKILL_SCHEMA",
    "SKILL_CREATOR_SCHEMA",
    "use_skill",
    "find_skill",
    "skill_creator",
]
