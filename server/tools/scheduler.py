"""Scheduled task tool."""
from datetime import datetime

from core.scheduler import scheduler_service
from models.schedule import ScheduledJobCreate


def _fmt_dt(value) -> str:
    if not value:
        return "-"
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


async def schedule_task(
    action: str,
    message: str = "",
    name: str = "",
    every_seconds: int | None = None,
    cron_expr: str | None = None,
    timezone: str | None = None,
    at: str | None = None,
    job_id: str | None = None,
    target_studio_id: str | None = None,
) -> str:
    """Create, list, delete, or immediately run AStudio scheduled tasks."""
    if action == "add":
        if not message.strip():
            return "[Error] Creating a scheduled task requires message."

        if every_seconds:
            schedule_kind = "every"
            at_time = None
        elif cron_expr:
            schedule_kind = "cron"
            at_time = None
        elif at:
            schedule_kind = "at"
            try:
                at_time = datetime.fromisoformat(at)
            except ValueError:
                return "[Error] at must be an ISO datetime, for example 2026-04-14T09:00:00."
        else:
            return "[Error] Creating a scheduled task requires every_seconds, cron_expr, or at."

        try:
            job = await scheduler_service.add_job(ScheduledJobCreate(
                name=name or message[:30],
                message=message,
                schedule_kind=schedule_kind,
                at_time=at_time,
                every_seconds=every_seconds,
                cron_expr=cron_expr,
                timezone=timezone,
                target_studio_id=target_studio_id,
                approval_policy="auto_execute",
                delete_after_run=schedule_kind == "at",
                created_by="agent",
            ))
        except Exception as e:
            return f"[Error] Failed to create scheduled task: {e}"
        return f"[Created] {job.name} (id: {job.id}), next run: {_fmt_dt(job.next_run_at)}"

    if action == "list":
        jobs = await scheduler_service.store.list_jobs(include_disabled=True)
        if not jobs:
            return "No scheduled tasks."
        lines = []
        for job in jobs:
            timing = job.schedule_kind
            if job.schedule_kind == "every":
                timing = f"every {job.every_seconds}s"
            elif job.schedule_kind == "cron":
                timing = f"cron {job.cron_expr}" + (f" ({job.timezone})" if job.timezone else "")
            elif job.schedule_kind == "at":
                timing = f"at {_fmt_dt(job.at_time)}"
            lines.append(
                f"- {job.name} (id: {job.id}, {'enabled' if job.enabled else 'disabled'}, {timing})\n"
                f"  Next run: {_fmt_dt(job.next_run_at)}; last status: {job.last_status or '-'}\n"
                f"  Task: {job.message}"
            )
        return "Scheduled tasks:\n" + "\n".join(lines)

    if action == "remove":
        if not job_id:
            return "[Error] Removing a scheduled task requires job_id."
        return "[Removed]" if await scheduler_service.store.delete_job(job_id) else "[Not found] The specified scheduled task does not exist."

    if action == "run_now":
        if not job_id:
            return "[Error] Running now requires job_id."
        return "[Triggered]" if await scheduler_service.run_now(job_id) else "[Not found] The specified scheduled task does not exist."

    return f"[Error] Unknown action: {action}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "schedule_task",
        "description": "Create, list, delete, or immediately run AStudio scheduled tasks. When triggered, a scheduled task creates a normal AStudio task and hands it to Agent Zero / the studio system for execution.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "list", "remove", "run_now"]},
                "message": {"type": "string", "description": "Task content to hand to AStudio when the schedule triggers"},
                "name": {"type": "string", "description": "Scheduled task name"},
                "every_seconds": {"type": "integer", "description": "Fixed interval in seconds"},
                "cron_expr": {"type": "string", "description": "Five-field cron expression, for example 0 9 * * 1-5"},
                "timezone": {"type": "string", "description": "IANA timezone, for example Asia/Shanghai; only used with cron_expr"},
                "at": {"type": "string", "description": "One-time ISO datetime, for example 2026-04-14T09:00:00"},
                "job_id": {"type": "string", "description": "Scheduled task ID, used for remove/run_now"},
                "target_studio_id": {"type": "string", "description": "Optional studio to assign after trigger; empty means Agent Zero routes it"},
            },
            "required": ["action"],
        },
    },
}
