"""Studio leader executor for planning, hiring, review, and synthesis inputs."""
import json
import re
from typing import Any, Dict, List

from loguru import logger

from agents.context import ContextBuilder
from services.llm_service import llm_service
from storage.studio_store import StudioStore
from storage.task_store import TaskStore
from tools.registry import describe_available_skills
from utils.language import is_chinese, response_language_instruction


def _format_skills_for_prompt(skills: List[Dict[str, str]]) -> str:
    """Format Skill pool entries as a Markdown table for leader planning."""
    if not skills:
        return "(No enabled skills are currently available.)"
    lines = ["| slug | Name | Description |", "|---|---|---|"]
    for s in skills:
        desc = (s.get("description") or "").replace("|", "/").replace("\n", " ")
        lines.append(f"| `{s['slug']}` | {s.get('name') or s['slug']} | {desc} |")
    return "\n".join(lines)


def _strip_json_fence(value: Any) -> str:
    text = str(value).strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
    return text


def _extract_json_object(text: str) -> str:
    stripped = _strip_json_fence(text)
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if match:
        return match.group(0)
    return stripped


def _coerce_steps(result: dict) -> list:
    raw = result.get("steps")
    if raw is None:
        raw = result.get("sub_tasks")
    if raw is None:
        raw = result.get("tasks")
    if raw is None and isinstance(result.get("plan"), dict):
        raw = result["plan"].get("steps")
    if raw is None and isinstance(result.get("plan"), list):
        raw = result.get("plan")
    return raw if isinstance(raw, list) else []


def _log_plan_audit(
    task_id: str,
    studio_id: str,
    result: dict,
    sub_agents: list,
    response_str: Any,
    note: str = "",
) -> None:
    questions = result.get("questions")
    logger.info(
        "[LeaderPlanAudit] "
        f"task={task_id} studio={studio_id} note={note or '-'} "
        f"action={result.get('action')} steps={len(_coerce_steps(result))} "
        f"questions={len(questions) if isinstance(questions, list) else 0} "
        f"employees={len(sub_agents)} raw_preview={str(response_str)[:1200]}"
    )


class StudioLeaderExecutor:
    def __init__(self):
        self.studio_store = StudioStore()
        self.task_store = TaskStore()

    async def plan_sub_tasks(self, task_id: str, studio_id: str, task_goal: str) -> Dict[str, Any]:
        """
        部门 Leader 拆解工作簿大目标，分配到下属员工，或决定新招聘。
        """
        # Fetch the studio data
        studio = await self.studio_store.get(studio_id)
        if not studio:
            logger.error(f"Studio {studio_id} not found when planning task {task_id}")
            return {"action": "error", "message": "Studio not found"}

        sub_agents = studio.sub_agents
        sub_agents_list = ", ".join([sa.role for sa in sub_agents]) if sub_agents else "No employees"
        sub_agents_json = json.dumps(
            [{"role": sa.role, "skills": sa.skills} for sa in sub_agents],
            ensure_ascii=False
        )

        facts = studio.card.user_facts or []
        user_facts_str = "\n".join(f"- {f}" for f in facts) if facts else ""

        # 把工作室近期经验一并喂给 Leader —— 这才是"越用越聪明"的关键回路
        topics = studio.card.recent_topics or []
        capabilities = studio.card.core_capabilities or []
        recent_topics_str = "\n".join(f"- {t}" for t in topics[:10]) if topics else ""
        core_capabilities_str = "\n".join(f"- {c}" for c in capabilities[:15]) if capabilities else ""

        # 可用 skill 运行时从 Skill 池动态拉，失败降级为空字符串（模板里有兜底文案）
        try:
            available_skills = await describe_available_skills()
        except Exception as e:
            logger.warning(f"Failed to read Skill pool; leader planning will use an empty skill list: {e}")
            available_skills = []
        available_skills_str = _format_skills_for_prompt(available_skills)

        system_prompt = ContextBuilder.build_leader_planning(
            studio_name=studio.scenario,
            sub_agents_list=sub_agents_list,
            task_goal=task_goal,
            sub_agents_json=sub_agents_json,
            sub_agent_count=len(sub_agents),
            user_facts=user_facts_str,
            recent_topics=recent_topics_str,
            core_capabilities=core_capabilities_str,
            task_count=studio.card.task_count or 0,
            available_skills=available_skills_str,
            language_instruction=response_language_instruction(task_goal),
        )

        try:
            response_str = await llm_service.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "Follow the instructions above and output the orchestration plan as JSON."}
                ],
                role="studio_leader",
                stream=False,
                temperature=0.1,
            )
        except Exception as e:
            logger.error(f"Leader 规划 LLM 调用失败: {e}")
            return {
                "action": "error",
                "message": f"Leader planning LLM call failed: {type(e).__name__}: {e}",
            }

        try:
            result = json.loads(_extract_json_object(str(response_str)))

            # 安全检查
            action = result.get("action")
            if action not in ["plan", "recruit_employee", "need_clarification"]:
                if _coerce_steps(result):
                    logger.warning(f"Leader 输出未知 action={action} 但包含 steps，回退为 plan")
                    action = "plan"
                    result["action"] = "plan"
                else:
                    logger.warning(f"Leader 输出未知 action={action}，使用规划兜底")
                    _log_plan_audit(task_id, studio_id, result, sub_agents, response_str, "unknown_action")
                    return self._fallback_plan_or_recruit(task_goal, sub_agents, available_skills, "unknown_action")

            if action == "plan":
                steps = _coerce_steps(result)
                if not steps:
                    logger.warning(
                        "Leader returned empty plan; using fallback. "
                        f"studio={studio_id}, employees={len(sub_agents)}, raw={str(response_str)[:500]}"
                    )
                    _log_plan_audit(task_id, studio_id, result, sub_agents, response_str, "empty_plan")
                    return self._fallback_plan_or_recruit(task_goal, sub_agents, available_skills, "empty_plan")
                result["steps"] = steps

            _log_plan_audit(task_id, studio_id, result, sub_agents, response_str)
            return result
        except json.JSONDecodeError as e:
            logger.error(f"Planning failed to parse JSON: {e}\nResponse: {response_str}")
            # 降级：把 LLM 原始输出作为 need_clarification 抛给用户，避免任务无声卡死
            return {
                "action": "need_clarification",
                "questions": [
                    {
                        "id": "leader_parse_error",
                        "question": _localized_leader_parse_error(task_goal),
                        "type": "text",
                    }
                ],
                "message": f"Leader planning format error: {e}",
            }

    def _fallback_plan_or_recruit(
        self,
        task_goal: str,
        sub_agents: list,
        available_skills: list[dict],
        reason: str,
        allow_recruit: bool = True,
    ) -> Dict[str, Any]:
        if allow_recruit and len(sub_agents) < 2:
            return {
                "action": "recruit_employee",
                "employee_role": _fallback_employee_role(task_goal),
                "required_skills": _fallback_skill_slugs(available_skills),
                "message": f"Fallback recruit because leader returned no usable plan: {reason}",
            }

        assignee = _choose_fallback_assignee(sub_agents)
        return {
            "action": "plan",
            "steps": [
                {
                    "id": "s1",
                    "step_label": _fallback_step_label(task_goal),
                    "assign_to_role": assignee,
                    "input_context": _fallback_input_context(task_goal),
                    "depends_on": [],
                }
            ],
            "message": f"Fallback single-step plan because leader returned no usable plan: {reason}",
        }

    async def review_sub_task(
        self,
        studio_id: str,
        step_label: str,
        original_spec: str,
        deliverable: str,
    ) -> dict:
        """
        Leader 对单个子任务产出进行质检。
        返回 {"verdict": "accept"|"revision_needed", "feedback": str}
        """
        studio = await self.studio_store.get(studio_id)
        studio_name = studio.scenario if studio else "Studio"

        system_prompt = ContextBuilder.build_leader_review(
            studio_name=studio_name,
            original_spec=original_spec,
            deliverable=deliverable,
            language_instruction=response_language_instruction(original_spec or deliverable),
        )

        try:
            response_str = await llm_service.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "Start the quality review."},
                ],
                role="studio_leader",
                stream=False,
                temperature=0.0,
            )
        except Exception as e:
            # 质检 LLM 调用失败 — 降级为 accept，避免阻塞整个任务推进
            logger.warning(f"Leader 质检 LLM 调用失败，降级为 accept: {e}")
            return {"verdict": "accept", "feedback": f"[LEADER_REVIEW_FAILED] {e}"}

        try:
            text = str(response_str).strip()
            if text.startswith("```"):
                first_newline = text.find("\n")
                if first_newline != -1:
                    text = text[first_newline + 1:]
                if text.endswith("```"):
                    text = text[: -3]
                text = text.strip()
            result = json.loads(text)
            verdict = result.get("verdict", "accept")
            if verdict not in ("accept", "revision_needed"):
                verdict = "accept"
            return {"verdict": verdict, "feedback": result.get("feedback", "")}
        except Exception as e:
            logger.warning(f"Leader 质检解析失败: {e}，默认 accept")
            return {"verdict": "accept", "feedback": ""}

    async def compile_findings(self, task_id: str) -> str:
        """
        所有员工完成工作后，汇总所有有价值的摘要作为 findings 字符串返回给 CEO。
        """
        task = await self.task_store.get(task_id)
        if not task or not getattr(task, 'sub_tasks', None):
            return "No sub tasks provided."

        findings = []
        current_iteration_id = getattr(task, "current_iteration_id", None)
        current_sub_tasks = [
            st for st in task.sub_tasks
            if not current_iteration_id or getattr(st, "iteration_id", None) == current_iteration_id
        ]
        if not current_sub_tasks:
            return "No sub tasks provided."

        for st in current_sub_tasks:
            status_tag = f"[{st.status.upper()}]"
            summary = st.distilled_summary or st.deliverable or st.blocker_reason or "No feedback data"
            findings.append(
                f"--- Step: {st.step_label} (Assigned to: {st.assign_to_role}) ---\n"
                f"Result status: {status_tag}\n"
                f"Core feedback: {summary}\n"
            )

        return "\n".join(findings)

studio_leader = StudioLeaderExecutor()


def _localized_leader_parse_error(source_text: str) -> str:
    if is_chinese(source_text):
        return "Leader 规划结果无法解析为 JSON。请补充说明你的需求，或稍后重试。"
    return "The Leader planning response could not be parsed as JSON. Please clarify your request or try again later."


def _fallback_employee_role(source_text: str) -> str:
    text = source_text.lower()
    if any(token in text for token in ("源码", "代码", "code", "repository", "repo")):
        return "Codebase Researcher" if not is_chinese(source_text) else "代码库研究员"
    if any(token in text for token in ("搜索", "调研", "research", "search")):
        return "Research Specialist" if not is_chinese(source_text) else "信息研究员"
    if any(token in text for token in ("excel", "csv", "data", "数据")):
        return "Data Analyst" if not is_chinese(source_text) else "数据分析师"
    return "Execution Specialist" if not is_chinese(source_text) else "任务执行专员"


def _fallback_skill_slugs(available_skills: list[dict]) -> list[str]:
    available = {str(item.get("slug") or "") for item in available_skills}
    preferred = [
        "list_files",
        "read_file",
        "write_file",
        "execute_code",
        "web_search",
        "browser_search",
        "use_skill",
    ]
    selected = [slug for slug in preferred if slug in available]
    if selected:
        return selected[:5]
    return [slug for slug in available if slug][:3]


def _choose_fallback_assignee(sub_agents: list) -> str:
    if not sub_agents:
        return "Execution Specialist"
    with_skills = [agent for agent in sub_agents if getattr(agent, "skills", None)]
    chosen = with_skills[0] if with_skills else sub_agents[0]
    return getattr(chosen, "role", "") or "Execution Specialist"


def _fallback_step_label(source_text: str) -> str:
    return "完成用户请求并交付结果" if is_chinese(source_text) else "Complete the user request and deliver the result"


def _fallback_input_context(source_text: str) -> str:
    if is_chinese(source_text):
        return (
            "Leader 规划模型未返回可用拆解方案。请作为兜底执行步骤，直接围绕用户目标完成必要的检索、"
            "文件读取、分析、代码或文档产出，并给出清晰结论、产物路径和后续建议。\n\n"
            f"用户目标：{source_text}"
        )
    return (
        "The Leader planning model did not return a usable decomposition. As a fallback execution step, "
        "complete the necessary search, file reading, analysis, coding, or document output directly around "
        "the user's goal. Return clear conclusions, artifact paths, and next-step suggestions.\n\n"
        f"User goal: {source_text}"
    )
