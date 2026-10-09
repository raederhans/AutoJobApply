"""Controlled, repeatable ingestion of reviewed history and provider coverage."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from applypilot import followup
from applypilot.application_progress import match_job, record_sync, register_application, sync_jobs

LEGACY_TYPES = {
    "receipt": "submission_observed", "sent": "outgoing_message", "contact": "recruiter_feedback",
    "rejection": "rejected", "assessment": "assessment", "pending_identity": "identity_pending",
    "interview": "interview", "withdrawn": "withdrawn", "offer": "offer",
}


def import_bundle(conn: sqlite3.Connection, payload: list | dict) -> dict:
    """Apply a reviewed bundle atomically. A success checkpoint cannot outlive its events."""
    if isinstance(payload, list):
        return followup.import_events(conn, payload)
    if not isinstance(payload, dict) or set(payload) - {"applications", "events", "sync_runs"}:
        raise ValueError("Expected an event array or applications/events/sync_runs bundle")
    for key in ("applications", "events", "sync_runs"):
        if not isinstance(payload.get(key, []), list):
            raise TypeError(f"{key} must be an array")
    with followup._atomic(conn):
        followup._ensure_schema(conn)
        for application in payload.get("applications", []):
            register_application(conn, **application)
        events = []
        for raw in payload.get("events", []):
            event = dict(raw)
            key = event.pop("application_key", None)
            if key:
                found = conn.execute("SELECT application_id FROM followup_applications WHERE source_key=?", (key,)).fetchone()
                if not found:
                    raise ValueError("Unknown application_key")
                event["application_id"] = found[0]
            events.append(event)
        result = followup.import_events(conn, events)
        registry = sync_jobs(conn)
        for run in payload.get("sync_runs", []):
            record_sync(conn, run, result)
    return {**result, "registry": registry, "sync_runs": len(payload.get("sync_runs", []))}


def preview(conn: sqlite3.Connection | None, operation, *args) -> dict:
    """Run the actual importer on a disposable SQLite snapshot, never the source."""
    clone = sqlite3.connect(":memory:")
    try:
        if conn is not None:
            conn.backup(clone)
        result = operation(clone, *args)
        return {**result, "dry_run": True}
    finally:
        clone.close()


def import_legacy(conn: sqlite3.Connection, directory: Path) -> dict:
    """Import the frozen structured baseline, not its one-off generation scripts.

    Original files remain unchanged. Markdown requires reviewed conversion into a
    bundle; prose and fuzzy company names are never automatically treated as facts.
    """
    path = directory / "events.jsonl"
    raw_events = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    events, duplicates = [], 0
    with followup._atomic(conn):
        followup._ensure_schema(conn)
        sync_jobs(conn)
        for raw in raw_events:
            kind = raw.get("kind")
            if kind not in LEGACY_TYPES:
                raise ValueError(f"Unknown legacy kind: {kind}")
            candidate = raw.get("matched_job_url")
            if raw.get("needs_review") or raw.get("match_state") == "ambiguous":
                candidate = None
            matched = match_job(conn, candidate)
            provider, message_id = raw["provider"].casefold(), raw["message_id"]
            event = {
                "provider": provider, "message_id": message_id, "event_type": LEGACY_TYPES[kind],
                "occurred_at": raw["occurred_at"], "job_url": matched,
                "company": raw.get("company") or None, "title": raw.get("title") or None,
                "evidence_ref": f"{provider}:{message_id}",
                "summary": f"Reviewed historical observation: {LEGACY_TYPES[kind]}",
            }
            if kind == "receipt" and raw.get("submission_confirmed") is True:
                event["event_type"] = "submission_confirmed"
            prior = conn.execute("SELECT payload,job_url FROM followup_events "
                                 "WHERE provider=? AND message_id=? AND fact_key=''", (provider, message_id)).fetchone()
            if prior:
                stored = json.loads(prior[0])
                if (stored["event_type"] != event["event_type"] or
                    followup.timestamp(stored["occurred_at"]) != followup.timestamp(event["occurred_at"]) or
                    (matched and prior[1] and matched != prior[1])):
                    raise ValueError("Legacy observation conflicts with an existing source identity")
                duplicates += 1
            else:
                events.append(event)
        result = followup.import_events(conn, events)
        result["duplicates"] += duplicates
        state_path = directory / "state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            for provider, details in state.get("providers", {}).items():
                complete = details.get("pagination_complete") is True
                success = details.get("status") == "success" and complete
                record_sync(conn, {
                    "provider": provider, "run_id": "legacy:" + state["baseline_run_id"],
                    "status": "success" if success else "partial", "complete": complete,
                    "attempted_at": state["updated_at"], "cutoff": details.get("last_successful_cutoff"),
                }, result)
    return {**result, "source_events": len(raw_events)}
