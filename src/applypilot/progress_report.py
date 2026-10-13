"""Local, read-only job progress reports with a deliberately small export surface."""

from __future__ import annotations

import csv
import hashlib
import html
import io
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from applypilot.application_progress import collect_progress
from applypilot.apply.failure_taxonomy import classify_failure

FIELDS = (
    "record_id", "title", "company", "location", "application_status", "recruiting_stage",
    "submission_basis", "submitted_at", "discovered_at", "updated_at", "updated_at_source",
    "todo_reason", "todo_due_at", "open_action_count",
)
DATE_FIELDS = ("updated_at", "submitted_at", "discovered_at")
APPLICATION_STATUSES = {
    "not_started", "in_progress", "applied", "already_applied", "failed", "previewed",
    "submission_uncertain", "expired", "skipped", "blocked", "needs_human", "submission_unverified", "unknown",
    "applying", "manual", "captcha", "duplicate",
}
RECRUITING_STAGES = {
    "applied", "submission_unverified", "contacted", "assessment", "offer", "accepted", "declined",
    "rejected", "withdrawn", "conflict", "interview_unknown",
}
_JOB_TIMES = (
    "discovered_at", "last_seen_at", "detail_scraped_at", "scored_at", "tailored_at", "cover_letter_at",
    "cover_letter_approved_at", "last_attempted_at", "applied_at", "application_recorded_at",
    "submission_observed_at", "unanswered_questions_updated_at", "application_readiness_reviewed_at",
)
_JOB_FIELDS = (
    "url", "title", "company_name", "location", "apply_status", "apply_error", "apply_retry_blocked",
    "apply_retry_reason", "application_readiness_status", *_JOB_TIMES,
)
_URL = re.compile(r"(?:[a-z][a-z0-9+.-]*://|www\.)\S+", re.IGNORECASE)
_SECRET = re.compile(
    r"\b(?:bearer\s+\S+|(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"token|authorization|cookie|secret)\s*[:=]\s*\S+)", re.IGNORECASE,
)


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = _URL.sub("[URL omitted]", value)
    if _SECRET.search(value):
        return "[credential omitted]"
    return " ".join(value.split())[:240]


def _instant(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value)
        # Legacy jobs timestamps may be naive. Treat those as UTC, never local time.
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC).isoformat()
    except (ValueError, OverflowError):
        return ""


def _rows(conn: sqlite3.Connection, table: str, fields: Sequence[str]) -> list[dict]:
    """Select only named scalar fields; tolerate absent legacy tables/columns."""
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    selected = [field for field in fields if field in columns]
    if not selected:
        return []
    cursor = conn.execute(f'SELECT {", ".join(selected)} FROM "{table}"')
    return [dict(zip(selected, row, strict=True)) for row in cursor]


def _latest(candidates: list[tuple[str, object]]) -> tuple[str, str]:
    valid = [(instant, source) for source, value in candidates if (instant := _instant(value))]
    return max(valid, default=("", ""), key=lambda item: datetime.fromisoformat(item[0]))


def _stage(value: object) -> str:
    if isinstance(value, str) and (value in RECRUITING_STAGES or re.fullmatch(r"interview_[1-9][0-9]*", value)):
        return value
    return "unknown"


def _todo(job: dict) -> str:
    if job.get("apply_status") == "submission_uncertain":
        return "submission_confirmation_missing"
    reason = job.get("apply_retry_reason") or job.get("apply_error")
    if reason:
        # Both columns can contain full logs or answers. Export fixed taxonomy only.
        return classify_failure(str(reason)).category
    if job.get("apply_retry_blocked"):
        return "retry_blocked_requires_review"
    if job.get("application_readiness_status") not in {None, "", "confirmed", "ready", "approved"}:
        return "application_readiness_requires_review"
    return ""


def collect_report(
    conn: sqlite3.Connection, *, since: str | None = None, until: str | None = None,
    statuses: Sequence[str] = (), date_field: str = "updated_at",
) -> dict:
    """Project jobs plus existing recruiting identities without synchronizing either."""
    if date_field not in DATE_FIELDS:
        raise ValueError("date_field must be updated_at, submitted_at or discovered_at")
    for bound in (since, until):
        if bound is not None and (date.fromisoformat(bound).isoformat() != bound):
            raise ValueError("Dates must use YYYY-MM-DD")
    if since and until and since > until:
        raise ValueError("since cannot be after until")
    status_filter = set(statuses)
    if any(s not in APPLICATION_STATUSES and _stage(s) == "unknown" for s in status_filter):
        raise ValueError("Unsupported status; use a known application status or recruiting stage")
    jobs = _rows(conn, "jobs", _JOB_FIELDS)
    progress = collect_progress(conn)
    applications = progress["applications"]
    by_url = defaultdict(list)
    for application in applications:
        if application.get("url"):
            by_url[application["url"]].append(application)
    created = {row["application_id"]: row.get("created_at") for row in
               _rows(conn, "followup_applications", ("application_id", "created_at"))}
    actions = defaultdict(list)
    for action in _rows(conn, "followup_actions", ("job_url", "due_at", "created_at", "completed_at")):
        if action.get("job_url"):
            actions[action["job_url"]].append(action)
    attempts = defaultdict(list)
    for attempt in _rows(conn, "application_attempts", ("job_url", "updated_at")):
        if attempt.get("job_url"):
            attempts[attempt["job_url"]].append(("application_attempts.updated_at", attempt.get("updated_at")))
    records = []
    matched = set()
    # Match only one exact identity. Never guess by employer/title or normalize token URLs.
    pairs = []
    for job in jobs:
        identities = by_url.get(job.get("url"), [])
        application = identities[0] if len(identities) == 1 else {}
        if application:
            matched.add(application["id"])
        pairs.append((job, application))
    pairs.extend(({}, application) for application in applications if application["id"] not in matched)
    for job, application in pairs:
        url = job.get("url") or application.get("url")
        candidates = [(f"jobs.{field}", job.get(field)) for field in _JOB_TIMES]
        candidates += attempts.get(url, [])
        candidates += [("followup_applications.created_at", created.get(application.get("id"))),
                       ("followup_applications.submitted_at", application.get("submitted_at"))]
        candidates += [("followup_events.occurred_at", event.get("at")) for event in application.get("timeline", [])]
        local_actions = actions.get(url, [])
        for action in local_actions:
            candidates += [("followup_actions.created_at", action.get("created_at")),
                           ("followup_actions.completed_at", action.get("completed_at"))]
        updated, updated_source = _latest(candidates)
        open_actions = [a for a in local_actions if not a.get("completed_at")]
        due_dates = [_instant(a.get("due_at")) for a in open_actions]
        due = min((d for d in due_dates if d), default="")
        status = job.get("apply_status")
        if job and status is None:
            status = "unknown" if job.get("applied_at") else "not_started"
        elif not job:
            status = "applied" if application.get("basis") in {"verified", "user_confirmed"} else "submission_unverified"
        status = status if status in APPLICATION_STATUSES else "unknown"
        reason = _todo(job)
        if open_actions:
            reason = "; ".join(filter(None, (reason, "followup_action_pending")))
        identity = str(application.get("id") or url or "")
        record = {
            "record_id": "application-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
            "title": _text(job.get("title") or application.get("title")),
            "company": _text(job.get("company_name") or application.get("company")),
            "location": _text(job.get("location")), "application_status": status,
            "recruiting_stage": _stage(application.get("current")) if application else "",
            "submission_basis": application.get("basis") if application.get("basis") in
                {"verified", "user_confirmed", "reported", "unverified", "uncertain"} else "",
            # Registry unknown dates are intentional (platform exports can record observation time).
            "submitted_at": _instant(application.get("submitted_at") if application else job.get("applied_at")),
            "discovered_at": _instant(job.get("discovered_at")), "updated_at": updated,
            "updated_at_source": updated_source, "todo_reason": reason,
            "todo_due_at": due, "open_action_count": len(open_actions),
        }
        day = record[date_field][:10]
        if (since or until) and (not day or (since and day < since) or (until and day > until)):
            continue
        if status_filter and status not in status_filter and record["recruiting_stage"] not in status_filter:
            continue
        records.append(record)
    records.sort(key=lambda row: (row["updated_at"], row["record_id"]), reverse=True)
    return {
        "generated_at": datetime.now(UTC).isoformat(), "timezone": "UTC", "rows": records,
        "filters": {"since": since, "until": until, "statuses": sorted(status_filter), "date_field": date_field},
        "application_status_counts": dict(sorted(Counter(r["application_status"] for r in records).items())),
        "recruiting_stage_counts": dict(sorted(Counter(r["recruiting_stage"] for r in records if r["recruiting_stage"]).items())),
        "todo_count": sum(bool(r["todo_reason"]) for r in records),
    }


def render_csv(report: dict) -> str:
    """Produce spreadsheet-safe CSV. The writer adds a UTF-8 BOM for Excel."""
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    for row in report["rows"]:
        safe = {key: ("'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
                      else value) for key, value in row.items()}
        writer.writerow(safe)
    return stream.getvalue()


def _markdown(value: object) -> str:
    text = html.escape(str(value), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|])", r"\\\1", text)


def render_markdown(report: dict) -> str:
    filters = report["filters"]
    lines = ["# Job application progress", "", f"Generated at (UTC): {report['generated_at']}", "",
             f"Date filter: {filters['date_field']}, {filters['since'] or 'any'} through {filters['until'] or 'any'} (inclusive UTC days).",
             f"Status filter: {', '.join(filters['statuses']) or 'all'}.", "",
             f"Records: {len(report['rows'])}. Records with pending reasons/actions: {report['todo_count']}.", "",
             "## Application status", ""]
    lines += [f"- {_markdown(status)}: {count}" for status, count in report["application_status_counts"].items()] or ["- No records."]
    if report["recruiting_stage_counts"]:
        lines += ["", "## Recruiting stage", ""]
        lines += [f"- {_markdown(stage)}: {count}" for stage, count in report["recruiting_stage_counts"].items()]
    columns = ("title", "company", "application_status", "recruiting_stage", "updated_at", "todo_reason", "todo_due_at")
    lines += ["", "## Records", "", "| Role | Company | Application status | Recruiting stage | Updated at (UTC) | Todo reason | Due at (UTC) |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    lines += ["| " + " | ".join(_markdown(row[key]) for key in columns) + " |" for row in report["rows"]]
    lines += ["", "Updated at is derived from the latest existing job, attempt, recruiting event or action timestamp; CSV names its source. Missing dates stay blank.",
              "Todo reasons are fixed failure categories or pending-action labels, not raw errors, questions or answers.",
              "URLs, application materials, credentials and action summaries are omitted. This report does not confirm submission or perform applications.", ""]
    return "\n".join(lines)


def export_progress(db_path: Path | str, output_dir: Path | str, **filters: object) -> dict:
    """Read an existing database snapshot and create two new local files, never overwrite."""
    database = Path(db_path).resolve()
    if not database.is_file():
        raise ValueError("Existing applypilot.db is required; export does not initialize a workspace")
    directory = Path(output_dir).resolve()
    paths = {"csv": directory / "job-progress.csv", "markdown": directory / "job-progress.md"}
    if any(path.exists() for path in paths.values()):
        raise ValueError("Report files already exist; choose a new output directory")
    conn = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=10)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        report = collect_report(conn, **filters)
    finally:
        conn.close()
    payloads = {"csv": render_csv(report), "markdown": render_markdown(report)}
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for kind, path in paths.items():
            with path.open("x", encoding="utf-8-sig" if kind == "csv" else "utf-8", newline="") as stream:
                written.append(path)
                stream.write(payloads[kind])
    except OSError:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return {"count": len(report["rows"]), "todo_count": report["todo_count"], "timezone": "UTC",
            "filters": report["filters"], "files": {kind: str(path) for kind, path in paths.items()}}
