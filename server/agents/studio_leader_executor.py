"""Studio leader executor for planning, hiring, review, and synthesis inputs."""
import json
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
            text = str(response_str).strip()
            # 更鲁棒的 fence 解析：兼容 ```json\n...\n``` 以及 ```\n...\n```
            if text.startswith("```"):
                first_newline = text.find("\n")
                if first_newline != -1:
                    text = text[first_newline + 1:]
                if text.endswith("```"):
                    text = text[: -3]
                text = text.strip()

            result = json.loads(text)

            # 安全检查
            action = result.get("action")
            if action not in ["plan", "recruit_employee", "need_clarification"]:
                logger.warning(f"Leader 输出未知action: {action}，回退为 plan")
                result["action"] = "plan"
                if "steps" not in result:
                    result["steps"] = []

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
