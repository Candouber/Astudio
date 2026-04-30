"""Encode task progress strings for frontend localization.

Stored format: __i18n__:{"k":"backendTaskStatus.some_key","p":{"label":"..."}}
"""

from __future__ import annotations

import json
from typing import Any

PREFIX = "__i18n__:"


def encode_task_status_msg(key: str, **params: Any) -> str:
    """Build a prefixed payload; omit empty params."""
    p: dict[str, Any] = {}
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        p[k] = v
    payload: dict[str, Any] = {"k": key}
    if p:
        payload["p"] = p
    return PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
