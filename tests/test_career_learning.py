"""M3 service integration against fictional jobs, materials and observations."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from test_career_service import prepared as base_prepared  # noqa: F401

from applypilot.career import learning, service
from applypilot.career.contracts import InputChangedError
from applypilot.career.importer import import_bundle
from applypilot.storage import career as store
from applypilot.storage import career_learning as learning_store


@pytest.fixture
def prepared(base_prepared):  # noqa: F811
    """Use an explicit programming condition so mapping is source-grounded."""
    conn, target, source, registry, bundle, db = base_prepared
    path = bundle / "companies/jobs.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["jobs"][0]["requirements"][0]["text"] = "Use Python for programming."
    path.write_text(json.dumps(payload), encoding="utf-8")
    (bundle / "companies/example/snapshots/one.txt").write_text("Use Python for programming.\n", encoding="utf-8")
    (bundle / "companies/example/raw/one.json").write_text(
        json.dumps({"description": "<p>Use Python for programming.</p>"}), encoding="utf-8")
    import_bundle(conn, bundle)
    target = service.create_target(conn, evidence_snapshot_id=target["bindings"]["evidence_snapshot_id"],
                                   job_ids=["example-one"], target_id="fictional-learning-target")
    return conn, target, source, registry, bundle, db


def artifact_registry(tmp_path, *, label="submitted", extra=""):
    root = tmp_path / label
    root.mkdir()
    source = root / "observation.md"
    source.write_text("Fictional task observation.\nThe input is a list of report rows.\n"
                      "The output is a checked report.\n"
                      "Fictional failed attempt: the candidate did not name the input.\n"
                      "Fictional candidate authored the report; the material index points to observation.md.\n"
                      "Related material is not task acceptance; deployment and scale remain unobserved.\n"
                      + extra, encoding="utf-8")
    registry = root / "evidence.json"
    registry.write_text(json.dumps({
        "materials": [{"id": label + "-material", "path": "observation.md", "kind": "project_evidence"}],
        "evidence": [{"id": label + "-record", "claim": "Fictional task observation",
                      "ownership": "fictional candidate", "support_type": "fictional observation",
                      "source_refs": [{"material_id": label + "-material", "quote": "The input is a list of report rows."}]}],
    }), encoding="utf-8")
    return registry, source, label + "-material"


def review_for(task, material_id, *, status="pass", candidate=False):
    observations = []
    for index, criterion in enumerate(task["acceptance"]):
        if status == "fail":
            quote, line = "Fictional failed attempt: the candidate did not name the input.", 4
        elif task["kind"] == "evidence_collection":
            quote, line = (
                ("Fictional candidate authored the report; the material index points to observation.md.", 5)
                if index == 0 else
                ("Related material is not task acceptance; deployment and scale remain unobserved.", 6))
        else:
            quote, line = "The input is a list of report rows.", 2
        observations.append({"criterion": criterion, "status": status, "evidence_refs": [
            {"material_id": material_id, "quote": quote, "line_start": line, "line_end": line}]})
    return {"method": "independent_task_review" if candidate else "artifact_review",
            "reviewer": "Fictional reviewer Mira" if candidate else "assistant",
            "subject": "candidate" if candidate else "artifact",
            "context": "Fictional observed report task using synthetic rows",
            "assistance": "Fictional reviewer recorded no assistance" if candidate else "Assistant organized existing materials",
            "limits": ["Synthetic test only; result concerns this exact task"],
            "acceptance_results": observations}


def progress_task(conn, route, task_id):
    return next(t for t in learning.read_progress(conn, route["route_id"])["tasks"] if t["task_id"] == task_id)


def submit_review(conn, route, task_id, registry, material_id, *, key, status="pass", candidate=False):
    progress = learning.read_progress(conn, route["route_id"])
    task = next(t for t in progress["tasks"] if t["task_id"] == task_id)
    submission = learning.submit_task(conn, route["route_id"], task_id, registry,
                                      event_key="submit-" + key, expected_revision=progress["revision"])
    review = review_for(task, material_id, status=status, candidate=candidate)
    assessment = learning.review_task(conn, route["route_id"], task_id, review,
                                      review_key="review-" + key, expected_revision=submission["revision"])
    return submission, review, assessment


def anchored_route(prepared):
    conn, target, *_ = prepared
    route = learning.create_route(conn, target["target_id"], selected_anchor_ids=["programming.guided"])
    return conn, target, route


def clear_evidence_dependency(conn, route, registry, material_id):
    tasks = learning.read_progress(conn, route["route_id"])["tasks"]
    evidence_task = next((t for t in tasks if t["kind"] == "evidence_collection"), None)
    if evidence_task:
        submit_review(conn, route, evidence_task["task_id"], registry, material_id, key="evidence")
        assert progress_task(conn, route, evidence_task["task_id"])["state"] == "passed"


def test_default_route_is_order_only_and_progress_reader_is_pure(prepared):
    conn, target, *_ = prepared
    route = learning.create_route(conn, target["target_id"])
    assert route["budget"]["mode"] == "order_only" and route["budget"]["total_hours"] is None
    assert route["tasks"] and all(not t["scheduled"] for t in route["tasks"])
    statements = []
    conn.set_trace_callback(statements.append)
    progress = learning.read_progress(conn, route["route_id"])
    conn.set_trace_callback(None)
    assert progress["revision"] == 0 and not progress["stale"]
    assert all(t["state"] == "pending" for t in progress["tasks"])
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert learning.create_route(conn, target["target_id"])["route_id"] == route["route_id"]


def test_marked_done_cannot_close_dependency_or_create_assessed_pass(prepared, tmp_path):
    conn, target, route = anchored_route(prepared)
    tasks = learning.read_progress(conn, route["route_id"])["tasks"]
    blocker = next(t for t in tasks if t["kind"] == "evidence_collection")
    anchor = next(t for t in tasks if t["anchor_id"] == "programming.guided")
    assert anchor["blocked_by"] == [blocker["task_id"]] and not anchor["ready"]
    learning.record_event(conn, route["route_id"], blocker["task_id"], event_type="marked_done",
                          reason="Fictional course completed", event_key="done", expected_revision=0)
    assert progress_task(conn, route, blocker["task_id"])["state"] == "completion_reported"
    registry, _, _ = artifact_registry(tmp_path)
    with pytest.raises(ValueError, match="not_ready"):
        learning.submit_task(conn, route["route_id"], anchor["task_id"], registry,
                             event_key="blocked-submit", expected_revision=1)
    assert learning_store.list_assessments(conn) == []
    diagnosis = service.diagnose(conn, target["target_id"])
    assert not any(row["observed_task_passes"] for row in diagnosis["requirements"])


def test_review_requires_submission_and_full_real_task_acceptance(prepared, tmp_path):
    conn, _, route = anchored_route(prepared)
    task = next(t for t in route["tasks"] if t["kind"] == "evidence_collection")
    assert len(task["acceptance"]) == 2
    registry, _, mid = artifact_registry(tmp_path)
    review = review_for(task, mid)
    with pytest.raises(ValueError, match="requires_submission"):
        learning.review_task(conn, route["route_id"], task["task_id"], review,
                             review_key="without-submission", expected_revision=0)
    event = learning.submit_task(conn, route["route_id"], task["task_id"], registry,
                                event_key="partial-submit", expected_revision=0)
    partial = deepcopy(review)
    partial["acceptance_results"].pop()
    with pytest.raises(ValueError, match="missing_criterion"):
        learning.review_task(conn, route["route_id"], task["task_id"], partial,
                             review_key="partial-review", expected_revision=event["revision"])
    assert learning_store.list_assessments(conn) == []
    assert learning.read_progress(conn, route["route_id"])["revision"] == event["revision"]


def test_candidate_failed_then_passed_rediagnosis_and_jobs_unchanged(prepared, tmp_path):
    conn, target, route = anchored_route(prepared)
    jobs_before = [tuple(row) for row in conn.execute("SELECT * FROM jobs ORDER BY url")]
    registry, _, mid = artifact_registry(tmp_path)
    clear_evidence_dependency(conn, route, registry, mid)
    task = next(t for t in route["tasks"] if t["anchor_id"] == "programming.guided")
    catalog = store.load_catalog(conn, route["catalog_id"])
    actual_anchor = next(a for s in catalog["skills"] for a in s["anchors"] if a["id"] == task["anchor_id"])
    assert task["acceptance"] == actual_anchor["acceptance"] == ["Names the input"]
    _, _, failure = submit_review(conn, route, task["task_id"], registry, mid,
                                   key="failed", status="fail", candidate=True)
    assert failure["outcome"] == "failed"
    assert progress_task(conn, route, task["task_id"])["state"] == "needs_revision"
    assert "task_assessments_changed" in service.read_diagnosis(conn, route["diagnosis_id"])["stale_reasons"]
    failed_refresh = learning.rediagnose(conn, route["route_id"])
    failed_rows = failed_refresh["diagnosis"]["requirements"]
    assert any(r["status"] == "observed_task_gap" and r["observed_task_failures"] for r in failed_rows)
    child = failed_refresh["route"]
    assert child["parent_route_id"] == route["route_id"]
    practice = next(t for t in child["tasks"] if t["anchor_id"] == "programming.guided")
    assert practice["kind"] == "practice" and practice["acceptance"] == actual_anchor["acceptance"]
    assert progress_task(conn, child, practice["task_id"])["ready"]
    _, _, passed = submit_review(conn, child, practice["task_id"], registry, mid,
                                 key="passed", candidate=True)
    assert passed["outcome"] == "passed" and passed["subject"] == "candidate"
    assert progress_task(conn, child, practice["task_id"])["state"] == "passed"
    refreshed = learning.rediagnose(conn, child["route_id"])
    rows = refreshed["diagnosis"]["requirements"]
    assert any(r["observed_task_passes"] for r in rows)
    assert not any(r["observed_task_failures"] for r in rows)
    assert all(s["level"] is None for s in refreshed["diagnosis"]["skills"])
    assert [tuple(row) for row in conn.execute("SELECT * FROM jobs ORDER BY url")] == jobs_before
    second = learning.rediagnose(conn, refreshed["route"]["route_id"])
    assert second["diagnosis"]["diagnosis_id"] == refreshed["diagnosis"]["diagnosis_id"]
    assert second["route"]["route_id"] == refreshed["route"]["route_id"]
    assert learning.active_assessments(conn, target)["assessments"]


def test_artifact_pass_stays_material_scoped_and_does_not_become_candidate_pass(prepared, tmp_path):
    conn, _, route = anchored_route(prepared)
    registry, _, mid = artifact_registry(tmp_path)
    clear_evidence_dependency(conn, route, registry, mid)
    task = next(t for t in route["tasks"] if t["anchor_id"] == "programming.guided")
    _, _, assessment = submit_review(conn, route, task["task_id"], registry, mid, key="artifact")
    assert assessment["outcome"] == "passed" and assessment["subject"] == "artifact"
    assert progress_task(conn, route, task["task_id"])["state"] == "completion_reported"
    refreshed = learning.rediagnose(conn, route["route_id"])
    assert not any(r["observed_task_passes"] for r in refreshed["diagnosis"]["requirements"])
    observations = [obs for r in refreshed["diagnosis"]["requirements"] for obs in r["task_observations"]]
    assert any(obs["subject"] == "artifact" and obs["candidate_task_observed"] is False for obs in observations)


def test_event_submission_and_review_retries_are_idempotent_and_cas_guarded(prepared, tmp_path):
    conn, target, *_ = prepared
    route = learning.create_route(conn, target["target_id"])
    task = route["tasks"][0]
    args = {"event_type": "started", "reason": "Fictional start", "event_key": "start", "expected_revision": 0}
    event = learning.record_event(conn, route["route_id"], task["task_id"], **args)
    assert learning.record_event(conn, route["route_id"], task["task_id"], **args) == event
    with pytest.raises(InputChangedError, match="revision changed"):
        learning.record_event(conn, route["route_id"], task["task_id"], **{**args, "event_key": "stale"})
    registry, _, mid = artifact_registry(tmp_path)
    submission = learning.submit_task(conn, route["route_id"], task["task_id"], registry,
                                      event_key="submit", expected_revision=1)
    assert learning.submit_task(conn, route["route_id"], task["task_id"], registry,
                                event_key="submit", expected_revision=1) == submission
    review = review_for(task, mid)
    saved = learning.review_task(conn, route["route_id"], task["task_id"], review,
                                 review_key="review", expected_revision=2)
    assert learning.review_task(conn, route["route_id"], task["task_id"], review,
                                review_key="review", expected_revision=2) == saved
    assert learning.read_progress(conn, route["route_id"])["revision"] == 3
    with pytest.raises(InputChangedError, match="different content"):
        learning.review_task(conn, route["route_id"], task["task_id"], {**review, "assistance": "Changed record"},
                             review_key="review", expected_revision=3)
    with pytest.raises(InputChangedError, match="revision changed"):
        learning.review_task(conn, route["route_id"], task["task_id"], review,
                             review_key="new-review", expected_revision=2)
    assert len(learning_store.list_assessments(conn)) == 1


def test_changed_artifact_revokes_active_pass_and_stales_diagnosis(prepared, tmp_path):
    conn, target, route = anchored_route(prepared)
    registry, source, mid = artifact_registry(tmp_path)
    clear_evidence_dependency(conn, route, registry, mid)
    task = next(t for t in route["tasks"] if t["anchor_id"] == "programming.guided")
    _, _, passed = submit_review(conn, route, task["task_id"], registry, mid, key="candidate", candidate=True)
    diagnosed = service.diagnose(conn, target["target_id"])
    assert any(r["observed_task_passes"] for r in diagnosed["requirements"])
    source.write_text(source.read_text(encoding="utf-8") + "Changed scope after review.\n", encoding="utf-8")
    active = learning.active_assessments(conn, target)
    assert passed["id"] not in {a["id"] for a in active["assessments"]}
    assert any(e["assessment_id"] == passed["id"] for e in active["excluded"])
    progress = progress_task(conn, route, task["task_id"])
    assert progress["state"] == "submitted" and progress["assessment_current"] is False
    assert service.read_diagnosis(conn, diagnosed["diagnosis_id"])["stale"]
    new = service.diagnose(conn, target["target_id"])
    assert not any(r["observed_task_passes"] for r in new["requirements"])
    assert learning_store.list_assessments(conn)[-1]["id"] == passed["id"]


def test_supplement_creates_new_target_revision_and_retains_old_route(prepared, tmp_path):
    conn, target, _, _, _, _ = prepared
    route = learning.create_route(conn, target["target_id"])
    registry, _, _ = artifact_registry(tmp_path, label="supplement")
    result = learning.supplement_materials(conn, target["target_id"], registry,
                                         expected_revision=1, parent_route_id=route["route_id"])
    current = store.get_target(conn, target["target_id"])
    assert current["revision"] == 2
    assert current["bindings"]["evidence_snapshot_id"] != target["bindings"]["evidence_snapshot_id"]
    assert result["added_materials"] == result["added_evidence"] == 1
    assert result["route"]["target_revision"] == 2 and result["route"]["parent_route_id"] == route["route_id"]
    assert learning_store.get_route(conn, route["route_id"])["target_revision"] == 1
    assert "target_revision_changed" in learning.read_progress(conn, route["route_id"])["stale_reasons"]
    assert not result["progress"]["stale"]


def test_review_retry_cannot_restore_success_for_stale_target_revision(prepared, tmp_path):
    conn, target, *_ = prepared
    route = learning.create_route(conn, target["target_id"])
    task = route["tasks"][0]
    registry, _, mid = artifact_registry(tmp_path)
    _, review, saved = submit_review(conn, route, task["task_id"], registry, mid, key="prior")
    addition, _, _ = artifact_registry(tmp_path, label="supplement")
    learning.supplement_materials(conn, target["target_id"], addition,
                                  expected_revision=1, parent_route_id=route["route_id"])
    with pytest.raises(InputChangedError, match="stale|revision|binding"):
        learning.review_task(conn, route["route_id"], task["task_id"], review,
                             review_key="review-prior", expected_revision=1)
    assert saved["id"] in {a["id"] for a in learning_store.list_assessments(conn)}


def test_reopened_task_revokes_current_observation_and_preserves_history(prepared, tmp_path):
    conn, target, route = anchored_route(prepared)
    registry, _, mid = artifact_registry(tmp_path)
    clear_evidence_dependency(conn, route, registry, mid)
    task = next(t for t in route["tasks"] if t["anchor_id"] == "programming.guided")
    _, _, saved = submit_review(conn, route, task["task_id"], registry, mid, key="candidate", candidate=True)
    before = learning.read_progress(conn, route["route_id"])
    learning.record_event(conn, route["route_id"], task["task_id"], event_type="reopened",
                          reason="Fictional repeat in new context", event_key="reopen", expected_revision=before["revision"])
    row = progress_task(conn, route, task["task_id"])
    assert row["state"] == "pending" and row["assessment"] is None and row["ready"]
    assert saved["id"] not in {a["id"] for a in learning.active_assessments(conn, target)["assessments"]}
    assert saved["id"] in {a["id"] for a in learning_store.list_assessments(conn)}


@pytest.mark.parametrize("changed", ["catalog", "job"])
def test_source_version_change_stales_route_and_blocks_writes(prepared, changed):
    conn, target, _, _, bundle, _ = prepared
    route = learning.create_route(conn, target["target_id"])
    if changed == "catalog":
        source = bundle / "skills/snapshots/framework.txt"
        source.write_text("Changed fictional task framework", encoding="utf-8")
    else:
        source = bundle / "companies/jobs.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["jobs"][0]["observed_at"] = "2026-01-02"
        source.write_text(json.dumps(payload), encoding="utf-8")
    import_bundle(conn, bundle)
    progress = learning.read_progress(conn, route["route_id"])
    assert progress["stale"] and progress["diagnosis_refresh_needed"]
    assert any(changed in reason for reason in progress["stale_reasons"])
    task = route["tasks"][0]
    with pytest.raises(InputChangedError, match="route_stale"):
        learning.record_event(conn, route["route_id"], task["task_id"], event_type="started",
                              reason="Fictional stale start", event_key="stale-start", expected_revision=0)
    assert learning_store.list_events(conn, route["route_id"]) == []


def test_pass_reopen_and_rediagnose_has_consistent_cache_identity(prepared, tmp_path):
    conn, target, *_ = prepared
    route = learning.create_route(conn, target["target_id"])
    task = route["tasks"][0]
    registry, _, mid = artifact_registry(tmp_path)
    submit_review(conn, route, task["task_id"], registry, mid, key="reopened-material")
    learning.record_event(conn, route["route_id"], task["task_id"], event_type="reopened",
                          reason="Check again", event_key="reopen-material", expected_revision=2)
    result = learning.rediagnose(conn, route["route_id"])
    assert result["diagnosis"]["assessment_context"]["active_ids"] == []
    assert result["diagnosis"]["assessment_context"]["excluded"]
    again = learning.rediagnose(conn, result["route"]["route_id"])
    assert again["diagnosis"]["diagnosis_id"] == result["diagnosis"]["diagnosis_id"]
    assert again["route"]["route_id"] == result["route"]["route_id"]


@pytest.mark.parametrize("reset_event", ["reopened", "skipped"])
def test_child_reset_revokes_inherited_pass_and_invalidates_diagnosis(prepared, tmp_path, reset_event):
    conn, target, *_ = prepared
    parent = learning.create_route(conn, target["target_id"])
    task = next(t for t in parent["tasks"] if t["kind"] == "evidence_collection")
    registry, _, mid = artifact_registry(tmp_path)
    _, _, original = submit_review(conn, parent, task["task_id"], registry, mid, key="parent-pass")
    child = learning.rediagnose(conn, parent["route_id"])["route"]
    assert progress_task(conn, child, task["task_id"])["state"] == "passed"
    before = learning.active_assessments(conn, target)
    learning.record_event(conn, child["route_id"], task["task_id"], event_type=reset_event,
                          reason="Recheck inherited evidence", event_key="child-reset", expected_revision=0)
    after = learning.active_assessments(conn, target)
    assert original["assessment_id"] not in {r["assessment_id"] for r in after["assessments"]}
    assert after["fingerprint"] != before["fingerprint"]
    assert service.read_diagnosis(conn, child["diagnosis_id"])["stale"]
    refreshed = learning.rediagnose(conn, child["route_id"])
    assert refreshed["diagnosis"]["diagnosis_id"] != child["diagnosis_id"]
    assert progress_task(conn, refreshed["route"], task["task_id"])["state"] == "pending"
    assert any(r["reason"] == "reset_in_child_route" for r in after["excluded"])
    assert learning_store.list_assessments(conn, route_id=parent["route_id"])[0]["assessment_id"] == original["assessment_id"]


def test_child_review_then_reopen_does_not_resurrect_ancestor(prepared, tmp_path):
    conn, target, *_ = prepared
    parent = learning.create_route(conn, target["target_id"])
    task = next(t for t in parent["tasks"] if t["kind"] == "evidence_collection")
    registry, _, mid = artifact_registry(tmp_path)
    submit_review(conn, parent, task["task_id"], registry, mid, key="ancestor")
    child = learning.rediagnose(conn, parent["route_id"])["route"]
    learning.record_event(conn, child["route_id"], task["task_id"], event_type="reopened",
                          reason="New attempt", event_key="child-first-reopen", expected_revision=0)
    _, _, current = submit_review(conn, child, task["task_id"], registry, mid, key="child")
    assert [a["assessment_id"] for a in learning.active_assessments(conn, target)["assessments"]] == [current["assessment_id"]]
    learning.record_event(conn, child["route_id"], task["task_id"], event_type="reopened",
                          reason="Repeat check", event_key="child-second-reopen", expected_revision=3)
    assert learning.active_assessments(conn, target)["assessments"] == []
    refreshed = learning.rediagnose(conn, child["route_id"])
    assert progress_task(conn, refreshed["route"], task["task_id"])["state"] == "pending"


def test_child_reset_does_not_revoke_a_later_fresh_parent_review(prepared, tmp_path):
    conn, target, *_ = prepared
    parent = learning.create_route(conn, target["target_id"])
    task = next(t for t in parent["tasks"] if t["kind"] == "evidence_collection")
    registry, _, mid = artifact_registry(tmp_path)
    submit_review(conn, parent, task["task_id"], registry, mid, key="original-parent")
    child = learning.rediagnose(conn, parent["route_id"])["route"]
    learning.record_event(conn, child["route_id"], task["task_id"], event_type="reopened",
                          reason="Request another observation", event_key="child-request", expected_revision=0)
    _, _, fresh = submit_review(conn, parent, task["task_id"], registry, mid, key="fresh-parent")
    assert [a["assessment_id"] for a in learning.active_assessments(conn, target)["assessments"]] == [fresh["assessment_id"]]
    row = progress_task(conn, parent, task["task_id"])
    assert row["state"] == "passed" and row["assessment_current"]


def test_qualification_artifact_review_does_not_close_or_inherit_pass(prepared, tmp_path):
    conn, target, *_ = prepared
    route = learning.create_route(conn, target["target_id"])
    revised = deepcopy(route)
    revised.pop("route_id")
    revised["tasks"][0]["kind"] = "qualification_review"
    route = learning_store.save_route(conn, revised)
    task = route["tasks"][0]
    registry, _, mid = artifact_registry(tmp_path)
    submit_review(conn, route, task["task_id"], registry, mid, key="qualification")
    assert progress_task(conn, route, task["task_id"])["state"] == "completion_reported"
    child = learning_store.save_route(conn, revised, parent_route_id=route["route_id"])
    assert progress_task(conn, child, task["task_id"])["state"] == "pending"


def test_nested_supplements_recheck_all_original_files(prepared, tmp_path):
    conn, target, *_ = prepared
    route = learning.create_route(conn, target["target_id"])
    first_registry, first_source, _ = artifact_registry(tmp_path, label="first")
    a = learning.supplement_materials(conn, target["target_id"], first_registry,
                                      expected_revision=1, parent_route_id=route["route_id"])
    second_registry, _, _ = artifact_registry(tmp_path, label="second")
    b = learning.supplement_materials(conn, target["target_id"], second_registry,
                                      expected_revision=2, parent_route_id=a["route"]["route_id"])
    assert not b["progress"]["stale"]
    first_source.write_text(first_source.read_text(encoding="utf-8") + "Changed original\n", encoding="utf-8")
    assert learning.read_progress(conn, b["route"]["route_id"])["stale"]
    with pytest.raises(ValueError, match="input_changed"):
        learning.rediagnose(conn, b["route"]["route_id"])


def test_assessment_change_during_diagnosis_is_not_published(prepared, tmp_path, monkeypatch):
    from applypilot.career import diagnosis

    conn, target, *_ = prepared
    route = learning.create_route(conn, target["target_id"])
    task = route["tasks"][0]
    registry, _, mid = artifact_registry(tmp_path)
    submit_review(conn, route, task["task_id"], registry, mid, key="race")
    count = len(store.list_diagnoses(conn, target["target_id"]))
    original = diagnosis.build_diagnosis

    def reopen_during_calculation(**kwargs):
        result = original(**kwargs)
        learning.record_event(conn, route["route_id"], task["task_id"], event_type="reopened",
                              reason="Concurrent input change", event_key="race-reopen", expected_revision=2)
        return result

    monkeypatch.setattr(diagnosis, "build_diagnosis", reopen_during_calculation)
    with pytest.raises(ValueError, match="assessments changed during computation"):
        service.diagnose(conn, target["target_id"], recompute=True)
    assert len(store.list_diagnoses(conn, target["target_id"])) == count
