"""Small shared contracts for versioned career inputs."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class BundleValidationError(ValueError):
    """A research bundle is incomplete, unsafe or not source-grounded."""


class InputChangedError(ValueError):
    """A result or update refers to an obsolete target revision."""


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_id(kind: str, payload: Any, raw_bytes: bytes = b"") -> str:
    """Identify exact immutable content, including its metadata and original bytes."""
    metadata = json_text(payload).encode("utf-8")
    digest = hashlib.sha256(len(metadata).to_bytes(8, "big") + metadata + raw_bytes).hexdigest()
    return f"{kind}:{digest}"


def required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value
