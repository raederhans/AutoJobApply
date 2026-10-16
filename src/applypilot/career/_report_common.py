"""Shared value formatting and link policy for local career reports."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit


def _text(value: Any, fallback: str = "未提供") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username or parsed.password:
        return None
    return value.strip()
