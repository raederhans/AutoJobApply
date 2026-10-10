from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from applypilot.writing_context import load_context, select_context, validate_context


def test_company_facts_can_use_only_the_exact_role_jd(context):
    context["company"]["facts"].append({"id": "jd-company", "source_id": "jd",
        "quote": "Analyst at Example.", "verification_status": "verified"})
    result = select_context(context, query="Python reporting")
    assert "jd-company" in {item["id"] for item in result["company"]["facts"]}
    context["sources"].append({"id": "other-jd", "kind": "jd", "text": "Analyst at Example."})
    context["company"]["facts"][-1]["source_id"] = "other-jd"
    with pytest.raises(ValueError, match="exact JD"):
        validate_context(context)


def test_browser_language_region_can_use_same_language_voice(context):
    result = select_context(context, query="Python reporting", language="en-SG")
    assert [example["id"] for example in result["voice_examples"]] == ["user"]


@pytest.fixture
def context() -> dict:
    return {
        "schema_version": 1,
        "sources": [
            {"id": "candidate", "kind": "candidate_facts", "text": "Built Python reporting. Pending approval.",
             "boundaries": ["Do not claim employer deployment."]},
            {"id": "project", "kind": "project_evidence", "text": "Developed Python data validation. 数据分析流程。"},
            {"id": "resume", "kind": "resume", "text": "Led a Python department."},
            {"id": "jd", "kind": "jd", "text": "Analyst at Example. Use Python. Validate data."},
            {"id": "company", "kind": "company", "text": "We deliver client projects. Team of 12."},
            {"id": "voice", "kind": "voice", "text": "I prefer a concrete Python example. I led 500 staff."},
            {"id": "generated", "kind": "generated", "text": "Achieved Python leadership."},
        ],
        "candidate_evidence": [
            {"id": "python", "source_id": "candidate", "quote": "Built Python reporting.", "status": "confirmed",
             "boundaries": ["Prototype only."], "tags": ["reporting"]},
            {"id": "validation", "source_id": "project", "quote": "Developed Python data validation.",
             "status": "confirmed"},
            {"id": "pending", "source_id": "candidate", "quote": "Pending approval.", "status": "conditional",
             "tags": ["Python"]},
            {"id": "resume-claim", "source_id": "resume", "quote": "Led a Python department.", "status": "confirmed"},
            {"id": "generated-claim", "source_id": "generated", "quote": "Achieved Python leadership.",
             "status": "generated"},
        ],
        "role": {"job_id": "job-1", "title": "Analyst", "company_name": "Example", "jd_source_id": "jd",
                 "requirements": [{"id": "python", "source_id": "jd", "quote": "Use Python."}],
                 "responsibilities": [{"id": "data", "source_id": "jd", "quote": "Validate data."}]},
        "company": {"name": "Example", "facts": [
            {"id": "delivery", "source_id": "company", "quote": "We deliver client projects.",
             "verification_status": "verified"},
            {"id": "scale", "source_id": "company", "quote": "Team of 12.", "verification_status": "unverified"},
        ], "delivery_model": {"value": "client_projects", "evidence_ids": ["delivery"]},
            "scale": {"value": "small", "evidence_ids": ["scale"]}},
        "voice_examples": [
            {"id": "user", "source_id": "voice", "quote": "I prefer a concrete Python example.", "language": "en",
             "genre": "application_answer", "authorship": "user", "user_approved": False},
            {"id": "assistant", "source_id": "voice", "quote": "I led 500 staff.", "language": "en",
             "genre": "application_answer", "authorship": "assistant", "user_approved": False},
        ],
    }


def test_selection_preserves_authority_boundaries_and_unknowns(context: dict) -> None:
    original = copy.deepcopy(context)
    selected = select_context(context, query="Python reporting")
    assert context == original
    assert [entry["id"] for entry in selected["candidate_evidence"]] == ["python", "validation"]
    assert selected["candidate_evidence"][0]["boundaries"] == ["Do not claim employer deployment.", "Prototype only."]
    reasons = {item["evidence"]["id"]: item["reason"] for item in selected["diagnostics"]["excluded_candidate_evidence"]}
    assert reasons == {"pending": "status_conditional", "resume-claim": "non_authoritative_source",
                       "generated-claim": "non_authoritative_source"}
    assert selected["company"]["scale"] == {"value": None, "evidence_ids": []}
    assert selected["company"]["stage"] == {"value": None, "evidence_ids": []}
    assert selected["company"]["delivery_model"]["value"] == "client_projects"
    assert [fact["id"] for fact in selected["company"]["facts"]] == ["delivery"]
    assert selected["diagnostics"]["unverified_company_facts"][0]["id"] == "scale"
    assert selected["diagnostics"]["unverified_company_attributes"][0]["attribute"] == "scale"
    assert selected["voice_examples"][0]["usage"] == "style_only"
    assert len(selected["voice_examples"]) == 1
    assert selected["resume_positioning"][0]["usage"] == "positioning_only"
    assert {source["id"] for source in selected["sources"]} == {"candidate", "project", "jd", "company", "voice"}


@pytest.mark.parametrize("status", ["conditional", "unresolved", "historical", "generated"])
def test_nonconfirmed_is_never_fallback(context: dict, status: str) -> None:
    context["candidate_evidence"] = [{**context["candidate_evidence"][0], "status": status}]
    result = select_context(context, query="Python")
    assert result["candidate_evidence"] == []
    assert "candidate_evidence" in result["diagnostics"]["missing"]
    assert result["diagnostics"]["excluded_candidate_evidence"][0]["evidence"]["status"] == status


@pytest.mark.parametrize("source_id", ["missing", "company", "voice", "jd"])
def test_candidate_source_refs_are_type_checked(context: dict, source_id: str) -> None:
    context["candidate_evidence"][0]["source_id"] = source_id
    with pytest.raises(ValueError, match="source"):
        validate_context(context)


@pytest.mark.parametrize("target", ["candidate", "role", "company", "voice"])
def test_quotes_must_exist_in_exact_snapshot(context: dict, target: str) -> None:
    entry = {"candidate": context["candidate_evidence"][0], "role": context["role"]["requirements"][0],
             "company": context["company"]["facts"][0], "voice": context["voice_examples"][0]}[target]
    entry["quote"] = "An invented detail"
    with pytest.raises(ValueError, match="quote is absent"):
        validate_context(context)


def test_scope_and_job_identity_are_enforced(context: dict) -> None:
    context["candidate_evidence"][0]["scope"] = {"job_id": "job-2"}
    context["candidate_evidence"][1]["scope"] = {"company": "Another"}
    result = select_context(context, query="Python")
    assert not result["candidate_evidence"]
    reasons = {item["reason"] for item in result["diagnostics"]["excluded_candidate_evidence"]}
    assert {"job_scope_mismatch", "company_scope_mismatch"} <= reasons
    context["candidate_evidence"][0]["scope"] = {"job_id": "job-1", "company": "EXAMPLE"}
    assert select_context(context, query="Python")["candidate_evidence"][0]["id"] == "python"
    mismatched = select_context(context, query="Python", job_id="job-2")
    assert mismatched["role"] is None and mismatched["company"] is None
    assert mismatched["diagnostics"]["role_scope_mismatch"]["context_job_id"] == "job-1"


def test_voice_approval_language_and_genre_do_not_add_candidate_facts(context: dict) -> None:
    context["voice_examples"][1]["user_approved"] = True
    context["voice_examples"][0]["language"] = "zh"
    result = select_context(context, query="500 staff")
    assert [entry["id"] for entry in result["voice_examples"]] == ["assistant"]
    assert result["candidate_evidence"] == []
    cover = select_context(context, query="Python", genre="cover_letter")
    assert cover["voice_examples"] == []
    assert cover["diagnostics"]["voice_status"] == "uncalibrated"


@pytest.mark.parametrize("attribute", ["scale", "stage", "delivery_model"])
def test_company_attributes_need_company_fact_refs(context: dict, attribute: str) -> None:
    context["company"][attribute] = {"value": "large", "evidence_ids": []}
    with pytest.raises(ValueError, match="requires evidence_ids"):
        validate_context(context)
    context["company"][attribute]["evidence_ids"] = ["python"]
    with pytest.raises(ValueError, match="unknown company fact"):
        validate_context(context)


def test_company_attributes_are_independent_and_not_inferred(context: dict) -> None:
    context["company"]["facts"][1]["verification_status"] = "verified"
    result = select_context(context, query="Python")
    assert result["company"]["scale"]["value"] == "small"
    assert result["company"]["stage"]["value"] is None
    assert result["company"]["delivery_model"]["value"] == "client_projects"


def test_empty_and_irrelevant_context_produce_missing_diagnostics(context: dict) -> None:
    result = select_context(context, query="astronomy")
    assert result["candidate_evidence"] == []
    assert "candidate_evidence" in result["diagnostics"]["missing"]
    empty = {"schema_version": 1, "sources": [], "candidate_evidence": [], "role": None,
             "company": None, "voice_examples": []}
    assert select_context(empty, query="anything")["diagnostics"]["missing"] == [
        "candidate_evidence", "role", "company", "voice_examples",
    ]


def test_chinese_relevance_and_selection_limit_retain_boundaries(context: dict) -> None:
    context["candidate_evidence"].append({"id": "zh", "source_id": "project", "quote": "数据分析流程。",
                                          "status": "confirmed"})
    assert [item["id"] for item in select_context(context, query="需要数据分析", language="zh")["candidate_evidence"]] == ["zh"]
    result = select_context(context, query="Python", max_evidence=0, max_voice=0)
    limit_entry = next(item for item in result["diagnostics"]["excluded_candidate_evidence"]
                       if item["evidence"]["id"] == "python")
    assert limit_entry["reason"] == "selection_limit"
    assert "Prototype only." in limit_entry["evidence"]["boundaries"]


def test_loader_resolves_only_explicit_sources_and_rejects_snapshot_drift(context: dict, tmp_path: Path) -> None:
    source = tmp_path / "facts.txt"
    source.write_bytes(context["sources"][0]["text"].encode("utf-8"))
    context["sources"][0]["path"] = "facts.txt"
    registry = tmp_path / "context.json"
    registry.write_text(json.dumps(context), encoding="utf-8")
    assert load_context(registry)["sources"][0]["text"] == context["sources"][0]["text"]
    del context["sources"][0]["text"]
    registry.write_text(json.dumps(context), encoding="utf-8")
    assert load_context(registry)["candidate_evidence"][0]["status"] == "confirmed"
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="quote is absent"):
        load_context(registry)
    context["sources"][0]["text"] = "previous snapshot"
    registry.write_text(json.dumps(context), encoding="utf-8")
    with pytest.raises(ValueError, match="text/file mismatch"):
        load_context(registry)
    source.unlink()
    with pytest.raises(FileNotFoundError):
        load_context(registry)


def test_loader_does_not_parse_whole_markdown_as_confirmed(context: dict, tmp_path: Path) -> None:
    context["candidate_evidence"] = []
    source = tmp_path / "facts.md"
    source.write_bytes(b"# Confirmed\nUnregistered claim about Python employment")
    context["sources"][0] = {"id": "candidate", "kind": "candidate_facts", "path": "facts.md"}
    registry = tmp_path / "context.json"
    registry.write_text(json.dumps(context), encoding="utf-8")
    assert select_context(load_context(registry), query="Python")["candidate_evidence"] == []


def test_loader_rejects_network_paths(context: dict, tmp_path: Path) -> None:
    context["sources"][0]["path"] = "//server/private/facts.txt"
    registry = tmp_path / "context.json"
    registry.write_text(json.dumps(context), encoding="utf-8")
    with pytest.raises(ValueError, match="local files"):
        load_context(registry)


def test_digest_binds_snapshots_without_claiming_authority(context: dict) -> None:
    first = select_context(context, query="Python")
    assert first["snapshot_digest"] == select_context(context, query="reporting")["snapshot_digest"]
    context["sources"][0]["text"] += " A new observation."
    assert first["snapshot_digest"] != select_context(context, query="Python")["snapshot_digest"]


def test_strict_schema_rejects_unknown_fields_duplicate_ids_and_invalid_types(context: dict) -> None:
    invalid = copy.deepcopy(context)
    invalid["profile_path"] = "private"
    with pytest.raises(ValueError, match="unknown fields"):
        validate_context(invalid)
    invalid = copy.deepcopy(context)
    invalid["sources"].append(invalid["sources"][0])
    with pytest.raises(ValueError, match="Duplicate source id"):
        validate_context(invalid)
    context["voice_examples"][0]["user_approved"] = "yes"
    with pytest.raises(ValueError, match="boolean"):
        validate_context(context)
