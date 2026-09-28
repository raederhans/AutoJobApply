import json
from pathlib import Path

import pytest

from applypilot import single_job
from applypilot.database import init_db
from applypilot.resume_versions import finish_resume_run, start_resume_run
from applypilot.scoring import cover_letter, tailor


def test_candidate_fact_loader_is_explicit_deduplicated_and_utf8(tmp_path):
    primary = tmp_path / "primary.txt"
    primary.write_text("Primary", encoding="utf-8")
    facts = tmp_path / "facts.md"
    facts.write_text("已核对项目事实", encoding="utf-8")
    profile = {
        "tailoring": {"evidence_sources": [str(facts), str(facts)]},
        "cover_letter": {"evidence_sources": [str(facts)]},
    }
    sources = cover_letter.load_evidence_sources(profile, primary, "Primary")
    fact_sources = [source for source in sources if source.get("kind") == "candidate_facts"]
    assert len(fact_sources) == 1
    assert fact_sources[0]["text"] == "已核对项目事实"
    assert [source["path"] for source in sources].count(str(facts.resolve())) == 1


def test_explicit_missing_or_empty_candidate_fact_source_fails_closed(tmp_path):
    primary = tmp_path / "primary.txt"
    missing = tmp_path / "missing.md"
    with pytest.raises(FileNotFoundError, match="candidate fact source"):
        cover_letter.load_evidence_sources(
            {"tailoring": {"evidence_sources": [str(missing)]}}, primary, "Primary"
        )
    missing.write_text("  ", encoding="utf-8")
    with pytest.raises(ValueError, match="candidate fact source is empty"):
        cover_letter.load_evidence_sources(
            {"tailoring": {"evidence_sources": [str(missing)]}}, primary, "Primary"
        )


def test_batch_generation_uses_and_binds_only_configured_candidate_fact_source(tmp_path, monkeypatch):
    conn = init_db(tmp_path / "jobs.db")
    url = "https://example.test/candidate-facts-job"
    conn.execute(
        "INSERT INTO jobs (url,title,company_name,full_description,fit_score,eligibility_status) "
        "VALUES (?,?,?,?,?,?)",
        (url, "Analyst Intern", "Example", "Build dashboards.", 8, "eligible"),
    )
    conn.commit()
    primary = tmp_path / "primary.txt"
    primary.write_text("Candidate\nPrimary resume", encoding="utf-8")
    facts = tmp_path / "facts.md"
    facts.write_text("Verified project context", encoding="utf-8")
    profile = {"tailoring": {"evidence_sources": [str(facts)]}}
    monkeypatch.setattr(tailor, "get_connection", lambda: conn)
    monkeypatch.setattr(tailor, "load_profile", lambda: profile)
    monkeypatch.setattr(tailor, "TAILORED_DIR", tmp_path / "tailored_resumes")
    monkeypatch.setattr(tailor, "select_resume_source", lambda *args: (primary, {"track": "data"}))
    observed = {}

    def fake_tailor(_resume_text, _job, _profile, **kwargs):
        observed["supplemental"] = kwargs["supplemental_evidence"]
        return "Candidate\nVerified project context", {"status": "machine_validated", "attempts": 1}

    monkeypatch.setattr(tailor, "tailor_resume", fake_tailor)
    from applypilot.scoring import pdf

    def fake_pdf(path):
        result = Path(path).with_suffix(".pdf")
        result.write_bytes(b"test-pdf")
        return result

    monkeypatch.setattr(pdf, "convert_to_pdf", fake_pdf)
    result = tailor.run_tailoring(min_score=0, limit=1, target_url=url)
    assert result["approved"] == 1
    assert "Verified project context" in observed["supplemental"]
    report_path = Path(result["results"][0]["report_path"])
    generation_path = Path(json.loads(report_path.read_text(encoding="utf-8"))["generation_record"])
    bindings = json.loads(generation_path.read_text(encoding="utf-8"))["evidence_source_bindings"]
    assert [binding["path"] for binding in bindings] == [str(facts.resolve())]


def test_revalidation_rejects_changed_previously_bound_fact_source(tmp_path, monkeypatch):
    conn = init_db(tmp_path / "jobs.db")
    url = "https://example.test/revalidate"
    source = tmp_path / "source.txt"
    source.write_text("Verified source", encoding="utf-8")
    tailored = tmp_path / "tailored.txt"
    tailored.write_text("Candidate\nVerified source", encoding="utf-8")
    facts = tmp_path / "facts.md"
    facts.write_text("Original adopted fact", encoding="utf-8")
    run = start_resume_run(tmp_path, {"url": url}, kind="tailoring")
    report = finish_resume_run(
        run, {"status": "machine_validated"},
        evidence_sources=[{"path": str(facts), "text": facts.read_text(encoding="utf-8")}],
    )
    conn.execute(
        "INSERT INTO jobs (url,title,company_name,full_description,eligibility_status,"
        "tailored_resume_path,tailor_source_resume_path,tailor_report_path,tailor_status) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (url, "Analyst Intern", "Example", "Build dashboards.", "eligible",
         str(tailored), str(source), str(report), "machine_validated"),
    )
    conn.commit()
    monkeypatch.setattr(single_job, "get_connection", lambda: conn)
    monkeypatch.setattr(single_job.config, "APP_DIR", tmp_path)
    monkeypatch.setattr(single_job, "load_profile", dict)
    facts.write_text("Corrected adopted fact", encoding="utf-8")
    result = single_job.revalidate_tailored_resume_for_url(url)
    assert result["status"] == "failed_revalidation"
    assert "Bound candidate fact source changed" in result["error"]
