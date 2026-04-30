from pathlib import Path

from loguru import logger

WORKSPACE_DIR = Path(__file__).parent.parent.parent / "data" / "workspace"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

MAX_READ_CHARS = 8000


def _safe_path(relative_path: str) -> Path:
    target = (WORKSPACE_DIR / relative_path).resolve()
    workspace_resolved = WORKSPACE_DIR.resolve()
    try:
        is_inside = target == workspace_resolved or target.is_relative_to(workspace_resolved)
    except AttributeError:  # 兼容 py3.8（is_relative_to 在 3.9+）
        try:
            target.relative_to(workspace_resolved)
            is_inside = True
        except ValueError:
            is_inside = False
    if not is_inside:
        raise PermissionError(f"Path escapes workspace: {relative_path}")
    return target


async def read_file(path: str) -> str:
    try:
        target = _safe_path(path)
        if not target.exists():
            return f"[File not found] {path}"
        if not target.is_file():
            return f"[Not a file] {path}"

        content = target.read_text(encoding="utf-8", errors="replace")
        if len(content) > MAX_READ_CHARS:
            content = content[:MAX_READ_CHARS] + f"\n... [File too long; truncated to {MAX_READ_CHARS} characters]"
        return content
    except PermissionError as e:
        return f"[Permission error] {e}"
    except Exception as e:
        logger.error(f"read_file failed: {e}")
        return f"[Read failed] {e}"


async def write_file(path: str, content: str) -> str:
    try:
        target = _safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"[Write succeeded] {path} ({len(content)} characters)"
    except PermissionError as e:
        return f"[Permission error] {e}"
    except Exception as e:
        logger.error(f"write_file failed: {e}")
        return f"[Write failed] {e}"


async def list_files(directory: str = ".") -> str:
    try:
        target = _safe_path(directory)
        if not target.exists():
            return f"[Directory not found] {directory}"
        items = []
        for item in sorted(target.iterdir()):
            kind = "📁" if item.is_dir() else "📄"
            size = f"({item.stat().st_size} bytes)" if item.is_file() else ""
            items.append(f"{kind} {item.name} {size}")
        return "\n".join(items) if items else "[Empty directory]"
    except PermissionError as e:
        return f"[Permission error] {e}"
    except Exception as e:
        logger.error(f"list_files failed: {e}")
        return f"[List directory failed] {e}"


READ_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read the content of a file in the workspace. Workspace paths are relative, such as 'output/result.txt'.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path inside the workspace"}
            },
            "required": ["path"]
        }
    }
}

WRITE_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write content to a file in the workspace, overwriting it. Suitable for saving code, reports, data, and intermediate artifacts.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path inside the workspace"},
                "content": {"type": "string", "description": "File content to write"}
            },
            "required": ["path", "content"]
        }
    }
}

LIST_FILES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": "List all files and folders under a directory in the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Directory path to list; defaults to workspace root", "default": "."}
            },
            "required": []
        }
    }
}
