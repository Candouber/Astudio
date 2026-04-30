"""Task sandbox tools bound to the current ToolContext."""
import asyncio
from pathlib import Path

from storage.sandbox_store import SandboxStore
from tools.context import get_current_tool_context
from tools.execution_safety import LocalExecutionBlocked, validate_local_command
from tools.sandbox_runtime import (
    SUBPROCESS_START_KWARGS,
    build_sandbox_env,
    prepare_sandbox_command,
    terminate_process_tree,
)

MAX_TOOL_READ_CHARS = 20_000
MAX_TOOL_OUTPUT_CHARS = 12_000

sandbox_store = SandboxStore()


async def ensure_sandbox() -> str:
    context = get_current_tool_context()
    sandbox, created = await sandbox_store.ensure_for_task(context.task_id)
    return (
        f"[Sandbox {'created' if created else 'ready'}]\n"
        f"sandbox_id: {sandbox.id}\n"
        f"path: {sandbox.path}\n"
        f"dev_port: {sandbox.dev_port}\n"
        "Use this port when starting a development server. The system also injects PORT/VITE_PORT/ASTUDIO_SANDBOX_PORT.\n"
        "Read AGENT_GUIDE.md first, and update RUNBOOK.md after completion."
    )


async def sandbox_list_files(directory: str = ".") -> str:
    sandbox, _ = await _current_sandbox()
    files = sandbox_store.list_files(sandbox, directory)
    if not files:
        return "[Empty directory]"
    lines = []
    for item in files:
        icon = "DIR " if item.kind == "directory" else "FILE"
        size = f" ({item.size} bytes)" if item.kind == "file" else ""
        lines.append(f"{icon} {item.path}{size}")
    return "\n".join(lines)


async def sandbox_read_file(path: str) -> str:
    sandbox, _ = await _current_sandbox()
    return sandbox_store.read_file(sandbox, path, max_chars=MAX_TOOL_READ_CHARS)


async def sandbox_write_file(path: str, content: str) -> str:
    sandbox, _ = await _current_sandbox()
    target = sandbox_store.write_file(sandbox, path, content)
    await sandbox_store.touch(sandbox.id)
    return f"[Write succeeded] {path} ({target.stat().st_size} bytes)"


async def sandbox_run_command(command: str, cwd: str = ".", timeout_seconds: int = 60) -> str:
    context = get_current_tool_context()
    sandbox, _ = await sandbox_store.ensure_for_task(context.task_id)
    sandbox = await sandbox_store.ensure_dev_port(sandbox)
    if not command.strip():
        return "[Error] Command cannot be empty."
    try:
        validate_local_command(command)
    except LocalExecutionBlocked as e:
        return f"[Safety blocked] {e}"

    workdir = sandbox_store.safe_path(sandbox, cwd)
    if not workdir.exists() or not workdir.is_dir():
        return f"[Error] Working directory does not exist: {cwd}"

    runs_dir = Path(sandbox.path) / ".astudio" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = runs_dir / "tool.stdout.log"
    stderr_path = runs_dir / "tool.stderr.log"
    prepared_command, preview_url = prepare_sandbox_command(command, sandbox)
    stdout_f = stdout_path.open("wb")
    stderr_f = stderr_path.open("wb")
    try:
        proc = await asyncio.create_subprocess_shell(
            prepared_command,
            cwd=str(workdir),
            env=build_sandbox_env(sandbox),
            stdout=stdout_f,
            stderr=stderr_f,
            **SUBPROCESS_START_KWARGS,
        )
    except Exception as e:
        stdout_f.close()
        stderr_f.close()
        return f"[Start failed] {e}"

    run = await sandbox_store.create_run(
        sandbox_id=sandbox.id,
        task_id=context.task_id,
        command=prepared_command,
        cwd=cwd,
        stdout_path="",
        stderr_path="",
        pid=proc.pid,
        preview_url=preview_url,
    )
    final_stdout = runs_dir / f"{run.id}.stdout.log"
    final_stderr = runs_dir / f"{run.id}.stderr.log"
    stdout_path.rename(final_stdout)
    stderr_path.rename(final_stderr)
    stdout_rel = final_stdout.relative_to(Path(sandbox.path)).as_posix()
    stderr_rel = final_stderr.relative_to(Path(sandbox.path)).as_posix()
    await _patch_run_log_paths(run.id, stdout_rel, stderr_rel)
    await sandbox_store.touch(sandbox.id, status="running", preview_url=preview_url)

    status = "ok"
    exit_code = None
    try:
        exit_code = await asyncio.wait_for(proc.wait(), timeout=max(1, min(timeout_seconds, 180)))
        status = "ok" if exit_code == 0 else "error"
    except asyncio.TimeoutError:
        exit_code = await terminate_process_tree(proc, timeout=3, force=True)
        status = "error"
    finally:
        stdout_f.close()
        stderr_f.close()
        await sandbox_store.finish_run(run.id, status, exit_code)
        await sandbox_store.touch(sandbox.id, status="ready" if status == "ok" else "error")

    stdout = sandbox_store.read_file(sandbox, stdout_rel, max_chars=MAX_TOOL_OUTPUT_CHARS)
    stderr = sandbox_store.read_file(sandbox, stderr_rel, max_chars=MAX_TOOL_OUTPUT_CHARS)
    return (
        f"[Run completed] status={status} exit_code={exit_code} run_id={run.id}\n"
        f"command={prepared_command}\n"
        f"dev_port={sandbox.dev_port}\n"
        f"preview_url={preview_url or '(none)'}\n"
        f"stdout_log={stdout_rel}\nstderr_log={stderr_rel}\n\n"
        f"[stdout]\n{stdout or '(none)'}\n\n[stderr]\n{stderr or '(none)'}"
    )


async def sandbox_start_preview() -> str:
    sandbox, _ = await _current_sandbox()
    root = Path(sandbox.path)
    index = None
    for rel in ("index.html", "public/index.html", "dist/index.html", "build/index.html"):
        candidate = root / rel
        if candidate.exists() and candidate.is_file():
            index = rel
            break
    if not index:
        return "[Preview failed] No index.html, public/index.html, dist/index.html, or build/index.html was found."
    preview_url = f"/api/sandboxes/{sandbox.id}/preview/{index}"
    await sandbox_store.touch(sandbox.id, preview_url=preview_url)
    return f"[Preview ready] {preview_url}"


async def _current_sandbox():
    context = get_current_tool_context()
    return await sandbox_store.ensure_for_task(context.task_id)


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


ENSURE_SANDBOX_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ensure_sandbox",
        "description": "Create or get the independent sandbox directory for the current task and return sandbox instructions.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

SANDBOX_LIST_FILES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sandbox_list_files",
        "description": "List files under a directory in the current task sandbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Relative directory inside the sandbox; defaults to root", "default": "."}
            },
            "required": [],
        },
    },
}

SANDBOX_READ_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sandbox_read_file",
        "description": "Read a file in the current task sandbox. The path must be sandbox-relative.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative file path inside the sandbox"}},
            "required": ["path"],
        },
    },
}

SANDBOX_WRITE_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sandbox_write_file",
        "description": "Write a file inside the current task sandbox. Use it for scripts, pages, data, and documentation.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path inside the sandbox"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"],
        },
    },
}

SANDBOX_RUN_COMMAND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sandbox_run_command",
        "description": "Run a command inside the current task sandbox and return stdout/stderr. Suitable for Python/Node scripts, tests, or builds.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to run"},
                "cwd": {"type": "string", "description": "Working directory inside the sandbox", "default": "."},
                "timeout_seconds": {"type": "integer", "description": "Timeout in seconds, maximum 180", "default": 60},
            },
            "required": ["command"],
        },
    },
}

SANDBOX_START_PREVIEW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sandbox_start_preview",
        "description": "Create a preview link for an HTML page generated in the current task sandbox.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
