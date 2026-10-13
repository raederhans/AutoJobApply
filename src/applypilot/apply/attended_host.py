"""Explicit Host transport adapter for the existing attended transaction owner.

The trusted operator selects a Host-produced review, never worker-supplied page
evidence. One outstanding permit lives in the same attempt payload. File delivery
happens after commit; interrupted delivery never grants another input permit.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from applypilot.apply.application_consistency import build_contract, check_contract, resolve_source
from applypilot.apply.attended_recovery import summarize_feedback
from applypilot.apply.visual_bridge import _atomic_write_json, _validate_operation, read_active_host


def _id(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("canonical continuity ID required")
    return value


def _read(path):
    raw = path.read_bytes()
    if len(raw) > 8_000_000:
        raise ValueError("bounded Host transport document required")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Host transport object required")  # noqa: TRY004
    return value, hashlib.sha256(raw).hexdigest()


def _evidence(base, envelope, path):
    return {**base, "source": "attending_host", "observed_at": envelope["observed_at"],
            "evidence_refs": [str(path)], "inspection": envelope.get("inspection")}


def _steps(request):
    operation, args = request["operation"], request["arguments"]
    if operation == "observe":
        if args.get("mode", "dom") != "dom":
            raise ValueError("continuity requires a DOM observation")
        return []
    if operation in {"fill_control", "select_control"}:
        return [{"operation": operation, "field_key": args["field_key"], "value": args["value"]}]
    if operation == "fill_batch" or (operation == "prepare_subgoal" and args.get("kind") == "record_fields"):
        return args["steps"]
    if operation == "prepare_subgoal" and args.get("kind") == "school_selection":
        if args.get("query", args["value"]) != args["value"]:
            raise ValueError("school search must use the confirmed source value")
        return [{"operation": "select_control", "field_key": args["field_key"], "value": args["value"]}]
    raise ValueError("operation is outside reviewed preparation continuity")


def _review_values(request, form, state, profile, job):
    steps = _steps(request)
    if not steps:
        return
    if (not isinstance(steps, list) or not 1 <= len(steps) <= 4
            or len({s["field_key"] for s in steps}) != len(steps)):
        raise ValueError("one to four distinct reviewed fields required")
    coverage = form.get("coverage") or {}
    if (coverage.get("scope") != "visible_top_document_open_shadow" or coverage.get("iframe_count") != 0
            or form.get("question_coverage", {}).get("fields_truncated") is not False):
        raise ValueError("current form coverage is incomplete")
    contract = state.get("consistency_contract") or {}
    items = {i["field_key"]: i for i in contract.get("items", [])}
    fields = {f["field_key"]: f for f in form["fields"]}
    for step in steps:
        if step.get("operation") not in {"fill_control", "select_control"} or step["field_key"] not in items:
            raise ValueError("every planned input requires an existing source binding")
        item = items[step["field_key"]]
        # Reuse D's current fact, question and record validation. This is plan
        # admission, never a claim that the desired value is already on the page.
        current = build_contract([{"field_key": item["field_key"], "source": item["source"]}],
                                 form, profile, job, state["materials"])["items"][0]
        if current != item:
            raise ValueError("source or question binding changed; bind and review afresh")
        expected, _, _ = resolve_source(item["source"], profile, job, state["materials"])
        value = step.get("value")
        field = fields[item["field_key"]]
        if step["operation"] == "select_control" and value != expected:
            options = [o for o in field.get("options", []) if not o.get("disabled") and o.get("value") == value]
            value = options[0].get("label") if len(options) == 1 else None
        if value != expected:
            raise ValueError("planned input differs from its confirmed source")


def _response(state, completion):
    return dict(completion["feedback"], attended_continuity={"attempt_id": state["attempt_id"],
        "recovery_status": state.get("recovery", {}).get("status"),
        "consistency_status": state.get("consistency_report", {}).get("status", "unbound"),
        "next_action": state.get("next_action", state.get("recovery", {}).get("next_action")), "submit_authority": False})


def _park_feedback(conn, state, feedback, reason):
    """Record historical transport evidence without renewing preparation authority."""
    from applypilot.apply import attended_application as owner

    summary = summarize_feedback(feedback, state, state["host_continuity"]["token_epoch"])
    previous = state.get("recovery") or {}
    history = list(previous.get("transport_history", []))
    old = next((item for item in list(previous.get("history", [])) + history
                if item["request_id"] == summary["request_id"]), None)
    missing = state["host_continuity"].get("missing_completion_request_id") == summary["request_id"]
    if old and old["response_digest"] != summary["response_digest"] and not missing:
        raise ValueError("recovery response identity replay conflict")
    if not old or (missing and old["response_digest"] != summary["response_digest"]):
        history.append(summary)
    unknown = previous.get("unknown_effect_latched", False) or summary["category"] == "effect_unknown"
    state["recovery"] = {**previous, "status": "parked", "transport_history": history[-16:],
        "category": summary["category"], "reason": summary["reason"],
        "unknown_effect_latched": unknown, "reconciliation_reason": reason,
        "next_action": "manual_effect_reconciliation_no_replay" if unknown else "finalize_after_host_reconciliation",
        "browser_write_authority": False, "submit_authority": False}
    # A historical result cannot attest current field values. Claimed/submitted
    # checkpoints never reach this bookkeeping path.
    for key in ("checkpoint", "checkpoint_digest", "consistency_report"):
        state.pop(key, None)
    for key in ("next_action", "blocked", "browser_write_authority", "submit_authority"):
        state.pop(key, None)
    owner._save(conn, state)
    return state


def transition(conn, req, profile):
    """Called within attended_application's existing BEGIN IMMEDIATE transaction."""
    from applypilot.apply import attended_application as owner

    action = req["action"]
    if action not in {"host-attach", "host-review", "host-complete", "host-reconcile"}:
        raise ValueError("unsupported attended Host transition")
    base = {key: req[key] for key in ("job_url", "attempt_id", "host_session_id", "tab_id")}
    bookkeeping = action in {"host-complete", "host-reconcile"}
    state = owner._execute(conn, {**base, "action": "resume"}, profile, host_transport=bookkeeping)
    root = Path(owner._required(req, "bridge_directory")).resolve()
    directory = root / "continuity"
    continuity = state.get("host_continuity")
    if continuity and continuity["directory"] != str(root):
        raise ValueError("Host transport is not attached to this attempt")
    if bookkeeping and continuity and continuity.get("last_completion") == req.get("request_id"):
        # Recovering A's acknowledgement must still revoke B when current
        # durable authority no longer permits preparation.
        if continuity.get("pending") and (state.get("next_action") != "fresh_host_inspection_before_input"
                                          or state.get("blocked") or state.get("gate_id")):
            (directory / "permits" / f"{_id(continuity['pending']['request_id'])}.json").unlink(missing_ok=True)
        request_id = _id(req["request_id"])
        completion, completion_sha = _read(directory / "completed" / f"{request_id}.json")
        if completion_sha != continuity["completion_sha256"]:
            raise ValueError("committed Host completion changed")
        return dict(state, next_action="already_recorded_no_input_replay"), (
            root / "responses" / f"{request_id}.json", _response(state, completion))
    historical_only = bookkeeping and not state.get("gate_id") and not state.get("submit_started") and (
        state.get("blocked") or state.get("next_action") == "manual_effect_reconciliation_no_replay"
        or state.get("next_action") == "host_effect_reconciliation_only")
    if (not historical_only
            and (state.get("next_action") != "fresh_host_inspection_before_input" or state.get("blocked"))):
        if continuity and continuity.get("pending"):
            (directory / "permits" / f"{_id(continuity['pending']['request_id'])}.json").unlink(missing_ok=True)
        return state, None
    if historical_only and continuity and continuity.get("pending"):
        # Revocation follows current owner authority even if the supplied
        # completion later fails parsing or binding validation.
        (directory / "permits" / f"{_id(continuity['pending']['request_id'])}.json").unlink(missing_ok=True)
    if not historical_only:
        state.pop("next_action", None)
        state.pop("browser_write_authority", None)
    if action == "host-attach":
        host = read_active_host(root)
        metadata, _ = _read(root / "host.json")
        if (host.session_id != base["host_session_id"] or host.target.get("tab_id") != base["tab_id"]
                or host.target.get("runtime") != "iab" or host.phase != "prepare"
                or metadata.get("attended_continuity") != 1):
            raise ValueError("same-attempt continuity prepare Host required")
        if continuity and (continuity["directory"] != str(root) or continuity["token_epoch"] != host.token_epoch):
            raise ValueError("cannot replace the attempt's Host transport")
        if state.get("host_token_epoch", host.token_epoch) != host.token_epoch:
            raise ValueError("cannot replace the attempt's Host epoch")
        state["host_token_epoch"] = host.token_epoch
        state["host_continuity"] = continuity or {"directory": str(root), "token_epoch": host.token_epoch}
        owner._save(conn, state)
        return state, (directory / "owner.json", {"schema_version": 1, **base, "host_token_epoch": host.token_epoch})
    if not continuity or continuity["directory"] != str(root):
        raise ValueError("Host transport is not attached to this attempt")
    base["host_token_epoch"] = continuity["token_epoch"]
    job = dict(conn.execute("SELECT * FROM jobs WHERE url=?", (base["job_url"],)).fetchone())
    job["application_url"] = job.get("application_url") or base["job_url"]
    if action == "host-review":
        if state.get("gate_id"):
            return dict(state, next_action="claimed_checkpoint_immutable; review_or_finalize",
                        browser_write_authority=False), None
        if continuity.get("pending"):
            raise ValueError("outstanding Host request must be completed or reconciled; no replay")
        host = read_active_host(root)
        if host.session_id != base["host_session_id"] or host.token_epoch != base["host_token_epoch"]:
            raise ValueError("Host session changed")
        review_path = directory / "reviews" / f"{_id(req.get('review_id'))}.json"
        review, review_sha = _read(review_path)
        request = review["request"]
        _validate_operation(request["operation"], request.get("observation_id"), request["arguments"])
        request_id = _id(request.get("request_id"))
        pending, pending_sha = _read(root / "pending" / f"{request_id}.json")
        deadline = request.get("deadline_at")
        if type(deadline) not in {int, float} or not math.isfinite(deadline):
            raise ValueError("finite Host request deadline required")
        if (review.get("attempt_id") != base["attempt_id"] or review.get("job_url") != base["job_url"]
                or review.get("host_token_epoch") != base["host_token_epoch"]
                or review.get("review_id") != req["review_id"] or pending != request
                or pending_sha != review.get("request_sha256") or deadline <= time.time()
                or request.get("session_id") != base["host_session_id"]
                or request.get("token_epoch") != base["host_token_epoch"]
                or request.get("phase") != "prepare" or request.get("target") != host.target):
            raise ValueError("review/request/attempt binding mismatch or expired request")
        evidence = _evidence(base, review, review_path)
        form = owner._fresh_form(evidence, state, job)
        if continuity.get("last_completion"):
            previous, previous_sha = _read(directory / "completed" / f"{continuity['last_completion']}.json")
            if previous_sha != continuity["completion_sha256"]:
                raise ValueError("committed Host completion changed")
            state = owner._execute(conn, {**evidence, "action": "recover", "feedback": previous["feedback"]}, profile,
                                   host_transport=True)
            if state.get("next_action") or state.get("recovery", {}).get("status") == "parked":
                return state, None
        if req.get("bindings") is not None:
            state = owner._execute(conn, {**evidence, "action": "bind-consistency", "bindings": req["bindings"]}, profile)
        _review_values(request, form, state, profile, job)
        if state.get("consistency_contract"):
            state["consistency_report"] = check_contract(state["consistency_contract"], {"form_state": form},
                                                         profile, job, state["materials"])
        permit = {"schema_version": 1, **base, "request_id": request_id, "review_id": review["review_id"],
                  "request_sha256": pending_sha, "review_sha256": review_sha,
                  "expires_at": min(request["deadline_at"], time.time() + 300)}
        state["host_continuity"]["pending"] = permit
        owner._save(conn, state)
        return dict(state, next_action="execute_exact_reviewed_request_once", submit_authority=False), (
            directory / "permits" / f"{request_id}.json", permit)

    pending = continuity.get("pending")
    request_id = _id(req.get("request_id"))
    if not pending or pending["request_id"] != request_id:
        if continuity.get("last_completion") == request_id:
            return dict(state, next_action="already_recorded_no_input_replay"), None
        raise ValueError("request is not outstanding on this attempt")
    completion_path = directory / "completed" / f"{request_id}.json"
    if (action == "host-reconcile" or historical_only) and not completion_path.exists():
        # Revoke an unused permit before parking. Atomic removal competes with
        # the Host's rename-to-used; an already consumed permit stays single use.
        (directory / "permits" / f"{request_id}.json").unlink(missing_ok=True)
        # This is an owner observation of missing transport evidence, not a
        # fabricated successful Host reply. Conservatively latch even if the
        # process died between committing the permit and publishing its file.
        feedback = {"schema_version": 1, "request_id": request_id, "session_id": base["host_session_id"],
                    "token_epoch": base["host_token_epoch"], "ok": False, "outcome": "outcome_unknown",
                    "content": [{"type": "text", "text": json.dumps({"owner_transport_evidence": "completion_missing"})}]}
        if historical_only:
            state = _park_feedback(conn, state, feedback, state.get("blocked") or "unresolved_host_effect")
        else:
            state = owner._execute(conn, {**base, "action": "recover", "feedback": feedback,
                "source": "attending_host", "observed_at": datetime.now(UTC).isoformat(),
                "evidence_refs": [str(completion_path)]}, profile, host_transport=True)
        # The owner observed absence, not an original Host response. A later
        # bound completion may be retained historically without clearing the latch.
        state["host_continuity"]["missing_completion_request_id"] = request_id
        owner._save(conn, state)
        return dict(state, next_action=state["recovery"]["next_action"],
                    browser_write_authority=False, submit_authority=False), None
    completion, completion_sha = _read(completion_path)
    if (completion.get("attempt_id") != base["attempt_id"] or completion.get("job_url") != base["job_url"]
            or completion.get("review_id") != pending["review_id"]
            or completion.get("request_sha256") != pending["request_sha256"]
            or completion.get("feedback", {}).get("request_id") != request_id):
        raise ValueError("Host completion does not bind the outstanding review")
    evidence = _evidence(base, completion, completion_path)
    observed = owner._observation(evidence, allow_stale=True)
    stale = (datetime.now(UTC) - observed).total_seconds() > 300
    if historical_only or stale:
        (directory / "permits" / f"{request_id}.json").unlink(missing_ok=True)
        state = _park_feedback(conn, state, completion["feedback"],
                               state.get("blocked") or ("stale_host_completion" if stale else "unresolved_host_effect"))
    else:
        state = owner._execute(conn, {**evidence, "action": "recover", "feedback": completion["feedback"]}, profile,
                               host_transport=True)
    if (not historical_only and not stale and not state.get("blocked") and completion.get("inspection")
            and not state.get("recovery", {}).get("unknown_effect_latched")):
        form = owner._fresh_form(evidence, state, job)
        if state.get("consistency_contract"):
            state["consistency_report"] = check_contract(state["consistency_contract"], {"form_state": form},
                                                         profile, job, state["materials"])
    continuity = state["host_continuity"]
    continuity.pop("pending", None)
    continuity.pop("missing_completion_request_id", None)
    continuity.update(last_completion=request_id, completion_sha256=completion_sha)
    owner._save(conn, state)
    response = _response(state, completion)
    return dict(state, browser_write_authority=False, submit_authority=False), (
        root / "responses" / f"{request_id}.json", response)


def publish(conn, delivery, profile):
    path, payload = delivery
    if path.parent.name != "permits":
        _atomic_write_json(path, payload)
        return
    from applypilot.apply import attended_application as owner

    # A second owner can reconcile or latch submit between the first commit and
    # delivery. Serialize publication with those transitions and recheck current
    # authority; otherwise a delayed publisher could resurrect a revoked permit.
    conn.execute("BEGIN IMMEDIATE")
    try:
        base = {key: payload[key] for key in ("job_url", "attempt_id", "host_session_id", "tab_id")}
        state = owner._execute(conn, {**base, "action": "resume"}, profile, host_transport=True)
        allowed = (state.get("next_action") == "fresh_host_inspection_before_input"
                   and not state.get("blocked") and not state.get("gate_id")
                   and state.get("host_continuity", {}).get("pending") == payload
                   and payload["expires_at"] > time.time())
        if allowed:
            _atomic_write_json(path, payload)
        else:
            path.unlink(missing_ok=True)
        conn.commit()
    except Exception as exc:
        cleanup_failed = False
        try:
            path.unlink(missing_ok=True)
        except OSError:
            cleanup_failed = True
        finally:
            conn.rollback()
        if isinstance(exc, OSError) or cleanup_failed:
            raise OSError("Host permit publication failed; "
                          + ("cleanup failed; " if cleanup_failed else "")
                          + "reconcile without replay") from None
        raise
    if not allowed:
        raise ValueError("Host permit became unavailable before publication; reconcile without replay")
