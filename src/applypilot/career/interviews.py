"""Browser adapter for existing recruiting observations, actions and offline packs."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from applypilot import followup
from applypilot.career.workbench import previous_request, save_request
from applypilot.storage.transactions import write_transaction

RESULTS = {"completed": "面试已完成", "next_round": "进入下一轮（用户记录）",
           "offer": "收到录用意向（用户记录）", "rejected": "未通过（用户记录）"}


def _safe_pack(value):
    if isinstance(value, dict):
        return {key: _safe_pack(item) for key, item in value.items() if key != "path" and not key.endswith("_path")}
    if isinstance(value, list):
        return [_safe_pack(item) for item in value]
    return value


def _warnings(pack):
    return ["sent_snapshot_unavailable: 已投材料快照不可核验。" if warning.startswith("sent_snapshot_unavailable:")
            else warning for warning in pack["warnings"]]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _identity(event):
    return followup.interview_identity(event)


def jobs(conn):
    return [dict(zip(("job_url", "title", "company"), row, strict=True)) for row in
            conn.execute("SELECT url,title,company_name FROM jobs ORDER BY company_name,title,url")]


def action_interview_links(conn):
    """Only round/link metadata; never load sources, answers, packs or action text."""
    from applypilot.application_progress import effective_events

    if not all(followup._exists(conn, table) for table in ("followup_events", "followup_actions", "jobs")):
        return {}
    columns = {row[1] for row in conn.execute("PRAGMA table_info(followup_events)")}
    application_column = "application_id" if "application_id" in columns else "NULL"
    names = ("event_id", "job_url", "application_id", "event_type", "stage_id", "round", "action_ids", "supersedes_event_id", "interview_origin_event_id")
    events = [dict(zip(names, row, strict=True)) for row in conn.execute(
        f"SELECT event_id,job_url,{application_column},event_type,json_extract(payload,'$.stage_id'),"
        "json_extract(payload,'$.round'),json_extract(payload,'$.action_ids'),"
        "json_extract(payload,'$.supersedes_event_id'),json_extract(payload,'$.interview_origin_event_id') "
        "FROM followup_events ORDER BY occurred_at,event_id")]
    events = effective_events(events)
    exact_jobs = {row[0] for row in conn.execute("SELECT url FROM jobs")}
    actions = dict(conn.execute("SELECT action_id,job_url FROM followup_actions"))
    anchors = {}
    for event in events:
        if event["event_type"].startswith("interview") and event["job_url"] in exact_jobs:
            anchors.setdefault(_identity(event), set()).add(event["job_url"])
    links = {}
    for event in events:
        identity = _identity(event)
        if (anchors.get(identity) != {event["job_url"]} or event["job_url"] not in exact_jobs
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", identity)):
            continue
        ids = json.loads(event["action_ids"] or "[]")
        if not isinstance(ids, list):
            continue
        for action_id in ids:
            if isinstance(action_id, str) and actions.get(action_id) == event["job_url"]:
                links.setdefault(action_id, set()).add(identity)
    return {action_id: next(iter(identities)) for action_id, identities in links.items() if len(identities) == 1}


def _groups(conn):
    from applypilot.application_progress import effective_events

    groups = {}
    for event in effective_events(followup._events(conn)):
        if (event["event_type"].startswith("interview") or event.get("stage_id")
                or event.get("interview_origin_event_id")) and event.get("job_url"):
            groups.setdefault(_identity(event), []).append(event)
    return {identity: history for identity, history in groups.items()
            if any(event["event_type"].startswith("interview") for event in history)}


def detail(conn, workspace, identity):
    history = _groups(conn).get(identity)
    if not history:
        raise KeyError("interview_not_found")
    current = {}
    for event in history:
        current.update({k: v for k, v in event.items() if v is not None})
    interview = next((e for e in reversed(history) if e["event_type"].startswith("interview")), history[-1])
    scheduled = next((e for e in reversed(history) if e["event_type"] in followup.INTERVIEW_TYPES), {})
    status = {"interview_cancelled": "cancelled", "interview_completed": "completed"}.get(
        interview["event_type"], "scheduled" if interview.get("scheduled_at") else "pending")
    ids = {aid for event in history for aid in event.get("action_ids", [])}
    actions = [a for a in followup.actions(conn, job_url=current["job_url"]) if a["action_id"] in ids]
    row = conn.execute("SELECT title,company_name,full_description,tailored_resume_path FROM jobs WHERE url=?",
                       (current["job_url"],)).fetchone()
    if row is None:
        raise ValueError("interview_job_changed")
    # Changes to JD, selected resume or action completion invalidate browser edits.
    source = list(row)
    if row[3]:
        candidate = Path(row[3])
        candidate = candidate if candidate.is_absolute() else Path(workspace) / candidate
        try:
            source.append([candidate.stat().st_size, candidate.stat().st_mtime_ns])
        except OSError:
            source.append("missing")
    preparation = current.get("preparation", {"status": "not_prepared", "missing_materials": [], "warnings": []})
    for table in ("resume_artifacts", "resume_render_versions", "job_resume_assignments"):
        if followup._exists(conn, table):
            source.append([table, [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]])
    for table in ("application_receipts", "application_receipt_gate_bindings", "application_submission_gates",
                  "application_attempts", "application_batch_consumptions"):
        if followup._exists(conn, table):
            source.append([table, [tuple(r) for r in conn.execute(f"SELECT * FROM {table} WHERE job_url=? ORDER BY rowid",
                                                                 (current["job_url"],))]])
    source_revision = _digest(source)
    if preparation.get("pack_id"):
        if not re.fullmatch(r"[a-f0-9]{32}", str(preparation["pack_id"])):
            raise ValueError("interview_pack_identity_invalid")
        path = Path(workspace) / "career" / "interviews" / preparation["pack_id"] / "pack.json"
        if not path.resolve().is_relative_to(Path(workspace).resolve()):
            raise ValueError("interview_pack_location_invalid")
        if not path.is_file():
            preparation = {"status": "unavailable", "missing_materials": ["saved_pack"], "warnings": []}
        else:
            pack = json.loads(path.read_text(encoding="utf-8"))
            pack["warnings"] = _warnings(pack)
            preparation = {**preparation, "content": _safe_pack(pack)}
            if preparation.get("source_revision") != source_revision:
                preparation = {**preparation, "status": "source_changed", "warnings":
                               [*preparation.get("warnings", []), "材料或岗位已变更；此包为先前快照，请重新准备。"]}
    return {"id": identity, "job_url": current["job_url"], "title": row[0], "company": row[1],
            "round": current.get("round"), "round_name": current.get("round_name", ""),
            "scheduled_at": scheduled.get("scheduled_at"), "timezone": current.get("timezone", "UTC"),
            "mode": current.get("mode", ""), "source_text": current.get("source_text", current.get("summary", "")),
            "result": current.get("result"), "status": status, "actions": actions, "history": history,
            "preparation": preparation, "revision": _digest([history, actions, source]),
            "source_revision": source_revision, "result_options": RESULTS}


def list_items(conn, workspace):
    return {"items": [detail(conn, workspace, identity) for identity in _groups(conn)],
            "jobs": jobs(conn), "pending_invitations": pending_invitations(conn), "result_options": RESULTS}


def pending_invitations(conn):
    candidates = jobs(conn)
    return [{**event, "revision": _digest([event, candidates])} for event in followup.pending_events(conn)
            if event["event_type"] in followup.INTERVIEW_TYPES]


def resolve_invitation(conn, workspace, event_id, data):
    if set(data) - {"request_key", "expected_revision", "job_url"}:
        raise ValueError("unsupported_input_field")
    route = "interviews/invitations/" + event_id + "/resolve"
    with write_transaction(conn):
        old = previous_request(conn, data.get("request_key"), route, data)
        if old is not None:
            return old["value"]
        invitation = next((event for event in pending_invitations(conn) if event["event_id"] == event_id), None)
        if invitation is None:
            raise ValueError("interview_invitation_changed")
        if data.get("expected_revision") != invitation["revision"]:
            raise ValueError("interview_invitation_revision_changed")
        event = followup.resolve_event(conn, event_id, _text(data, "job_url", 10000))
        result = {"item": detail(conn, workspace, _identity(event))}
        save_request(conn, data["request_key"], route, data, {"status": 200, "value": result})
        return result


def _text(data, name, maximum=1000, optional=False):
    value = data.get(name)
    if optional and value in (None, ""):
        return None
    value = followup._text(value, name)
    if len(value) > maximum:
        raise ValueError(f"{name}_too_long")
    return value


def _schedule(data):
    zone = _text(data, "timezone", 100)
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("interview_timezone_invalid") from exc
    raw = data.get("scheduled_at")
    scheduled = followup.timestamp(raw).isoformat() if raw else None
    if raw and datetime.fromisoformat(raw).utcoffset() != followup.timestamp(raw).astimezone(ZoneInfo(zone)).utcoffset():
        raise ValueError("interview_timezone_offset_mismatch")
    return {"scheduled_at": scheduled, "timezone": zone}


def mutate(conn, workspace, operation, data, identity=None):
    allowed = {"request_key", "expected_revision"}
    fields = {"create": {"job_url", "round", "round_name", "scheduled_at", "timezone", "mode", "source_text"},
              "reschedule": {"scheduled_at", "timezone", "source_text"},
              "cancel": {"source_text"}, "result": {"result", "source_text"},
              "actions": {"summary", "due_at"}, "complete-action": {"action_id"}, "prepare": set()}
    if operation not in fields:
        raise KeyError("endpoint_not_found")
    if set(data) - allowed - fields[operation]:
        raise ValueError("unsupported_input_field")
    route = "interviews/" + (identity + "/" if identity else "") + operation
    with write_transaction(conn):
        old = previous_request(conn, data.get("request_key"), route, data)
        if old is not None:
            return old["value"]
        item = detail(conn, workspace, identity) if identity else None
        if item and data.get("expected_revision") != item["revision"]:
            raise ValueError("interview_revision_changed")
        terminal_operations = {"reschedule", "cancel", "result", "prepare"}
        if item and ((item["status"] in {"cancelled", "completed"} and operation in terminal_operations)
                     or (item["status"] == "cancelled" and operation == "actions")):
            raise ValueError("interview_terminal_state")
        if operation == "create":
            url = _text(data, "job_url", 10000)
            if not followup._exact_job(conn, url):
                raise ValueError("interview_exact_job_required")
            round_number = data.get("round")
            if type(round_number) is not int or not 1 <= round_number <= 100:
                raise ValueError("interview_round_invalid")
            if any(e["job_url"] == url and e.get("round") == round_number for e in followup._events(conn)
                   if e["event_type"].startswith("interview")):
                raise ValueError("interview_round_conflict")
            stage_id = "iv-" + uuid4().hex
            values = {"job_url": url, "round": round_number, "round_name": _text(data, "round_name", optional=True),
                      "mode": _text(data, "mode", optional=True), **_schedule(data)}
            event_type = "interview_invited"
        else:
            anchor = next(event for event in item["history"] if event["event_type"].startswith("interview"))
            stage_id = anchor.get("stage_id")
            values = {"job_url": item["job_url"], "round": item["round"],
                      "application_id": anchor.get("application_id"),
                      "interview_origin_event_id": anchor.get("interview_origin_event_id") or anchor["event_id"]}
            event_type = "manual_note"
        summary = operation
        if operation in {"create", "reschedule", "cancel", "result"}:
            values["source_text"] = _text(data, "source_text", 20000)
            summary = values["source_text"][:1000]
        if operation == "reschedule":
            values.update(_schedule(data))
            if not values["scheduled_at"]:
                raise ValueError("interview_reschedule_time_required")
            event_type = "interview_rescheduled"
        elif operation == "cancel":
            event_type = "interview_cancelled"
        elif operation == "result":
            if data.get("result") not in RESULTS:
                raise ValueError("interview_result_invalid")
            values["result"] = data["result"]
            event_type = "interview_completed"
        elif operation == "actions":
            action = followup.add_action(conn, item["job_url"], _text(data, "summary", 1000), data.get("due_at"))
            values["action_ids"] = [action["action_id"]]
        elif operation == "complete-action":
            aid = data.get("action_id")
            if aid not in {a["action_id"] for a in item["actions"]}:
                raise ValueError("interview_action_not_associated")
            followup.complete_action(conn, aid)
        elif operation == "prepare":
            from applypilot.interview import build_pack, write_pack

            try:
                pack = build_pack(Path(workspace), url=item["job_url"], round_name=item["round_name"])
            except (OSError, ValueError) as exc:
                reason = "source_changed" if "stale" in str(exc) or "cross_job" in str(exc) else "jd_or_resume"
                values["preparation"] = {"status": "missing_materials", "missing_materials": [reason],
                                         "warnings": ["岗位说明或可核验简历缺失，请补充现有岗位材料后重试。"]}
            else:
                pack_id = uuid4().hex
                output = Path(workspace) / "career" / "interviews" / pack_id
                if not output.resolve().is_relative_to(Path(workspace).resolve()):
                    raise ValueError("interview_pack_location_invalid")
                write_pack(pack, output)
                values["preparation"] = {"status": "ready", "pack_id": pack_id, "missing_materials": [],
                                         "warnings": _warnings(pack), "binding_status": pack["resume"]["binding_status"],
                                         "source_revision": item["source_revision"]}
        if item and item["status"] == "pending" and operation == "reschedule" and item["round"] is None:
            values["round"] = None
        message_id = "web-" + uuid4().hex
        followup.import_events(conn, [{"provider": "user", "message_id": message_id,
            "occurred_at": datetime.now(UTC).isoformat(), "event_type": event_type,
            "summary": summary, "evidence_ref": "user supplied local interview record",
            "stage_id": stage_id, **values}])
        if operation == "create":
            identity = _identity(next(event for event in followup._events(conn) if event["message_id"] == message_id))
        result = {"item": detail(conn, workspace, identity)}
        save_request(conn, data["request_key"], route, data, {"status": 200, "value": result})
        return result
