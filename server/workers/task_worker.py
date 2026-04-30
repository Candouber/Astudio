"""
Task pipeline worker.

This module is executed as a separate Python process by
`core.task_process_runner`. It imports the existing orchestration functions and
runs exactly one task pipeline, then exits.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from loguru import logger

from storage.database import close_all_pool_conns, init_database


async def _run(kind: str, payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    await init_database()

    # Import after DB initialization so the worker owns its own pool/process state.
    from routers import task as task_router

    if kind == "ask":
        await task_router._run_ask_pipeline(
            payload["task_id"],
            payload["question"],
            preferred_studio_id=payload.get("preferred_studio_id"),
        )
        return

    if kind == "orchestrate":
        await task_router._execute_background_orchestration(
            payload["task_id"],
            payload["studio_id"],
            payload.get("route_cmd") or {},
            payload.get("feedback") or "",
        )
        return

    if kind == "rerun":
        await task_router._rerun_with_steps(
            payload["task_id"],
            payload["studio_id"],
            payload.get("steps") or [],
        )
        return

    if kind == "scheduled":
        await task_router.run_scheduled_task_pipeline(
            payload["task_id"],
            payload["studio_id"],
            payload.get("message") or "",
            payload.get("job_label") or "",
        )
        return

    raise ValueError(f"unknown task worker kind: {kind}")


async def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python -m workers.task_worker <kind> <payload.json>", file=sys.stderr)
        return 2

    kind = sys.argv[1]
    payload_path = Path(sys.argv[2])
    try:
        await _run(kind, payload_path)
        return 0
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception(f"task worker failed kind={kind} payload={payload_path}: {e}")
        return 1
    finally:
        await close_all_pool_conns()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
