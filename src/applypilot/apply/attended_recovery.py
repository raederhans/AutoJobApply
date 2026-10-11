"""Bounded recovery assessment for the existing attended ledger, not a second executor.

Only trusted Host envelopes are admitted. The caller checks leases, frozen materials,
job/session identity and irreversible submission evidence inside its transaction.
No browser, model, authority, new Episode or automatic retry is created here.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from uuid import UUID


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def json_blocks(content):
    if not isinstance(content, list) or len(content) > 32:
        raise ValueError("bounded Host content required")
    blocks = []
    total = 0
    for item in content:
        if not isinstance(item, Mapping):
            raise ValueError("invalid Host content block")  # noqa: TRY004 - one attended validation error contract
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if not isinstance(text, str):
            raise ValueError("invalid Host text block")  # noqa: TRY004 - one attended validation error contract
        total += len(text)
        if total > 4_000_000:
            raise ValueError("Host observation is too large")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue  # Visible DOM text is not a protocol object.
        if isinstance(data, dict):
            blocks.append(data)
    return blocks


def extract_form(inspection, state):
    if not isinstance(inspection, Mapping) or inspection.get("session_id") != state["host_session_id"]:
        raise ValueError("fresh inspection session mismatch")
    target = inspection.get("target")
    if (not isinstance(target, Mapping) or target.get("runtime") != "iab"
            or target.get("tab_id") != state["tab_id"]
            or inspection.get("phase") != "prepare" or inspection.get("submission_authorized") is not False):
        raise ValueError("fresh inspection must bind the IAB prepare tab")
    blocks = json_blocks(inspection.get("content"))
    forms = [b["form_state"] for b in blocks if "form_state" in b]
    pages = [b for b in blocks if "page_url" in b and "tab_id" in b]
    if len(forms) != 1 or len(pages) != 1 or not isinstance(forms[0], dict):
        raise ValueError("exactly one fresh form and page identity required")
    form, page = forms[0], pages[0]
    if (page["tab_id"] != state["tab_id"] or page["page_url"] != form.get("page_url")
            or not isinstance(form.get("fields"), list) or len(form["fields"]) > 1000):
        raise ValueError("fresh form/page identity mismatch")
    keys = [f.get("field_key") if isinstance(f, dict) else None for f in form["fields"]]
    if any(not isinstance(k, str) or not k for k in keys) or len(set(keys)) != len(keys):
        raise ValueError("ambiguous fresh field identity")
    return form


def summarize_feedback(feedback, state, token_epoch):
    if not isinstance(feedback, Mapping):
        raise ValueError("original Host response required, not worker prose")  # noqa: TRY004 - one attended validation error contract
    try:
        request_id = str(UUID(feedback.get("request_id", "")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("canonical Host request ID required") from exc
    if (feedback.get("request_id") != request_id or type(feedback.get("schema_version")) is not int or feedback["schema_version"] != 1 or feedback.get("session_id") != state["host_session_id"]
            or feedback.get("token_epoch") != token_epoch or not isinstance(feedback.get("ok"), bool)
            or feedback.get("outcome") not in {"completed", "failed", "outcome_unknown"}
            or (feedback["outcome"] == "completed") != feedback["ok"]):
        raise ValueError("Host response identity or outcome mismatch")
    blocks = json_blocks(feedback.get("content"))
    reports = [(kind, b[kind]) for b in blocks for kind in ("subgoal_result", "batch_result", "control_result")
               if b.get(kind) is not None]
    if len(reports) > 1 or any(not isinstance(r, Mapping) for _, r in reports):
        raise ValueError("ambiguous Host operation result")
    targets = []
    if feedback["outcome"] == "outcome_unknown":
        category, reason, verified = "effect_unknown", "unresolved_host_effect", []
    elif feedback["outcome"] == "failed":
        errors = [b for b in blocks if "host_state" in b]
        no_input = len(errors) == 1 and errors[0].get("host_state") == "active" and errors[0].get("reobserve_before_retry") is True
        category, reason, verified = ("no_effect" if no_input else "effect_unknown"), "host_preinput_rejection" if no_input else "unresolved_host_effect", []
    elif not reports:
        category, reason, verified = "observation", "no_control_completion_claim", []
    else:
        kind, report = reports[0]
        allowed_reasons = {"target_option_not_observed", "required_record_fields_not_supplied", "ambiguous_target_option",
            "dependencies_changed", "preinput_rejected", "business_postcondition_unproven", "delayed_readback_changed",
            "record_postconditions_verified", "selection_already_verified", "selection_postcondition_verified",
            "form_changed", "readback_not_verified", "control_changed"}
        reason = report.get("reason") if report.get("reason") in allowed_reasons else "host_result_observed"
        status = report.get("status")
        if status == "waiting":
            category = "waiting"
        elif status == "needs_fact":
            category = "needs_fact"
        elif status == "verified" or (kind == "control_result" and report.get("persisted") is True and report.get("invalid") is False):
            category = "verified"
        elif report.get("persisted") is None and report.get("outcome") in {"opened", "searched"}:
            category = "observation"
        else:
            category = "needs_correction"
        progress = report.get("progress", report.get("results", [report] if kind == "control_result" else []))
        if not isinstance(progress, list) or len(progress) > 16:
            raise ValueError("unbounded Host progress")
        targets = [p["field_key"] for p in progress if isinstance(p, Mapping) and isinstance(p.get("field_key"), str)]
        verified = [p["field_key"] for p in progress if isinstance(p, Mapping) and isinstance(p.get("field_key"), str)
                    and (p.get("verified") is True or (p.get("persisted") is True and p.get("invalid") is False))]
    return {"request_id": request_id, "response_digest": _digest(feedback), "category": category, "reason": reason,
            "verified_field_refs": sorted({_digest(key) for key in verified}),
            "target_field_refs": sorted({_digest(key) for key in targets})}


def episode_barrier(connection, attempt_id):
    """Inspect the existing Episode aggregate without running schema/lease setup.

    IAB has its own lease. Do not manufacture a BrowserLeaseBundle to create an
    Episode, and do not let an attended recovery mutate the other owner's state.
    """
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='application_episodes'").fetchone():
        return None
    rows = connection.execute("SELECT state,command_sequence,result_sequence FROM application_episodes WHERE attempt_id=?",
                              (attempt_id,)).fetchall()
    if len(rows) > 1:
        return "episode_identity_conflict"
    if not rows:
        return None
    state, commands, results = rows[0]
    if state == "receipt_only":
        return "reconcile_receipt_only"
    if commands > results:
        return "episode_effect_unresolved"
    if state in {"human_required", "recovering", "complete"}:
        return "episode_owner_review_required"
    return None


def assess_recovery(form, summary, previous, *, attempt_id, observed_at):
    from applypilot.apply.application_supervisor_loop import ApplicationSupervisorLoop, SupervisorObservation

    if not isinstance(observed_at, (int, float)) or not math.isfinite(observed_at):
        raise ValueError("finite recovery observation time required")
    previous = previous or {}
    history = list(previous.get("history", []))
    event = {**summary, "observed_at": observed_at, "page_signature": _digest(form)}
    old = next((item for item in history if item["request_id"] == summary["request_id"]), None)
    if old and old["response_digest"] != event["response_digest"]:
        raise ValueError("recovery response identity replay conflict")
    if history and observed_at < history[-1]["observed_at"]:
        raise ValueError("recovery observations must be time ordered")
    if not old:
        history.append(event)
    # One original response is one completed action, however often it is read.
    # Retain a bounded diagnostic history, not private page contents or answers.
    history = history[-16:]
    loop = ApplicationSupervisorLoop(attempt_id=attempt_id, turn_id="attended-recovery")
    loop.start(observed_at=history[0]["observed_at"])
    decision = None
    prior_page = None
    for item in history:
        progress = item["category"] == "verified" or (prior_page is not None and prior_page != item["page_signature"])
        decision = loop.observe(SupervisorObservation(attempt_id=attempt_id, turn_id="attended-recovery",
            event_type="tool.completed", observed_at=item["observed_at"], tool_call_id=item["request_id"],
            tool_name="attended_host_result", tool_params={"category": item["category"], "reason": item["reason"]},
            meaningful_progress=progress, page_signature=item["page_signature"]))
        prior_page = item["page_signature"]
    category = summary["category"]
    ambiguous = any(not isinstance(f.get("semantic"), Mapping) or f["semantic"].get("identification") != "identified" for f in form["fields"])
    coverage = form.get("coverage") or {}
    question_coverage = form.get("question_coverage") or {}
    coverage_missing = (not form["fields"] or coverage.get("scope") != "visible_top_document_open_shadow"
                        or type(coverage.get("iframe_count")) is not int or coverage["iframe_count"] != 0
                        or question_coverage.get("fields_truncated") is not False)
    if previous.get("unknown_effect_latched") or category == "effect_unknown":
        status, next_action = "parked", "manual_effect_reconciliation_no_replay"
    elif category == "needs_fact":
        status, next_action = "needs_fact", "supply_confirmed_facts_then_reobserve"
    elif ambiguous or coverage_missing:
        status, next_action = "needs_observation", "inspect_ambiguous_or_uncovered_fields"
    elif category == "waiting":
        targets = [f for f in form["fields"] if _digest(f["field_key"]) in summary.get("target_field_refs", [])]
        options_ready = targets and all(isinstance(f.get("semantic"), Mapping) and (f["semantic"].get("options") or {}).get("status") == "identified" for f in targets)
        waiting_count = sum(item["category"] == "waiting" for item in history)
        if options_ready and not ambiguous and not coverage_missing:
            status, next_action = "reviewed", "replan_from_observed_options_without_replaying_input"
        elif waiting_count >= 3:
            status, next_action = "parked", "waiting_budget_exhausted_inspect_or_handoff"
        else:
            status, next_action = "waiting", "observe_pending_options_without_repeating_input"
    elif decision and decision.level >= 2:
        status, next_action = "parked", "review_repeated_no_progress_with_supervisor"
    elif category == "no_effect":
        status, next_action = "no_effect", "review_alternative_against_current_page"
    elif category == "needs_correction":
        status, next_action = "needs_correction", "repair_only_observed_unresolved_fields"
    else:
        status, next_action = "reviewed", "continue_from_fresh_page_after_host_review"
    return {"schema_version": 1, "status": status, "next_action": next_action,
        "category": category, "history": history, "observation_digest": _digest(form),
        "unknown_effect_latched": bool(previous.get("unknown_effect_latched") or category == "effect_unknown"),
        "historical_verified_field_refs": sorted({ref for item in history for ref in item.get("verified_field_refs", [])})[:128],
        "progress_evidence": "historical_action_readback_only_not_current_fact_validation",
        "supervisor_level": decision.level if decision else 0,
        "supervisor_reason": decision.reason_code if decision else "OBSERVING",
        "browser_write_authority": False, "submit_authority": False,
        "whole_application_verified": False}
