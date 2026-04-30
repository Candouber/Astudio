"""Task sandbox API."""
import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger

from models.sandbox import SandboxRunRequest, SandboxWriteFileRequest
from storage.sandbox_store import SandboxStore
from tools.execution_safety import LocalExecutionBlocked, validate_local_command
from tools.sandbox_runtime import (
    SUBPROCESS_START_KWARGS,
    build_sandbox_env,
    prepare_sandbox_command,
    terminate_process_tree,
)

router = APIRouter()
sandbox_store = SandboxStore()

_RUNNING_PROCS: dict[str, asyncio.subprocess.Process] = {}


@router.get("/")
async def list_sandboxes():
    return await sandbox_store.list_all()


@router.get("/{sandbox_id}")
async def get_sandbox(sandbox_id: str):
    sandbox = await sandbox_store.get(sandbox_id)
    if not sandbox:
        raise HTTPException(404, "Sandbox does not exist.")
    return sandbox


@router.get("/{sandbox_id}/start-command")
async def get_start_command(sandbox_id: str):
    sandbox = await _require_sandbox(sandbox_id)
    return sandbox_store.infer_start_command(sandbox)


@router.post("/tasks/{task_id}")
async def ensure_task_sandbox(task_id: str):
    try:
        sandbox, created = await sandbox_store.ensure_for_task(task_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return {"sandbox": sandbox, "created": created}


@router.get("/tasks/{task_id}/current")
async def get_task_sandbox(task_id: str):
    sandbox = await sandbox_store.get_by_task(task_id)
    if not sandbox:
        raise HTTPException(404, "This task has not created a sandbox yet.")
    return sandbox


@router.delete("/{sandbox_id}", status_code=204)
async def delete_sandbox(sandbox_id: str, delete_files: bool = True):
    await _stop_sandbox_processes(sandbox_id)
    ok = await sandbox_store.delete(sandbox_id, delete_files=delete_files)
    if not ok:
        raise HTTPException(404, "Sandbox does not exist.")


@router.get("/{sandbox_id}/files")
async def list_files(sandbox_id: str, directory: str = "."):
    sandbox = await _require_sandbox(sandbox_id)
    try:
        return sandbox_store.list_files(sandbox, directory)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


@router.get("/{sandbox_id}/files/read")
async def read_file(sandbox_id: str, path: str = Query(...)):
    sandbox = await _require_sandbox(sandbox_id)
    try:
        return {"path": path, "content": sandbox_store.read_file(sandbox, path)}
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"File not found: {path}") from e
    except IsADirectoryError as e:
        raise HTTPException(400, f"Not a file: {path}") from e


@router.put("/{sandbox_id}/files/write")
async def write_file(sandbox_id: str, payload: SandboxWriteFileRequest):
    sandbox = await _require_sandbox(sandbox_id)
    try:
        target = sandbox_store.write_file(sandbox, payload.path, payload.content)
        await sandbox_store.touch(sandbox_id)
        return {"path": payload.path, "size": target.stat().st_size}
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


@router.post("/{sandbox_id}/run")
async def run_command(sandbox_id: str, payload: SandboxRunRequest):
    sandbox = await _require_sandbox(sandbox_id)
    sandbox = await sandbox_store.ensure_dev_port(sandbox)
    if not payload.command.strip():
        raise HTTPException(400, "Command cannot be empty.")
    try:
        validate_local_command(payload.command)
    except LocalExecutionBlocked as e:
        raise HTTPException(400, str(e)) from e

    cwd = sandbox_store.safe_path(sandbox, payload.cwd)
    if not cwd.exists() or not cwd.is_dir():
        raise HTTPException(400, f"Working directory does not exist: {payload.cwd}")

    runs_dir = Path(sandbox.path) / ".astudio" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = runs_dir / "pending.stdout.log"
    stderr_path = runs_dir / "pending.stderr.log"
    prepared_command, preview_url = prepare_sandbox_command(payload.command, sandbox)

    stdout_f = stdout_path.open("wb")
    stderr_f = stderr_path.open("wb")
    try:
        proc = await asyncio.create_subprocess_shell(
            prepared_command,
            cwd=str(cwd),
            env=build_sandbox_env(sandbox),
            stdout=stdout_f,
            stderr=stderr_f,
            **SUBPROCESS_START_KWARGS,
        )
    except Exception as e:
        stdout_f.close()
        stderr_f.close()
        raise HTTPException(400, f"Failed to start command: {e}") from e

    run = await sandbox_store.create_run(
        sandbox_id=sandbox.id,
        task_id=sandbox.task_id,
        command=prepared_command,
        cwd=payload.cwd,
        stdout_path="",
        stderr_path="",
        pid=proc.pid,
        preview_url=preview_url,
    )
    final_stdout = runs_dir / f"{run.id}.stdout.log"
    final_stderr = runs_dir / f"{run.id}.stderr.log"
    stdout_path.rename(final_stdout)
    stderr_path.rename(final_stderr)
    run.stdout_path = str(final_stdout.relative_to(Path(sandbox.path)))
    run.stderr_path = str(final_stderr.relative_to(Path(sandbox.path)))
    await _patch_run_log_paths(run.id, run.stdout_path, run.stderr_path)

    _RUNNING_PROCS[run.id] = proc
    await sandbox_store.touch(sandbox_id, status="running", preview_url=preview_url)

    async def _wait_and_finish(timeout: int | None = None):
        status = "ok"
        exit_code = None
        try:
            if timeout:
                exit_code = await asyncio.wait_for(proc.wait(), timeout=timeout)
            else:
                exit_code = await proc.wait()
            status = "ok" if exit_code == 0 else "error"
        except asyncio.TimeoutError:
            exit_code = await terminate_process_tree(proc, timeout=3, force=True)
            status = "error"
        except Exception as e:
            logger.error(f"Sandbox run failed: {e}")
            status = "error"
        finally:
            stdout_f.close()
            stderr_f.close()
            _RUNNING_PROCS.pop(run.id, None)
            await sandbox_store.finish_run(run.id, status, exit_code)
            await sandbox_store.touch(sandbox_id, status="ready" if status == "ok" else "error")

    if payload.background:
        asyncio.create_task(_wait_and_finish())
        return await sandbox_store.get_run(run.id)

    await _wait_and_finish(max(1, min(payload.timeout_seconds, 600)))
    return await sandbox_store.get_run(run.id)


@router.post("/{sandbox_id}/stop")
async def stop_sandbox(sandbox_id: str):
    await _require_sandbox(sandbox_id)
    stopped = await _stop_sandbox_processes(sandbox_id)
    await sandbox_store.touch(sandbox_id, status="stopped")
    return {"status": "ok", "stopped": stopped}


@router.get("/{sandbox_id}/runs")
async def list_runs(sandbox_id: str):
    await _require_sandbox(sandbox_id)
    return await sandbox_store.list_runs(sandbox_id)


@router.get("/{sandbox_id}/runs/{run_id}/logs")
async def get_run_logs(sandbox_id: str, run_id: str):
    sandbox = await _require_sandbox(sandbox_id)
    run = await sandbox_store.get_run(run_id)
    if not run or run.sandbox_id != sandbox_id:
        raise HTTPException(404, "Run record does not exist.")

    def _read(rel: str | None) -> str:
        if not rel:
            return ""
        try:
            return sandbox_store.read_file(sandbox, rel, max_chars=120_000)
        except Exception:
            return ""

    return {
        "stdout": _read(run.stdout_path),
        "stderr": _read(run.stderr_path),
    }


@router.post("/{sandbox_id}/preview")
async def start_preview(sandbox_id: str):
    sandbox = await _require_sandbox(sandbox_id)
    index_path = _find_index_file(Path(sandbox.path))
    if not index_path:
        raise HTTPException(404, "No previewable index.html was found. Generate a page file first.")
    rel = index_path.relative_to(Path(sandbox.path)).as_posix()
    preview_url = f"/api/sandboxes/{sandbox_id}/preview/{rel}"
    await sandbox_store.touch(sandbox_id, status="ready", preview_url=preview_url)
    return {"preview_url": preview_url}


@router.get("/{sandbox_id}/preview/{file_path:path}")
async def preview_file(sandbox_id: str, file_path: str = "index.html"):
    sandbox = await _require_sandbox(sandbox_id)
    try:
        target = sandbox_store.safe_path(sandbox, file_path or "index.html")
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    if target.is_dir():
        target = target / "index.html"
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Preview file does not exist.")
    return FileResponse(str(target))


async def _require_sandbox(sandbox_id: str):
    sandbox = await sandbox_store.get(sandbox_id)
    if not sandbox:
        raise HTTPException(404, "Sandbox does not exist.")
    return sandbox


async def _patch_run_log_paths(run_id: str, stdout_path: str, stderr_path: str) -> None:
    from storage.database import get_db

    db = await get_db()
    try:
        await db.execute(
            "UPDATE sandbox_runs SET stdout_path = ?, stderr_path = ? WHERE id = ?",
            (stdout_path, stderr_path, run_id),
        )
        await db.commit()
    finally:
        await db.close()


async def _stop_sandbox_processes(sandbox_id: str) -> int:
    runs = await sandbox_store.list_runs(sandbox_id)
    run_ids = {run.id for run in runs if run.status == "running"}
    stopped = 0
    for run_id in list(run_ids):
        proc = _RUNNING_PROCS.get(run_id)
        if proc and proc.returncode is None:
            await terminate_process_tree(proc, timeout=3)
            await sandbox_store.finish_run(run_id, "stopped", proc.returncode)
            _RUNNING_PROCS.pop(run_id, None)
            stopped += 1
    return stopped


async def stop_all_sandbox_processes() -> int:
    stopped = 0
    for run_id, proc in list(_RUNNING_PROCS.items()):
        if proc.returncode is None:
            await terminate_process_tree(proc, timeout=3)
            await sandbox_store.finish_run(run_id, "stopped", proc.returncode)
            stopped += 1
        _RUNNING_PROCS.pop(run_id, None)
    return stopped


def _find_index_file(root: Path) -> Path | None:
    for rel in ("index.html", "public/index.html", "dist/index.html", "build/index.html"):
        path = root / rel
        if path.exists() and path.is_file():
            return path
    return None
