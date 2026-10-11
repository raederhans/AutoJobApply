"""Pure, quoted-material assessment validation; no execution or remote fetching."""

from __future__ import annotations

from copy import deepcopy

from applypilot.career.contracts import required_text
from applypilot.career.evidence import digest

ASSESSMENT_VERSION = "career-assessment-1"
ASSESSMENT_SCHEMA = "career-assessment-v1"
METHODS = {"artifact_review", "self_task_review", "independent_task_review", "runtime_check"}


def _quote(ref: dict, materials: dict[str, dict]) -> dict:
    if not isinstance(ref, dict):
        raise TypeError("assessment_reference_invalid")
    material = materials.get(ref.get("material_id"))
    quote = required_text(ref.get("quote"), "assessment quote").replace("\r\n", "\n")
    if material is None:
        raise ValueError("assessment_unknown_material")
    if ref.get("snapshot_digest") is not None and ref["snapshot_digest"] != material["snapshot_digest"]:
        raise ValueError("assessment_reference_snapshot_changed")
    start, end = ref.get("line_start"), ref.get("line_end")
    text = material["text"]
    if start is not None or end is not None:
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            raise ValueError("assessment_line_range_invalid")
        lines = text.splitlines()
        if end > len(lines):
            raise ValueError("assessment_line_range_invalid")
        text = "\n".join(lines[start - 1:end])
    if quote not in text:
        raise ValueError("assessment_quote_not_found")
    return {"material_id": material["id"], "quote": quote, "line_start": start,
            "line_end": end, "snapshot_digest": material["snapshot_digest"]}


def validate_review(*, task: dict, review: dict, artifact: dict) -> dict:
    """Validate observations against the exact submitted evidence snapshot.

    A passed result is scoped to this task, method and subject. It never supplies
    a global skill level. Self assessments retain their explicit method label.
    """
    if not all(isinstance(value, dict) for value in (task, review, artifact)):
        raise TypeError("task, review and artifact must be objects")
    required_text(task.get("task_id"), "task_id")
    task_bindings = {"task_id": task["task_id"], **{
        key: task[key] for key in ("anchor_id", "skill_id", "requirement_ids") if key in task},
        **({"task_kind": task["kind"]} if "kind" in task else {})}
    for key, value in task_bindings.items():
        if key in review and review[key] != value:
            raise ValueError(f"assessment_task_binding_changed: {key}")
    criteria = task.get("acceptance")
    if (not isinstance(criteria, list) or not criteria
            or any(not isinstance(c, str) or not c.strip() for c in criteria)
            or len(set(criteria)) != len(criteria)):
        raise ValueError("assessment_task_acceptance_invalid")
    if artifact.get("schema_version") != "career-evidence-1":
        raise ValueError("assessment_requires_loaded_evidence")
    fingerprint = required_text(artifact.get("fingerprint"), "artifact fingerprint")
    core = {k: v for k, v in artifact.items() if k not in {"fingerprint", "selection"}}
    if digest(core) != fingerprint:
        raise ValueError("assessment_artifact_fingerprint_changed")
    materials = {}
    for material in artifact.get("materials", []):
        if not isinstance(material, dict):
            raise TypeError("assessment_material_invalid")
        mid = required_text(material.get("id"), "material_id")
        if mid in materials:
            raise ValueError("assessment_duplicate_material")
        text = required_text(material.get("text"), "material text")
        if material.get("snapshot_digest") != digest(text):
            raise ValueError("assessment_material_changed")
        materials[mid] = material
    if not materials or not artifact.get("evidence"):
        raise ValueError("assessment_requires_loaded_evidence")
    for record in artifact["evidence"]:
        if not isinstance(record, dict) or record.get("source_checked") is not True:
            raise ValueError("assessment_requires_source_checked_evidence")
        refs = record.get("source_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError("assessment_evidence_reference_missing")
        for ref in refs:
            _quote(ref, materials)

    method = review.get("method")
    if method not in METHODS:
        raise ValueError("assessment_method_invalid")
    reviewer = required_text(review.get("reviewer"), "reviewer")
    subject = review.get("subject")
    if subject not in {"artifact", "candidate"}:
        raise ValueError("assessment_subject_invalid")
    if method == "artifact_review" and subject != "artifact":
        raise ValueError("artifact_review_requires_artifact_subject")
    if (subject == "candidate" and reviewer.casefold().strip() in {"assistant", "ai", "codex"}):
        raise ValueError("assistant_review_requires_artifact_subject")
    if method == "independent_task_review" and reviewer.casefold().strip() in {
        "self", "candidate", "me", "myself", "assistant", "ai", "codex", "unknown", "anonymous",
    }:
        raise ValueError("independent_review_requires_named_reviewer")
    required_text(review.get("context"), "assessment context")
    required_text(review.get("assistance"), "assessment assistance")
    rows = review.get("acceptance_results")
    if not isinstance(rows, list):
        raise TypeError("assessment_acceptance_results_required")
    observed = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("assessment_acceptance_result_invalid")
        criterion = row.get("criterion")
        if criterion not in criteria or criterion in observed:
            raise ValueError("assessment_unknown_or_duplicate_criterion")
        status = row.get("status")
        if status not in {"pass", "fail", "not_observed"}:
            raise ValueError("assessment_observation_status_invalid")
        refs = row.get("evidence_refs")
        if not isinstance(refs, list) or (status != "not_observed" and not refs):
            raise ValueError("assessment_observation_requires_evidence")
        observed[criterion] = {**deepcopy(row), "evidence_refs": [_quote(r, materials) for r in refs]}
    if set(observed) != set(criteria):
        raise ValueError("assessment_missing_criterion")
    statuses = [observed[c]["status"] for c in criteria]
    outcome = "failed" if "fail" in statuses else "passed" if all(s == "pass" for s in statuses) else "pending"
    return {**deepcopy(review), **deepcopy(task_bindings), "acceptance_results": [observed[c] for c in criteria],
            "outcome": outcome, "artifact_fingerprint": fingerprint,
            "schema_version": ASSESSMENT_SCHEMA, "assessment_version": ASSESSMENT_VERSION,
            "measured_skill_levels": 0}
