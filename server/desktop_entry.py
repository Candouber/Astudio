import os
import sys
from pathlib import Path

import uvicorn


def _resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def _configure_packaged_resources() -> None:
    if os.environ.get("TIKTOKEN_CACHE_DIR"):
        return
    cache_dir = _resource_path("resources", "tiktoken-cache")
    if cache_dir.exists():
        os.environ["TIKTOKEN_CACHE_DIR"] = str(cache_dir)


_configure_packaged_resources()

from main import app  # noqa: E402


def _run_task_worker() -> int:
    from workers.task_worker import main as worker_main
    import asyncio

    return asyncio.run(worker_main())


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--task-worker":
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        raise SystemExit(_run_task_worker())

    host = os.environ.get("ASTUDIO_SERVER_HOST") or os.environ.get("ANTIT_SERVER_HOST") or "127.0.0.1"
    port = int(os.environ.get("ASTUDIO_SERVER_PORT") or os.environ.get("ANTIT_SERVER_PORT") or "8000")
    log_level = os.environ.get("ASTUDIO_LOG_LEVEL", "info")
    uvicorn.run(app, host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    main()
