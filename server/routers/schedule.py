"""
定时任务 API 路由
"""
from fastapi import APIRouter, HTTPException, Query

from core.scheduler import compute_next_run, scheduler_service
from models.schedule import ScheduledJob, ScheduledJobCreate, ScheduledJobRun, ScheduledJobUpdate

router = APIRouter()


@router.get("/")
async def list_schedules() -> list[ScheduledJob]:
    return await scheduler_service.store.list_jobs(include_disabled=True)


@router.post("/", status_code=201)
async def create_schedule(req: ScheduledJobCreate) -> ScheduledJob:
    try:
        req.approval_policy = "auto_execute"
        return await scheduler_service.add_job(req)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/runs/results")
async def list_schedule_run_results(
    limit: int = Query(100, ge=1, le=300),
    job_id: str | None = None,
) -> list[dict]:
    return await scheduler_service.store.list_run_results(limit=limit, job_id=job_id)


@router.get("/{job_id}")
async def get_schedule(job_id: str) -> ScheduledJob:
    job = await scheduler_service.store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return job


@router.put("/{job_id}")
async def update_schedule(job_id: str, req: ScheduledJobUpdate) -> ScheduledJob:
    existing = await scheduler_service.store.get_job(job_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    req.approval_policy = "auto_execute"
    data = existing.model_dump()
    data.update(req.model_dump(exclude_unset=True))
    try:
        schedule_req = ScheduledJobCreate(**{
            key: data[key]
            for key in (
                "name", "message", "enabled", "schedule_kind", "at_time", "every_seconds",
                "cron_expr", "timezone", "target_studio_id", "approval_policy",
                "overlap_policy", "delete_after_run", "created_by",
            )
        })
        next_run_at = compute_next_run(schedule_req) if schedule_req.enabled else None
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    updated = await scheduler_service.store.update_job(job_id, req)
    await scheduler_service.store.set_next_run(job_id, next_run_at, enabled=schedule_req.enabled)
    return await scheduler_service.store.get_job(job_id) or updated


@router.delete("/{job_id}", status_code=204)
async def delete_schedule(job_id: str):
    if not await scheduler_service.store.delete_job(job_id):
        raise HTTPException(status_code=404, detail="定时任务不存在")


@router.post("/{job_id}/run-now")
async def run_schedule_now(job_id: str):
    ok = await scheduler_service.run_now(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return {"status": "ok", "message": "已触发定时任务"}


@router.get("/{job_id}/runs")
async def list_schedule_runs(job_id: str) -> list[ScheduledJobRun]:
    if not await scheduler_service.store.get_job(job_id):
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return await scheduler_service.store.list_runs(job_id)
