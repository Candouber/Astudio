"""
Isolated task execution process manager.

The FastAPI process should stay responsive even when a local agent run blocks on
LLM IO, parsing, or an external tool. This module starts one Python worker
process per task pipeline and keeps only lightweight process control in the API
process.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from storage.database import DATA_DIR
from storage.task_store import TaskStore

PROCESS_DIR = DATA_DIR / "task_processes"
WORKER_MODULE = "workers.task_worker"


@dataclass
class ManagedTaskProcess:
    task_id: str
    kind: str
    process: asyncio.subprocess.Process
    log_path: Path
    payload_path: Path
    log_handle: Any


_processes: dict[str, ManagedTaskProcess] = {}
_watchers: dict[str, asyncio.Task] = {}
_expected_terminations: set[str] = set()


def should_run_inline() -> bool:
    """Allow local debugging to opt out of process isolation explicitly."""
    return (
        os.environ.get("ASTUDIO_TASK_EXECUTION")
        or os.environ.get("ANTIT_TASK_EXECUTION")
        or "process"
    ).lower() == "inline"


async def start_task_worker(kind: str, task_id: str, payload: dict[str, Any]) -> Path:
    """Start an isolated worker process for a task pipeline.

    Returns the worker log path. Raises RuntimeError if the same task already has
    an active worker.
    """
    current = _processes.get(task_id)
    if current and current.process.returncode is None:
        raise RuntimeError(f"任务 {task_id} 已有隔离执行进程在运行")

    PROCESS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:8]
    payload_path = PROCESS_DIR / f"{task_id}-{kind}-{run_id}.json"
    log_path = PROCESS_DIR / f"{task_id}-{kind}-{run_id}.log"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    env = os.environ.copy()
    env["ASTUDIO_EXECUTION_MODE"] = "worker"
    env["ANTIT_EXECUTION_MODE"] = "worker"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])

    log_handle = log_path.open("ab")
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            WORKER_MODULE,
            kind,
            str(payload_path),
            cwd=str(Path(__file__).resolve().parents[1]),
            env=env,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
        )
    except Exception:
        log_handle.close()
        raise
    managed = ManagedTaskProcess(
        task_id=task_id,
        kind=kind,
        process=process,
        log_path=log_path,
        payload_path=payload_path,
        log_handle=log_handle,
    )
    _processes[task_id] = managed
    _watchers[task_id] = asyncio.create_task(_watch_worker(managed))
    logger.info(
        f"[Task {task_id}] started isolated worker pid={process.pid} kind={kind} log={log_path}"
    )
    return log_path


async def _watch_worker(managed: ManagedTaskProcess) -> None:
    task_id = managed.task_id
    try:
        returncode = await managed.process.wait()
        logger.info(
            f"[Task {task_id}] isolated worker exited pid={managed.process.pid} "
            f"kind={managed.kind} returncode={returncode}"
        )
        expected_termination = task_id in _expected_terminations
        if returncode != 0 and not expected_termination:
            await _mark_failed_if_still_active(task_id, returncode, managed.log_path)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception(f"[Task {task_id}] worker watcher failed: {e}")
    finally:
        try:
            managed.log_handle.close()
        except Exception:
            pass
        _processes.pop(task_id, None)
        _watchers.pop(task_id, None)
        _expected_terminations.discard(task_id)


async def _mark_failed_if_still_active(task_id: str, returncode: int, log_path: Path) -> None:
    task_store = TaskStore()
    try:
        task = await task_store.get(task_id)
        if not task or task.status not in ("planning", "executing"):
            return
        message = (
            f"隔离执行进程异常退出（exit={returncode}）。"
            f"日志：{log_path}"
        )
        await task_store.set_task_failure(task_id, "failed", message)
    except Exception as e:
        logger.warning(f"[Task {task_id}] failed to mark worker crash: {e}")


async def terminate_task_worker(task_id: str, timeout: float = 5.0) -> int:
    """Terminate an active isolated worker for a task. Returns 1 if one existed."""
    managed = _processes.get(task_id)
    if not managed or managed.process.returncode is not None:
        return 0

    pid = managed.process.pid
    _expected_terminations.add(task_id)
    logger.info(f"[Task {task_id}] terminating isolated worker pid={pid}")
    try:
        if pid:
            os.killpg(pid, signal.SIGTERM)
        else:
            managed.process.terminate()
        await asyncio.wait_for(managed.process.wait(), timeout=timeout)
    except ProcessLookupError:
        pass
    except TimeoutError:
        logger.warning(f"[Task {task_id}] worker did not terminate in {timeout}s, killing pid={pid}")
        try:
            if pid:
                os.killpg(pid, signal.SIGKILL)
            else:
                managed.process.kill()
            await managed.process.wait()
        except ProcessLookupError:
            pass
    return 1


async def stop_all_task_workers() -> None:
    """Stop all worker processes during API shutdown."""
    task_ids = list(_processes.keys())
    for task_id in task_ids:
        try:
            await terminate_task_worker(task_id)
        except Exception as e:
            logger.warning(f"[Task {task_id}] stop worker failed: {e}")


def active_worker_count() -> int:
    return sum(1 for managed in _processes.values() if managed.process.returncode is None)
