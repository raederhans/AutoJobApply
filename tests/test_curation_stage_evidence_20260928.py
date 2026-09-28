import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from applypilot.database import init_db


@pytest.fixture
def staged_setup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "curation_stage_evidence", Path(__file__).resolve().parents[1] / "tools" / "curate_resume_library.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    db = tmp_path / "jobs.db"
    conn = init_db(db)
    tool.ensure_resume_library_schema(conn)
    base = tmp_path / "base.txt"
    base.write_text("Candidate\nBASE PROJECT\nBase source proof", encoding="utf-8")
    old = tmp_path / "parent.txt"
    old.write_text("Candidate\nPROJECTS\nOld project", encoding="utf-8")
    old.with_suffix(".pdf").write_bytes(b"old-pdf")
    parent_supplemental = tmp_path / "parent-supplemental.txt"
    parent_supplemental.write_text("Parent registered proof", encoding="utf-8")
    facts = tmp_path / "resume-project-context.md"
    facts.write_text("Current adopted project proof", encoding="utf-8")
    metadata = {"registered_from_job": "https://example.test/job",
                "supplemental_evidence_path": str(parent_supplemental),
                "library_family": "AI implementation", "library_label": "Reviewed edition"}
    conn.execute("INSERT INTO jobs (url,title,company_name,full_description) VALUES (?,?,?,?)",
                 ("https://example.test/job", "AI Intern", "Example", "Build a project."))
    conn.execute(
        "INSERT INTO resume_artifacts (artifact_id,content_sha256,kind,track,text_path,pdf_path,"
        "source_resume_path,pdf_sha256,pdf_size,validation_status,active,metadata_json,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("parent", tool.fingerprint(old), "tailored", "ai_implementation", str(old),
         str(old.with_suffix(".pdf")), str(base), tool.fingerprint(old.with_suffix(".pdf")),
         old.with_suffix(".pdf").stat().st_size, "machine_validated", 1,
         json.dumps(metadata), "created", "staged"),
    )
    conn.commit()
    conn.close()
    profile = {"tailoring": {"resume_variants": [{"track": "ai_implementation", "path": str(base)}],
                             "evidence_sources": [str(facts)]}}
    monkeypatch.setattr(tool.config, "DB_PATH", db)
    monkeypatch.setattr(tool.config, "APP_DIR", tmp_path)
    monkeypatch.setattr(tool, "load_profile", lambda: profile)
    observed = {}

    def refine(text, *, supplemental_text, **kwargs):
        observed["refine_evidence"] = supplemental_text
        return {"text": text + "\nCurrent adopted project proof", "changes": [{"operation": "add_fact"}],
                "claims_preserved": False}

    monkeypatch.setattr(tool, "refine_existing_text", refine)
    def validate(text, profile, *, original_text, **kwargs):
        observed["validation_evidence"] = original_text
        return {"passed": True}

    monkeypatch.setattr(tool, "validate_tailored_resume", validate)

    def render(path, *, layout_override, layout_warnings):
        layout_warnings.append("Sparse page advisory")
        pdf = Path(path).with_suffix(".pdf")
        pdf.write_bytes(b"staged-pdf")
        return pdf

    monkeypatch.setattr(tool, "convert_to_pdf", render)
    monkeypatch.setattr(tool, "PdfReader", lambda path: SimpleNamespace(pages=[SimpleNamespace(
        extract_text=lambda: Path(path).with_suffix(".txt").read_text(encoding="utf-8")
    )]))
    report_dir = tmp_path / "report"
    return tool, report_dir, parent_supplemental, facts, observed


def test_stage_uses_parent_supplemental_and_live_candidate_facts(staged_setup):
    tool, report_dir, parent_supplemental, facts, observed = staged_setup
    tool.stage(report_dir)
    report = json.loads((report_dir / "staged-editions.json").read_text(encoding="utf-8"))
    record = report["records"][0]
    assert "Parent registered proof" in observed["refine_evidence"]
    assert "Current adopted project proof" in observed["refine_evidence"]
    assert "Parent registered proof" in observed["validation_evidence"]
    assert "Current adopted project proof" in observed["validation_evidence"]
    assert record["layout_warnings"] == ["Sparse page advisory"]
    validation = json.loads(Path(record["report_path"]).read_text(encoding="utf-8"))
    assert validation["layout_warnings"] == ["Sparse page advisory"]
    generation = json.loads((Path(record["run_dir"]) / "generation.json").read_text(encoding="utf-8"))
    assert record["generation_sha256"] == tool.fingerprint(Path(record["run_dir"]) / "generation.json")
    assert [item["path"] for item in generation["evidence_source_bindings"]] == [str(facts)]
    assert str(parent_supplemental) in report["source_hashes"]
    assert str(facts) in report["source_hashes"]


def test_stage_rejects_missing_registered_parent_supplemental(staged_setup):
    tool, report_dir, parent_supplemental, _, _ = staged_setup
    parent_supplemental.unlink()
    with pytest.raises(FileNotFoundError, match="Registered parent supplemental evidence"):
        tool.stage(report_dir)
    assert not (report_dir / "staged-editions.json").exists()
