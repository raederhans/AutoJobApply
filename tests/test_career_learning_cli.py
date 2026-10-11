"""M3 CLI integration uses fictional temporary workspaces and quoted artifacts."""

from __future__ import annotations

import json
import sqlite3

import pytest
from test_career_evidence_model import evidence_registry
from test_career_import import make_bundle
from test_career_service import invoke

from applypilot.career import service
from applypilot.career.importer import import_bundle
from applypilot.database import close_connection, init_db


def learning_bundle(root):
    bundle = make_bundle(root)
    path = bundle / "companies/jobs.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["jobs"][0]["requirements"][0]["text"] = "Experience with Python"
    path.write_text(json.dumps(payload), encoding="utf-8")
    (bundle / "companies/example/snapshots/one.txt").write_text("Experience with Python\n", encoding="utf-8")
    (bundle / "companies/example/raw/one.json").write_text(
        json.dumps({"description": "<p>Experience with Python</p>"}), encoding="utf-8")
    return bundle


@pytest.fixture
def prepared_cli(tmp_path):
    workspace = tmp_path / "data"
    bundle = learning_bundle(tmp_path / "bundle")
    materials = tmp_path / "materials"
    materials.mkdir()
    registry, source, _ = evidence_registry(materials)
    conn = init_db(workspace / "applypilot.db")
    import_bundle(conn, bundle)
    eid = service.import_evidence(conn, registry)["evidence_snapshot_id"]
    target = service.create_target(conn, evidence_snapshot_id=eid, job_ids=["example-one"])
    yield workspace, target, registry, source
    close_connection(workspace / "applypilot.db")


def write_review(path, task, *, candidate=False):
    payload = {"method": "self_task_review" if candidate else "artifact_review",
               "reviewer": "Fictional candidate" if candidate else "assistant",
               "subject": "candidate" if candidate else "artifact", "context": task["context"],
               "assistance": "Fictional review of a local note; no external execution",
               "limits": ["Only this fictional task and environment"],
               "acceptance_results": [{"criterion": criterion, "status": "pass", "evidence_refs": [
                   {"material_id": "m1", "quote": "I built a Python report.", "line_start": 2, "line_end": 2}]}
                   for criterion in task["acceptance"]]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def assert_reports(files):
    from pathlib import Path
    assert set(files) == {"markdown", "html", "json"}
    assert all(Path(path).is_file() for path in files.values())


def test_real_cli_route_events_submission_review_refresh_chain(tmp_path):
    workspace = tmp_path / "chosen"
    bundle = learning_bundle(tmp_path / "bundle")
    invoke(workspace, "import", "--bundle", str(bundle))
    materials = tmp_path / "materials"
    materials.mkdir()
    registry, _, _ = evidence_registry(materials)
    eid = invoke(workspace, "evidence-import", "--registry", str(registry))["evidence_snapshot_id"]
    target = invoke(workspace, "target", "--evidence-id", eid, "--job-id", "example-one")
    did = invoke(workspace, "diagnose", "--target-id", target["target_id"])["diagnosis_id"]
    saved = invoke(workspace, "route", "--target-id", target["target_id"], "--diagnosis-id", did,
                   "--anchor-id", "programming.guided", "--prefer-skill", "programming",
                   "--output", str(tmp_path / "route-report"))
    assert saved["budget"]["mode"] == "order_only"
    assert saved["budget"]["weekly_hours"] is None
    assert_reports(saved["files"]["route"])
    route_id = saved["route_id"]
    listed = invoke(workspace, "routes", "--target-id", target["target_id"])
    assert len(listed) == 1 and listed[0]["route_id"] == route_id
    current = invoke(workspace, "progress", "--route-id", route_id)
    evidence = next(t for t in current["tasks"] if t["kind"] == "evidence_collection")
    anchor = next(t for t in current["tasks"] if t["anchor_id"] == "programming.guided")
    assert evidence["ready"] and not anchor["ready"]
    assert current["revision"] == 0
    common = ["--route-id", route_id, "--task-id", evidence["task_id"]]
    event = invoke(workspace, "task-event", *common, "--event", "started", "--reason", "Begin fictional note",
                   "--event-key", "started-1", "--expected-revision", "0")
    assert event["revision"] == 1
    replay = invoke(workspace, "task-event", *common, "--event", "started", "--reason", "Begin fictional note",
                    "--event-key", "started-1", "--expected-revision", "0")
    assert replay == event
    invoke(workspace, "task-event", *common, "--event", "marked_done", "--reason", "Note prepared",
           "--event-key", "done-1", "--expected-revision", "1")
    done = invoke(workspace, "progress", "--route-id", route_id)
    assert done["summary"]["completion_reported"] == 1
    submitted = invoke(workspace, "task-submit", *common, "--registry", str(registry),
                       "--event-key", "submit-1", "--expected-revision", "2")
    assert submitted["revision"] == 3 and submitted["artifact_evidence_snapshot_id"]
    review = write_review(tmp_path / "evidence-review.json", evidence)
    checked = invoke(workspace, "task-review", *common, "--review", str(review),
                     "--review-key", "review-1", "--expected-revision", "3")
    assert checked["revision"] == 4 and checked["outcome"] == "passed" and checked["subject"] == "artifact"
    anchor_common = ["--route-id", route_id, "--task-id", anchor["task_id"]]
    invoke(workspace, "task-submit", *anchor_common, "--registry", str(registry),
           "--event-key", "submit-2", "--expected-revision", "4")
    write_review(review, anchor, candidate=True)
    candidate = invoke(workspace, "task-review", *anchor_common, "--review", str(review),
                       "--review-key", "review-2", "--expected-revision", "5")
    assert candidate["revision"] == 6 and candidate["subject"] == "candidate"
    output = tmp_path / "refresh"
    refreshed = invoke(workspace, "rediagnose", "--route-id", route_id, "--output", str(output))
    assert refreshed["diagnosis_id"] != did
    assert refreshed["parent_route_id"] == route_id
    assert_reports(refreshed["files"]["route"])
    assert_reports(refreshed["files"]["diagnosis"])
    assert all(str(output / "diagnosis") in path for path in refreshed["files"]["diagnosis"].values())
    replay_refresh = invoke(workspace, "rediagnose", "--route-id", refreshed["route_id"])
    assert replay_refresh["route_id"] == refreshed["route_id"]
    progress_report = invoke(workspace, "progress", "--route-id", refreshed["route_id"],
                             "--output", str(tmp_path / "progress-report"))
    assert_reports(progress_report["files"]["route"])
    assert not (tmp_path / "wrong-workspace").exists()
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_cli_supplement_preserves_parent_and_uses_target_revision(prepared_cli, tmp_path):
    workspace, target, *_ = prepared_cli
    parent = invoke(workspace, "route", "--target-id", target["target_id"], "--weekly-hours", "2", "--weeks", "3")
    addition = tmp_path / "addition"
    addition.mkdir()
    registry, _, data = evidence_registry(addition)
    data["materials"][0]["id"] = "extra-material"
    data["evidence"][0]["id"] = "extra-evidence"
    data["evidence"][0]["source_refs"][0]["material_id"] = "extra-material"
    registry.write_text(json.dumps(data), encoding="utf-8")
    saved = invoke(workspace, "supplement", "--target-id", target["target_id"], "--registry", str(registry),
                   "--expected-revision", str(target["revision"]), "--parent-route-id", parent["route_id"],
                   "--output", str(tmp_path / "supplement-report"))
    assert saved["added_materials"] == 1 and saved["added_evidence"] == 1
    assert saved["parent_route_id"] == parent["route_id"]
    assert saved["budget"]["weekly_hours"] == 2 and saved["budget"]["weeks"] == 3
    assert_reports(saved["files"]["route"])
    assert_reports(saved["files"]["diagnosis"])
    old = invoke(workspace, "progress", "--route-id", parent["route_id"])
    assert old["stale"]
    error = invoke(workspace, "supplement", "--target-id", target["target_id"], "--registry", str(registry),
                   "--expected-revision", str(target["revision"]), code=2)
    assert "target revision changed" in error


def test_default_evidence_route_review_advances_to_ready_candidate_verification(prepared_cli, tmp_path):
    workspace, target, registry, _ = prepared_cli
    saved = invoke(workspace, "route", "--target-id", target["target_id"])
    initial = invoke(workspace, "progress", "--route-id", saved["route_id"])
    assert len(initial["tasks"]) == 1 and initial["tasks"][0]["kind"] == "evidence_collection"
    evidence = initial["tasks"][0]
    common = ["--route-id", saved["route_id"], "--task-id", evidence["task_id"]]
    invoke(workspace, "task-submit", *common, "--registry", str(registry),
           "--event-key", "submit", "--expected-revision", "0")
    review = write_review(tmp_path / "material-review.json", evidence)
    invoke(workspace, "task-review", *common, "--review", str(review),
           "--review-key", "review", "--expected-revision", "1")
    refreshed = invoke(workspace, "rediagnose", "--route-id", saved["route_id"])
    current = invoke(workspace, "progress", "--route-id", refreshed["route_id"])
    states = {task["task_id"]: task for task in current["tasks"]}
    assert states[evidence["task_id"]]["state"] == "passed"
    anchor = states["anchor:programming.guided"]
    assert anchor["kind"] == "verification" and anchor["state"] == "pending" and anchor["ready"]
    assert not anchor["assessment_current"]


@pytest.mark.parametrize("command,args", [("routes", []), ("progress", ["--route-id", "unknown"])])
def test_readonly_missing_workspace_never_created(tmp_path, command, args):
    workspace = tmp_path / "missing"
    error = invoke(workspace, command, *args, code=2)
    assert "career_workspace_missing" in error
    assert not workspace.exists()


def test_routes_reads_old_database_without_migration(tmp_path):
    workspace = tmp_path / "old"
    workspace.mkdir()
    path = workspace / "applypilot.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sentinel(value TEXT)")
        conn.execute("INSERT INTO sentinel VALUES('untouched')")
    before = path.stat().st_mtime_ns
    assert invoke(workspace, "routes") == []
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("sentinel",)]
        assert conn.execute("SELECT value FROM sentinel").fetchone() == ("untouched",)
    assert path.stat().st_mtime_ns == before


@pytest.mark.parametrize("command", ["task-event", "task-submit", "task-review", "supplement"])
def test_mutation_cli_requires_expected_revision_before_opening_workspace(tmp_path, command):
    workspace = tmp_path / "missing"
    registry, _, _ = evidence_registry(tmp_path)
    review = tmp_path / "review.json"
    review.write_text("{}", encoding="utf-8")
    if command == "supplement":
        args = ["--target-id", "unknown", "--registry", str(registry)]
    else:
        args = ["--route-id", "unknown", "--task-id", "unknown"]
        if command == "task-event":
            args += ["--event", "started", "--reason", "Fictional", "--event-key", "key"]
        elif command == "task-submit":
            args += ["--registry", str(registry), "--event-key", "key"]
        else:
            args += ["--review", str(review), "--review-key", "key"]
    error = invoke(workspace, command, *args, code=2)
    assert "expected-revision" in error
    assert not workspace.exists()


def test_raw_events_rejected_and_stale_revision_does_not_write(prepared_cli):
    workspace, target, *_ = prepared_cli
    saved = invoke(workspace, "route", "--target-id", target["target_id"])
    route_id = saved["route_id"]
    task_id = invoke(workspace, "progress", "--route-id", route_id)["tasks"][0]["task_id"]
    common = ["--route-id", route_id, "--task-id", task_id, "--reason", "Fictional",
              "--event-key", "key", "--expected-revision", "0"]
    invoke(workspace, "task-event", *common, "--event", "assessment_recorded", code=2)
    event = invoke(workspace, "task-event", *common, "--event", "skipped")
    assert event["revision"] == 1
    error = invoke(workspace, "task-event", "--route-id", route_id, "--task-id", task_id,
                   "--reason", "Fictional", "--event-key", "new", "--expected-revision", "0", "--event", "reopened", code=2)
    assert "route revision changed" in error
    progress = invoke(workspace, "progress", "--route-id", route_id)
    assert progress["revision"] == 1 and len(progress["history"]) == 1


@pytest.mark.parametrize("payload", ["[]", "not json"])
def test_review_requires_json_dictionary_without_appending_event(prepared_cli, tmp_path, payload):
    workspace, target, *_ = prepared_cli
    saved = invoke(workspace, "route", "--target-id", target["target_id"])
    task_id = invoke(workspace, "progress", "--route-id", saved["route_id"])["tasks"][0]["task_id"]
    review = tmp_path / "bad-review.json"
    review.write_text(payload, encoding="utf-8")
    invoke(workspace, "task-review", "--route-id", saved["route_id"], "--task-id", task_id,
           "--review", str(review), "--review-key", "bad", "--expected-revision", "0", code=2)
    assert invoke(workspace, "progress", "--route-id", saved["route_id"])["revision"] == 0


def test_cli_selected_allow_root_material_is_forwarded(prepared_cli, tmp_path):
    workspace, target, registry, source = prepared_cli
    root = tmp_path / "registry-only"
    root.mkdir()
    data = json.loads(registry.read_text(encoding="utf-8"))
    data["materials"][0]["path"] = str(source)
    separate = root / "registry.json"
    separate.write_text(json.dumps(data), encoding="utf-8")
    saved = invoke(workspace, "route", "--target-id", target["target_id"])
    task_id = invoke(workspace, "progress", "--route-id", saved["route_id"])["tasks"][0]["task_id"]
    common = ["--route-id", saved["route_id"], "--task-id", task_id, "--registry", str(separate),
              "--event-key", "submit", "--expected-revision", "0"]
    error = invoke(workspace, "task-submit", *common, code=2)
    assert "outside_selected_roots" in error
    saved_event = invoke(workspace, "task-submit", *common, "--allow-root", str(source.parent))
    assert saved_event["revision"] == 1
