"""Immutable learning versions and append-only, transaction-composable records."""

from __future__ import annotations

import json
import sqlite3

from applypilot.career.assessment import validate_review
from applypilot.career.contracts import InputChangedError, content_id, json_text, required_text
from applypilot.storage import career
from applypilot.storage.database_migrations import require_supported_database_schema
from applypilot.storage.transactions import execute_transactional_script, write_transaction


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Migration entry point. Read operations never bootstrap tables."""
    execute_transactional_script(conn, """
    CREATE TABLE IF NOT EXISTS career_learning_routes (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, route_id TEXT NOT NULL UNIQUE,
        route_family_id TEXT NOT NULL, target_id TEXT NOT NULL,
        parent_route_id TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_career_routes_target ON career_learning_routes(target_id, sequence);
    CREATE TABLE IF NOT EXISTS career_task_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
        route_id TEXT NOT NULL, task_id TEXT NOT NULL, event_type TEXT NOT NULL,
        event_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,
        expected_revision INTEGER NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(route_id, revision)
    );
    CREATE INDEX IF NOT EXISTS idx_career_events_route ON career_task_events(route_id, sequence);
    CREATE TABLE IF NOT EXISTS career_assessments (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, assessment_id TEXT NOT NULL UNIQUE,
        review_key TEXT NOT NULL UNIQUE, route_id TEXT NOT NULL, task_id TEXT NOT NULL,
        target_id TEXT NOT NULL, submission_event_id TEXT NOT NULL,
        payload_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_career_assessments_route ON career_assessments(route_id, sequence);
    """)


def _supported(conn: sqlite3.Connection, *, writing: bool = False) -> None:
    from applypilot.database import DATABASE_SCHEMA_VERSION

    require_supported_database_schema(conn, latest_version=DATABASE_SCHEMA_VERSION)
    if writing and any(not career._exists(conn, t) for t in (
        "career_learning_routes", "career_task_events", "career_assessments",
    )):
        raise RuntimeError("career learning schema is missing; initialize the database before writing")


def _route(row: dict) -> dict:
    return {**json.loads(row["payload_json"]), **{k: v for k, v in row.items() if k != "payload_json"}}


def get_route(conn: sqlite3.Connection, route_id: str) -> dict | None:
    _supported(conn)
    required_text(route_id, "route_id")
    if not career._exists(conn, "career_learning_routes"):
        return None
    rows = career._rows(conn, "SELECT * FROM career_learning_routes WHERE route_id=?", (route_id,))
    return _route(rows[0]) if rows else None


def list_routes(conn: sqlite3.Connection, target_id: str | None = None) -> list[dict]:
    _supported(conn)
    if not career._exists(conn, "career_learning_routes"):
        return []
    where, args = (" WHERE target_id=?", (required_text(target_id, "target_id"),)) if target_id is not None else ("", ())
    return [_route(r) for r in career._rows(conn, "SELECT * FROM career_learning_routes" + where + " ORDER BY sequence", args)]


def _positive(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _strings(value: object, name: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{name} must be a list")
    if any(not isinstance(v, str) or not v.strip() for v in value) or len(set(value)) != len(value):
        raise ValueError(f"{name} contains invalid or duplicate entries")
    return value


def _bindings(conn: sqlite3.Connection, route: dict) -> None:
    target = career.get_target(conn, required_text(route.get("target_id"), "target_id"))
    revision = _positive(route.get("target_revision"), "target_revision")
    if target is None:
        raise ValueError("unknown target_id")
    if target["revision"] != revision:
        raise InputChangedError("learning target revision changed")
    for key in ("catalog_id", "evidence_snapshot_id"):
        required_text(route.get(key), key)
        if route[key] != target.get("bindings", {}).get(key):
            raise InputChangedError(f"learning {key} differs from target binding")
    if not career.load_catalog(conn, route["catalog_id"]):
        raise ValueError("unknown catalog_id")
    snapshot = career.get_snapshot(conn, route["evidence_snapshot_id"])
    if snapshot is None or snapshot["kind"] != "candidate_evidence":
        raise ValueError("candidate_evidence_snapshot_required")


def _tasks(conn: sqlite3.Connection, route: dict) -> None:
    tasks = route.get("tasks")
    if not isinstance(tasks, list) or any(not isinstance(t, dict) for t in tasks):
        raise ValueError("route tasks required")
    ids = [required_text(t.get("task_id"), "task_id") for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate task_id")
    catalog = career.load_catalog(conn, route["catalog_id"])
    skills = {s["id"]: s for s in catalog.get("skills", [])}
    anchors = {a["id"]: s["id"] for s in skills.values() for a in s.get("anchors", [])}
    target = career.get_target(conn, route["target_id"])
    requirement_ids = set()
    for job_id, snapshot_id in target.get("bindings", {}).get("job_snapshots", {}).items():
        job = career.get_job(conn, job_id, snapshot_id=snapshot_id)
        if job is None:
            raise ValueError("route research job unavailable")
        requirement_ids.update(r["id"] for r in job.get("requirements", []))
    for task in tasks:
        if task.get("kind") not in {"qualification_review", "evidence_collection", "verification", "practice"}:
            raise ValueError("task kind invalid")
        skill, anchor = task.get("skill_id"), task.get("anchor_id")
        if skill is not None and skill not in skills:
            raise ValueError("unknown skill_id")
        if anchor is not None and (anchor not in anchors or anchors[anchor] != skill):
            raise ValueError("unknown or mismatched anchor_id")
        refs = _strings(task.get("requirement_ids"), "requirement_ids", nonempty=True)
        if not set(refs).issubset(requirement_ids):
            raise ValueError("unknown requirement_ids")
        _strings(task.get("acceptance"), "acceptance", nonempty=True)
        required_text(task.get("context"), "task context")
        dependencies = _strings(task.get("depends_on"), "depends_on")
        if task["task_id"] in dependencies or not set(dependencies).issubset(ids):
            raise ValueError("task dependency invalid")
    # Reject cycles rather than storing an impossible prerequisite graph.
    visiting, visited = set(), set()
    by_id = {t["task_id"]: t for t in tasks}

    def visit(tid: str) -> None:
        if tid in visiting:
            raise ValueError("task dependency cycle")
        if tid not in visited:
            visiting.add(tid)
            for dep in by_id[tid]["depends_on"]:
                visit(dep)
            visiting.remove(tid)
            visited.add(tid)

    for tid in ids:
        visit(tid)


def save_route(conn: sqlite3.Connection, route: dict, *, parent_route_id: str | None = None) -> dict:
    if not isinstance(route, dict):
        raise TypeError("route must be an object")
    _supported(conn, writing=True)
    ignored = {"route_id", "sequence", "created_at", "revision", "parent_route_id"}
    payload = {k: v for k, v in route.items() if k not in ignored}
    if route.get("parent_route_id") is not None and route["parent_route_id"] != parent_route_id:
        raise ValueError("parent_route_id conflict")
    payload["parent_route_id"] = parent_route_id
    payload["route_family_id"] = required_text(route.get("route_family_id", route.get("target_id")), "route_family_id")
    route_id = content_id("route", payload)
    if route.get("route_id") is not None and route["route_id"] != route_id:
        raise ValueError("route_id does not match content")
    with write_transaction(conn):
        old = get_route(conn, route_id)
        if old:
            return old
        _bindings(conn, payload)
        _tasks(conn, payload)
        if parent_route_id is not None:
            parent = get_route(conn, parent_route_id)
            if (parent is None or parent["target_id"] != payload["target_id"]
                    or parent["route_family_id"] != payload["route_family_id"]):
                raise ValueError("parent_route_id invalid")
        conn.execute("""INSERT INTO career_learning_routes
            (route_id,route_family_id,target_id,parent_route_id,payload_json,created_at) VALUES (?,?,?,?,?,?)""",
            (route_id, payload["route_family_id"], payload["target_id"], parent_route_id, json_text(payload), career._now()))
        return get_route(conn, route_id)  # type: ignore[return-value]


def route_revision(conn: sqlite3.Connection, route_id: str) -> int:
    if get_route(conn, route_id) is None:
        raise ValueError("unknown route_id")
    row = conn.execute("SELECT COALESCE(MAX(revision),0) FROM career_task_events WHERE route_id=?", (route_id,)).fetchone()
    return row[0]


def _event(row: dict) -> dict:
    return {**{k: v for k, v in row.items() if k != "payload_json"}, "payload": json.loads(row["payload_json"])}


def list_events(conn: sqlite3.Connection, route_id: str) -> list[dict]:
    _supported(conn)
    required_text(route_id, "route_id")
    if not career._exists(conn, "career_task_events"):
        return []
    return [_event(r) for r in career._rows(conn,
        "SELECT * FROM career_task_events WHERE route_id=? ORDER BY sequence", (route_id,))]


def append_event(conn: sqlite3.Connection, *, route_id: str, task_id: str, event_type: str,
                 payload: dict, event_key: str, expected_revision: int) -> dict:
    for name, value in (("route_id", route_id), ("task_id", task_id), ("event_type", event_type), ("event_key", event_key)):
        required_text(value, name)
    if not isinstance(payload, dict):
        raise TypeError("event payload must be an object")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision must be a non-negative integer")
    serialized = json_text(payload)
    _supported(conn, writing=True)
    with write_transaction(conn):
        previous = career._rows(conn, "SELECT * FROM career_task_events WHERE event_key=?", (event_key,))
        if previous:
            row = previous[0]
            if (row["route_id"], row["task_id"], row["event_type"], row["payload_json"]) != (
                route_id, task_id, event_type, serialized,
            ):
                raise InputChangedError("event_key already contains different content")
            return _event(row)
        route = get_route(conn, route_id)
        if route is None:
            raise ValueError("unknown route_id")
        if task_id not in {t["task_id"] for t in route["tasks"]}:
            raise ValueError("unknown task_id")
        _bindings(conn, route)
        actual = route_revision(conn, route_id)
        if actual != expected_revision:
            raise InputChangedError("route revision changed")
        identity = {"route_id": route_id, "task_id": task_id, "event_type": event_type,
                    "payload": payload, "event_key": event_key}
        event_id = content_id("task-event", identity)
        conn.execute("""INSERT INTO career_task_events
            (event_id,route_id,task_id,event_type,event_key,payload_json,expected_revision,revision,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""", (event_id, route_id, task_id, event_type, event_key, serialized,
                                             expected_revision, actual + 1, career._now()))
        return _event(career._rows(conn, "SELECT * FROM career_task_events WHERE event_id=?", (event_id,))[0])


def _assessment(row: dict) -> dict:
    return {**json.loads(row["payload_json"]), **{k: v for k, v in row.items() if k != "payload_json"},
            "id": row["assessment_id"]}


def list_assessments(conn: sqlite3.Connection, target_id: str | None = None,
                     route_id: str | None = None) -> list[dict]:
    _supported(conn)
    if not career._exists(conn, "career_assessments"):
        return []
    filters, values = [], []
    for key, value in (("target_id", target_id), ("route_id", route_id)):
        if value is not None:
            filters.append(key + "=?")
            values.append(required_text(value, key))
    where = " WHERE " + " AND ".join(filters) if filters else ""
    return [_assessment(r) for r in career._rows(conn,
        "SELECT * FROM career_assessments" + where + " ORDER BY sequence", tuple(values))]


def save_assessment(conn: sqlite3.Connection, assessment: dict, *, review_key: str) -> dict:
    required_text(review_key, "review_key")
    if not isinstance(assessment, dict):
        raise TypeError("assessment must be an object")
    _supported(conn, writing=True)
    ignored = {"id", "assessment_id", "sequence", "created_at", "review_key"}
    payload = {k: v for k, v in assessment.items() if k not in ignored}
    with write_transaction(conn):
        route = get_route(conn, required_text(payload.get("route_id"), "route_id"))
        if route is None:
            raise ValueError("unknown route_id")
        task = next((t for t in route["tasks"] if t["task_id"] == payload.get("task_id")), None)
        if task is None:
            raise ValueError("unknown task_id")
        for key in ("target_id", "target_revision", "catalog_id", "evidence_snapshot_id"):
            if payload.get(key) != route[key]:
                raise InputChangedError(f"assessment {key} binding differs from route")
        submission_id = required_text(payload.get("submission_event_id"), "submission_event_id")
        submissions = career._rows(conn, "SELECT * FROM career_task_events WHERE event_id=?", (submission_id,))
        if not submissions or (submissions[0]["route_id"], submissions[0]["task_id"]) != (route["route_id"], task["task_id"]):
            raise ValueError("assessment submission binding invalid")
        submission = _event(submissions[0])
        if submission["event_type"] not in {"submitted", "submission"}:
            raise ValueError("assessment requires submitted event")
        artifact_id = required_text(payload.get("artifact_evidence_snapshot_id"), "artifact_evidence_snapshot_id")
        if artifact_id != submission["payload"].get("artifact_evidence_snapshot_id"):
            raise ValueError("assessment artifact differs from submission")
        artifact = career.get_snapshot(conn, artifact_id)
        if artifact is None or artifact["kind"] != "candidate_evidence":
            raise ValueError("assessment artifact snapshot invalid")
        if payload.get("artifact_fingerprint") != artifact["payload"].get("fingerprint"):
            raise InputChangedError("assessment artifact fingerprint differs from snapshot")
        normalized = validate_review(task=task, review=payload, artifact=artifact["payload"])
        serialized = json_text(normalized)
        old = career._rows(conn, "SELECT * FROM career_assessments WHERE review_key=?", (review_key,))
        if old:
            if old[0]["payload_json"] != serialized:
                raise InputChangedError("review_key already contains different content")
            return _assessment(old[0])
        _bindings(conn, route)
        assessment_id = content_id("assessment", {"review_key": review_key, "assessment": normalized})
        conn.execute("""INSERT INTO career_assessments
            (assessment_id,review_key,route_id,task_id,target_id,submission_event_id,payload_json,created_at)
            VALUES (?,?,?,?,?,?,?,?)""", (assessment_id, review_key, route["route_id"], task["task_id"],
                                           route["target_id"], submission_id, serialized, career._now()))
        append_event(conn, route_id=route["route_id"], task_id=task["task_id"], event_type="assessment_recorded",
                     payload={"assessment_id": assessment_id, "submission_event_id": submission_id,
                              "outcome": normalized["outcome"]}, event_key="assessment:" + review_key,
                     expected_revision=route_revision(conn, route["route_id"]))
        return _assessment(career._rows(conn,
            "SELECT * FROM career_assessments WHERE assessment_id=?", (assessment_id,))[0])
