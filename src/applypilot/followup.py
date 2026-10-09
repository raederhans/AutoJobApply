"""Local recruiting observations and next actions, separate from submission truth.

Imports here deliberately do not bind configuration or database workspace paths.
Callers provide connections; read projections never create tables.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from applypilot.storage.followup_schema import ensure_event_schema, ensure_schema

EVENT_TYPES = frozenset({
    "recruiter_feedback", "rejected", "assessment", "interview_invited",
    "interview", "interview_completed", "offer", "withdrawn", "manual_note",
    "offer_accepted", "offer_declined", "reopened", "retracted",
    "interview_rescheduled", "interview_cancelled",
    "submission_observed", "outgoing_message", "identity_pending",
    "submission_confirmed", "submission_user_confirmed",
})
INTERVIEW_TYPES = frozenset({"interview", "interview_invited", "interview_rescheduled"})


def timestamp(value: str) -> datetime:
    """Require an explicit offset and compare all dates in UTC."""
    if not isinstance(value, str):
        raise TypeError("Date must be an ISO 8601 string with timezone")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Date must be an ISO 8601 string with timezone") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Date requires a timezone")
    return result.astimezone(UTC)


def _text(value, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ValueError(f"{field} contains unsupported control characters")
    return value.strip()


def _exists(conn: sqlite3.Connection | None, table: str) -> bool:
    return conn is not None and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone() is not None


@contextmanager
def _atomic(conn: sqlite3.Connection):
    # Savepoints also preserve a caller's surrounding transaction.
    conn.execute("SAVEPOINT followup_write")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO followup_write")
        conn.execute("RELEASE followup_write")
        raise
    else:
        conn.execute("RELEASE followup_write")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    ensure_schema(conn)
    ensure_event_schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS followup_actions (
        action_id TEXT PRIMARY KEY, job_url TEXT NOT NULL, summary TEXT NOT NULL,
        due_at TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT
    )""")


def _exact_job(conn: sqlite3.Connection, url: str | None) -> bool:
    if not url or not _exists(conn, "jobs"):
        return False
    return len(conn.execute("SELECT url FROM jobs WHERE url=?", (url,)).fetchall()) == 1


def _normalise_event(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise TypeError("Each event must be an object")
    allowed = {"provider", "message_id", "occurred_at", "event_type", "job_url",
               "company", "title", "evidence_ref", "summary", "scheduled_at", "round",
               "application_id", "fact_key", "stage_id", "supersedes_event_id",
               "time_basis", "submitted_at", "date_precision"}
    if set(raw) - allowed:
        raise ValueError(f"Unknown event fields: {', '.join(sorted(set(raw) - allowed))}")
    event = {field: _text(raw.get(field), field) for field in
             ("provider", "message_id", "event_type", "summary")}
    event["provider"] = event["provider"].casefold()
    if event["event_type"] not in EVENT_TYPES:
        raise ValueError("Unsupported event_type")
    event["occurred_at"] = timestamp(raw.get("occurred_at")).isoformat()
    event["time_basis"] = raw.get("time_basis", "occurred")
    if event["time_basis"] not in {"occurred", "observed"}:
        raise ValueError("time_basis must be occurred or observed")
    event["date_precision"] = raw.get("date_precision", "instant")
    if event["date_precision"] not in {"instant", "day"}:
        raise ValueError("date_precision must be instant or day")
    event["submitted_at"] = raw.get("submitted_at")
    if event["submitted_at"] is not None:
        from applypilot.application_progress import application_date

        if event["event_type"] not in {"submission_confirmed", "submission_user_confirmed"}:
            raise ValueError("submitted_at is only allowed on a submission confirmation")
        if application_date(event["submitted_at"])[0] is None:
            raise ValueError("submitted_at must be an ISO date or timezone-aware timestamp")
    for field in ("job_url", "company", "title", "evidence_ref"):
        event[field] = _text(raw.get(field), field, optional=True)
    if event["event_type"] != "manual_note" and not event["evidence_ref"]:
        raise ValueError("Recruiting observations require evidence_ref; use manual_note for notes")
    event["scheduled_at"] = None
    if raw.get("scheduled_at") is not None:
        if event["event_type"] not in INTERVIEW_TYPES:
            raise ValueError("scheduled_at is only supported for interviews")
        event["scheduled_at"] = timestamp(raw["scheduled_at"]).isoformat()
    event["round"] = raw.get("round")
    if event["round"] is not None and (
        type(event["round"]) is not int or event["round"] < 1
    ):
        raise ValueError("round must be a positive integer")
    for field in ("application_id", "stage_id", "supersedes_event_id"):
        event[field] = _text(raw.get(field), field, optional=True)
    event["fact_key"] = raw.get("fact_key", "")
    if not isinstance(event["fact_key"], str) or len(event["fact_key"]) > 200:
        raise ValueError("fact_key must be a string of at most 200 characters")
    if event["event_type"] == "retracted" and not event["supersedes_event_id"]:
        raise ValueError("Retraction requires supersedes_event_id")
    if event["event_type"] in {"interview_rescheduled", "interview_cancelled"} and not (
        event["stage_id"] or event["round"]
    ):
        raise ValueError("Rescheduling/cancellation requires stage_id or round")
    return event


def import_events(conn: sqlite3.Connection, events: list[dict]) -> dict:
    """Atomically import reviewed JSON. Stable keys never overwrite observations.

    A company/title candidate is never sufficient to bind a local job. Evidence
    references are provenance supplied by the operator, not verified mail access.
    """
    if not isinstance(events, list):
        raise TypeError("Expected an event array")
    normalised = [_normalise_event(raw) for raw in events]
    result = {"imported": 0, "duplicates": 0, "pending": 0}
    with _atomic(conn):
        _ensure_schema(conn)
        for event in normalised:
            payload = json.dumps(event, ensure_ascii=False, sort_keys=True)
            prior = conn.execute(
                "SELECT payload FROM followup_events WHERE provider=? AND message_id=? AND fact_key=?",
                (event["provider"], event["message_id"], event["fact_key"]),
            ).fetchone()
            if prior:
                if _normalise_event(json.loads(prior[0])) != event:
                    raise ValueError("provider/message_id already exists with different content")
                result["duplicates"] += 1
                continue
            matched = event["job_url"] if _exact_job(conn, event["job_url"]) else None
            bound_application = event["application_id"]
            if event["application_id"]:
                application = conn.execute("SELECT job_url FROM followup_applications WHERE application_id=?",
                                           (event["application_id"],)).fetchone()
                if application is None:
                    raise ValueError("Unknown application_id")
                if event["job_url"] and event["job_url"] != application[0]:
                    raise ValueError("application_id and job_url disagree")
                matched = application[0]
            if event["supersedes_event_id"]:
                prior_event = conn.execute("SELECT job_url,application_id FROM followup_events WHERE event_id=?",
                                           (event["supersedes_event_id"],)).fetchone()
                if not prior_event:
                    raise ValueError("Unknown supersedes_event_id")
                superseded = [json.loads(row[0]).get("supersedes_event_id")
                              for row in conn.execute("SELECT payload FROM followup_events")]
                if event["supersedes_event_id"] in superseded:
                    raise ValueError("Event already corrected; correct its replacement instead")
                if not event["job_url"] and not bound_application:
                    matched, bound_application = prior_event
            conn.execute("""INSERT INTO followup_events
                (event_id,provider,message_id,occurred_at,event_type,payload,job_url,resolved_at,created_at,
                 fact_key,application_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                str(uuid4()), event["provider"], event["message_id"], event["occurred_at"],
                event["event_type"], payload, matched, None, datetime.now(UTC).isoformat(),
                event["fact_key"], bound_application,
            ))
            result["imported"] += 1
            result["pending"] += int(matched is None and not bound_application)
        from applypilot.application_progress import sync_jobs

        sync_jobs(conn)
    return result


def _events(conn: sqlite3.Connection | None, *, job_url: str | None = None,
            pending: bool = False) -> list[dict]:
    if not _exists(conn, "followup_events"):
        return []
    schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='followup_events' AND type='table'").fetchone()[0]
    has_application = "application_id" in schema
    where, params = "", ()
    if pending:
        where = " WHERE job_url IS NULL" + (" AND application_id IS NULL" if has_application else "")
    elif job_url is not None:
        where, params = " WHERE job_url=?", (job_url,)
    application_column = "application_id" if has_application else "NULL"
    rows = conn.execute(
        f"SELECT event_id, payload, job_url, resolved_at, created_at, {application_column} FROM followup_events"
        + where + " ORDER BY occurred_at, event_id", params,
    ).fetchall()
    result = []
    for row in rows:
        payload = json.loads(row[1])
        result.append(dict(payload, event_id=row[0], source_job_url=payload["job_url"],
                           job_url=row[2], resolved_at=row[3], created_at=row[4],
                           application_id=row[5], match_status="matched" if row[2] or row[5] else "pending"))
    return result


def pending_events(conn: sqlite3.Connection | None) -> list[dict]:
    from applypilot.application_progress import effective_events

    return [event for event in effective_events(_events(conn)) if event["match_status"] == "pending"]


def resolve_event(conn: sqlite3.Connection, event_id: str, job_url: str | None = None,
                  *, application_id: str | None = None) -> dict:
    """Bind one pending observation after an explicit operator choice."""
    with _atomic(conn):
        if bool(job_url) == bool(application_id):
            raise ValueError("Choose exactly one job_url or application_id")
        if job_url and not _exact_job(conn, job_url):
            raise ValueError("Resolve requires an exact existing jobs.url")
        if not _exists(conn, "followup_events"):
            raise ValueError("Unknown event_id")
        _ensure_schema(conn)
        if application_id:
            application = conn.execute("SELECT job_url FROM followup_applications WHERE application_id=?", (application_id,)).fetchone()
            if application is None:
                raise ValueError("Unknown application_id")
            job_url = application[0]
        row = conn.execute("SELECT job_url,application_id FROM followup_events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown event_id")
        if row[0] and row[0] != job_url:
            raise ValueError("An already matched event cannot be reassigned")
        if row[1] and application_id and row[1] != application_id:
            raise ValueError("An already matched event cannot be reassigned")
        if row[1] and not application_id and row[0] != job_url:
            raise ValueError("An already matched event cannot be reassigned")
        if row[0] is None and row[1] is None:
            conn.execute("UPDATE followup_events SET job_url=?,application_id=?,resolved_at=? WHERE event_id=?",
                         (job_url, application_id, datetime.now(UTC).isoformat(), event_id))
            from applypilot.application_progress import sync_jobs

            _ensure_schema(conn)
            sync_jobs(conn)
    return next(event for event in _events(conn) if event["event_id"] == event_id)


def actions(conn: sqlite3.Connection | None, *, job_url: str | None = None) -> list[dict]:
    if not _exists(conn, "followup_actions"):
        return []
    query = "SELECT action_id, job_url, summary, due_at, created_at, completed_at FROM followup_actions"
    params = ()
    if job_url is not None:
        query, params = query + " WHERE job_url=?", (job_url,)
    columns = ("action_id", "job_url", "summary", "due_at", "created_at", "completed_at")
    return [dict(zip(columns, row, strict=True)) for row in conn.execute(query + " ORDER BY due_at, action_id", params)]


def add_action(conn: sqlite3.Connection, job_url: str, summary: str, due_at: str) -> dict:
    summary = _text(summary, "summary")
    due_at = timestamp(due_at).isoformat()
    if not _exact_job(conn, job_url):
        raise ValueError("Action requires an exact existing jobs.url")
    action_id = str(uuid4())
    with _atomic(conn):
        _ensure_schema(conn)
        conn.execute("INSERT INTO followup_actions VALUES (?, ?, ?, ?, ?, NULL)",
                     (action_id, job_url, summary, due_at, datetime.now(UTC).isoformat()))
    return next(action for action in actions(conn, job_url=job_url) if action["action_id"] == action_id)


def complete_action(conn: sqlite3.Connection, action_id: str) -> dict:
    if not _exists(conn, "followup_actions"):
        raise ValueError("Unknown action_id")
    with _atomic(conn):
        result = conn.execute(
            "UPDATE followup_actions SET completed_at=COALESCE(completed_at, ?) WHERE action_id=?",
            (datetime.now(UTC).isoformat(), action_id),
        )
        if result.rowcount != 1:
            raise ValueError("Unknown action_id")
    return next(action for action in actions(conn) if action["action_id"] == action_id)


def due_actions(conn: sqlite3.Connection | None, *, at: str | None = None) -> list[dict]:
    now = timestamp(at) if at else datetime.now(UTC)
    return [action for action in actions(conn) if not action["completed_at"] and timestamp(action["due_at"]) <= now]


def timeline(conn: sqlite3.Connection | None, job_url: str) -> dict:
    return {"job_url": job_url, "events": _events(conn, job_url=job_url), "actions": actions(conn, job_url=job_url)}


def scheduled_interviews(events: list[dict]) -> list[dict]:
    """A reschedule replaces the same round; cancellation/completion removes it."""
    from applypilot.application_progress import effective_events

    latest = {}
    for event in effective_events(events):
        if not event["event_type"].startswith("interview"):
            continue
        identity = (event.get("application_id") or event["job_url"],
                    event.get("stage_id") or event.get("round") or event["event_id"])
        latest[identity] = event
    return [event for event in latest.values() if event.get("scheduled_at")
            and event["event_type"] not in {"interview_completed", "interview_cancelled"}]


def followup_summary(conn: sqlite3.Connection | None, *, now: datetime | None = None, limit: int = 20) -> dict:
    """Dashboard projection; absent schema or connection yields empty data."""
    now = now or datetime.now(UTC)
    now = timestamp(now.isoformat())
    if limit < 1:
        raise ValueError("limit must be positive")
    from applypilot.application_progress import effective_events

    all_events, all_actions = effective_events(_events(conn)), actions(conn)
    due = due_actions(conn, at=now.isoformat())
    upcoming = [event for event in scheduled_interviews(all_events) if event["job_url"]
                and timestamp(event["scheduled_at"]) >= now]
    upcoming.sort(key=lambda event: (event["scheduled_at"], event["event_id"]))
    return {"pending_count": sum(event["match_status"] == "pending" for event in all_events),
            "open_action_count": sum(action["completed_at"] is None for action in all_actions),
            "due_action_count": len(due), "due_actions": due[:limit],
            "upcoming_interviews": upcoming[:limit], "recent_events": list(reversed(all_events))[:limit]}


def _ics_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace(
        "\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def _ics_time(value: str) -> str:
    return timestamp(value).strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> str:
    # RFC 5545 limits physical lines to 75 octets, not 75 Unicode characters.
    parts, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            parts.append(current)
            current = " "
        current += char
    return "\r\n".join(parts + [current])


def export_ics(conn: sqlite3.Connection | None, *, job_url: str | None = None) -> str:
    """Export matched dated interviews and open actions as UTC VEVENTs."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ApplyPilot//Local Followup//EN", "CALSCALE:GREGORIAN"]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    items = [(event["event_id"], event["scheduled_at"], event["summary"], event["job_url"])
             for event in scheduled_interviews(_events(conn, job_url=job_url)) if event["job_url"]]
    items += [(action["action_id"], action["due_at"], action["summary"], action["job_url"])
              for action in actions(conn, job_url=job_url) if not action["completed_at"]]
    for identity, when, summary, url in sorted(items, key=lambda item: (item[1], item[0])):
        lines.extend(["BEGIN:VEVENT", f"UID:{identity}@applypilot.local", f"DTSTAMP:{stamp}",
                      f"DTSTART:{_ics_time(when)}", f"SUMMARY:{_ics_text(summary)}",
                      f"DESCRIPTION:{_ics_text(url)}", "END:VEVENT"])
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
