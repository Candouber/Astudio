"""Context builder for prompt templates."""
from pathlib import Path
from typing import Any

from loguru import logger

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


class ContextBuilder:
    """Inject runtime data into agent system prompt templates."""

    @classmethod
    def _read_template(cls, template_name: str) -> str:
        """Read a prompt template from the template directory."""
        template_path = TEMPLATES_DIR / template_name
        if not template_path.exists():
            # Allow callers to omit the .md suffix.
            if not template_name.endswith(".md"):
                template_path = TEMPLATES_DIR / f"{template_name}.md"
            if not template_path.exists():
                logger.error(f"Template not found: {template_path}")
                raise FileNotFoundError(f"Cannot find prompt template: {template_name}")

        return template_path.read_text(encoding="utf-8")

    @classmethod
    def build(cls, template_name: str, **kwargs: Any) -> str:
        """
        Load a Markdown template and inject the provided context variables.
        Literal braces in templates, such as JSON examples, must be escaped as {{}}.
        """
        template_content = cls._read_template(template_name)

        try:
            return template_content.format(**kwargs)
        except KeyError as e:
            logger.error(f"Missing required context variable for {template_name}: {e}")
            raise ValueError(f"Failed to build prompt for {template_name}. Missing variable: {e}")
        except Exception as e:
            logger.error(f"Failed to build prompt template {template_name}: {e}")
            raise

    @classmethod
    def build_agent_zero(cls, studio_cards_json: str) -> str:
        return cls.build("AGENT_ZERO.md", studio_cards_json=studio_cards_json)

    @classmethod
    def build_leader_planning(
        cls,
        studio_name: str,
        sub_agents_list: str,
        task_goal: str,
        sub_agents_json: str,
        sub_agent_count: int = 0,
        user_facts: str = "",
        recent_topics: str = "",
        core_capabilities: str = "",
        task_count: int = 0,
        available_skills: str = "",
    ) -> str:
        return cls.build(
            "LEADER_PLANNING.md",
            studio_name=studio_name,
            sub_agents_list=sub_agents_list,
            task_goal=task_goal,
            sub_agents_json=sub_agents_json,
            sub_agent_count=sub_agent_count,
            user_facts=user_facts or "(No historical records yet.)",
            recent_topics=recent_topics or "(No recent tasks yet.)",
            core_capabilities=core_capabilities or "(No accumulated capabilities yet.)",
            task_count=task_count,
            available_skills=available_skills or "(No enabled skills are currently available.)",
        )

    @classmethod
    def build_employee(
        cls,
        agent_role: str,
        agent_md_content: str,
        soul_content: str,
        leader_input: str,
        bundle_skills_block: str = "",
    ) -> str:
        """
        `bundle_skills_block` is omitted from the prompt when empty.
        When present, it is assembled from the runtime Skill pool, for example:

        ## Available Skill Bundles (load with use_skill(slug=...), then follow SKILL.md)
        - `acme__data-analysis`  Data Analysis: Analyze CSV and JSON data
        - `local__report-writer`  Report Writer: Generate slides from a SOP
        """
        return cls.build(
            "EMPLOYEE.md",
            agent_role=agent_role,
            agent_md_content=agent_md_content or "(No persona card configured.)",
            soul_content=soul_content or "(No historical memory yet.)",
            leader_input=leader_input,
            bundle_skills_block=bundle_skills_block or "",
        )

    @classmethod
    def build_synthesis(cls, user_question: str, sub_agent_findings: str) -> str:
        return cls.build(
            "SYNTHESIS.md",
            user_question=user_question,
            sub_agent_findings=sub_agent_findings
        )

    @classmethod
    def build_distiller(cls, agent_role: str, step_label: str, input_text: str, output_text: str) -> str:
        return cls.build(
            "DISTILLER.md",
            agent_role=agent_role,
            step_label=step_label,
            input_text=input_text,
            output_text=output_text
        )

    @classmethod
    def build_leader_review(cls, studio_name: str, original_spec: str, deliverable: str) -> str:
        return cls.build(
            "LEADER_REVIEW.md",
            studio_name=studio_name,
            original_spec=original_spec,
            deliverable=deliverable,
        )

    @classmethod
    def build_soul_update(cls, agent_role: str, step_label: str, distilled_summary: str) -> str:
        return cls.build(
            "SOUL_UPDATE.md",
            agent_role=agent_role,
            step_label=step_label,
            distilled_summary=distilled_summary,
        )

    @classmethod
    def build_hr_agent(cls, available_skills: str = "") -> str:
        """Build the HR recruiter system prompt."""
        return cls.build(
            "HR_AGENT.md",
            available_skills=available_skills or "(No enabled skills are currently available.)",
        )

    @classmethod
    def build_soul_compress(cls, agent_role: str, existing_soul: str, new_experience: str, max_chars: int) -> str:
        return cls.build(
            "SOUL_COMPRESS.md",
            agent_role=agent_role,
            existing_soul=existing_soul,
            new_experience=new_experience,
            max_chars=max_chars,
        )

    @classmethod
    def build_fact_extract(cls, task_question: str, clarification_qa: str) -> str:
        return cls.build(
            "FACT_EXTRACT.md",
            task_question=task_question,
            clarification_qa=clarification_qa,
        )

    @classmethod
    def build_system_task_classifier(cls) -> str:
        """Build the static system-management task classifier prompt."""
        return cls._read_template("SYSTEM_TASK_CLASSIFIER.md")
