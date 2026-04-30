"""
定时任务存储层
"""
import uuid
from datetime import datetime
from typing import Optional

from models.schedule import ScheduledJob, ScheduledJobCreate, ScheduledJobRun, ScheduledJobUpdate
from storage.database import get_db


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _dt_value(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


class ScheduleStore:
    """定时任务数据存储"""

    async def list_jobs(self, include_disabled: bool = True) -> list[ScheduledJob]:
        db = await get_db()
        try:
            sql = "SELECT * FROM scheduled_jobs"
            params = ()
            if not include_disabled:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY enabled DESC, next_run_at IS NULL, next_run_at ASC, created_at DESC"
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_job(dict(row)) for row in rows]
        finally:
            await db.close()

    async def due_jobs(self, now: datetime) -> list[ScheduledJob]:
        db = await get_db()
        try:
            cursor = await db.execute(
                """SELECT * FROM scheduled_jobs
                   WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
                   ORDER BY next_run_at ASC""",
                (now.isoformat(),),
            )
            rows = await cursor.fetchall()
            return [self._row_to_job(dict(row)) for row in rows]
        finally:
            await db.close()

    async def get_job(self, job_id: str) -> Optional[ScheduledJob]:
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,))
            row = await cursor.fetchone()
            return self._row_to_job(dict(row)) if row else None
        finally:
            await db.close()

    async def create_job(self, req: ScheduledJobCreate, next_run_at: Optional[datetime]) -> ScheduledJob:
        job_id = str(uuid.uuid4())[:8]
        now = datetime.now()
        name = req.name.strip() or req.message[:30] or "定时任务"
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO scheduled_jobs
                   (id, name, message, enabled, schedule_kind, at_time, every_seconds,
                    cron_expr, timezone, target_studio_id, approval_policy, overlap_policy,
                    next_run_at, delete_after_run, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id, name, req.message, req.enabled, req.schedule_kind,
                    _dt_value(req.at_time), req.every_seconds, req.cron_expr, req.timezone,
                    req.target_studio_id, "auto_execute", req.overlap_policy,
                    _dt_value(next_run_at), req.delete_after_run, req.created_by,
                    now.isoformat(), now.isoformat(),
                ),
            )
            await db.commit()
        finally:
            await db.close()
        job = await self.get_job(job_id)
        assert job is not None
        return job

    async def update_job(self, job_id: str, req: ScheduledJobUpdate, next_run_at: Optional[datetime] | None = None) -> Optional[ScheduledJob]:
        existing = await self.get_job(job_id)
        if not existing:
            return None

        fields = req.model_dump(exclude_unset=True)
        fields["approval_policy"] = "auto_execute"
        column_map = {
            "name": "name",
            "message": "message",
            "enabled": "enabled",
            "schedule_kind": "schedule_kind",
            "at_time": "at_time",
            "every_seconds": "every_seconds",
            "cron_expr": "cron_expr",
            "timezone": "timezone",
            "target_studio_id": "target_studio_id",
            "approval_policy": "approval_policy",
            "overlap_policy": "overlap_policy",
            "delete_after_run": "delete_after_run",
        }

        updates: list[str] = []
        params: list = []
        for key, value in fields.items():
            if key not in column_map:
                continue
            updates.append(f"{column_map[key]} = ?")
            params.append(_dt_value(value) if isinstance(value, datetime) else value)

        if next_run_at is not None:
            updates.append("next_run_at = ?")
            params.append(_dt_value(next_run_at))

        if not updates:
            return existing

        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(job_id)

        db = await get_db()
        try:
            await db.execute(f"UPDATE scheduled_jobs SET {', '.join(updates)} WHERE id = ?", params)
            await db.commit()
        finally:
            await db.close()
        return await self.get_job(job_id)

    async def delete_job(self, job_id: str) -> bool:
        db = await get_db()
        try:
            cursor = await db.execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
            await db.commit()
            return cursor.rowcount > 0
        finally:
            await db.close()

    async def set_next_run(self, job_id: str, next_run_at: Optional[datetime], enabled: Optional[bool] = None) -> None:
        db = await get_db()
        try:
            if enabled is None:
                await db.execute(
                    "UPDATE scheduled_jobs SET next_run_at = ?, updated_at = ? WHERE id = ?",
                    (_dt_value(next_run_at), datetime.now().isoformat(), job_id),
                )
            else:
                await db.execute(
                    "UPDATE scheduled_jobs SET next_run_at = ?, enabled = ?, updated_at = ? WHERE id = ?",
                    (_dt_value(next_run_at), enabled, datetime.now().isoformat(), job_id),
                )
            await db.commit()
        finally:
            await db.close()

    async def mark_job_result(
        self,
        job_id: str,
        *,
        status: str,
        last_run_at: datetime,
        next_run_at: Optional[datetime],
        error: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        db = await get_db()
        try:
            enabled_sql = ", enabled = ?" if enabled is not None else ""
            params = [
                last_run_at.isoformat(),
                status,
                error,
                _dt_value(next_run_at),
                datetime.now().isoformat(),
            ]
            if enabled is not None:
                params.append(enabled)
            params.append(job_id)
            await db.execute(
                f"""UPDATE scheduled_jobs
                    SET last_run_at = ?, last_status = ?, last_error = ?,
                        next_run_at = ?, updated_at = ?{enabled_sql}
                    WHERE id = ?""",
                params,
            )
            await db.commit()
        finally:
            await db.close()

    async def create_run(self, job_id: str) -> ScheduledJobRun:
        run_id = str(uuid.uuid4())[:8]
        now = datetime.now()
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO scheduled_job_runs (id, job_id, status, started_at) VALUES (?, ?, ?, ?)",
                (run_id, job_id, "running", now.isoformat()),
            )
            await db.commit()
        finally:
            await db.close()
        return ScheduledJobRun(id=run_id, job_id=job_id, status="running", started_at=now)

    async def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        task_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        db = await get_db()
        try:
            await db.execute(
                """UPDATE scheduled_job_runs
                   SET status = ?, task_id = COALESCE(?, task_id), finished_at = ?, error = ?
                   WHERE id = ?""",
                (status, task_id, datetime.now().isoformat(), error, run_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def list_runs(self, job_id: str) -> list[ScheduledJobRun]:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM scheduled_job_runs WHERE job_id = ? ORDER BY started_at DESC",
                (job_id,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_run(dict(row)) for row in rows]
        finally:
            await db.close()

    async def list_run_results(self, limit: int = 100, job_id: Optional[str] = None) -> list[dict]:
        """聚合定时任务运行记录、关联任务状态和最终摘要，供结果浏览页使用。"""
        limit = max(1, min(limit, 300))
        params: list = []
        where = ""
        if job_id:
            where = "WHERE r.job_id = ?"
            params.append(job_id)
        params.append(limit)

        db = await get_db()
        try:
            cursor = await db.execute(
                f"""SELECT
                        r.id AS run_id,
                        r.job_id,
                        r.task_id,
                        r.status AS run_status,
                        r.started_at,
                        r.finished_at,
                        r.error AS run_error,
                        j.name AS job_name,
                        j.message AS job_message,
                        j.schedule_kind,
                        j.cron_expr,
                        j.every_seconds,
                        j.timezone,
                        t.status AS task_status,
                        t.question AS task_question,
                        t.completed_at AS task_completed_at,
                        (
                            SELECT pn.output
                            FROM path_nodes pn
                            WHERE pn.task_id = t.id
                              AND pn.type = 'agent_zero'
                              AND pn.status = 'completed'
                            ORDER BY pn.created_at DESC
                            LIMIT 1
                        ) AS result_output
                    FROM scheduled_job_runs r
                    JOIN scheduled_jobs j ON j.id = r.job_id
                    LEFT JOIN tasks t ON t.id = r.task_id
                    {where}
                    ORDER BY r.started_at DESC
                    LIMIT ?""",
                params,
            )
            rows = await cursor.fetchall()
        finally:
            await db.close()

        results = []
        for row in rows:
            item = dict(row)
            output = item.pop("result_output", "") or ""
            item["result_excerpt"] = self._excerpt(output)
            results.append(item)
        return results

    async def has_running_run(self, job_id: str) -> bool:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT 1 FROM scheduled_job_runs WHERE job_id = ? AND status = 'running' LIMIT 1",
                (job_id,),
            )
            return await cursor.fetchone() is not None
        finally:
            await db.close()

    async def mark_stale_running_runs(self) -> None:
        """服务重启后，将上次未正常收尾的运行记录标记为错误，避免 overlap 检查永久卡住。"""
        db = await get_db()
        try:
            await db.execute(
                """UPDATE scheduled_job_runs
                   SET status = 'error', finished_at = ?, error = 'server restarted before run finished'
                   WHERE status = 'running'""",
                (datetime.now().isoformat(),),
            )
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    def _row_to_job(row: dict) -> ScheduledJob:
        return ScheduledJob(
            id=row["id"],
            name=row.get("name", ""),
            message=row.get("message", ""),
            enabled=bool(row.get("enabled", True)),
            schedule_kind=row.get("schedule_kind", "every"),
            at_time=_parse_dt(row.get("at_time")),
            every_seconds=row.get("every_seconds"),
            cron_expr=row.get("cron_expr"),
            timezone=row.get("timezone"),
            target_studio_id=row.get("target_studio_id"),
            approval_policy="auto_execute",
            overlap_policy=row.get("overlap_policy") or "skip",
            next_run_at=_parse_dt(row.get("next_run_at")),
            last_run_at=_parse_dt(row.get("last_run_at")),
            last_status=row.get("last_status"),
            last_error=row.get("last_error"),
            delete_after_run=bool(row.get("delete_after_run", False)),
            created_by=row.get("created_by") or "agent",
            created_at=_parse_dt(row.get("created_at")) or datetime.now(),
            updated_at=_parse_dt(row.get("updated_at")) or datetime.now(),
        )

    @staticmethod
    def _row_to_run(row: dict) -> ScheduledJobRun:
        return ScheduledJobRun(
            id=row["id"],
            job_id=row["job_id"],
            task_id=row.get("task_id"),
            status=row.get("status", "running"),
            started_at=_parse_dt(row.get("started_at")) or datetime.now(),
            finished_at=_parse_dt(row.get("finished_at")),
            error=row.get("error"),
        )

    @staticmethod
    def _excerpt(text: str, limit: int = 700) -> str:
        text = " ".join(str(text or "").split())
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."
