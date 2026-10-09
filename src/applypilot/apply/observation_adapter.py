"""Adapt Stagehand observations to the existing supervised prepare bridge.

External actions are suggestions, never executable selectors or candidate facts.
Only a reviewed binding and a fresh host observation can admit a small batch.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from applypilot.apply.visual_bridge import read_active_host, request_visual_operation

ROUTINE_FACTS = frozenset({
    "city", "country", "email", "phone", "portfolio_url", "preferred_name", "postal_code", "state",
})


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError(f"{name} must be a nonempty string of at most 2000 characters")
    return value


def observation_payload(response: Mapping) -> dict:
    """Extract the one form report in the host's text content blocks."""
    if not isinstance(response, Mapping) or response.get("ok") is not True or response.get("outcome") != "completed":
        raise ValueError("host observation did not complete")
    reports = []
    content = response.get("content")
    if not isinstance(content, list):
        raise TypeError("host content array is required")
    for item in content:
        if not isinstance(item, Mapping) or item.get("type") != "text":
            continue
        try:
            value = json.loads(item.get("text", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and "form_state" in value:
            reports.append(value)
    if len(reports) != 1 or not isinstance(reports[0]["form_state"], dict):
        raise ValueError("one host form_state report is required")
    return reports[0]


def build_prepare_plan(actions: object, bindings: object, form: object, facts: object, *, page_url: str) -> dict:
    """Validate Stagehand v3 action arrays or v4 {data: [...]} results.

Bindings are operator-reviewed objects: selector, field_key, label, semantic.
Matching the visible label prevents a saved field key from changing meaning.
Values come solely from the separate facts mapping; external arguments are ignored.
The complete batch is validated before any browser effect.
"""
    if isinstance(actions, Mapping):
        actions = actions.get("data")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 4:
        raise ValueError("provide one to four observed actions per batch")
    if not isinstance(bindings, list) or not isinstance(form, Mapping) or not isinstance(facts, Mapping):
        raise TypeError("bindings array, host form and trusted facts object are required")
    if form.get("page_url") != _text(page_url, "page_url"):
        raise ValueError("host page URL does not match the expected application URL")
    fields = form.get("fields")
    if not isinstance(fields, list) or any(not isinstance(f, Mapping) for f in fields):
        raise ValueError("host fields array is required")
    by_key = {}
    for field in fields:
        key = _text(field.get("field_key"), "host field_key")
        if key in by_key:
            raise ValueError("ambiguous host field key")
        by_key[key] = field
    by_selector = {}
    for binding in bindings:
        if not isinstance(binding, Mapping):
            raise TypeError("each reviewed binding must be an object")
        selector = _text(binding.get("selector"), "external selector")
        if selector in by_selector:
            raise ValueError("ambiguous reviewed selector binding")
        by_selector[selector] = binding
    steps, review, seen = [], [], set()
    for action in actions:
        if not isinstance(action, Mapping):
            raise TypeError("each observed action must be an object")
        method = action.get("method")
        if method not in {"fill", "selectOption"}:
            raise ValueError("only routine fill/selectOption observations are admitted")
        binding = by_selector.get(_text(action.get("selector"), "external selector"))
        if binding is None:
            raise ValueError("external selector has no reviewed field binding")
        key = _text(binding.get("field_key"), "bound field_key")
        semantic = binding.get("semantic")
        if not isinstance(semantic, str) or semantic not in ROUTINE_FACTS:
            raise ValueError("only routine candidate facts are admitted")
        if key in seen:
            raise ValueError("duplicate target field in batch")
        seen.add(key)
        field = by_key.get(key)
        if field is None or field.get("label") != _text(binding.get("label"), "reviewed label"):
            raise ValueError("reviewed field identity is absent or changed")
        if (field.get("disabled") is not False or field.get("readonly") is not False
                or field.get("value_source") == "unavailable"):
            raise ValueError("field is not confirmed writable")
        if any(field.get(flag) for flag in ("sensitive", "custom", "dynamic", "stateful")):
            raise ValueError("field requires individual review")
        value = _text(facts.get(semantic), "trusted fact")
        operation = "fill_control" if method == "fill" else "select_control"
        if operation == "fill_control":
            if field.get("control") not in {"text", "email", "tel", "url"}:
                raise ValueError("only routine text controls are admitted")
        else:
            if field.get("control") != "select" or not isinstance(field.get("options"), list):
                raise ValueError("only observed native select controls are admitted")
            options = [o for o in field["options"] if isinstance(o, Mapping) and o.get("disabled") is False
                       and value in (o.get("value"), o.get("label"))]
            if len(options) != 1:
                raise ValueError("trusted fact has no unique enabled option")
            value = _text(options[0].get("value"), "option value")
        steps.append({"operation": operation, "field_key": key, "value": value})
        review.append({"operation": operation, "field_key": key, "semantic": semantic})
    return {"steps": steps, "review": review, "submit_authority": False}


def execute_prepare(root: Path, actions: object, bindings: object, facts: object, *, page_url: str,
                    timeout_seconds: float = 45) -> dict:
    """One fresh observation and at most one prepare batch. Never auto-retry."""
    host = read_active_host(root)
    if (host.phase != "prepare" or host.surface != "browser" or host.target.get("runtime") != "iab"
            or host.target.get("application_url") != page_url):
        raise ValueError("a matching supervised IAB prepare host is required")
    response = request_visual_operation(root, operation="observe", arguments={"mode": "dom"},
                                        timeout_seconds=timeout_seconds)
    if response.get("session_id") != host.session_id or response.get("token_epoch") != host.token_epoch:
        raise ValueError("host session changed while observing")
    report = observation_payload(response)
    plan = build_prepare_plan(actions, bindings, report["form_state"], facts, page_url=page_url)
    if read_active_host(root) != host:
        raise ValueError("host binding changed before input")
    # fill_batch is also constrained to prepare phase by both existing bridge and host.
    result = request_visual_operation(root, operation="fill_batch",
                                      observation_id=_text(response.get("observation_id"), "observation_id"),
                                      arguments={"steps": plan["steps"]}, timeout_seconds=timeout_seconds)
    try:
        post = observation_payload(result)
    except (ValueError, TypeError):
        return {"status": "manual_review", "reason": "batch_outcome_unverified", "submit_authority": False}
    batch = post.get("batch_result")
    batch = batch if isinstance(batch, Mapping) else {}
    results = batch.get("results")
    verified = (result.get("session_id") == host.session_id and result.get("token_epoch") == host.token_epoch
                and post["form_state"].get("page_url") == page_url and batch.get("status") == "verified"
                and batch.get("completed") == len(plan["steps"]) and isinstance(results, list)
                and len(results) == len(plan["steps"]))
    if verified:
        verified = all(isinstance(r, Mapping) and r.get("field_key") == s["field_key"]
                       and r.get("persisted") is True and r.get("invalid") is False
                       for r, s in zip(results, plan["steps"], strict=True))
    return {"status": "prepared" if verified else "manual_review", "fields": plan["review"],
            "submit_authority": False, "reobserve_required": not verified}
