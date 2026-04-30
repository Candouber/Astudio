"""
Local execution guardrails.

This project intentionally runs agents on the user's machine. These checks are
not a sandbox; they reduce common foot-guns from model-generated commands/code.
"""
import os
import re
from typing import Mapping


class LocalExecutionBlocked(ValueError):
    """Raised when a local command or code snippet matches a high-risk pattern."""


_SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "COOKIE",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "SESSION",
    "OAUTH",
)
_SENSITIVE_ENV_EXACT = {
    "SSH_AUTH_SOCK",
    "GPG_AGENT_INFO",
    "NETRC",
}

_COMMAND_DENY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(^|[;&|]\s*)sudo(\s|$)"), "sudo requires explicit user control"),
    (re.compile(r"(^|[;&|]\s*)su(\s|$)"), "switching users is not allowed"),
    (re.compile(r"\brm\s+-[^\n;&|]*[rf][^\n;&|]*\s+(/|~|\$HOME|\.\.|\*)"), "broad recursive deletion is blocked"),
    (re.compile(r"\b(shutdown|reboot|halt)\b"), "system power commands are blocked"),
    (re.compile(r"\bmkfs(\.|\s|$)"), "filesystem formatting commands are blocked"),
    (re.compile(r"\bdd\s+.*\bof=/dev/"), "raw device writes are blocked"),
    (re.compile(r"\b(chmod|chown)\s+-R\s+.*\s(/|~|\$HOME)(\s|$)"), "recursive permission changes outside the sandbox are blocked"),
    (re.compile(r"\b(curl|wget)\b[^\n]*\|\s*(sh|bash|zsh|python|python3)\b"), "piping remote scripts into an interpreter is blocked"),
    (re.compile(r"(:\s*\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;?\s*:)"), "fork-bomb pattern is blocked"),
)

_SENSITIVE_PATH_MARKERS = (
    "/.ssh",
    "~/.ssh",
    "$HOME/.ssh",
    "id_rsa",
    "id_ed25519",
    ".aws/credentials",
    ".config/gh/hosts.yml",
    ".netrc",
)

_PYTHON_DENY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsubprocess\b"), "subprocess use is blocked in execute_code; use sandbox_run_command instead"),
    (re.compile(r"\bos\.system\s*\("), "shell execution is blocked in execute_code"),
    (re.compile(r"\bshutil\.rmtree\s*\("), "recursive deletion is blocked in execute_code"),
    (re.compile(r"\bos\.environ\b|\bos\.getenv\s*\("), "environment variable access is blocked in execute_code"),
    (re.compile(r"\bPath\.home\s*\(|\.expanduser\s*\("), "home-directory access is blocked in execute_code"),
)


def build_sanitized_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a copy of the process env with common secret-bearing keys removed."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not _is_sensitive_env_key(key)
    }
    if extra:
        env.update(extra)
    return env


def validate_local_command(command: str) -> None:
    """Block high-risk local shell command patterns before execution."""
    lowered = command.lower()
    for marker in _SENSITIVE_PATH_MARKERS:
        if marker.lower() in lowered:
            raise LocalExecutionBlocked(f"Command accesses a sensitive path or credential file: {marker}")
    for pattern, reason in _COMMAND_DENY_PATTERNS:
        if pattern.search(lowered):
            raise LocalExecutionBlocked(f"Command blocked by local safety policy: {reason}")


def validate_python_snippet(code: str) -> None:
    """Block high-risk Python snippets used by the lightweight execute_code tool."""
    lowered = code.lower()
    for marker in _SENSITIVE_PATH_MARKERS:
        if marker.lower() in lowered:
            raise LocalExecutionBlocked(f"Code accesses a sensitive path or credential file: {marker}")
    for pattern, reason in _PYTHON_DENY_PATTERNS:
        if pattern.search(lowered):
            raise LocalExecutionBlocked(f"Code blocked by local safety policy: {reason}")


def _is_sensitive_env_key(key: str) -> bool:
    upper = key.upper()
    return upper in _SENSITIVE_ENV_EXACT or any(marker in upper for marker in _SENSITIVE_ENV_MARKERS)
