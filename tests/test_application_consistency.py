"""Controlled source/review invariants; no model, employer or private workspace."""
from copy import deepcopy

import pytest

from applypilot.apply.application_consistency import build_contract, check_contract, digest

JOB = {"url": "https://boards.greenhouse.io/example/jobs/123", "application_url": "https://boards.greenhouse.io/example/jobs/123"}
PROFILE = {"personal": {"email": "test@example.test", "phone": "+6512345678", "city": "Example City"},
           "education": [{"institution": "Example University", "start_date": "2020-09"}],
           "experience": {"current_company": "Example Corp"},
           "resume_facts": {"preserved_projects": ["Example Project"]}}
MATERIALS = {"materials": [{"kind": "resume", "sha256": "a" * 64, "size": 123}]}


def field(key="email", label="Email", value="test@example.test", record=None):
    path = [{"key": "record-1", "label": record, "label_status": "identified", "dom_identity": 22}] if record else []
    return {"field_key": key, "value": value, "control": "text", "value_source": "live_dom_snapshot", "invalid": False,
            "record_path": path, "semantic": {"identification": "identified"},
            "application_question": {"text": label, "help_text": "", "constraints": [], "section_path": [record] if record else [],
                                     "language": "en", "completeness": "known"}}


def form(fields=None):
    return {"page_url": JOB["url"], "fields": fields if fields is not None else [field()],
            "coverage": {"iframe_count": 0}, "question_coverage": {"fields_truncated": False}}


def binding(key="email", path=None):
    return {"field_key": key, "source": {"kind": "profile", "path": path or ["personal", "email"]}}


def contract():
    return build_contract([binding()], form(), PROFILE, JOB, MATERIALS)


def test_consistency_retains_only_sources_and_digests():
    c = contract()
    assert c["raw_values_stored"] is False and c["submit_authority"] is False
    assert "test@example.test" not in str(c)
    result = check_contract(c, {"form_state": form()}, PROFILE, JOB, MATERIALS)
    assert result["status"] == "consistent"
    assert result["checked"] == 1 and result["whole_application_verified"] is False


@pytest.mark.parametrize("change,code", [
    (lambda f: f.update(value="stale@example.test"), "bound_value_mismatch"),
    (lambda f: f.update(invalid=True), "source_or_observation_unavailable"),
    (lambda f: f.update(value_source="unavailable"), "source_or_observation_unavailable"),
    (lambda f: f["application_question"].update(text="Employer email"), "question_or_record_changed"),
    (lambda f: f["application_question"].update(help_text="New instructions"), "question_or_record_changed"),
    (lambda f: f["application_question"].update(constraints=[{"max": 3}]), "question_or_record_changed"),
    (lambda f: f["semantic"].update(identification="ambiguous"), "field_semantics_unproven"),
    (lambda f: f.update(record_path=[{"label": "Another record"}]), "question_or_record_changed"),
])
def test_current_field_drift_is_blocked(change, code):
    current = form()
    change(current["fields"][0])
    result = check_contract(contract(), {"form_state": current}, PROFILE, JOB, MATERIALS)
    assert result["status"] == "blocked" and result["issues"][0]["code"] == code
    assert "stale@example.test" not in str(result)


def test_parser_overwrite_on_non_active_bound_field():
    fields = [field(), field("city", "City", "Example City")]
    c = build_contract([binding(), binding("city", ["personal", "city"])], form(fields), PROFILE, JOB, MATERIALS)
    fields[0]["value"] = "old@example.test"
    result = check_contract(c, {"form_state": form(fields)}, PROFILE, JOB, MATERIALS)
    assert result["checked"] == 2
    assert [i["code"] for i in result["issues"]] == ["bound_value_mismatch"]


def test_profile_revision_does_not_silently_refresh_expected_value():
    profile = deepcopy(PROFILE)
    profile["personal"]["email"] = "new@example.test"
    assert check_contract(contract(), {"form_state": form()}, profile, JOB, MATERIALS)["issues"][0]["code"] == "source_revision_changed"


def test_material_revision_invalidates_cross_step_contract():
    changed = deepcopy(MATERIALS)
    changed["materials"][0]["sha256"] = "b" * 64
    assert check_contract(contract(), {"form_state": form()}, PROFILE, JOB, changed)["issues"][0]["code"] == "material_revision_changed"


@pytest.mark.parametrize("path", [["personal", "password"], ["availability", "earliest_start_date"],
    ["career", "claimed_skill"], ["education", True, "institution"], ["personal", "missing"]])
def test_reject_unapproved_or_missing_profile_authority(path):
    with pytest.raises(ValueError):
        build_contract([binding(path=path)], form(), PROFILE, JOB, MATERIALS)


def test_caller_cannot_supply_expected_answers():
    forged = binding()
    forged["source"]["expected"] = "whatever the page contains"
    with pytest.raises(ValueError, match="source shape"):
        build_contract([forged], form(), PROFILE, JOB, MATERIALS)


@pytest.mark.parametrize("path,record", [(["resume_facts", "preserved_projects", 0], "Employment"),
    (["education", 0, "institution"], "Employment"), (["experience", "current_company"], "Projects")])
def test_project_education_and_employment_sources_cannot_be_relabelled(path, record):
    with pytest.raises(ValueError, match="source_record_kind_mismatch"):
        build_contract([binding("entry", path)], form([field("entry", "Name", "anything", record)]), PROFILE, JOB, MATERIALS)


@pytest.mark.parametrize("path,record,value", [(["resume_facts", "preserved_projects", 0], "Projects", "Example Project"),
    (["education", 0, "institution"], "Education", "Example University"),
    (["experience", "current_company"], "Employment", "Example Corp"),
    (["education", 0, "start_date"], "Education", "2020-09")])
def test_typed_record_sources_pass_only_the_corresponding_observed_record(path, record, value):
    f = form([field("entry", "Name", value, record)])
    c = build_contract([binding("entry", path)], f, PROFILE, JOB, MATERIALS)
    assert check_contract(c, {"form_state": f}, PROFILE, JOB, MATERIALS)["status"] == "consistent"


def review_entry(c, **overrides):
    item = c["items"][0]
    return {"claim_id": item["claim_id"], "question_digest": item["question_digest"],
            "record_kind": item["record_kind"], "value": "test@example.test", **overrides}


def test_final_review_can_use_explicit_host_bound_projection_not_absence_of_field():
    c = contract()
    assert check_contract(c, {"review_entries": [review_entry(c)]}, PROFILE, JOB, MATERIALS)["status"] == "consistent"
    assert check_contract(c, {"review_entries": []}, PROFILE, JOB, MATERIALS)["issues"][0]["code"] == "bound_field_not_observed"


@pytest.mark.parametrize("change,code", [({"value": "wrong@example.test"}, "review_value_mismatch"),
    ({"record_kind": "employment"}, "review_binding_mismatch"), ({"question_digest": "old"}, "review_binding_mismatch")])
def test_final_review_contradictions_are_not_accepted(change, code):
    c = contract()
    result = check_contract(c, {"review_entries": [review_entry(c, **change)]}, PROFILE, JOB, MATERIALS)
    assert result["issues"][0]["code"] == code


def test_present_correct_edit_field_cannot_hide_wrong_review_text():
    c = contract()
    result = check_contract(c, {"form_state": form(), "review_entries": [review_entry(c, value="wrong")]}, PROFILE, JOB, MATERIALS)
    assert result["issues"][0]["code"] == "review_value_mismatch"


def test_duplicate_and_unbound_review_entries_are_rejected():
    c = contract()
    for entries in ([review_entry(c)] * 2, [review_entry(c, claim_id="another")]):
        with pytest.raises(ValueError):
            check_contract(c, {"review_entries": entries}, PROFILE, JOB, MATERIALS)


def material_contract():
    f = field("resume", "Resume", "")
    f.update(control="file", semantic={"identification": "identified", "attachment": {"status": "identified", "purpose": "resume"}})
    c = build_contract([{"field_key": "resume", "source": {"kind": "material", "material_kind": "resume"}}], form([f]), PROFILE, JOB, MATERIALS)
    evidence = {"claim_id": c["items"][0]["claim_id"], "purpose": "resume", "material_sha256": "a" * 64,
                "size": 123, "acceptance_marker": "attachment_card"}
    return c, f, evidence


def test_file_picker_selection_is_not_attachment_acceptance():
    c, f, evidence = material_contract()
    f["files"] = [{"name": "resume.pdf", "size": 123}]
    assert check_contract(c, {"form_state": form([f])}, PROFILE, JOB, MATERIALS)["issues"][0]["code"] == "attachment_acceptance_unproven"
    assert check_contract(c, {"form_state": form([f]), "attachments": [evidence]}, PROFILE, JOB, MATERIALS)["status"] == "consistent"


@pytest.mark.parametrize("changed", [{"purpose": "cover_letter"}, {"material_sha256": "b" * 64}, {"size": 124}, {"size": True},
                                      {"acceptance_marker": "file_selection_done"}])
def test_wrong_attachment_purpose_revision_or_acceptance_blocks(changed):
    c, f, evidence = material_contract()
    result = check_contract(c, {"form_state": form([f]), "attachments": [{**evidence, **changed}]}, PROFILE, JOB, MATERIALS)
    assert result["status"] == "blocked"


def test_swapped_upload_control_purpose_is_not_ignored():
    c, f, evidence = material_contract()
    f["semantic"]["attachment"]["purpose"] = "cover_letter"
    assert check_contract(c, {"form_state": form([f]), "attachments": [evidence]}, PROFILE, JOB, MATERIALS)["issues"][0]["code"] == "attachment_purpose_mismatch"


def test_phone_prefix_and_date_precision_are_not_removed_to_force_match():
    f = form([field("phone", "Phone", "12345678")])
    c = build_contract([binding("phone", ["personal", "phone"])], f, PROFILE, JOB, MATERIALS)
    assert check_contract(c, {"form_state": f}, PROFILE, JOB, MATERIALS)["status"] == "blocked"


def test_native_school_selection_checks_visible_option_not_internal_id():
    f = field("school", "School", "id-123", "Education")
    f.update(control="select", options=[{"value": "id-123", "label": "Example University"}])
    c = build_contract([binding("school", ["education", 0, "institution"])], form([f]), PROFILE, JOB, MATERIALS)
    assert check_contract(c, {"form_state": form([f])}, PROFILE, JOB, MATERIALS)["status"] == "consistent"


def test_combobox_query_does_not_prove_school_selection():
    f = field("school", "School", "Example University", "Education")
    f.update(control="combobox", options=[], selected_display=[])
    c = build_contract([binding("school", ["education", 0, "institution"])], form([f]), PROFILE, JOB, MATERIALS)
    assert check_contract(c, {"form_state": form([f])}, PROFILE, JOB, MATERIALS)["status"] == "blocked"
    f["selected_display"] = ["Example University"]
    assert check_contract(c, {"form_state": form([f])}, PROFILE, JOB, MATERIALS)["status"] == "consistent"


def writing_fixture(tmp_path, text="I would bring a careful approach."):
    from applypilot.writing_common import build_artifact, save_artifact, validate_draft
    task = {"job_id": JOB["url"], "question": {"text": "Why this role?", "help_text": ""}, "constraints": []}
    context = {"sources": [], "candidate_evidence": [], "role": {"job_id": JOB["url"]}}
    draft = {"text": text, "claims": [], "missing_facts": []}
    review = {"verdict": "pass", "issues": [], "unsupported_claims": [], "missed_parts": [], "voice_fit": "uncalibrated",
              "scores": {"relevance": 1, "specificity": 1, "naturalness": 1, "concision": 1}}
    validation = validate_draft(draft, context)
    artifact = build_artifact(genre="application_answer", task=task, context=context, draft=draft,
                              validation=validation, review=review)
    files = save_artifact(artifact, tmp_path)
    source = {"kind": "writing", "path": files["artifact_path"], "revision_id": artifact["revision_id"]}
    f = form([field("answer", "Why this role?", text)])
    return artifact, source, f


def test_writing_revision_reuses_existing_artifact_checks(tmp_path):
    artifact, source, f = writing_fixture(tmp_path)
    c = build_contract([{"field_key": "answer", "source": source}], f, PROFILE, JOB, MATERIALS)
    assert check_contract(c, {"form_state": f}, PROFILE, JOB, MATERIALS)["status"] == "consistent"
    assert artifact["text"] not in str(c)
    f["fields"][0]["value"] = "I already accomplished all of these things."
    assert check_contract(c, {"form_state": f}, PROFILE, JOB, MATERIALS)["status"] == "blocked"


@pytest.mark.parametrize("mutation", ["text", "context", "review", "job", "revision"])
def test_writing_tampering_or_wrong_job_never_becomes_a_fact(tmp_path, mutation):
    import json
    from pathlib import Path
    artifact, source, f = writing_fixture(tmp_path)
    if mutation == "text": artifact["text"] = "Different text"
    elif mutation == "context": artifact["context_snapshot"]["candidate_evidence"] = [{"id": "invented"}]
    elif mutation == "review": artifact["review"]["verdict"] = "revise"
    elif mutation == "job": artifact["task"]["job_id"] = "https://other.test/job"
    else: artifact["revision_id"] = "different"
    Path(source["path"]).write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError):
        build_contract([{"field_key": "answer", "source": source}], f, PROFILE, JOB, MATERIALS)


def test_writing_question_change_requires_new_review_binding(tmp_path):
    _, source, f = writing_fixture(tmp_path)
    f["fields"][0]["application_question"]["help_text"] = "Describe a past accomplishment instead."
    with pytest.raises(ValueError, match="question_revision"):
        build_contract([{"field_key": "answer", "source": source}], f, PROFILE, JOB, MATERIALS)


def test_writing_reviewed_flag_cannot_override_current_numeric_validation(tmp_path):
    import json
    from pathlib import Path
    artifact, source, f = writing_fixture(tmp_path, "I managed 987 employees.")
    artifact["status"] = "reviewed_draft"
    artifact["validation"]["passed"] = True
    Path(source["path"]).write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="deterministic"):
        build_contract([{"field_key": "answer", "source": source}], f, PROFILE, JOB, MATERIALS)


def test_fact_resolution_uses_current_scope_freshness_and_lineage():
    from datetime import UTC, datetime, timedelta
    profile = deepcopy(PROFILE)
    fact = {"fact_ref": "fact-city", "key": "city", "value": "Example City", "source": "user_confirmed",
            "scope": "global:*", "confirmed_at": (datetime.now(UTC)-timedelta(days=1)).isoformat(), "sensitivity": "low"}
    profile["application_facts"] = [fact]
    source = {"kind": "fact", "fact_ref": "fact-city", "scope": "global:*"}
    f = form([field("city", "City", "Example City")])
    c = build_contract([{"field_key": "city", "source": source}], f, profile, JOB, MATERIALS)
    assert check_contract(c, {"form_state": f}, profile, JOB, MATERIALS)["status"] == "consistent"
    profile["application_facts"].append({**fact, "fact_ref": "fact-city-2", "supersedes": ["fact-city"], "value": "Different City"})
    assert check_contract(c, {"form_state": f}, profile, JOB, MATERIALS)["status"] == "blocked"


@pytest.mark.parametrize("mutation", ["expired", "high", "wrong_scope"])
def test_fact_expired_sensitive_or_unrelated_scope_does_not_pass(mutation):
    from datetime import UTC, datetime, timedelta
    profile = deepcopy(PROFILE)
    fact = {"fact_ref": "fact-city", "key": "city", "value": "Example City", "source": "user_confirmed",
            "scope": "global:*", "confirmed_at": (datetime.now(UTC)-timedelta(days=2)).isoformat(), "sensitivity": "low"}
    source = {"kind": "fact", "fact_ref": "fact-city", "scope": "global:*"}
    if mutation == "expired": fact["expires_at"] = (datetime.now(UTC)-timedelta(days=1)).isoformat()
    elif mutation == "high": fact["sensitivity"] = "high"
    else: fact["scope"] = source["scope"] = "some-other-employer"
    profile["application_facts"] = [fact]
    with pytest.raises(ValueError):
        build_contract([{"field_key": "city", "source": source}], form([field("city", "City", "Example City")]), profile, JOB, MATERIALS)


@pytest.mark.parametrize("change", [{"question_digest": "old"}, {"record_kind": "employment"}])
def test_present_edit_field_cannot_hide_wrong_review_identity(change):
    c = contract()
    result = check_contract(c, {"form_state": form(), "review_entries": [review_entry(c, **change)]}, PROFILE, JOB, MATERIALS)
    assert result["issues"][0]["code"] == "review_binding_mismatch"


def test_attachment_record_drift_requires_rebinding():
    c, f, evidence = material_contract()
    f["record_path"] = [{"key": "new-record", "label": "Documents", "dom_identity": 99}]
    result = check_contract(c, {"form_state": form([f]), "attachments": [evidence]}, PROFILE, JOB, MATERIALS)
    assert result["issues"][0]["code"] == "question_or_record_changed"


def test_writing_new_native_limit_requires_current_task_revision(tmp_path):
    _, source, f = writing_fixture(tmp_path)
    f["fields"][0]["application_question"]["constraints"] = [{"kind": "max", "unit": "characters", "value": 3, "source": "native:maxlength"}]
    with pytest.raises(ValueError, match="question_revision"):
        build_contract([{"field_key": "answer", "source": source}], f, PROFILE, JOB, MATERIALS)
