"""Sandbox command runtime helpers."""
import asyncio
import os
import re
import signal
from pathlib import Path
from urllib.parse import quote

from models.sandbox import Sandbox
from tools.execution_safety import build_sanitized_env

SUBPROCESS_START_KWARGS = {"start_new_session": True} if os.name == "posix" else {}
PREVIEW_INDEX_CANDIDATES = (
    "output/index.html",
    "index.html",
    "deliverables/index.html",
    "public/index.html",
    "dist/index.html",
    "build/index.html",
)
ITERATION_OUTPUT_INDEX_PATTERN = "output/{iteration_id}/index.html"


async def terminate_process_tree(
    proc: asyncio.subprocess.Process,
    timeout: float = 3.0,
    force: bool = False,
) -> int | None:
    """Terminate a subprocess and its process group when supported."""
    if proc.returncode is not None:
        return proc.returncode

    if os.name == "posix" and proc.pid:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            return proc.returncode
        except Exception:
            if force:
                proc.kill()
            else:
                proc.terminate()
    else:
        if force:
            proc.kill()
        else:
            proc.terminate()

    try:
        return await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        if force:
            raise
        return await terminate_process_tree(proc, timeout=timeout, force=True)


def build_sandbox_env(sandbox: Sandbox) -> dict[str, str]:
    env = build_sanitized_env()
    if sandbox.dev_port:
        port = str(sandbox.dev_port)
        env.update({
            "PORT": port,
            "VITE_PORT": port,
            "ASTUDIO_SANDBOX_PORT": port,
            "ANTIT_SANDBOX_PORT": port,
            "HOST": "127.0.0.1",
        })
    return env


def prepare_sandbox_command(
    command: str,
    sandbox: Sandbox,
    cwd: str | Path | None = None,
    iteration_id: str | None = None,
) -> tuple[str, str | None]:
    """Attach the reserved sandbox port to common dev-server commands."""
    port = sandbox.dev_port
    if not port:
        return command, None

    stripped = command.strip()
    lowered = stripped.lower()
    preview_url = f"http://127.0.0.1:{port}"
    server_root = Path(cwd).resolve() if cwd is not None else Path(sandbox.path).resolve()

    if _has_explicit_port(lowered):
        if "python" in lowered and "-m http.server" in lowered:
            return stripped, _static_preview_url(sandbox, server_root, preview_url, iteration_id)
        return stripped, preview_url if _looks_like_server(lowered) else None

    if re.search(r"\b((pnpm|yarn)\s+(run\s+)?dev|npm\s+run\s+dev)\b", lowered):
        if " -- " in stripped:
            return f"{stripped} --host 127.0.0.1 --port {port}", preview_url
        return f"{stripped} -- --host 127.0.0.1 --port {port}", preview_url

    if re.search(r"(^|\s)(vite|astro|storybook)\b", lowered):
        return f"{stripped} --host 127.0.0.1 --port {port}", preview_url

    if re.search(r"(^|\s)next\s+dev\b", lowered):
        return f"{stripped} -H 127.0.0.1 -p {port}", preview_url

    if "python" in lowered and "-m http.server" in lowered:
        return (
            f"{stripped} {port} --bind 127.0.0.1",
            _static_preview_url(sandbox, server_root, preview_url, iteration_id),
        )

    if re.search(r"(^|\s)uvicorn\b", lowered):
        return f"{stripped} --host 127.0.0.1 --port {port}", preview_url

    return stripped, preview_url if _looks_like_server(lowered) else None


def _has_explicit_port(command: str) -> bool:
    return bool(re.search(r"(--port|-p)\s+\d+", command) or re.search(r":\d{4,5}\b", command))


def _looks_like_server(command: str) -> bool:
    return any(
        marker in command
        for marker in (
            " dev",
            "serve",
            "server",
            "http.server",
            "uvicorn",
            "vite",
            "next dev",
            "astro",
            "storybook",
        )
    )


def safe_iteration_id(iteration_id: str | None) -> str:
    if not iteration_id:
        return ""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", iteration_id).strip(".-")


def find_preview_index(root: Path, iteration_id: str | None = None) -> Path | None:
    safe_id = safe_iteration_id(iteration_id)
    if safe_id:
        path = root / ITERATION_OUTPUT_INDEX_PATTERN.format(iteration_id=safe_id)
        if path.exists() and path.is_file():
            return path
        for rel in PREVIEW_INDEX_CANDIDATES:
            path = root / rel
            if path.exists() and path.is_file():
                return path
        return None

    iterated_indexes = [
        path for path in (root / "output").glob("*/index.html")
        if path.is_file()
    ]
    if iterated_indexes:
        iterated_indexes.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return iterated_indexes[0]

    for rel in PREVIEW_INDEX_CANDIDATES:
        path = root / rel
        if path.exists() and path.is_file():
            return path
    return None


def _static_preview_url(
    sandbox: Sandbox,
    server_root: Path,
    base_url: str,
    iteration_id: str | None = None,
) -> str:
    index_path = find_preview_index(Path(sandbox.path), iteration_id=iteration_id)
    if not index_path:
        return base_url
    try:
        rel = index_path.resolve().relative_to(server_root.resolve()).as_posix()
    except ValueError:
        return base_url
    if rel in ("", "index.html"):
        return base_url
    return f"{base_url}/{quote(rel)}"
