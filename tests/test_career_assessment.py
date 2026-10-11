"""Fictional submitted materials only; assessments never execute content."""

from __future__ import annotations

from copy import deepcopy

import pytest
from test_career_evidence_model import evidence_registry

from applypilot.career.assessment import validate_review
from applypilot.career.evidence import digest, load_evidence


@pytest.fixture
def inputs(tmp_path):
    registry, _, _ = evidence_registry(tmp_path)
    artifact = load_evidence(registry)
    task = {"task_id": "fictional-task", "acceptance": ["Names the input", "Explains the output"]}
    ref = {"material_id": "m1", "quote": "I built a Python report.", "line_start": 2, "line_end": 2}
    review = {"method": "artifact_review", "reviewer": "assistant", "subject": "artifact",
              "context": "Review of a fictional report note", "assistance": "Assistant inspected existing note",
              "limits": ["Does not establish candidate proficiency"],
              "acceptance_results": [{"criterion": c, "status": "pass", "evidence_refs": [deepcopy(ref)]}
                                     for c in task["acceptance"]]}
    return task, review, artifact


def test_artifact_pass_preserves_scope_and_does_not_mutate(inputs):
    task, review, artifact = inputs
    original = deepcopy(review)
    result = validate_review(task=task, review=review, artifact=artifact)
    assert result["outcome"] == "passed"
    assert result["subject"] == "artifact" and result["limits"] == original["limits"]
    assert result["assistance"] == original["assistance"] and result["measured_skill_levels"] == 0
    assert result["artifact_fingerprint"] == artifact["fingerprint"]
    assert result["schema_version"] == "career-assessment-v1"
    assert result["acceptance_results"][0]["evidence_refs"][0]["snapshot_digest"]
    assert review == original


@pytest.mark.parametrize("status,outcome", [("not_observed", "pending"), ("fail", "failed")])
def test_done_or_course_completion_cannot_replace_observation(inputs, status, outcome):
    task, review, artifact = inputs
    review.update(status="marked_done", completed_courses=["fictional-course"], outcome="passed")
    review["acceptance_results"][1]["status"] = status
    if status == "not_observed":
        review["acceptance_results"][1]["evidence_refs"] = []
    assert validate_review(task=task, review=review, artifact=artifact)["outcome"] == outcome


@pytest.mark.parametrize("mutation,error", [
    (lambda r: r["acceptance_results"].pop(), "missing_criterion"),
    (lambda r: r["acceptance_results"].append(deepcopy(r["acceptance_results"][0])), "duplicate_criterion"),
    (lambda r: r["acceptance_results"][0].update(criterion="Invented criterion"), "unknown_or_duplicate"),
    (lambda r: r["acceptance_results"][0].update(status="completed"), "status_invalid"),
    (lambda r: r["acceptance_results"][0].update(evidence_refs=[]), "requires_evidence"),
    (lambda r: r["acceptance_results"][0]["evidence_refs"][0].update(material_id="absent"), "unknown_material"),
    (lambda r: r["acceptance_results"][0]["evidence_refs"][0].update(quote="Expert in Rust"), "quote_not_found"),
    (lambda r: r["acceptance_results"][0]["evidence_refs"][0].update(line_start=1, line_end=1), "quote_not_found"),
    (lambda r: r["acceptance_results"][0]["evidence_refs"][0].update(line_end=True), "range_invalid"),
    (lambda r: r["acceptance_results"][0]["evidence_refs"][0].update(snapshot_digest="changed"), "snapshot_changed"),
])
def test_unknown_criteria_and_unbacked_observations_rejected(inputs, mutation, error):
    task, review, artifact = inputs
    mutation(review)
    with pytest.raises(ValueError, match=error):
        validate_review(task=task, review=review, artifact=artifact)


def test_snapshot_tampering_rejected_even_if_quote_survives(inputs):
    task, review, artifact = inputs
    artifact["materials"][0]["text"] += "Tampered extra claim.\n"
    with pytest.raises(ValueError, match="fingerprint_changed"):
        validate_review(task=task, review=review, artifact=artifact)
    artifact["fingerprint"] = digest({k: v for k, v in artifact.items() if k not in {"fingerprint", "selection"}})
    with pytest.raises(ValueError, match="material_changed"):
        validate_review(task=task, review=review, artifact=artifact)


def test_self_and_named_independent_reviews_remain_distinct(inputs):
    task, review, artifact = inputs
    review.update(method="self_task_review", reviewer="Fictional candidate", subject="candidate",
                  assistance="No assistance recorded")
    result = validate_review(task=task, review=review, artifact=artifact)
    assert result["method"] == "self_task_review" and result["subject"] == "candidate"
    review.update(method="independent_task_review", reviewer="self")
    with pytest.raises(ValueError, match="named_reviewer"):
        validate_review(task=task, review=review, artifact=artifact)
    review["reviewer"] = "Fictional reviewer Mira"
    assert validate_review(task=task, review=review, artifact=artifact)["method"] == "independent_task_review"


def test_assistant_or_artifact_review_cannot_claim_candidate_proficiency(inputs):
    task, review, artifact = inputs
    review["subject"] = "candidate"
    with pytest.raises(ValueError, match="artifact_subject"):
        validate_review(task=task, review=review, artifact=artifact)
    review["method"] = "runtime_check"
    with pytest.raises(ValueError, match="assistant_review"):
        validate_review(task=task, review=review, artifact=artifact)


def test_arbitrary_uri_is_not_a_submission(inputs):
    task, review, _ = inputs
    with pytest.raises(ValueError, match="loaded_evidence"):
        validate_review(task=task, review=review, artifact={"uri": "https://fictional.example/script.py"})


def test_review_cannot_rebind_task_or_requirement(inputs):
    task, review, artifact = inputs
    task["requirement_ids"] = ["fictional-requirement"]
    review["requirement_ids"] = ["different-requirement"]
    with pytest.raises(ValueError, match="task_binding_changed"):
        validate_review(task=task, review=review, artifact=artifact)
