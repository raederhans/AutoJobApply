"""Runtime failure classification for apply attempts.

Pure policy helpers that map agent timeouts, CLI process exits and browser
tool results onto apply statuses, and decide which failures block retries.
"""

from __future__ import annotations

from collections.abc import Mapping

from applypilot.apply.failure_taxonomy import classify_failure


def _runtime_timeout_status(*, submission_phase: str, dry_run: bool) -> str:
    """Keep an interrupted real submit uncertain; identify all other budget exhaustion."""
    if submission_phase == "submit" and not dry_run:
        return "submission_uncertain"
    return "failed:agent_runtime_timeout"


def _resolve_cli_process_failure(
    returncode: int | None,
    status: str,
    result_source: str,
    *,
    submission_phase: str,
    dry_run: bool,
    structured_result_present: bool,
) -> tuple[str, str, dict[str, object] | None]:
    """Classify a direct CLI failure without erasing admitted result evidence."""
    if returncode in {None, 0}:
        return status, result_source, None
    if structured_result_present or status != "failed:invalid_result_marker":
        return status, result_source, None

    if submission_phase == "submit" and not dry_run:
        return (
            "submission_uncertain",
            "runtime_provider_failed",
            {
                "category": "submission_confirmation_missing",
                "recoverability": "submission_uncertain",
                "next_action": "reconcile_receipt_without_resubmitting",
            },
        )
    return (
        "failed:agent_runtime_process_exit",
        "runtime_provider_failed",
        {
            "category": "agent_runtime_failure",
            "recoverability": "retry_new_session",
            "next_action": "inspect_typed_process_exit_before_retry",
        },
    )


_TOOL_FAILURE_STATUSES = {
    "cancelled",
    "canceled",
    "error",
    "failed",
    "timed_out",
    "timeout",
}
_TOOL_SUCCESS_STATUSES = {"completed", "success", "succeeded"}


def _tool_result_reports_error(payload: object) -> bool:
    """Recognize transport and nested MCP failures without parsing tool prose."""
    if isinstance(payload, list):
        return any(_tool_result_reports_error(item) for item in payload)
    if not isinstance(payload, Mapping):
        return False
    if payload.get("isError") is True or payload.get("is_error") is True:
        return True
    status = str(payload.get("status") or "").strip().casefold()
    if status in _TOOL_FAILURE_STATUSES:
        return True
    error = payload.get("error")
    if error not in (None, "", False, (), [], {}):
        return True
    return any(
        _tool_result_reports_error(payload.get(key))
        for key in ("result", "output", "content")
        if key in payload
    )


def _tool_result_succeeded(payload: object, *, terminal_event: bool) -> bool:
    """Return true only for a terminal tool result with no typed error marker."""
    if _tool_result_reports_error(payload):
        return False
    if not terminal_event or not isinstance(payload, Mapping):
        return True
    status = str(payload.get("status") or "completed").strip().casefold()
    return status in _TOOL_SUCCESS_STATUSES


def _tool_failure_text(payload: object, *, depth: int = 0) -> str:
    """Collect bounded transient error text for classification only."""
    if depth > 4:
        return ""
    if isinstance(payload, str):
        return payload[:1000]
    if isinstance(payload, list):
        return " ".join(_tool_failure_text(item, depth=depth + 1) for item in payload[:8])
    if not isinstance(payload, Mapping):
        return ""
    return " ".join(
        _tool_failure_text(payload.get(key), depth=depth + 1)
        for key in ("status", "error", "message", "result", "output", "content")
        if key in payload
    )[:2000]


def _classify_browser_tool_failure(payload: object) -> str:
    """Reduce Playwright/MCP failures to a non-sensitive actionable category."""
    text = " ".join(_tool_failure_text(payload).casefold().split())
    if "strict mode violation" in text or "resolved to" in text and "elements" in text:
        return "ambiguous_locator"
    if "file chooser" in text or "setinputfiles" in text or "set input files" in text:
        return "file_chooser_unavailable"
    if "frame was detached" in text or "frame detached" in text:
        return "stale_frame"
    if "target closed" in text or "page closed" in text or "context closed" in text:
        return "target_closed"
    if "not attached" in text or "element is detached" in text:
        return "stale_element"
    if "intercepts pointer events" in text or "does not receive pointer events" in text:
        return "actionability_intercepted"
    if any(token in text for token in ("not visible", "not stable", "not enabled", "not editable")):
        return "actionability_failed"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if any(token in text for token in ("connection refused", "econnrefused", "transport closed")):
        return "transport_unavailable"
    return "unclassified"


def _normalize_browser_runtime_failure(
    status: str,
    *,
    browser_tool_call_count: int,
    browser_tool_success_count: int,
    browser_failure_summary: Mapping[str, object] | None = None,
    failure_context: dict[str, object] | None,
) -> tuple[str, dict[str, object] | None]:
    """Distinguish an absent browser MCP from a later site interaction failure."""
    normalized_status = status.strip().casefold()
    safe_failures = {
        str(key)[:64]: min(max(int(value), 0), 99)
        for key, value in (browser_failure_summary or {}).items()
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    }
    if normalized_status == "failed:conflicting_agent_results":
        context = dict(failure_context or {})
        context.update(
            {
                "category": "agent_result_conflict",
                "recoverability": "retry_same_application",
                "next_action": "reobserve_same_page_and_emit_one_consistent_turn_result",
                "attempts": min(max(browser_tool_call_count, 0), 10),
            }
        )
        if safe_failures:
            context["browser_tool_failures"] = safe_failures
        return status, context
    if (
        normalized_status != "failed:browser_mcp_unavailable"
        or browser_tool_success_count < 1
    ):
        return status, failure_context

    context = dict(failure_context or {})
    context.update(
        {
            "category": "browser_interaction_unavailable",
            "recoverability": "requires_capability",
            "missing_capability": "site_specific_browser_interaction_or_app_handoff",
            "next_action": "inspect_page_state_or_route_to_authorized_app_browser",
            "visible_state": (
                f"{browser_tool_success_count} browser tool call(s) succeeded before "
                "the site interaction became unavailable"
            ),
            "attempts": min(max(browser_tool_call_count, 0), 10),
        }
    )
    return "failed:browser_interaction_unavailable", context


PERMANENT_FAILURES: set[str] = {
    "expired",
    "already_applied",
    "not_a_job_application", "unsafe_permissions",
    "unsafe_verification",
    "assessment", "assessment_required",
}

PERMANENT_PREFIXES: tuple[str, ...] = (
    "manual_review_required:submission_validation",
    "assessment",
    "unsafe_verification",
)

def _is_permanent_failure(result: str) -> bool:
    """Determine whether automatic retries should remain blocked."""
    reason = result.split(":", 1)[-1] if ":" in result else result
    descriptor = classify_failure(result)
    return (
        descriptor.permanent
        or
        result in PERMANENT_FAILURES
        or reason in PERMANENT_FAILURES
        or any(reason.startswith(p) for p in PERMANENT_PREFIXES)
    )
