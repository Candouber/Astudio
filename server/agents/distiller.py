"""Context distiller for task memory summaries."""
from agents.context import ContextBuilder
from services.llm_service import llm_service
from storage.task_store import TaskStore


class ContextDistiller:
    def __init__(self):
        self.task_store = TaskStore()

    async def distill_sub_task(self, sub_task_id: str, agent_role: str, step_label: str, input_text: str, output_text: str) -> str:
        """Distill one sub-task run into a structured summary stored in sub_tasks."""
        if not output_text:
            return ""

        system_prompt = ContextBuilder.build_distiller(
            agent_role=agent_role,
            step_label=step_label,
            input_text=input_text,
            output_text=output_text
        )

        response_str = await llm_service.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Concise summarize the execution process and conclusions above."}
            ],
            role="distillation",
            stream=False,
            temperature=0.1,
        )

        summary = str(response_str).strip()
        await self.task_store.update_sub_task_summary(sub_task_id, summary)

        return summary

distiller = ContextDistiller()
