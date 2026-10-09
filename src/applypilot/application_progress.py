"""Recruiting identity, history projection and statistics, independent of execution."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

from applypilot.followup import _atomic, _events, timestamp
from applypilot.storage.followup_schema import ensure_schema, exists
from applypilot.storage.job_identity import canonicalize_job_url

BASES = {"verified", "user_confirmed", "reported", "unverified", "uncertain"}
CONFIRMED = {"verified", "user_confirmed"}
TERMINAL = {"rejected", "withdrawn", "accepted", "declined"}
SGT = timezone(timedelta(hours=8))
EVENT_STAGE = {
    "recruiter_feedback": "contacted", "assessment": "assessment", "offer": "offer",
    "offer_accepted": "accepted", "offer_declined": "declined", "rejected": "rejected",
    "withdrawn": "withdrawn", "reopened": "applied",
    "submission_confirmed": "applied", "submission_user_confirmed": "applied",
}


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cursor = conn.execute(sql, params)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


def application_date(value: str | None) -> tuple[str | None, str]:
    if not value:
        return None, "unknown"
    if len(value) == 10:
        try:
            return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=SGT).astimezone(UTC).isoformat(), "day"
        except ValueError:
            return None, "unknown"
    try:
        return timestamp(value).isoformat(), "instant"
    except (ValueError, TypeError):
        return None, "unknown"


def register_application(conn: sqlite3.Connection, *, source_key: str, company: str, title: str,
                         job_url: str | None = None, submitted_at: str | None = None,
                         submission_basis: str = "unverified") -> dict:
    if submission_basis not in BASES:
        raise ValueError("Unsupported submission_basis")
    if not all(isinstance(v, str) and v.strip() for v in (source_key, company, title)):
        raise ValueError("source_key, company and title are required")
    when, precision = application_date(submitted_at)
    if submitted_at and when is None:
        raise ValueError("submitted_at must be an ISO date or timezone-aware timestamp")
    record = {"source_key": source_key, "company": company, "title": title, "job_url": job_url,
              "submitted_at": when, "date_precision": precision, "submission_basis": submission_basis}
    with _atomic(conn):
        ensure_schema(conn)
        existing = _rows(conn, "SELECT * FROM followup_applications WHERE source_key=?", (source_key,))
        if existing:
            if any(existing[0][key] != value for key, value in record.items()):
                raise ValueError("Application source_key already exists with different content")
            return existing[0]
        if job_url and application_for_url(conn, job_url):
            raise ValueError("A recruiting identity already exists for this job_url; use its application_id")
        record.update(application_id=str(uuid4()), created_at=datetime.now(UTC).isoformat())
        conn.execute("""INSERT INTO followup_applications
            (application_id,source_key,job_url,company,title,submitted_at,date_precision,submission_basis,created_at)
            VALUES (:application_id,:source_key,:job_url,:company,:title,:submitted_at,:date_precision,
                    :submission_basis,:created_at)""", record)
    return record


def application_for_url(conn: sqlite3.Connection, url: str | None) -> str | None:
    if not url or not exists(conn, "followup_applications"):
        return None
    matches = conn.execute("SELECT application_id FROM followup_applications WHERE job_url=?", (url,)).fetchall()
    return str(matches[0][0]) if len(matches) == 1 else None


def sync_jobs(conn: sqlite3.Connection) -> dict:
    """Copy existing submission facts into recruiting identities; never update jobs."""
    result = {"created": 0, "updated": 0}
    if not exists(conn, "jobs"):
        return result
    with _atomic(conn):
        ensure_schema(conn)
        observed_urls = ({row[0] for row in conn.execute("SELECT DISTINCT job_url FROM followup_events")}
                         if exists(conn, "followup_events") else set())
        receipts = set()
        if exists(conn, "application_receipts"):
            receipts = {row[0] for row in conn.execute("SELECT DISTINCT job_url FROM application_receipts")}
        for job in _rows(conn, "SELECT * FROM jobs"):
            url, status = job["url"], job.get("apply_status")
            if not (status or job.get("applied_at") or url in observed_urls or url in receipts):
                continue
            confidence = job.get("verification_confidence")
            basis = "unverified"
            if status == "submission_uncertain":
                basis = "uncertain"
            elif status in {"applied", "already_applied"}:
                if url in receipts or confidence in {
                    "durable_receipt_reconciled", "browser_observation", "manual_visual_confirmation",
                    "visible_confirmation", "direct_email_sent_verified", "gmail_sent_verified",
                }:
                    basis = "verified"
                elif confidence == "platform_export" or status == "already_applied":
                    basis = "reported"
            # Platform exports may use observation time as applied_at. Do not fabricate a cohort date.
            when, precision = application_date(job.get("applied_at") if basis == "verified" else None)
            key = "job:" + url
            old = _rows(conn, "SELECT * FROM followup_applications WHERE source_key=?", (key,))
            if not old:
                existing_id = application_for_url(conn, url)
                if existing_id:
                    old = _rows(conn, "SELECT * FROM followup_applications WHERE application_id=?", (existing_id,))
            if not old:
                record = register_application(
                    conn, source_key=key, company=job.get("company_name") or "Unknown employer",
                    title=job.get("title") or "Unknown role", job_url=url,
                    submitted_at=job.get("applied_at") if when else None, submission_basis=basis,
                )
                result["created"] += 1
            else:
                record = old[0]
                rank = {"unverified": 0, "uncertain": 1, "reported": 2, "user_confirmed": 3, "verified": 4}
                new_basis = basis if rank[basis] > rank[record["submission_basis"]] else record["submission_basis"]
                new_when = record["submitted_at"] or when
                new_precision = record["date_precision"] if record["submitted_at"] else precision
                if (new_basis, new_when) != (record["submission_basis"], record["submitted_at"]):
                    conn.execute("UPDATE followup_applications SET submission_basis=?,submitted_at=?,date_precision=? "
                                 "WHERE application_id=?", (new_basis, new_when, new_precision, record["application_id"]))
                    result["updated"] += 1
            conn.execute("INSERT OR IGNORE INTO followup_application_refs VALUES (?, 'job_url', ?)",
                         (record["application_id"], url))
        # Only bind unique identities, including observations imported before the registry existed.
        for url in observed_urls:
            identity = application_for_url(conn, url)
            if identity:
                conn.execute("UPDATE followup_events SET application_id=? WHERE job_url=? AND application_id IS NULL",
                             (identity, url))
    return result


def match_job(conn: sqlite3.Connection, url: str | None) -> str | None:
    """Match exact or normalized URLs uniquely, never by approximate company/title."""
    if not url or not exists(conn, "jobs"):
        return None
    normalized = canonicalize_job_url(url)
    matches = [row[0] for row in conn.execute("SELECT url FROM jobs")
               if row[0] == url or canonicalize_job_url(row[0]) == normalized]
    return matches[0] if len(matches) == 1 else None


def record_sync(conn: sqlite3.Connection, run: dict, counts: dict) -> dict:
    allowed = {"provider", "run_id", "status", "attempted_at", "cutoff", "complete"}
    if set(run) - allowed or not run.get("provider") or not run.get("run_id"):
        raise ValueError("Sync requires a provider and run_id; unknown fields are rejected")
    if run.get("status") not in {"success", "partial", "failed"} or type(run.get("complete")) is not bool:
        raise ValueError("Sync status/complete is invalid")
    attempted = timestamp(run["attempted_at"]).isoformat()
    cutoff = timestamp(run["cutoff"]).isoformat() if run.get("cutoff") else None
    if cutoff and timestamp(cutoff) > timestamp(attempted):
        raise ValueError("cutoff cannot be after attempted_at")
    if run["status"] == "success" and (not run["complete"] or not cutoff):
        raise ValueError("Successful sync requires complete coverage and cutoff")
    if run["status"] != "success" and run["complete"]:
        raise ValueError("Incomplete or failed sync cannot claim complete coverage")
    normalized = {**run, "provider": run["provider"].casefold(), "attempted_at": attempted, "cutoff": cutoff}
    values = (normalized["provider"], normalized["run_id"], run["status"], attempted, cutoff,
              int(run["complete"]), counts.get("imported", 0), counts.get("duplicates", 0), counts.get("pending", 0))
    with _atomic(conn):
        ensure_schema(conn)
        old = conn.execute("SELECT * FROM followup_sync_runs WHERE provider=? AND run_id=?", values[:2]).fetchone()
        if old:
            if tuple(old)[:6] != values[:6]:
                raise ValueError("Sync run_id already exists with different coverage")
        else:
            conn.execute("INSERT INTO followup_sync_runs VALUES (?,?,?,?,?,?,?,?,?)", values)
    return normalized


def source_health(conn: sqlite3.Connection | None) -> list[dict]:
    if not exists(conn, "followup_sync_runs"):
        return []
    runs = _rows(conn, "SELECT * FROM followup_sync_runs ORDER BY attempted_at,run_id")
    sources = {}
    for run in runs:
        prior = sources.get(run["provider"], {})
        cutoff = prior.get("last_successful_cutoff")
        if run["status"] == "success" and run["complete"] and run["cutoff"]:
            cutoff = max(cutoff or run["cutoff"], run["cutoff"])
        sources[run["provider"]] = {
            "provider": run["provider"], "status": run["status"], "attempted_at": run["attempted_at"],
            "last_successful_cutoff": cutoff, "complete": bool(run["complete"]),
        }
    return list(sources.values())


def effective_events(events: list[dict]) -> list[dict]:
    superseded = {event.get("supersedes_event_id") for event in events if event.get("supersedes_event_id")}
    return [event for event in events if event["event_id"] not in superseded and event["event_type"] != "retracted"]


def _stage(event: dict) -> str | None:
    if event["event_type"] in {"interview", "interview_invited", "interview_completed", "interview_rescheduled"}:
        return f"interview_{event['round']}" if event.get("round") else "interview_unknown"
    return EVENT_STAGE.get(event["event_type"])


def _order(stage: str) -> tuple[int, int]:
    if stage.startswith("interview_"):
        return (3, int(stage.split("_")[1]) if stage != "interview_unknown" else 0)
    return ({"submission_unverified": -1, "applied": 0, "contacted": 1, "assessment": 2, "offer": 4, "accepted": 5}.get(stage, 6), 0)


def _projection(record: dict, events: list[dict]) -> dict:
    current = "applied" if record["submission_basis"] in CONFIRMED else "submission_unverified"
    entered = record["submitted_at"]
    reached = {"applied": entered} if record["submission_basis"] in CONFIRMED else {}
    history = [{"at": entered, "stage": current}]
    interviews = {}
    for event in effective_events(events):
        when, stage = event["occurred_at"], _stage(event)
        if stage:
            reached.setdefault(stage, when)
            if stage.startswith("interview_"):
                reached.setdefault("interview", when)
            if event["event_type"] == "interview_completed":
                reached.setdefault("interview_completed", when)
            # Late receipts/contact do not undo a terminal decision. Reopening must be explicit.
            advance = current not in TERMINAL and current != "conflict"
            if current in TERMINAL and stage in TERMINAL and current != stage:
                stage, advance = "conflict", True
            if event["event_type"] == "reopened":
                advance = True
            elif advance and _order(stage) < _order(current) and stage not in TERMINAL:
                advance = False
            if advance and stage != current:
                current, entered = stage, when
                history.append({"at": when, "stage": stage})
        identity = event.get("stage_id") or (f"round:{event['round']}" if event.get("round") else None)
        if identity and event["event_type"].startswith("interview"):
            interviews[identity] = event
    return {"current": current, "entered_at": entered, "reached": reached, "history": history,
            "scheduled": [{"at": e["scheduled_at"], "round": e.get("round")}
                          for e in interviews.values() if e.get("scheduled_at")
                          and e["event_type"] not in {"interview_completed", "interview_cancelled"}]}


def collect_progress(conn: sqlite3.Connection | None) -> dict:
    """Privacy-bounded complete dataset. Reads never create schema or import history."""
    now = datetime.now(UTC).isoformat()
    if not exists(conn, "followup_applications"):
        return {"state": "not_initialized", "generated_at": now, "applications": [], "pending_count": 0,
                "sources": [], "timezone": "Asia/Singapore"}
    records = _rows(conn, "SELECT * FROM followup_applications ORDER BY created_at,application_id")
    by_url = defaultdict(list)
    for record in records:
        by_url[record["job_url"]].append(record["application_id"])
    grouped = defaultdict(list)
    pending = 0
    ids = {record["application_id"] for record in records}
    for event in effective_events(_events(conn)):
        identity = event.get("application_id")
        if not identity and event.get("job_url") and len(by_url[event["job_url"]]) == 1:
            identity = by_url[event["job_url"]][0]
        if identity not in ids:
            pending += 1
        else:
            grouped[identity].append(event)
    applications = []
    for record in records:
        events = grouped[record["application_id"]]
        for event in events:
            if event["event_type"] in {"submission_confirmed", "submission_user_confirmed"}:
                basis = "verified" if event["event_type"] == "submission_confirmed" else "user_confirmed"
                record["submission_basis"] = "verified" if record["submission_basis"] == "verified" else basis
                if event.get("submitted_at"):
                    record["submitted_at"], record["date_precision"] = application_date(event["submitted_at"])
        projection = _projection(record, events)
        applications.append({
            "id": record["application_id"], "company": record["company"], "title": record["title"],
            "url": record["job_url"], "submitted_at": record["submitted_at"],
            "date_precision": record["date_precision"], "basis": record["submission_basis"],
            "first_evidence_at": min([e["occurred_at"] for e in events], default=None),
            **projection,
            # Do not serialize source message IDs, summaries, evidence refs or mailbox URLs.
            "timeline": [{"at": e["occurred_at"], "type": e["event_type"], "round": e.get("round"),
                          "scheduled_at": e.get("scheduled_at"), "source": e["provider"],
                          "time_basis": e.get("time_basis", "occurred"),
                          "date_precision": e.get("date_precision", "instant"),
                          "corrected": bool(e.get("supersedes_event_id"))}
                         for e in effective_events(events)],
            "correction_count": sum(bool(e.get("supersedes_event_id")) for e in events),
        })
    return {"state": "ready" if records else "not_imported", "generated_at": now,
            "timezone": "Asia/Singapore", "applications": applications, "pending_count": pending,
            "sources": source_health(conn)}


def select_applications(data: dict, *, as_of: str | None = None, since: str | None = None,
                        until: str | None = None, scope: str = "confirmed", query: str = "") -> list[dict]:
    if scope not in {"confirmed", "verified", "all"}:
        raise ValueError("scope must be confirmed, verified or all")
    cutoff = timestamp(as_of) if as_of else timestamp(data["generated_at"])
    for bound in (since, until):
        if bound:
            datetime.strptime(bound, "%Y-%m-%d").replace(tzinfo=SGT)
    if since and until and since > until:
        raise ValueError("since cannot be after until")
    selected = []
    for item in data["applications"]:
        if scope == "confirmed" and item["basis"] not in CONFIRMED:
            continue
        if scope == "verified" and item["basis"] != "verified":
            continue
        if query.casefold() not in (item["company"] + " " + item["title"]).casefold():
            continue
        submitted = timestamp(item["submitted_at"]) if item["submitted_at"] else None
        day = submitted.astimezone(SGT).date().isoformat() if submitted else None
        if submitted and submitted > cutoff:
            continue
        if not submitted and item.get("first_evidence_at") and timestamp(item["first_evidence_at"]) > cutoff:
            continue
        if (since or until) and (not day or (since and day < since) or (until and day > until)):
            continue
        history = [event for event in item["history"] if not event["at"] or timestamp(event["at"]) <= cutoff]
        latest = history[-1] if history else {"stage": "applied", "at": None}
        reached = {key: value for key, value in item["reached"].items() if value and timestamp(value) <= cutoff}
        if item["basis"] in CONFIRMED:
            reached["applied"] = item["submitted_at"]
        wait = max(0, (cutoff - timestamp(latest["at"])).total_seconds() / 86400) if latest["at"] else None
        selected.append({**item, "current": latest["stage"], "entered_at": latest["at"],
                         "history": history, "reached": reached, "waiting_days": wait,
                         "timeline": [e for e in item["timeline"] if timestamp(e["at"]) <= cutoff]})
    return selected


def summarize(data: dict, **filters) -> dict:
    applications = select_applications(data, **filters)
    reached_ids = defaultdict(list)
    states = Counter()
    for item in applications:
        states[item["current"]] += 1
        for stage in item["reached"]:
            reached_ids[stage].append(item["id"])
    stages = ["applied", "interview_1", "interview_2", "interview_3", "offer", "accepted"]
    funnel = []
    for index, stage in enumerate(stages):
        reached = set(reached_ids[stage])
        prior = set(reached_ids[stages[index - 1]]) if index else set()
        converted = [a["id"] for a in applications if index and a["id"] in reached & prior
                     and (not a["reached"][stages[index - 1]] or
                          a["reached"][stage] >= a["reached"][stages[index - 1]])]
        funnel.append({"stage": stage, "count": len(reached), "ids": sorted(reached),
                       "overall_rate": len(reached) / len(applications) if applications else None,
                       "previous_count": len(prior) if index else None,
                       "converted_count": len(converted) if index else None,
                       "conversion_rate": len(converted) / len(prior) if prior else None,
                       "missing_previous": len(reached - prior) if index else 0})
    return {"state": data["state"], "total": len(applications), "current": dict(states), "funnel": funnel,
            "unknown_date_count": sum(not a["submitted_at"] for a in applications),
            "unknown_round_count": len(reached_ids["interview_unknown"]),
            "pending_count": data["pending_count"], "sources": data["sources"],
            "generated_at": data["generated_at"], "applications": applications}
