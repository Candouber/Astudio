"""
定时任务调度服务
"""
import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

from models.schedule import ScheduledJob, ScheduledJobCreate
from storage.schedule_store import ScheduleStore

ScheduleCallback = Callable[[ScheduledJob, str], Awaitable[str | None]]


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _now() -> datetime:
    return datetime.now()


def _parse_cron_field(field: str, min_value: int, max_value: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, step_raw = part.split("/", 1)
            step = int(step_raw)
            if step <= 0:
                raise ValueError("cron step must be positive")

        if part == "*":
            start, end = min_value, max_value
        elif "-" in part:
            start_raw, end_raw = part.split("-", 1)
            start, end = int(start_raw), int(end_raw)
        else:
            start = end = int(part)

        if start < min_value or end > max_value or start > end:
            raise ValueError("cron field out of range")
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError("empty cron field")
    return values


def compute_next_run(job: ScheduledJob | ScheduledJobCreate, now: datetime | None = None) -> datetime | None:
    """计算下一次运行时间。支持 at/every/常见 5 段 cron 表达式。"""
    now = now or _now()

    if job.schedule_kind == "at":
        return job.at_time if job.at_time and job.at_time > now else None

    if job.schedule_kind == "every":
        if not job.every_seconds or job.every_seconds <= 0:
            return None
        return now + timedelta(seconds=job.every_seconds)

    if job.schedule_kind != "cron" or not job.cron_expr:
        return None

    parts = job.cron_expr.split()
    if len(parts) != 5:
        raise ValueError("cron_expr must contain 5 fields: minute hour day month weekday")

    minute_set = _parse_cron_field(parts[0], 0, 59)
    hour_set = _parse_cron_field(parts[1], 0, 23)
    day_set = _parse_cron_field(parts[2], 1, 31)
    month_set = _parse_cron_field(parts[3], 1, 12)
    weekday_set = _parse_cron_field(parts[4], 0, 7)
    if 7 in weekday_set:
        weekday_set.add(0)
        weekday_set.discard(7)

    tz = ZoneInfo(job.timezone) if job.timezone else _local_tz()
    cursor = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    cursor = (cursor + timedelta(minutes=1)).replace(second=0, microsecond=0)

    # 最多向前扫描一年，足够覆盖常见日/月/周计划。
    for _ in range(366 * 24 * 60):
        cron_weekday = (cursor.weekday() + 1) % 7  # cron: 0=Sunday
        if (
            cursor.minute in minute_set
            and cursor.hour in hour_set
            and cursor.day in day_set
            and cursor.month in month_set
            and cron_weekday in weekday_set
        ):
            return cursor.astimezone(_local_tz()).replace(tzinfo=None)
        cursor += timedelta(minutes=1)
    return None


class SchedulerService:
    """轻量 SQLite 定时任务调度器。"""

    def __init__(self, store: ScheduleStore | None = None, poll_seconds: int = 5):
        self.store = store or ScheduleStore()
        self.poll_seconds = poll_seconds
        self.on_job: ScheduleCallback | None = None
        self._loop_task: asyncio.Task | None = None
        self._running = False
        self._active_jobs: set[str] = set()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.store.mark_stale_running_runs()
        await self.recompute_next_runs()
        self._loop_task = asyncio.create_task(self._loop())
        logger.info("Scheduler service started")

    def stop(self) -> None:
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            self._loop_task = None

    async def recompute_next_runs(self) -> None:
        jobs = await self.store.list_jobs(include_disabled=False)
        now = _now()
        for job in jobs:
            if job.next_run_at is None:
                await self.store.set_next_run(job.id, compute_next_run(job, now))

    async def add_job(self, req: ScheduledJobCreate) -> ScheduledJob:
        next_run = compute_next_run(req)
        return await self.store.create_job(req, next_run)

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"Scheduler tick failed: {e}")
            await asyncio.sleep(self.poll_seconds)

    async def tick(self) -> None:
        due_jobs = await self.store.due_jobs(_now())
        for job in due_jobs:
            if job.id in self._active_jobs:
                continue
            asyncio.create_task(self._run_job(job))

    async def run_now(self, job_id: str) -> bool:
        job = await self.store.get_job(job_id)
        if not job:
            return False
        await self._run_job(job)
        return True

    async def _run_job(self, job: ScheduledJob) -> None:
        if job.overlap_policy == "skip" and await self.store.has_running_run(job.id):
            next_run = compute_next_run(job)
            await self.store.mark_job_result(
                job.id,
                status="skipped",
                last_run_at=_now(),
                next_run_at=next_run,
                error="previous run is still active",
            )
            return

        self._active_jobs.add(job.id)
        run = await self.store.create_run(job.id)
        started_at = _now()
        task_id: str | None = None
        error: str | None = None
        status = "ok"
        next_run: datetime | None = None
        enabled: bool | None = None

        try:
            if not self.on_job:
                raise RuntimeError("scheduler on_job callback is not configured")
            task_id = await self.on_job(job, run.id)
        except Exception as e:
            status = "error"
            error = str(e)
            logger.exception(f"Scheduled job {job.id} failed: {e}")
        finally:
            if job.schedule_kind == "at":
                next_run = None
                enabled = False
            else:
                next_run = compute_next_run(job)
            await self.store.finish_run(run.id, status=status, task_id=task_id, error=error)
            await self.store.mark_job_result(
                job.id,
                status=status,
                last_run_at=started_at,
                next_run_at=next_run,
                error=error,
                enabled=enabled,
            )
            if job.schedule_kind == "at" and job.delete_after_run:
                await self.store.delete_job(job.id)
            self._active_jobs.discard(job.id)


scheduler_service = SchedulerService()
