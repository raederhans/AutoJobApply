"""Career-only persistence. Readers are pure SELECT; migrations own schema creation."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from applypilot.contracts.career import InputChangedError, content_id, json_text, required_text
from applypilot.storage.database_migrations import require_supported_database_schema
from applypilot.storage.transactions import execute_transactional_script, write_transaction


def ensure_schema(connection: sqlite3.Connection) -> None:
    """The fourth database migration; never called by a read projection."""
    execute_transactional_script(connection, """
    CREATE TABLE IF NOT EXISTS career_companies (
        company_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
        aliases_json TEXT NOT NULL, identity_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS career_source_snapshots (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, source_id TEXT NOT NULL,
        company_id TEXT, payload_json TEXT NOT NULL, raw_bytes BLOB NOT NULL,
        body_text TEXT, observed_at TEXT, source_url TEXT, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_career_snapshots_source
        ON career_source_snapshots(kind, source_id, sequence);
    CREATE TABLE IF NOT EXISTS career_catalogs (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        catalog_id TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,
        raw_bytes BLOB NOT NULL, source_snapshot_ids_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS career_targets (
        target_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision > 0),
        input_fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS career_diagnoses (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        diagnosis_id TEXT NOT NULL UNIQUE, target_id TEXT NOT NULL,
        input_revision INTEGER NOT NULL, input_fingerprint TEXT NOT NULL,
        input_digest TEXT, run_id TEXT UNIQUE, payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_career_diagnoses_target
        ON career_diagnoses(target_id, sequence);
    CREATE TABLE IF NOT EXISTS career_model_attempts (
        attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_id TEXT NOT NULL,
        attempt_number INTEGER NOT NULL DEFAULT 1, target_id TEXT,
        input_revision INTEGER, input_fingerprint TEXT, input_digest TEXT,
        state TEXT NOT NULL, request_json TEXT NOT NULL DEFAULT '{}',
        response_json TEXT, response_snapshot_id TEXT, provider_request_id TEXT,
        provider TEXT, model TEXT, budget_reserved REAL, usage_json TEXT,
        usage_known INTEGER NOT NULL DEFAULT 0, supersedes_attempt_id TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(run_id, step_id, attempt_number)
    );
    """)


def _require_write_schema(connection: sqlite3.Connection) -> None:
    # Import lazily to avoid a database -> migration -> storage import cycle.
    from applypilot.database import DATABASE_SCHEMA_VERSION

    require_supported_database_schema(connection, latest_version=DATABASE_SCHEMA_VERSION)
    if not _exists(connection, "career_targets"):
        raise RuntimeError("career schema is missing; initialize the database before writing")


def _exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _rows(connection: sqlite3.Connection, sql: str, values: tuple = ()) -> list[dict]:
    cursor = connection.execute(sql, values)
    keys = [column[0] for column in cursor.description]
    return [dict(zip(keys, row)) for row in cursor.fetchall()]


def _snapshot(row: dict) -> dict:
    row["payload"] = json.loads(row.pop("payload_json"))
    return row


def _put_snapshot(
    connection: sqlite3.Connection, *, kind: str, source_id: str, payload: dict,
    raw_bytes: bytes, observed_at: str | None = None, source_url: str | None = None,
    company_id: str | None = None, body_text: str | None = None,
) -> tuple[str, bool]:
    identity = {"kind": kind, "source_id": source_id, "payload": payload,
                "observed_at": observed_at, "source_url": source_url,
                "company_id": company_id, "body_text": body_text}
    snapshot_id = content_id("snapshot", identity, raw_bytes)
    cursor = connection.execute("""INSERT OR IGNORE INTO career_source_snapshots
        (snapshot_id,kind,source_id,company_id,payload_json,raw_bytes,body_text,
         observed_at,source_url,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (snapshot_id, kind, source_id, company_id, json_text(payload), raw_bytes,
         body_text, observed_at, source_url, _now()))
    return snapshot_id, cursor.rowcount == 1


def put_snapshot(
    connection: sqlite3.Connection, *, kind: str, source_id: str, payload: dict,
    raw_bytes: bytes, observed_at: str | None = None, source_url: str | None = None,
) -> str:
    required_text(kind, "kind")
    required_text(source_id, "source_id")
    if not isinstance(payload, dict) or not isinstance(raw_bytes, bytes):
        raise TypeError("snapshot requires a dict payload and bytes raw_bytes")
    json_text(payload)
    _require_write_schema(connection)
    with write_transaction(connection):
        return _put_snapshot(connection, kind=kind, source_id=source_id, payload=payload,
                             raw_bytes=raw_bytes, observed_at=observed_at,
                             source_url=source_url)[0]


def get_snapshot(connection: sqlite3.Connection, snapshot_id: str) -> dict | None:
    if not _exists(connection, "career_source_snapshots"):
        return None
    rows = _rows(connection, "SELECT * FROM career_source_snapshots WHERE snapshot_id=?", (snapshot_id,))
    return _snapshot(rows[0]) if rows else None


def list_companies(connection: sqlite3.Connection) -> list[dict]:
    if not _exists(connection, "career_companies"):
        return []
    rows = _rows(connection, "SELECT * FROM career_companies ORDER BY company_id")
    for row in rows:
        row["aliases"] = json.loads(row.pop("aliases_json"))
        row["identity"] = json.loads(row.pop("identity_json"))
    return rows


def get_catalog_id(connection: sqlite3.Connection) -> str | None:
    if not _exists(connection, "career_catalogs"):
        return None
    row = connection.execute("SELECT catalog_id FROM career_catalogs ORDER BY sequence DESC LIMIT 1").fetchone()
    return row[0] if row else None


def load_catalog(connection: sqlite3.Connection, catalog_id: str | None = None) -> dict:
    if not _exists(connection, "career_catalogs"):
        return {}
    catalog_id = catalog_id or get_catalog_id(connection)
    row = connection.execute("SELECT payload_json FROM career_catalogs WHERE catalog_id=?", (catalog_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def _job(row: dict) -> dict:
    payload = json.loads(row["payload_json"])
    return {**payload, "company_id": row["company_id"],
            "source_snapshot_id": row["snapshot_id"], "body_text": row["body_text"]}


def list_jobs(connection: sqlite3.Connection, *, company_id: str | None = None,
              role_family: str | None = None) -> list[dict]:
    if not _exists(connection, "career_source_snapshots"):
        return []
    rows = _rows(connection, """SELECT s.* FROM career_source_snapshots s
        WHERE s.kind='research_jd' AND s.sequence=(
            SELECT MAX(v.sequence) FROM career_source_snapshots v
            WHERE v.kind=s.kind AND v.source_id=s.source_id) ORDER BY s.source_id""")
    jobs = [_job(row) for row in rows]
    return [job for job in jobs if (company_id is None or job["company_id"] == company_id)
            and (role_family is None or job.get("role_family") == role_family)]


def get_job(connection: sqlite3.Connection, job_id: str, *, snapshot_id: str | None = None) -> dict | None:
    if not _exists(connection, "career_source_snapshots"):
        return None
    sql = "SELECT * FROM career_source_snapshots WHERE kind='research_jd' AND source_id=?"
    values: tuple = (job_id,)
    if snapshot_id is not None:
        sql += " AND snapshot_id=?"
        values += (snapshot_id,)
    rows = _rows(connection, sql + " ORDER BY sequence DESC LIMIT 1", values)
    return _job(rows[0]) if rows else None


def _target(row: dict) -> dict:
    return {**json.loads(row["payload_json"]), "target_id": row["target_id"],
            "revision": row["revision"], "input_fingerprint": row["input_fingerprint"]}


def get_target(connection: sqlite3.Connection, target_id: str) -> dict | None:
    if not _exists(connection, "career_targets"):
        return None
    rows = _rows(connection, "SELECT * FROM career_targets WHERE target_id=?", (target_id,))
    return _target(rows[0]) if rows else None


def list_targets(connection: sqlite3.Connection) -> list[dict]:
    if not _exists(connection, "career_targets"):
        return []
    return [_target(row) for row in _rows(connection, "SELECT * FROM career_targets ORDER BY target_id")]


def _validate_target_refs(connection: sqlite3.Connection, target: dict) -> None:
    bindings = target.get("bindings", {})
    if not isinstance(bindings, dict):
        raise TypeError("target.bindings must be an object")
    for data in (target, bindings):
        if data.get("catalog_id") and not load_catalog(connection, data["catalog_id"]):
            raise ValueError("unknown catalog_id")
        for key in ("evidence_snapshot_id", "source_snapshot_id"):
            if data.get(key) and get_snapshot(connection, data[key]) is None:
                raise ValueError(f"unknown {key}")
        job_ids = data.get("job_ids", [])
        if not isinstance(job_ids, list) or any(get_job(connection, job_id) is None for job_id in job_ids):
            raise ValueError("unknown job_ids")
        if data.get("company_id") and not connection.execute(
            "SELECT 1 FROM career_companies WHERE company_id=?", (data["company_id"],)
        ).fetchone():
            raise ValueError("unknown company_id")


def save_target(connection: sqlite3.Connection, target: dict, *, expected_revision: int | None = None) -> dict:
    target_id = required_text(target.get("target_id"), "target_id")
    fingerprint = required_text(target.get("input_fingerprint"), "input_fingerprint")
    payload = {key: value for key, value in target.items() if key != "revision"}
    serialized = json_text(payload)
    _require_write_schema(connection)
    with write_transaction(connection):
        current = get_target(connection, target_id)
        actual_revision = current["revision"] if current else 0
        if expected_revision is not None and expected_revision != actual_revision:
            raise InputChangedError("target revision changed")
        if current and expected_revision is None:
            raise InputChangedError("updating a target requires expected_revision")
        _validate_target_refs(connection, payload)
        if current and json_text({k: v for k, v in current.items() if k != "revision"}) == serialized:
            return current
        revision = actual_revision + 1
        now = _now()
        connection.execute("""INSERT INTO career_targets
            (target_id,revision,input_fingerprint,payload_json,created_at,updated_at) VALUES (?,?,?,?,?,?)
            ON CONFLICT(target_id) DO UPDATE SET revision=excluded.revision,
            input_fingerprint=excluded.input_fingerprint,payload_json=excluded.payload_json,
            updated_at=excluded.updated_at""", (target_id, revision, fingerprint, serialized, now, now))
        return {**payload, "revision": revision}


def save_diagnosis(connection: sqlite3.Connection, diagnosis: dict, *, target_id: str,
                   input_revision: int, input_fingerprint: str, run_id: str | None = None) -> dict:
    required_text(input_fingerprint, "input_fingerprint")
    serialized = json_text(diagnosis)
    diagnosis_id = content_id("diagnosis", {"target_id": target_id, "input_revision": input_revision,
        "input_fingerprint": input_fingerprint, "run_id": run_id, "diagnosis": diagnosis})
    _require_write_schema(connection)
    with write_transaction(connection):
        target = get_target(connection, target_id)
        if target is None or target["revision"] != input_revision or target["input_fingerprint"] != input_fingerprint:
            raise InputChangedError("diagnosis inputs changed")
        if run_id is not None:
            rows = _rows(connection, "SELECT diagnosis_id FROM career_diagnoses WHERE run_id=?", (run_id,))
            if rows and rows[0]["diagnosis_id"] != diagnosis_id:
                raise InputChangedError("run_id already published a different diagnosis")
        connection.execute("""INSERT OR IGNORE INTO career_diagnoses
            (diagnosis_id,target_id,input_revision,input_fingerprint,input_digest,run_id,payload_json,created_at)
            VALUES (?,?,?,?,?,?,?,?)""", (diagnosis_id, target_id, input_revision, input_fingerprint,
                diagnosis.get("input_digest"), run_id, serialized, _now()))
        return get_diagnosis(connection, diagnosis_id)  # type: ignore[return-value]


def _diagnosis(connection: sqlite3.Connection, row: dict) -> dict:
    target = get_target(connection, row["target_id"])
    stale = target is None or target["revision"] != row["input_revision"] or target["input_fingerprint"] != row["input_fingerprint"]
    return {**json.loads(row["payload_json"]), **{k: v for k, v in row.items() if k != "payload_json"}, "stale": stale}


def get_diagnosis(connection: sqlite3.Connection, diagnosis_id: str) -> dict | None:
    if not _exists(connection, "career_diagnoses"):
        return None
    rows = _rows(connection, "SELECT * FROM career_diagnoses WHERE diagnosis_id=?", (diagnosis_id,))
    return _diagnosis(connection, rows[0]) if rows else None


def list_diagnoses(connection: sqlite3.Connection, target_id: str) -> list[dict]:
    if not _exists(connection, "career_diagnoses"):
        return []
    return [_diagnosis(connection, row) for row in _rows(connection,
        "SELECT * FROM career_diagnoses WHERE target_id=? ORDER BY sequence DESC", (target_id,))]
