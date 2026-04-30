"""Task sandbox storage layer."""
import hashlib
import json
import re
import shutil
import socket
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from models.sandbox import Sandbox, SandboxFile, SandboxRun
from storage.database import SANDBOXES_DIR, get_db
from storage.task_store import TaskStore

SANDBOX_PORT_START = 9100
SANDBOX_PORT_END = 9999
START_SCRIPT_PRIORITY = ("dev", "start", "preview", "serve")


class SandboxStore:
    def __init__(self):
        self.task_store = TaskStore()

    async def ensure_for_task(self, task_id: str) -> tuple[Sandbox, bool]:
        task = await self.task_store.get(task_id)
        if not task:
            raise ValueError(f"Task does not exist: {task_id}")

        owner_type = task.sandbox_owner_type or "task"
        owner_id = task.sandbox_owner_id or task_id
        title = "Task" if owner_type == "task" else "Scheduled task"
        path_prefix = "task" if owner_type == "task" else "schedule"

        existing = await self.get_by_owner(owner_type, owner_id)
        if existing:
            existing = await self._attach_task(existing, task_id, task.question[:300])
            if not existing.dev_port:
                existing = await self.ensure_dev_port(existing)
            self._ensure_docs(existing)
            return existing, False

        sandbox_id = f"sb_{uuid.uuid4().hex[:8]}"
        path = SANDBOXES_DIR / f"{path_prefix}_{owner_id}"
        path.mkdir(parents=True, exist_ok=True)
        dev_port = await self.allocate_dev_port(f"{owner_type}:{owner_id}")

        now = datetime.now().isoformat()
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO sandboxes
                   (id, owner_type, owner_id, task_id, path, status, title, description, runtime_type,
                    dev_port, created_at, updated_at, last_active_at)
                   VALUES (?, ?, ?, ?, ?, 'ready', ?, ?, 'local', ?, ?, ?, ?)""",
                (
                    sandbox_id,
                    owner_type,
                    owner_id,
                    task_id,
                    str(path),
                    f"{title} {owner_id} sandbox",
                    task.question[:300],
                    dev_port,
                    now,
                    now,
                    now,
                ),
            )
            await db.commit()
        finally:
            await db.close()

        sandbox = await self.get(sandbox_id)
        if not sandbox:
            raise RuntimeError("Sandbox creation failed.")
        self._write_bootstrap_docs(sandbox, task.question)
        return sandbox, True

    async def ensure_dev_port(self, sandbox: Sandbox) -> Sandbox:
        if sandbox.dev_port and self.is_port_free(sandbox.dev_port):
            return sandbox
        port = await self.allocate_dev_port(f"{sandbox.owner_type}:{sandbox.owner_id or sandbox.task_id}")
        db = await get_db()
        try:
            await db.execute(
                "UPDATE sandboxes SET dev_port = ?, updated_at = ? WHERE id = ?",
                (port, datetime.now().isoformat(), sandbox.id),
            )
            await db.commit()
        finally:
            await db.close()
        fresh = await self.get(sandbox.id)
        return fresh or sandbox

    async def allocate_dev_port(self, seed: str) -> int:
        used_ports = await self._list_used_dev_ports()
        span = SANDBOX_PORT_END - SANDBOX_PORT_START + 1
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        start = int(digest[:8], 16) % span
        for offset in range(span):
            port = SANDBOX_PORT_START + ((start + offset) % span)
            if port in used_ports:
                continue
            if self.is_port_free(port):
                return port
        raise RuntimeError("No available sandbox preview port.")

    async def _list_used_dev_ports(self) -> set[int]:
        db = await get_db()
        try:
            cursor = await db.execute("SELECT dev_port FROM sandboxes WHERE dev_port IS NOT NULL")
            rows = await cursor.fetchall()
        finally:
            await db.close()
        return {int(row["dev_port"]) for row in rows if row["dev_port"]}

    @staticmethod
    def is_port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex(("127.0.0.1", port)) != 0

    async def list_all(self) -> list[Sandbox]:
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM sandboxes ORDER BY updated_at DESC")
            rows = await cursor.fetchall()
        finally:
            await db.close()
        return [self._row_to_sandbox(row) for row in rows]

    async def get(self, sandbox_id: str) -> Optional[Sandbox]:
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM sandboxes WHERE id = ?", (sandbox_id,))
            row = await cursor.fetchone()
        finally:
            await db.close()
        return self._row_to_sandbox(row) if row else None

    async def get_by_task(self, task_id: str) -> Optional[Sandbox]:
        task = await self.task_store.get(task_id)
        if task:
            owner_type = task.sandbox_owner_type or "task"
            owner_id = task.sandbox_owner_id or task_id
            sandbox = await self.get_by_owner(owner_type, owner_id)
            if sandbox:
                return sandbox
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM sandboxes WHERE task_id = ? ORDER BY updated_at DESC LIMIT 1", (task_id,))
            row = await cursor.fetchone()
        finally:
            await db.close()
        return self._row_to_sandbox(row) if row else None

    async def get_by_owner(self, owner_type: str, owner_id: str) -> Optional[Sandbox]:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM sandboxes WHERE owner_type = ? AND owner_id = ? ORDER BY updated_at DESC LIMIT 1",
                (owner_type, owner_id),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()
        return self._row_to_sandbox(row) if row else None

    async def _attach_task(self, sandbox: Sandbox, task_id: str, description: str = "") -> Sandbox:
        now = datetime.now().isoformat()
        db = await get_db()
        try:
            await db.execute(
                """UPDATE sandboxes
                   SET task_id = ?, description = COALESCE(NULLIF(?, ''), description),
                       updated_at = ?, last_active_at = ?
                   WHERE id = ?""",
                (task_id, description, now, now, sandbox.id),
            )
            await db.commit()
        finally:
            await db.close()
        fresh = await self.get(sandbox.id)
        return fresh or sandbox

    async def touch(self, sandbox_id: str, status: str | None = None, preview_url: str | None = None) -> None:
        updates = ["updated_at = ?", "last_active_at = ?"]
        now = datetime.now().isoformat()
        params: list = [now, now]
        if status:
            updates.append("status = ?")
            params.append(status)
        if preview_url is not None:
            updates.append("preview_url = ?")
            params.append(preview_url)
        params.append(sandbox_id)
        db = await get_db()
        try:
            await db.execute(f"UPDATE sandboxes SET {', '.join(updates)} WHERE id = ?", params)
            await db.commit()
        finally:
            await db.close()

    async def delete(self, sandbox_id: str, delete_files: bool = True) -> bool:
        sandbox = await self.get(sandbox_id)
        if not sandbox:
            return False
        db = await get_db()
        try:
            cursor = await db.execute("DELETE FROM sandboxes WHERE id = ?", (sandbox_id,))
            await db.commit()
        finally:
            await db.close()
        if delete_files:
            path = Path(sandbox.path)
            if path.exists() and self.is_inside_root(path):
                shutil.rmtree(path, ignore_errors=True)
        return cursor.rowcount > 0

    async def create_run(
        self,
        sandbox_id: str,
        task_id: str,
        command: str,
        cwd: str,
        stdout_path: str,
        stderr_path: str,
        pid: int | None = None,
        preview_url: str | None = None,
    ) -> SandboxRun:
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        now = datetime.now().isoformat()
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO sandbox_runs
                   (id, sandbox_id, task_id, command, cwd, status, pid, stdout_path,
                    stderr_path, preview_url, started_at)
                   VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)""",
                (run_id, sandbox_id, task_id, command, cwd, pid, stdout_path, stderr_path, preview_url, now),
            )
            await db.commit()
        finally:
            await db.close()
        return (await self.get_run(run_id))  # type: ignore[return-value]

    async def finish_run(self, run_id: str, status: str, exit_code: int | None = None) -> None:
        db = await get_db()
        try:
            await db.execute(
                "UPDATE sandbox_runs SET status = ?, exit_code = ?, finished_at = ? WHERE id = ?",
                (status, exit_code, datetime.now().isoformat(), run_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def get_run(self, run_id: str) -> Optional[SandboxRun]:
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM sandbox_runs WHERE id = ?", (run_id,))
            row = await cursor.fetchone()
        finally:
            await db.close()
        return self._row_to_run(row) if row else None

    async def list_runs(self, sandbox_id: str) -> list[SandboxRun]:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM sandbox_runs WHERE sandbox_id = ? ORDER BY started_at DESC",
                (sandbox_id,),
            )
            rows = await cursor.fetchall()
        finally:
            await db.close()
        return [self._row_to_run(row) for row in rows]

    async def mark_stale_running_runs(self) -> None:
        """后端重启后，内存中的进程句柄丢失，遗留 running 记录不能再视为可管理。"""
        db = await get_db()
        try:
            now = datetime.now().isoformat()
            await db.execute(
                """UPDATE sandbox_runs
                   SET status = 'stopped', finished_at = COALESCE(finished_at, ?)
                   WHERE status = 'running'""",
                (now,),
            )
            await db.execute(
                """UPDATE sandboxes
                   SET status = 'stopped', updated_at = ?
                   WHERE status = 'running'""",
                (now,),
            )
            await db.commit()
        finally:
            await db.close()

    def list_files(self, sandbox: Sandbox, directory: str = ".") -> list[SandboxFile]:
        root = Path(sandbox.path).resolve()
        target = self.safe_path(sandbox, directory)
        if not target.exists() or not target.is_dir():
            return []
        files = []
        for item in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            stat = item.stat()
            files.append(
                SandboxFile(
                    name=item.name,
                    path=str(item.resolve().relative_to(root)),
                    kind="directory" if item.is_dir() else "file",
                    size=stat.st_size if item.is_file() else 0,
                    updated_at=datetime.fromtimestamp(stat.st_mtime),
                )
            )
        return files

    def read_file(self, sandbox: Sandbox, file_path: str, max_chars: int = 200_000) -> str:
        target = self.safe_path(sandbox, file_path)
        if not target.exists():
            raise FileNotFoundError(file_path)
        if not target.is_file():
            raise IsADirectoryError(file_path)
        content = target.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            return content[:max_chars] + f"\n... [File too long; truncated to {max_chars} characters]"
        return content

    def write_file(self, sandbox: Sandbox, file_path: str, content: str) -> Path:
        target = self.safe_path(sandbox, file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def infer_start_command(self, sandbox: Sandbox) -> dict:
        root = Path(sandbox.path).resolve()

        runbook_command = self._extract_runbook_command(root / "RUNBOOK.md")
        if runbook_command:
            return {"command": runbook_command, "cwd": ".", "source": "RUNBOOK.md"}

        package_match = self._infer_package_command(root)
        if package_match:
            return package_match

        static_match = self._infer_static_command(root)
        if static_match:
            return static_match

        python_match = self._infer_python_command(root)
        if python_match:
            return python_match

        return {
            "command": "",
            "cwd": ".",
            "source": "none",
            "message": "No start command was detected. Ask the Agent to update the recommended start command in RUNBOOK.md.",
        }

    @staticmethod
    def _extract_runbook_command(path: Path) -> str:
        if not path.exists() or not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        section_match = re.search(
            r"##\s*(?:Recommended Start Command|推荐启动命令)\s*(.*?)(?:\n##\s|\Z)",
            text,
            flags=re.S,
        )
        if section_match:
            section = section_match.group(1).strip()
            fenced = re.search(r"```(?:bash|sh|shell)?\s*\n(.+?)\n```", section, flags=re.S)
            candidates = fenced.group(1).splitlines() if fenced else section.splitlines()
            for line in candidates:
                command = line.strip().lstrip("-").strip()
                if command and not command.startswith("#") and command not in {"待生成", "待生成。", "TBD", "TBD."}:
                    return command

        inline_match = re.search(
            r"(?:Start command|Run command|启动命令|运行命令)\s*[:：]\s*`?([^`\n]+)`?",
            text,
            flags=re.I,
        )
        if inline_match:
            command = inline_match.group(1).strip()
            if command and command not in {"待生成", "待生成。", "TBD", "TBD."}:
                return command
        return ""

    def _infer_package_command(self, root: Path) -> dict | None:
        packages = [
            path for path in root.rglob("package.json")
            if "node_modules" not in path.parts and ".astudio" not in path.parts and ".antit" not in path.parts
        ]
        packages.sort(key=lambda path: (len(path.relative_to(root).parts), path.as_posix()))

        for package_path in packages[:8]:
            try:
                data = json.loads(package_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            scripts = data.get("scripts") or {}
            if not isinstance(scripts, dict):
                continue
            script_name = next((name for name in START_SCRIPT_PRIORITY if name in scripts), "")
            if not script_name:
                continue

            package_dir = package_path.parent
            manager = self._detect_package_manager(package_dir, root)
            rel_dir = package_dir.relative_to(root).as_posix()
            cwd = "." if rel_dir == "." else rel_dir
            if manager == "npm":
                command = "npm start" if script_name == "start" else f"npm run {script_name}"
            else:
                command = f"{manager} {script_name}"
            return {"command": command, "cwd": cwd, "source": f"{cwd}/package.json" if cwd != "." else "package.json"}
        return None

    @staticmethod
    def _detect_package_manager(package_dir: Path, root: Path) -> str:
        for base in (package_dir, root):
            if (base / "pnpm-lock.yaml").exists():
                return "pnpm"
            if (base / "yarn.lock").exists():
                return "yarn"
            if (base / "package-lock.json").exists():
                return "npm"
        return "pnpm"

    @staticmethod
    def _infer_static_command(root: Path) -> dict | None:
        for rel in ("index.html", "public/index.html", "dist/index.html", "build/index.html"):
            path = root / rel
            if path.exists() and path.is_file():
                cwd_path = path.parent
                rel_dir = cwd_path.relative_to(root).as_posix()
                cwd = "." if rel_dir == "." else rel_dir
                return {
                    "command": "python3 -m http.server",
                    "cwd": cwd,
                    "source": rel,
                }
        return None

    @staticmethod
    def _infer_python_command(root: Path) -> dict | None:
        if (root / "streamlit_app.py").exists():
            return {"command": "streamlit run streamlit_app.py", "cwd": ".", "source": "streamlit_app.py"}
        for filename in ("main.py", "app.py"):
            path = root / filename
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")[:4000]
            if "FastAPI(" in text:
                module = path.stem
                return {"command": f"uvicorn {module}:app", "cwd": ".", "source": filename}
            return {"command": f"python3 {filename}", "cwd": ".", "source": filename}
        return None

    def safe_path(self, sandbox: Sandbox, relative_path: str = ".") -> Path:
        root = Path(sandbox.path).resolve()
        target = (root / (relative_path or ".")).resolve()
        if not self.is_inside(root, target):
            raise PermissionError(f"Path escapes sandbox: {relative_path}")
        return target

    @staticmethod
    def is_inside(root: Path, target: Path) -> bool:
        root_resolved = root.resolve()
        target_resolved = target.resolve()
        try:
            target_resolved.relative_to(root_resolved)
            return True
        except ValueError:
            return False

    @staticmethod
    def is_inside_root(target: Path) -> bool:
        target_resolved = target.resolve()
        root_resolved = SANDBOXES_DIR.resolve()
        try:
            target_resolved.relative_to(root_resolved)
            return True
        except ValueError:
            return False

    def _ensure_docs(self, sandbox: Sandbox) -> None:
        self._write_bootstrap_docs(sandbox, sandbox.description)

    def _write_bootstrap_docs(self, sandbox: Sandbox, question: str) -> None:
        root = Path(sandbox.path)
        root.mkdir(parents=True, exist_ok=True)
        (root / ".astudio" / "runs").mkdir(parents=True, exist_ok=True)
        (root / "src").mkdir(exist_ok=True)
        (root / "output").mkdir(exist_ok=True)
        (root / "public").mkdir(exist_ok=True)

        files = {
            "README.md": f"""# Task Sandbox

Sandbox owner: {sandbox.owner_type}:{sandbox.owner_id or sandbox.task_id}
Related task: {sandbox.task_id}

Task goal:
{question}

This directory stores scripts, pages, processed data, results, and intermediate files for this task or scheduled task.

Reserved development port: {sandbox.dev_port or "not allocated"}
""",
            "RUNBOOK.md": """# Runbook

## Recommended Start Command
TBD.

## Page Preview
If `index.html` or `public/index.html` is generated, open the preview from the AStudio sandbox detail page.

## Main Files
TBD.

## Latest Run Result
TBD.
""",
            "AGENT_GUIDE.md": """# Agent Sandbox Work Guide

- Write all code, data, reports, and generated artifacts inside the current task sandbox.
- Do not access paths outside this sandbox.
- If you generate a page, provide the start command and preview method.
- If you start a development server, prefer the port from `PORT` / `VITE_PORT` / `ASTUDIO_SANDBOX_PORT`.
- If you run scripts, record inputs, outputs, dependencies, and result files.
- Update `RUNBOOK.md` when finished.
""",
            ".astudio/sandbox.json": (
                "{\n"
                f'  "sandbox_id": "{sandbox.id}",\n'
                f'  "owner_type": "{sandbox.owner_type}",\n'
                f'  "owner_id": "{sandbox.owner_id}",\n'
                f'  "task_id": "{sandbox.task_id}",\n'
                '  "runtime_type": "local"\n'
                "}\n"
            ),
        }
        for rel, content in files.items():
            path = root / rel
            if self._should_write_bootstrap_doc(path, rel):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

    @staticmethod
    def _should_write_bootstrap_doc(path: Path, rel: str) -> bool:
        if not path.exists():
            return True
        if rel == ".astudio/sandbox.json":
            return False
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return False
        legacy_markers = {
            "README.md": ("# 任务沙箱", "沙箱归属", "这里保存本任务"),
            "RUNBOOK.md": ("# 运行说明", "推荐启动命令", "待生成"),
            "AGENT_GUIDE.md": ("# Agent 沙箱工作指南", "不要访问沙箱外路径", "完成后更新 `RUNBOOK.md`"),
        }
        markers = legacy_markers.get(rel)
        return bool(markers and all(marker in text for marker in markers))

    @staticmethod
    def _row_to_sandbox(row) -> Sandbox:
        return Sandbox(
            id=row["id"],
            owner_type=row["owner_type"] if "owner_type" in row.keys() and row["owner_type"] else "task",
            owner_id=row["owner_id"] if "owner_id" in row.keys() and row["owner_id"] else row["task_id"],
            task_id=row["task_id"],
            path=row["path"],
            status=row["status"],
            title=row["title"] or "",
            description=row["description"] or "",
            runtime_type=row["runtime_type"] or "local",
            dev_port=row["dev_port"] if "dev_port" in row.keys() else None,
            preview_url=row["preview_url"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_active_at=datetime.fromisoformat(row["last_active_at"]) if row["last_active_at"] else None,
        )

    @staticmethod
    def _row_to_run(row) -> SandboxRun:
        return SandboxRun(
            id=row["id"],
            sandbox_id=row["sandbox_id"],
            task_id=row["task_id"],
            command=row["command"],
            cwd=row["cwd"] or ".",
            status=row["status"],
            pid=row["pid"],
            exit_code=row["exit_code"],
            stdout_path=row["stdout_path"],
            stderr_path=row["stderr_path"],
            preview_url=row["preview_url"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        )
