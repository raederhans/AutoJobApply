"""Career queue projections; schema creation belongs to database migrations."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from applypilot.contracts.runtime import TaskResult, contract_json
from applypilot.storage.database_migrations import require_supported_database_schema
from applypilot.storage.transactions import execute_transactional_script


def ensure_schema(conn: sqlite3.Connection) -> None:
    execute_transactional_script(conn, """
    CREATE TABLE IF NOT EXISTS career_workbench_jobs (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL UNIQUE REFERENCES agent_tasks(task_id),
        request_key TEXT NOT NULL UNIQUE,
        parent_job_id TEXT,
        operation TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        input_lease_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS career_workbench_materials (
        material_id TEXT PRIMARY KEY,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS career_workbench_requests (
        request_key TEXT PRIMARY KEY,
        operation TEXT NOT NULL,
        input_json TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    ensure_publication_schema(conn)


def ensure_publication_schema(conn: sqlite3.Connection) -> None:
    """Migration 7: atomically bind committed business results to their job."""
    execute_transactional_script(conn, """
    CREATE TABLE IF NOT EXISTS career_workbench_publications (
        job_id TEXT PRIMARY KEY,
        result_json TEXT NOT NULL
    );
    """)


def record_publication(conn: sqlite3.Connection, job_id: str, result_refs: dict) -> None:
    if not conn.in_transaction:
        raise RuntimeError("publication result requires its business write transaction")
    if not isinstance(result_refs, dict):
        raise TypeError("publication result_refs must be an object")
    if any(not isinstance(k, str) or not k.endswith("_id")
           or not isinstance(v, str) or not v.strip() or len(v) > 500
           or any(c in v for c in ("/", "\\", "\x00")) for k, v in result_refs.items()):
        raise ValueError("publication result_refs must contain only bounded IDs, never paths")
    encoded = json.dumps(result_refs, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    old = conn.execute("SELECT result_json FROM career_workbench_publications WHERE job_id=?", (job_id,)).fetchone()
    if old is not None:
        if json.loads(old[0]) != result_refs:
            raise ValueError("publication result conflict")
        return
    conn.execute("INSERT INTO career_workbench_publications VALUES(?,?)", (job_id, encoded))


def published_result(conn: sqlite3.Connection, job_id: str) -> dict | None:
    row = conn.execute("SELECT result_json FROM career_workbench_publications WHERE job_id=?", (job_id,)).fetchone()
    return json.loads(row[0]) if row is not None else None


def supported(conn: sqlite3.Connection, *, writing: bool = False) -> bool:
    from applypilot.database import DATABASE_SCHEMA_VERSION

    require_supported_database_schema(conn, latest_version=DATABASE_SCHEMA_VERSION)
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                          "AND name='career_workbench_jobs'").fetchone() is not None
    if writing and not exists:
        raise RuntimeError("career workbench schema is missing; initialize the database first")
    return exists


_SELECT = """SELECT w.*, t.status, t.cancel_requested, t.result_json, t.result_ref,
    t.attempt_count, t.lease_epoch, t.updated_at, t.progress_json
    FROM career_workbench_jobs w JOIN agent_tasks t ON t.task_id=w.job_id"""


def _decode(cursor, row) -> dict | None:
    if row is None:
        return None
    value = dict(zip((d[0] for d in cursor.description), row))
    value["payload"] = json.loads(value.pop("payload_json"))
    value["input_lease"] = json.loads(value.pop("input_lease_json"))
    value["result"] = json.loads(value.pop("result_json") or "null")
    value["progress"] = json.loads(value.pop("progress_json") or "null")
    value["cancel_requested"] = bool(value["cancel_requested"])
    value["result_refs"] = (value["result"] or {}).get("output", {}).get("result_refs", {})
    value["error"] = (value["result"] or {}).get("failure_category")
    if value["status"] == "blocked" and value["error"] == "interrupted":
        value["status"] = "interrupted"
    return value


def get(conn: sqlite3.Connection, job_id: str) -> dict | None:
    if not supported(conn):
        return None
    cursor = conn.execute(_SELECT + " WHERE w.job_id=?", (job_id,))
    return _decode(cursor, cursor.fetchone())


def by_request(conn: sqlite3.Connection, request_key: str) -> dict | None:
    if not supported(conn):
        return None
    cursor = conn.execute(_SELECT + " WHERE w.request_key=?", (request_key,))
    return _decode(cursor, cursor.fetchone())


def list_jobs(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict]:
    if not supported(conn):
        return []
    cursor = conn.execute(_SELECT + " ORDER BY w.sequence DESC LIMIT ?", (limit,))
    return [_decode(cursor, row) for row in cursor.fetchall()]


def recover_interrupted(conn: sqlite3.Connection) -> None:
    """Startup under exclusive worker ownership never retries an old request."""
    now = datetime.now(UTC).isoformat()
    rows = conn.execute("SELECT t.task_id,p.result_json FROM agent_tasks t "
                        "JOIN career_workbench_jobs w ON w.job_id=t.task_id "
                        "LEFT JOIN career_workbench_publications p ON p.job_id=t.task_id "
                        "WHERE t.status IN ('pending','running')").fetchall()
    for row in rows:
        refs = json.loads(row[1]) if row[1] is not None else None
        result = (TaskResult(task_id=row[0], status="completed", output={"result_refs": refs}) if refs is not None
                  else TaskResult(task_id=row[0], status="blocked", failure_category="interrupted"))
        conn.execute("UPDATE agent_tasks SET status=?,owner_id=NULL,worker_id=NULL,"
                     "lease_expires_at=NULL,result_json=?,result_ref=?,updated_at=?,"
                     "cancel_requested=CASE WHEN ?='completed' THEN 0 ELSE cancel_requested END WHERE task_id=?",
                     (result.status, json.dumps(contract_json(result)), next(iter(refs.values()), None) if refs else None,
                      now, result.status, row[0]))


def check_input_lease(conn: sqlite3.Connection, lease: dict) -> None:
    if lease.get("target_id"):
        row = conn.execute("SELECT revision,input_fingerprint FROM career_targets WHERE target_id=?",
                           (lease["target_id"],)).fetchone()
        if row is None or tuple(row) != (lease.get("target_revision"), lease.get("input_fingerprint")):
            raise ValueError("input_changed: career target lease is stale")
    if lease.get("route_id"):
        row = conn.execute("SELECT COALESCE(MAX(revision),0) FROM career_task_events WHERE route_id=?",
                           (lease["route_id"],)).fetchone()
        if row[0] != lease.get("route_revision"):
            raise ValueError("input_changed: career route lease is stale")
