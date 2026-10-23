"""Isolated SQLite tests using fictional learning and candidate fixtures."""

from __future__ import annotations

import sqlite3
from copy import deepcopy

import pytest
from test_career_evidence_model import evidence_registry
from test_career_import import make_bundle

from applypilot.career import service
from applypilot.career.assessment import validate_review
from applypilot.career.contracts import InputChangedError
from applypilot.career.importer import import_bundle
from applypilot.database import DATABASE_SCHEMA_VERSION
from applypilot.storage import career
from applypilot.storage import career_learning as store
from applypilot.storage.database_migrations import DATABASE_SCHEMA_COMPONENT
from applypilot.storage.transactions import write_transaction


@pytest.fixture
def prepared(tmp_path):
    conn = sqlite3.connect(":memory:")
    career.ensure_schema(conn)
    store.ensure_schema(conn)
    import_bundle(conn, make_bundle(tmp_path / "bundle"))
    materials = tmp_path / "materials"
    materials.mkdir()
    registry, _, _ = evidence_registry(materials)
    eid = service.import_evidence(conn, registry)["evidence_snapshot_id"]
    target = service.create_target(conn, evidence_snapshot_id=eid, job_ids=["example-one"])
    route = {"target_id": target["target_id"], "target_revision": target["revision"],
             **{k: target["bindings"][k] for k in ("catalog_id", "evidence_snapshot_id")},
             "requirements": [{"id": "example-one-r001"}], "tasks": [{
                 "task_id": "fictional-task", "kind": "verification", "skill_id": "programming",
                 "anchor_id": "programming.guided", "requirement_ids": ["example-one-r001"],
                 "context": "Fictional tool report", "acceptance": ["Names the input"], "depends_on": [],
                 "status": "pending"}]}
    yield conn, route, target, eid
    conn.close()


def submitted(prepared):
    conn, route, _target, eid = prepared
    saved = store.save_route(conn, route)
    event = store.append_event(conn, route_id=saved["route_id"], task_id="fictional-task",
                               event_type="submitted", payload={"artifact_evidence_snapshot_id": eid},
                               event_key="submission-1", expected_revision=0)
    artifact = career.get_snapshot(conn, eid)["payload"]
    review = {**{k: saved[k] for k in ("route_id", "target_id", "target_revision", "catalog_id", "evidence_snapshot_id")},
              "task_id": "fictional-task", "submission_event_id": event["event_id"],
              "artifact_evidence_snapshot_id": eid, "artifact_fingerprint": artifact["fingerprint"],
              "method": "artifact_review", "reviewer": "assistant", "subject": "artifact",
              "context": "Fictional review", "assistance": "Assistant reviewed existing material",
              "acceptance_results": [{"criterion": "Names the input", "status": "pass", "evidence_refs": [
                  {"material_id": "m1", "quote": "I built a Python report.", "line_start": 2, "line_end": 2}]}]}
    return conn, saved, event, review


def test_readers_do_not_create_schema_or_write():
    conn = sqlite3.connect(":memory:")
    statements = []
    conn.set_trace_callback(statements.append)
    assert store.get_route(conn, "unknown") is None
    assert store.list_routes(conn) == store.list_events(conn, "unknown") == store.list_assessments(conn) == []
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0


def test_future_schema_rejected_for_reads_and_writes():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE applypilot_schema_version(component TEXT,version INTEGER)")
    conn.execute("INSERT INTO applypilot_schema_version VALUES(?,?)", (DATABASE_SCHEMA_COMPONENT, DATABASE_SCHEMA_VERSION + 1))
    before = conn.total_changes
    for reader in (lambda: store.list_routes(conn), lambda: store.list_assessments(conn),
                   lambda: store.list_events(conn, "absent"), lambda: store.get_route(conn, "absent"),
                   lambda: store.save_route(conn, {})):
        with pytest.raises(RuntimeError, match="newer than supported"):
            reader()
    assert conn.total_changes == before


def test_route_versions_are_immutable_content_addressed_and_parent_linked(prepared):
    conn, route, target, _ = prepared
    first = store.save_route(conn, route)
    assert store.save_route(conn, deepcopy(route)) == first
    changed = deepcopy(route)
    changed["tasks"][0]["context"] = "Different fictional context"
    second = store.save_route(conn, changed, parent_route_id=first["route_id"])
    assert second["route_id"] != first["route_id"]
    assert second["parent_route_id"] == first["route_id"]
    assert second["route_family_id"] == first["route_family_id"] == target["target_id"]
    assert store.get_route(conn, first["route_id"]) == first
    assert store.list_routes(conn, target["target_id"]) == [first, second]


@pytest.mark.parametrize("mutation,error", [
    (lambda r: r.update(target_revision=9), "revision changed"),
    (lambda r: r.update(evidence_snapshot_id="unknown"), "binding"),
    (lambda r: r["tasks"][0].update(skill_id="unknown"), "unknown skill"),
    (lambda r: r["tasks"][0].update(anchor_id="unknown"), "anchor_id"),
    (lambda r: r["tasks"][0].update(depends_on=["unknown"]), "dependency invalid"),
    (lambda r: r["tasks"][0].update(requirement_ids=["unknown"]), "unknown requirement_ids"),
])
def test_invalid_route_bindings_and_refs_rejected(prepared, mutation, error):
    conn, route, *_ = prepared
    mutation(route)
    with pytest.raises(ValueError, match=error):
        store.save_route(conn, route)
    assert store.list_routes(conn) == []


def test_event_cas_retry_idempotence_and_key_conflict(prepared):
    conn, route, *_ = prepared
    rid = store.save_route(conn, route)["route_id"]
    args = {"route_id": rid, "task_id": "fictional-task", "event_type": "started",
            "payload": {"reason": "Fictional start"}, "event_key": "start-1", "expected_revision": 0}
    first = store.append_event(conn, **args)
    second = store.append_event(conn, **{**args, "event_type": "marked_done", "event_key": "done-1", "expected_revision": 1})
    assert store.route_revision(conn, rid) == 2 and second["sequence"] > first["sequence"]
    assert store.append_event(conn, **args) == first
    assert store.append_event(conn, **{**args, "expected_revision": 2}) == first
    with pytest.raises(InputChangedError, match="different content"):
        store.append_event(conn, **{**args, "payload": {"changed": True}})
    with pytest.raises(InputChangedError, match="route revision"):
        store.append_event(conn, **{**args, "event_key": "another-start"})
    with pytest.raises(ValueError, match="unknown task"):
        store.append_event(conn, **{**args, "task_id": "other", "event_key": "bad-task", "expected_revision": 2})
    assert store.list_events(conn, rid) == [first, second]


def test_assessment_binding_normalization_idempotence_and_revision(prepared):
    conn, route, event, review = submitted(prepared)
    review["outcome"] = "Invented outcome"
    first = store.save_assessment(conn, review, review_key="review-1", validate_review=validate_review)
    assert first["outcome"] == "passed" and first["id"] == first["assessment_id"]
    assert store.save_assessment(conn, review, review_key="review-1", validate_review=validate_review) == first
    assert store.save_assessment(conn, first, review_key="review-1", validate_review=validate_review) == first
    assert store.route_revision(conn, route["route_id"]) == 2
    assert store.list_events(conn, route["route_id"])[-1]["event_type"] == "assessment_recorded"
    assert store.list_assessments(conn, route_id=route["route_id"]) == [first]
    assert first["submission_event_id"] == event["event_id"]
    changed = {**review, "assistance": "Different assistance"}
    with pytest.raises(InputChangedError, match="different content"):
        store.save_assessment(conn, changed, review_key="review-1", validate_review=validate_review)
    assert store.route_revision(conn, route["route_id"]) == 2


@pytest.mark.parametrize("key,value,error", [
    ("target_revision", 9, "binding"), ("target_id", "other", "binding"),
    ("catalog_id", "other", "binding"), ("evidence_snapshot_id", "other", "binding"),
    ("artifact_evidence_snapshot_id", "other", "differs from submission"),
    ("artifact_fingerprint", "other", "fingerprint"), ("submission_event_id", "absent", "submission binding"),
])
def test_assessment_wrong_snapshot_revision_or_submission_rejected(prepared, key, value, error):
    conn, route, _, review = submitted(prepared)
    review[key] = value
    with pytest.raises(ValueError, match=error):
        store.save_assessment(conn, review, review_key="bad-review", validate_review=validate_review)
    assert store.list_assessments(conn) == [] and store.route_revision(conn, route["route_id"]) == 1


def test_outer_transaction_rolls_back_route_event_assessment_together(prepared):
    conn, _, *_ = prepared
    with pytest.raises(RuntimeError, match="fictional cancellation"), write_transaction(conn):
        _, route, _, review = submitted(prepared)
        store.save_assessment(conn, review, review_key="review-1", validate_review=validate_review)
        assert store.route_revision(conn, route["route_id"]) == 2
        raise RuntimeError("fictional cancellation")
    assert store.list_routes(conn) == store.list_assessments(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM career_task_events").fetchone()[0] == 0


def test_stale_target_prevents_new_writes_but_history_remains_readable(prepared):
    conn, route, target, _ = prepared
    rid = store.save_route(conn, route)["route_id"]
    career.save_target(conn, {**target, "label": "Updated fictional target", "input_fingerprint": "changed"}, expected_revision=1)
    with pytest.raises(InputChangedError, match="target revision"):
        store.append_event(conn, route_id=rid, task_id="fictional-task", event_type="started", payload={},
                           event_key="stale-start", expected_revision=0)
    assert store.get_route(conn, rid)["target_revision"] == 1
    assert store.list_events(conn, rid) == []


def test_failed_assessment_event_append_rolls_back_review_only(prepared):
    conn, route, _, review = submitted(prepared)
    store.append_event(conn, route_id=route["route_id"], task_id="fictional-task", event_type="started",
                       payload={}, event_key="assessment:review-1", expected_revision=1)
    with pytest.raises(InputChangedError, match="different content"):
        store.save_assessment(conn, review, review_key="review-1", validate_review=validate_review)
    assert store.list_assessments(conn) == [] and store.route_revision(conn, route["route_id"]) == 2


def test_assessment_requires_real_submitted_event(prepared):
    conn, route, _, review = submitted(prepared)
    event = store.append_event(conn, route_id=route["route_id"], task_id="fictional-task", event_type="marked_done",
                               payload={}, event_key="done", expected_revision=1)
    review["submission_event_id"] = event["event_id"]
    with pytest.raises(ValueError, match="submitted event"):
        store.save_assessment(conn, review, review_key="review-1", validate_review=validate_review)
    assert store.list_assessments(conn) == []
