"""Compatibility facade for :mod:`applypilot.contracts.career`."""

from __future__ import annotations

from applypilot.contracts.career import (
    BundleValidationError,
    InputChangedError,
    content_id,
    json_text,
    required_text,
)

__all__ = [
    "BundleValidationError",
    "InputChangedError",
    "content_id",
    "json_text",
    "required_text",
]
