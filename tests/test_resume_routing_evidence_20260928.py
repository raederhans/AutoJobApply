import json
from pathlib import Path

import pytest

from applypilot import resume_library
from applypilot.database import init_db
from applypilot.resume_versions import finish_resume_run, health_input_digest, start_resume_run


@pytest.mark.parametrize(("title", "description", "subtype"), [
    (
        "AI Product Intern",
        ("Interview pilot users, define workflows for an AI assistant, coordinate prototype delivery "
         "with engineers and turn usability findings into an iteration plan."),
        "product_management",
    ),
    (
        "Applied ML Research Intern",
        ("Design reproducible experiments across multiple events, evaluate out-of-sample transfer, "
         "quantify domain shift and state limits of predictive claims."),
        "ai_research",
    ),
])
def test_adjacent_titles_need_matching_duties(title, description, subtype):
    matched = resume_library.extract_job_profile({"title": title, "full_description": description})
    assert matched["subtype"] == subtype
    assert matched["confidence"] >= 0.8
    unrelated = resume_library.extract_job_profile({
        "title": title, "full_description": "Coordinate general office administration and scheduling."
    })
    assert unrelated["subtype"] != subtype


def test_generic_product_or_research_words_do_not_force_alias():
    for title in ("Product Intern", "Research Intern"):
        result = resume_library.extract_job_profile({
            "title": title,
            "full_description": "Interview users and design experiments for a general research product.",
        })
        assert result["subtype"] not in {"product_management", "ai_research"}


def test_applied_ml_research_alias_recognizes_experiments_and_generalization_validation():
    description = (
        "Run reproducible experiments and evaluate model generalization with "
        "train-fold preprocessing and leave-one-event-out validation."
    )
    alias = resume_library.extract_job_profile({
        "title": "Applied ML Research Intern", "full_description": description,
    })
    canonical = resume_library.extract_job_profile({
        "title": "AI Research Intern", "full_description": description,
    })
    assert alias["subtype"] == canonical["subtype"] == "ai_research"
    assert alias["confidence"] >= 0.8


@pytest.mark.parametrize("description", [
    "Research customer preferences and evaluate product options for an internal report.",
    "Evaluate general market trends and write a research summary for stakeholders.",
    "Run one reproducible experiment for a product survey and summarize the feedback.",
])
def test_applied_ml_research_alias_stays_unclassified_without_two_ml_duty_signals(description):
    result = resume_library.extract_job_profile({
        "title": "Applied ML Research Intern", "full_description": description,
    })
    assert result["subtype"] != "ai_research"


def _artifact(tmp_path: Path, metadata: dict) -> dict:
    text = tmp_path / "resume.txt"
    text.write_text("Candidate\nPROJECTS\nA real project\n", encoding="utf-8")
    return {"text_path": str(text), "kind": "base", "validation_status": "source_only",
            "metadata_json": json.dumps(metadata)}


def test_registered_supplemental_missing_or_unreadable_requires_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(resume_library, "validate_tailored_resume", lambda *args, **kwargs: {"errors": []})
    monkeypatch.setattr(resume_library, "current_profile_resume_fact_errors", lambda *args: [])
    supplemental = tmp_path / "supplemental.txt"
    supplemental.write_text("Supported claim", encoding="utf-8")
    artifact = _artifact(tmp_path, {"supplemental_evidence_path": str(supplemental)})
    first_digest = health_input_digest(artifact, {})
    assert resume_library.assess_resume_artifact_health(artifact, {})["status"] == "eligible"
    supplemental.unlink()
    assert health_input_digest(artifact, {}) != first_digest
    missing = resume_library.assess_resume_artifact_health(artifact, {})
    assert missing["status"] == "repair_required"
    assert any("supplemental evidence" in reason for reason in missing["reasons"])
    supplemental.write_bytes(b"\xff")
    unreadable = resume_library.assess_resume_artifact_health(artifact, {})
    assert unreadable["status"] == "repair_required"
    assert any("supplemental evidence" in reason for reason in unreadable["reasons"])


def test_adopted_live_fact_source_tracks_change_and_legacy_is_unbound(tmp_path, monkeypatch):
    monkeypatch.setattr(resume_library, "validate_tailored_resume", lambda *args, **kwargs: {"errors": []})
    monkeypatch.setattr(resume_library, "current_profile_resume_fact_errors", lambda *args: [])
    source = tmp_path / "facts.md"
    source.write_text("Adopted project fact", encoding="utf-8")
    run = start_resume_run(tmp_path, {"url": "https://example.test/job"}, kind="tailoring")
    report_path = finish_resume_run(
        run, {"status": "machine_validated"},
        evidence_sources=[{"path": str(source), "text": source.read_text(encoding="utf-8")}],
    )
    generation = json.loads((run / "generation.json").read_text(encoding="utf-8"))
    bindings = generation["evidence_source_bindings"]
    assert len(bindings) == 1
    assert json.loads(report_path.read_text(encoding="utf-8"))["generation_record"] == str(run / "generation.json")
    artifact = _artifact(tmp_path, {"evidence_source_bindings": bindings})
    first = resume_library.assess_resume_artifact_health(artifact, {})
    assert first["status"] == "eligible"
    assert first["metrics"]["evidence_binding_state"] == "bound"
    source.write_text("Corrected project fact", encoding="utf-8")
    changed = resume_library.assess_resume_artifact_health(artifact, {})
    assert changed["status"] == "repair_required"
    assert any("Bound fact source changed" in reason for reason in changed["reasons"])
    assert health_input_digest(artifact, {}) != first["metrics"]["input_digest"]
    legacy = resume_library.assess_resume_artifact_health(_artifact(tmp_path, {}), {})
    assert legacy["metrics"]["evidence_binding_state"] == "unbound_legacy"


def test_registration_carries_explicit_fact_bindings(tmp_path, monkeypatch):
    monkeypatch.setattr(resume_library, "validate_tailored_resume", lambda *args, **kwargs: {"errors": []})
    monkeypatch.setattr(resume_library, "current_profile_resume_fact_errors", lambda *args: [])
    conn = init_db(tmp_path / "jobs.db")
    source = tmp_path / "source.txt"
    source.write_text("Verified source", encoding="utf-8")
    facts = tmp_path / "facts.md"
    facts.write_text("Adopted detail", encoding="utf-8")
    text = tmp_path / "tailored_resumes" / "resume.txt"
    text.parent.mkdir()
    text.write_text("Candidate\nPROJECTS\nAdopted detail", encoding="utf-8")
    text.with_suffix(".pdf").write_bytes(b"test-pdf")
    run = start_resume_run(tmp_path, {"url": "https://example.test/job"}, kind="tailoring")
    report = finish_resume_run(
        run, {"status": "machine_validated"},
        evidence_sources=[{"path": str(facts), "text": facts.read_text(encoding="utf-8")}],
    )
    result = resume_library.register_tailored_artifact(
        conn, job={"url": "https://example.test/job", "title": "Data Analyst",
                   "full_description": "Build data dashboards."}, text_path=text,
        source_resume_path=str(source), report_path=str(report), profile={},
    )
    row = conn.execute("SELECT metadata_json FROM resume_artifacts WHERE artifact_id=?",
                       (result["artifact_id"],)).fetchone()
    bindings = json.loads(row["metadata_json"])["evidence_source_bindings"]
    assert bindings == json.loads((run / "generation.json").read_text(encoding="utf-8"))["evidence_source_bindings"]
