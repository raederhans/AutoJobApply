"""Source evidence about posting availability, independent of application state."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from applypilot.storage.job_identity import _normalized_identity_text, _usable_requisition_id, canonicalize_job_url
from applypilot.storage.transactions import execute_transactional_script, write_transaction

_INVENTORY_PROVIDERS = {"greenhouse", "lever", "ashby", "smartrecruiters", "workable"}
_UNBOUND_CONFLICTING_IDENTITY = "__unbound_conflicting_inventory_identity__"


def _time(value: str | datetime | None = None) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value) if value else datetime.now(UTC)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def ensure_posting_lifecycle_schema(conn: sqlite3.Connection) -> None:
    """Add tables without modifying jobs or backfilling legacy verification."""
    with write_transaction(conn, prefix="posting_schema"):
        execute_transactional_script(
            conn,
            """
            CREATE TABLE IF NOT EXISTS posting_source_states (
                job_url TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                inventory_scope TEXT NOT NULL,
                posting_identity TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                last_verified_at TEXT NOT NULL,
                first_missing_at TEXT,
                missing_count INTEGER NOT NULL DEFAULT 0,
                last_evidence_at TEXT NOT NULL,
                last_run_id TEXT NOT NULL,
                PRIMARY KEY (job_url, source_id, source_type, inventory_scope)
            );
            CREATE TABLE IF NOT EXISTS posting_lifecycle_runs (
                run_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                inventory_scope TEXT NOT NULL,
                evidence_at TEXT NOT NULL,
                summary_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_posting_runs_scope
                ON posting_lifecycle_runs(source_id, source_type, inventory_scope, evidence_at);
        """,
        )
        if "discovered_at" in {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_posting_repost_recent ON jobs(discovered_at DESC)")


def _scope(metadata: dict) -> str:
    scope = metadata.get("inventory_scope")
    if not isinstance(scope, dict) or not scope.get("provider") or not scope.get("tenant"):
        return ""
    return json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _resolve_job(conn: sqlite3.Connection, raw: dict, source_id: str) -> sqlite3.Row | None:
    """Resolve raw pre-filter inventory to an existing persisted identity only."""
    urls = list(
        dict.fromkeys(
            str(raw.get(key) or "")
            for key in ("url", "application_url", "canonical_url", "canonical_job_url")
            if raw.get(key)
        )
    )
    for url in urls:
        canonical = canonicalize_job_url(url)
        row = conn.execute(
            "SELECT * FROM jobs WHERE url=? OR application_url=? OR canonical_job_url=? LIMIT 1",
            (url, url, canonical),
        ).fetchone()
        if row is not None:
            return row
    if raw.get("platform_job_id"):
        row = conn.execute("SELECT * FROM jobs WHERE platform_job_id=? LIMIT 1", (raw["platform_job_id"],)).fetchone()
        if row is not None:
            return row
    external_id = str(raw.get("external_id") or raw.get("job_id") or raw.get("requisition_id") or "")
    if external_id:
        return conn.execute(
            "SELECT j.* FROM jobs j JOIN radar_job_sources l ON l.job_url=j.url "
            "JOIN radar_source_observations o ON o.observation_key=l.observation_key "
            "WHERE o.source_id=? AND o.external_id=? LIMIT 1",
            (source_id, external_id),
        ).fetchone()
    return None


def _identity(raw: dict) -> str:
    return str(
        raw.get("platform_job_id")
        or _usable_requisition_id(raw.get("external_id"))
        or _usable_requisition_id(raw.get("job_id"))
        or _usable_requisition_id(raw.get("requisition_id"))
        or canonicalize_job_url(str(raw.get("url") or ""))
    )


def reconcile_posting_lifecycle(
    conn: sqlite3.Connection,
    run_id: str,
    inventory_jobs: list[dict],
    *,
    missing_runs: int = 2,
    missing_hours: float = 24,
) -> dict:
    """Reconcile a finished run atomically; replay and late completion are harmless.

    Only the adapters' scoped, validated official inventory contract can provide
    absence evidence. Verified appearances can be used from partial RSS/JSONLD
    results, but failed runs and unverified records never prove availability.
    """
    if missing_runs < 2 or missing_hours < 24:
        raise ValueError("closure requires at least two missing runs across 24 hours")
    ensure_posting_lifecycle_schema(conn)
    with write_transaction(conn, prefix="posting_reconcile"):
        prior = conn.execute("SELECT summary_json FROM posting_lifecycle_runs WHERE run_id=?", (run_id,)).fetchone()
        if prior:
            return {**json.loads(prior["summary_json"]), "skipped": True, "reason": "run_already_reconciled"}
        run = conn.execute("SELECT * FROM radar_fetch_runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None or not run["finished_at"]:
            return {"skipped": True, "reason": "run_not_finished"}
        try:
            metadata = json.loads(run["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        scope = _scope(metadata)
        if not scope:
            return {"skipped": True, "reason": "inventory_scope_unverified"}
        source_id = run["source_id"]
        source_type = str(run["source_type"] or "")
        evidence_at = _time(run["started_at"]).isoformat()
        summary = {"seen": 0, "missing": 0, "closed": 0, "reopened": 0, "skipped": False, "reason": "reconciled"}
        latest = conn.execute(
            "SELECT MAX(evidence_at) FROM posting_lifecycle_runs WHERE source_id=? AND source_type=? AND inventory_scope=?",
            (source_id, source_type, scope),
        ).fetchone()[0]
        if latest and evidence_at <= latest:
            summary.update(skipped=True, reason="older_source_run")
        else:
            provider = str(metadata.get("provider") or metadata["inventory_scope"].get("provider") or "").casefold()
            valid_inventory = isinstance(inventory_jobs, list) and all(
                isinstance(raw, dict)
                and raw.get("verification_status") in {"verified_official", "official_open"}
                and bool(raw.get("title"))
                and bool(raw.get("url"))
                for raw in inventory_jobs
            )
            complete = bool(
                run["status"] == "complete"
                and run["pagination_complete"] == 1
                and metadata.get("inventory_contract") == "official_api_v1"
                and metadata.get("inventory_complete") is True
                and metadata.get("coverage_mode") == "full"
                and provider in _INVENTORY_PROVIDERS
                and metadata["inventory_scope"].get("provider") == provider
                and source_type == "official_careers"
                and valid_inventory
                and run["normalized_count"] == len(inventory_jobs)
            )
            seen_urls: set[str] = set()
            inventory_by_url: dict[str, dict] = {}
            conflicted_urls: set[str] = set()
            if run["status"] in {"complete", "partial"}:
                for raw in inventory_jobs if isinstance(inventory_jobs, list) else []:
                    if (
                        not isinstance(raw, dict)
                        or raw.get("verification_status") not in {"verified_official", "official_open"}
                        or not raw.get("url")
                        or not raw.get("title")
                    ):
                        continue
                    job = _resolve_job(conn, raw, source_id)
                    if job is None:
                        continue
                    url = job["url"]
                    if url in inventory_by_url and _identity(inventory_by_url[url]) != _identity(raw):
                        conflicted_urls.add(url)
                    inventory_by_url[url] = raw
                for url, raw in inventory_by_url.items():
                    seen_urls.add(url)
                    old = conn.execute(
                        "SELECT * FROM posting_source_states WHERE job_url=? AND source_id=? AND source_type=? AND inventory_scope=?",
                        (url, source_id, source_type, scope),
                    ).fetchone()
                    identity = _identity(raw)
                    status, reason = "open", "verified_presence"
                    bound_identity = old["posting_identity"] if old else identity
                    if url in conflicted_urls:
                        status, reason = "needs_reverification", "conflicting_inventory_identity"
                        if old is None:
                            # There is no prior identity to preserve. Keep this
                            # ambiguous observation from binding whichever raw
                            # record happened to be processed last.
                            bound_identity = _UNBOUND_CONFLICTING_IDENTITY
                    elif old and (
                        old["posting_identity"] == _UNBOUND_CONFLICTING_IDENTITY
                        or old["posting_identity"] != identity
                    ):
                        status, reason = "needs_reverification", "posting_identity_changed"
                    elif old and old["status"] == "closed":
                        status, reason = "reopened", "same_identity_returned"
                        summary["reopened"] += 1
                    conn.execute(
                        "INSERT INTO posting_source_states VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?) "
                        "ON CONFLICT(job_url,source_id,source_type,inventory_scope) DO UPDATE SET "
                        "posting_identity=excluded.posting_identity,status=excluded.status,reason=excluded.reason,"
                        "last_verified_at=excluded.last_verified_at,first_missing_at=NULL,missing_count=0,"
                        "last_evidence_at=excluded.last_evidence_at,last_run_id=excluded.last_run_id",
                        (
                            url,
                            source_id,
                            source_type,
                            scope,
                            bound_identity,
                            status,
                            reason,
                            evidence_at,
                            evidence_at,
                            run_id,
                        ),
                    )
                    summary["seen"] += 1
            if complete and not conflicted_urls:
                states = conn.execute(
                    "SELECT * FROM posting_source_states WHERE source_id=? AND source_type=? AND inventory_scope=?",
                    (source_id, source_type, scope),
                ).fetchall()
                for state in states:
                    if state["job_url"] in seen_urls:
                        continue
                    count = state["missing_count"] + 1
                    first_missing = state["first_missing_at"] or evidence_at
                    closed = count >= missing_runs and _time(evidence_at) - _time(first_missing) >= timedelta(
                        hours=missing_hours
                    )
                    status = "closed" if closed else "needs_reverification"
                    reason = "complete_inventory_absence" if closed else "awaiting_repeated_inventory_absence"
                    conn.execute(
                        "UPDATE posting_source_states SET status=?,reason=?,first_missing_at=?,missing_count=?,"
                        "last_evidence_at=?,last_run_id=? WHERE job_url=? AND source_id=? AND source_type=? AND inventory_scope=?",
                        (
                            status,
                            reason,
                            first_missing,
                            count,
                            evidence_at,
                            run_id,
                            state["job_url"],
                            source_id,
                            source_type,
                            scope,
                        ),
                    )
                    summary["missing"] += 1
                    summary["closed"] += int(closed)
            else:
                summary["reason"] = "presence_only_inventory_incomplete"
        conn.execute(
            "INSERT INTO posting_lifecycle_runs VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, source_id, source_type, scope, evidence_at, json.dumps(summary)),
        )
        return summary


def get_posting_lifecycle(
    conn: sqlite3.Connection,
    job_url: str,
    *,
    now: str | datetime | None = None,
    ttl_hours: float = 72,
) -> dict:
    """Read aggregate evidence without writes; untracked legacy rows stay compatible."""
    if (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='posting_source_states'").fetchone()
        is None
    ):
        rows = []
    else:
        rows = conn.execute(
            "SELECT * FROM posting_source_states WHERE job_url=? ORDER BY source_id,source_type,inventory_scope",
            (job_url,),
        ).fetchall()
    if not rows:
        return {"posting_status": "legacy_untracked", "posting_reason": "no_lifecycle_evidence", "posting_sources": []}
    current = _time(now)
    sources = []
    for row in rows:
        source = dict(row)
        if source["status"] in {"open", "reopened"} and current - _time(source["last_verified_at"]) > timedelta(
            hours=ttl_hours
        ):
            source.update(status="needs_reverification", reason="verified_presence_expired")
        sources.append(source)
    fresh = [source for source in sources if source["status"] in {"open", "reopened"}]
    if fresh:
        newest = max(fresh, key=lambda source: source["last_verified_at"])
        status, reason = newest["status"], newest["reason"]
        if any(source["status"] == "open" for source in fresh):
            status, reason = "open", "verified_presence"
    elif all(source["status"] == "closed" for source in sources):
        status, reason = "closed", "all_sources_closed"
    else:
        status = "needs_reverification"
        reason = (
            "verified_presence_expired"
            if all(source["reason"] == "verified_presence_expired" for source in sources)
            else "source_evidence_requires_reverification"
        )
    return {"posting_status": status, "posting_reason": reason, "posting_sources": sources}


def posting_allows_application(conn: sqlite3.Connection, job_url: str) -> bool:
    return get_posting_lifecycle(conn, job_url)["posting_status"] in {"open", "reopened", "legacy_untracked"}


def get_possible_repost_hints(
    conn: sqlite3.Connection, job_url: str, *, now: str | datetime | None = None
) -> list[dict]:
    """Advisory similarities only; never merge identities or change application state."""
    job = conn.execute("SELECT * FROM jobs WHERE url=?", (job_url,)).fetchone()
    if job is None or not job["company_name"] or not job["title"]:
        return []
    current = _time(now)
    hints = []
    # Bound both the time window and work per report row. Similarity remains
    # advisory, so a limit may omit hints but cannot affect admission.
    for other in conn.execute(
        "SELECT url,company_name,title,platform_job_id,discovered_at FROM jobs "
        "WHERE url<>? AND lower(trim(company_name))=lower(trim(?)) "
        "AND discovered_at>=? AND discovered_at<=? "
        "ORDER BY discovered_at DESC LIMIT 200",
        # Day bounds accept the existing ISO date/time formats; exact 30-day
        # checks below reject boundary records outside the actual window.
        (
            job_url,
            job["company_name"],
            (current - timedelta(days=30)).date().isoformat(),
            (current + timedelta(days=1)).date().isoformat(),
        ),
    ):
        if _normalized_identity_text(other["company_name"]) != _normalized_identity_text(
            job["company_name"]
        ) or _normalized_identity_text(other["title"]) != _normalized_identity_text(job["title"]):
            continue
        if other["platform_job_id"] and other["platform_job_id"] == job["platform_job_id"]:
            continue
        try:
            age = current - _time(other["discovered_at"]) if other["discovered_at"] else None
        except (TypeError, ValueError):
            continue
        if age is not None and timedelta(0) <= age <= timedelta(days=30):
            hints.append({"url": other["url"], "reason": "same_company_title_different_posting", "advisory": True})
    return hints
