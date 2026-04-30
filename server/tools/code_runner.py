"""Python code execution tool."""
import asyncio
import sys
import textwrap

from loguru import logger

from tools.execution_safety import LocalExecutionBlocked, build_sanitized_env, validate_python_snippet

TIMEOUT_SECONDS = 15
MAX_OUTPUT_CHARS = 4000


async def execute_code(code: str) -> str:
    """
    在隔离子进程中执行 Python 代码，返回 stdout + stderr 组合结果。
    """
    # 清理缩进，避免 IndentationError
    code = textwrap.dedent(code).strip()
    try:
        validate_python_snippet(code)
    except LocalExecutionBlocked as e:
        return f"[Safety blocked] {e}"

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code,
            env=build_sanitized_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            # 关键：等子进程真正结束，回收资源，避免僵尸
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except Exception:
                pass
            return f"[Timeout] Code execution exceeded {TIMEOUT_SECONDS} seconds and was terminated."

        output_parts = []
        if stdout:
            output_parts.append(f"[stdout]\n{stdout.decode('utf-8', errors='replace')}")
        if stderr:
            output_parts.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace')}")

        result = "\n".join(output_parts) if output_parts else "[No output]"

        # 截断过长输出
        if len(result) > MAX_OUTPUT_CHARS:
            result = result[:MAX_OUTPUT_CHARS] + f"\n... [Output too long; truncated to {MAX_OUTPUT_CHARS} characters]"

        return result

    except asyncio.CancelledError:
        # 任务被终止时也要确保子进程被 kill，避免泄漏
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=3)
            except Exception:
                pass
        raise
    except Exception as e:
        logger.error(f"execute_code failed: {e}")
        return f"[Execution failed] {e}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "execute_code",
        "description": "Execute Python code in a sandboxed subprocess and return output. Suitable for data calculation, logic verification, format conversion, and testing code snippets.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code string to execute; supports multiple lines"
                }
            },
            "required": ["code"]
        }
    }
}
