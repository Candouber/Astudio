import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.scheduler import scheduler_service
from core.task_process_runner import active_worker_count, stop_all_task_workers
from core.tasks_monitor import task_monitor
from routers import config, sandbox, schedule, skill, studio, task
from storage.database import close_all_pool_conns, db_pool_status, init_database
from storage.sandbox_store import SandboxStore

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIST_DIR = Path(os.environ.get("ASTUDIO_WEB_DIST_DIR") or (ROOT_DIR / "web" / "dist")).expanduser()

app = FastAPI(
    title="AStudio",
    description="本地优先的多 Agent 协作任务执行后端",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(studio.router, prefix="/api/studios", tags=["studios"])
app.include_router(task.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(config.router, prefix="/api/config", tags=["config"])
app.include_router(skill.router, prefix="/api/skills", tags=["skills"])
app.include_router(schedule.router, prefix="/api/schedules", tags=["schedules"])
app.include_router(sandbox.router, prefix="/api/sandboxes", tags=["sandboxes"])


@app.on_event("startup")
async def startup():
    await init_database()
    await SandboxStore().mark_stale_running_runs()
    scheduler_service.on_job = task.create_and_run_scheduled_task
    await scheduler_service.start()
    await task.recover_interrupted_executions()
    await task_monitor.recover_running_tasks()


@app.on_event("shutdown")
async def shutdown():
    scheduler_service.stop()
    await stop_all_task_workers()
    await sandbox.stop_all_sandbox_processes()
    await close_all_pool_conns()


@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "version": "0.1.0",
        "active_task_workers": active_worker_count(),
        "db_pool": db_pool_status(),
    }


if WEB_DIST_DIR.exists():
    assets_dir = WEB_DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint not found")

        requested = (WEB_DIST_DIR / full_path).resolve()
        if requested.is_file() and requested.is_relative_to(WEB_DIST_DIR):
            return FileResponse(requested)
        return FileResponse(WEB_DIST_DIR / "index.html")
