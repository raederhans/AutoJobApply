"""Synthetic workspace contracts; no personal files, browser or paid model calls."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Annotated

import pytest
import typer
from typer.testing import CliRunner

from applypilot.apply.authorization import compute_job_fingerprint
from applypilot.interview import _lines, _local_sections, build_pack, write_pack
from applypilot.resume_versions import text_digest

URL = "https://careers.example/jobs/analysis-intern"
OTHER_URL = "https://careers.example/jobs/other-intern"
TEXT = "项目经历\n- 开发Python分析工具，产物为一份可复核报告。\n- 与同学协作研究数据质量。\n"
JD = "岗位职责\n负责Python数据分析。\n必须具备沟通能力。\nRequired: Kubernetes deployment.\n"


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    resume = root / "current.txt"
    resume.write_bytes(TEXT.encode("utf-8"))
    conn = sqlite3.connect(root / "applypilot.db")
    conn.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title TEXT, company_name TEXT, location TEXT, application_url TEXT, full_description TEXT, tailored_resume_path TEXT, apply_status TEXT, applied_at TEXT)")
    conn.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)", (URL, "数据分析实习生", "示例公司", "Singapore", URL, JD, str(resume), "applied", "2026-09-01"))
    conn.commit()
    conn.close()
    return root


def _add_sent(root: Path, *, source_text=TEXT):
    text = root / "frozen.txt"
    pdf = root / "frozen.pdf"
    text.write_bytes(source_text.encode("utf-8"))
    # The verified render reads immutable text, not this synthetic PDF's content.
    pdf.write_bytes(b"synthetic registered PDF bytes")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    conn = sqlite3.connect(root / "applypilot.db")
    conn.row_factory = sqlite3.Row
    job = dict(conn.execute("SELECT * FROM jobs WHERE url=?", (URL,)).fetchone())
    material = {"job_fingerprint": compute_job_fingerprint(job), "materials": [{"kind": "resume", "sha256": digest, "size": pdf.stat().st_size}]}
    conn.executescript("""
        CREATE TABLE application_receipts (receipt_source TEXT, receipt_id TEXT, job_url TEXT, admitted_at TEXT);
        CREATE TABLE application_receipt_gate_bindings (receipt_source TEXT, receipt_id TEXT, job_url TEXT, gate_id TEXT, attempt_id TEXT, batch_id TEXT);
        CREATE TABLE application_submission_gates (job_url TEXT, gate_id TEXT, attempt_id TEXT, batch_id TEXT, evidence_json TEXT);
        CREATE TABLE application_attempts (job_url TEXT, attempt_id TEXT, batch_id TEXT, evidence_json TEXT);
        CREATE TABLE application_batch_consumptions (job_url TEXT, batch_id TEXT, evidence_json TEXT);
        CREATE TABLE resume_artifacts (artifact_id TEXT, content_sha256 TEXT, text_path TEXT);
        CREATE TABLE resume_render_versions (render_id TEXT, artifact_id TEXT, text_path TEXT, pdf_path TEXT, pdf_sha256 TEXT, pdf_size INTEGER);
    """)
    conn.execute("INSERT INTO application_receipts VALUES (?,?,?,?)", ("browser_receipt", "receipt-1", URL, "2026-09-01"))
    conn.execute("INSERT INTO application_receipt_gate_bindings VALUES (?,?,?,?,?,?)", ("browser_receipt", "receipt-1", URL, "gate-1", "attempt-1", "batch-1"))
    conn.execute("INSERT INTO application_submission_gates VALUES (?,?,?,?,?)", (URL, "gate-1", "attempt-1", "batch-1", "{}"))
    conn.execute("INSERT INTO application_attempts VALUES (?,?,?,?)", (URL, "attempt-1", "batch-1", json.dumps({"material_binding": material})))
    conn.execute("INSERT INTO resume_artifacts VALUES (?,?,?)", ("resume:1", text_digest(source_text), str(text)))
    conn.execute("INSERT INTO resume_render_versions VALUES (?,?,?,?,?,?)", ("render:1", "resume:1", str(text), str(pdf), digest, pdf.stat().st_size))
    conn.commit()
    conn.close()
    return text, pdf


def test_local_pack_chinese_is_not_called_sent_and_database_unchanged(workspace, tmp_path):
    db = workspace / "applypilot.db"
    before = db.read_bytes()
    pack = build_pack(workspace, url=URL, round_name="技术面")
    assert pack["resume"]["binding_status"] == "current_unverified"
    assert "未证实为已投版本" in pack["resume"]["binding_label"]
    assert pack["jd"]["text"] == JD
    assert pack["resume"]["text"] == TEXT
    assert pack["sections"]["star_evidence"][0]["result"].startswith("[")
    assert pack["sections"]["gaps"]
    output = tmp_path / "prep-中文"
    paths = write_pack(pack, output)
    assert json.loads(Path(paths["json"]).read_text(encoding="utf-8"))["resume"]["text"] == TEXT
    markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
    assert "开发Python" in markdown and "STAR" in markdown and "current_unverified" in markdown
    assert db.read_bytes() == before
    assert not (workspace / "profile.json").exists()
    assert sorted(p.name for p in workspace.iterdir()) == ["applypilot.db", "current.txt"]


def test_exact_receipt_and_render_select_sent_snapshot(workspace):
    text, _ = _add_sent(workspace)
    (workspace / "current.txt").write_text("changed current material", encoding="utf-8")
    pack = build_pack(workspace, url=URL)
    assert pack["resume"]["binding_status"] == "sent_snapshot_verified"
    assert pack["resume"]["path"] == str(text)
    assert pack["resume"]["text"] == TEXT
    assert pack["resume"]["submission_binding"]["receipt_id"] == "receipt-1"


@pytest.mark.parametrize("change", ["text", "pdf", "missing", "link"])
def test_stale_missing_or_cross_job_receipt_binding_downgrades(workspace, change):
    text, pdf = _add_sent(workspace)
    if change == "text":
        text.write_text("not the frozen text", encoding="utf-8")
    elif change == "pdf":
        pdf.write_bytes(b"changed PDF")
    elif change == "missing":
        text.unlink()
    else:
        conn = sqlite3.connect(workspace / "applypilot.db")
        conn.execute("UPDATE application_receipt_gate_bindings SET job_url=?", (OTHER_URL,))
        conn.commit()
        conn.close()
    pack = build_pack(workspace, url=URL)
    assert pack["resume"]["binding_status"] == "current_unverified"
    assert pack["resume"]["submission_binding"] is None
    assert pack["warnings"]


def test_cross_job_explicit_material_rejected(workspace):
    other = workspace / "other.txt"
    other.write_text(TEXT, encoding="utf-8")
    conn = sqlite3.connect(workspace / "applypilot.db")
    conn.execute("INSERT INTO jobs (url,tailored_resume_path) VALUES (?,?)", (OTHER_URL, str(other)))
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="cross_job_material"):
        build_pack(workspace, url=URL, resume=str(other))


@pytest.mark.parametrize("selection", ["resume:1", "render:1", "frozen.txt", "frozen.pdf"])
def test_library_material_only_assigned_elsewhere_rejected_without_job_path(workspace, selection):
    _add_sent(workspace)
    conn = sqlite3.connect(workspace / "applypilot.db")
    conn.execute("CREATE TABLE job_resume_assignments (artifact_id TEXT, job_url TEXT)")
    conn.execute("INSERT INTO job_resume_assignments VALUES (?,?)", ("resume:1", OTHER_URL))
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="cross_job_material"):
        build_pack(workspace, url=URL, resume=selection)


@pytest.mark.parametrize("selection", ["resume:1", "render:1", "frozen.txt", "frozen.pdf"])
@pytest.mark.parametrize("owners", [(URL,), (URL, OTHER_URL), ()])
@pytest.mark.parametrize("has_receipt", [True, False])
def test_library_selection_accepts_current_job_shared_and_unassigned_material(workspace, selection, owners, has_receipt):
    text, _ = _add_sent(workspace)
    conn = sqlite3.connect(workspace / "applypilot.db")
    conn.execute("CREATE TABLE job_resume_assignments (artifact_id TEXT, job_url TEXT)")
    conn.executemany("INSERT INTO job_resume_assignments VALUES (?,?)", [("resume:1", owner) for owner in owners])
    if not has_receipt:
        conn.execute("DELETE FROM application_receipts")
    conn.commit()
    conn.close()

    pack = build_pack(workspace, url=URL, resume=selection)

    assert pack["resume"]["path"] == str(text)
    assert pack["resume"]["artifact_id"] == "resume:1"
    assert pack["resume"]["text"] == TEXT
    assert pack["resume"]["binding_status"] == ("sent_snapshot_verified" if has_receipt else "explicit_unverified")
    assert bool(pack["resume"]["submission_binding"]) == has_receipt


def test_artifact_text_path_cannot_bypass_assignment_without_any_render(workspace):
    _add_sent(workspace)
    conn = sqlite3.connect(workspace / "applypilot.db")
    conn.execute("DELETE FROM resume_render_versions")
    conn.execute("CREATE TABLE job_resume_assignments (artifact_id TEXT, job_url TEXT)")
    conn.execute("INSERT INTO job_resume_assignments VALUES (?,?)", ("resume:1", OTHER_URL))
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="cross_job_material"):
        build_pack(workspace, url=URL, resume="frozen.txt")


def test_missing_current_material_and_missing_database_fail_without_creation(workspace, tmp_path):
    (workspace / "current.txt").unlink()
    with pytest.raises(FileNotFoundError):
        build_pack(workspace, url=URL)
    missing = tmp_path / "missing-workspace"
    with pytest.raises(FileNotFoundError):
        build_pack(missing, url=URL)
    assert not missing.exists()


def test_explicit_current_material_never_inherits_sent_label(workspace):
    _add_sent(workspace)
    pack = build_pack(workspace, url=URL, resume=str(workspace / "current.txt"))
    assert pack["resume"]["binding_status"] == "explicit_unverified"
    assert pack["resume"]["submission_binding"] is None


def test_sent_snapshot_does_not_claim_changed_jd_is_historical(workspace):
    _add_sent(workspace)
    conn = sqlite3.connect(workspace / "applypilot.db")
    conn.execute("UPDATE jobs SET full_description='New current JD' WHERE url=?", (URL,))
    conn.commit()
    conn.close()
    pack = build_pack(workspace, url=URL)
    assert pack["resume"]["binding_status"] == "sent_snapshot_verified"
    assert pack["jd"]["binding_status"] == "current_database_snapshot"
    assert any("jd_changed_or_unbound" in warning for warning in pack["warnings"])


class FakeModel:
    def __init__(self, response):
        self.response = response

    def chat(self, **kwargs):
        assert kwargs["response_format"] == {"type": "json_object"}
        assert kwargs["max_tokens"] == 8192
        return json.dumps(self.response)


@pytest.mark.parametrize("response", [
    {"pairs": [{"jd_source": "jd:L2", "resume_source": "resume:L999"}]},
    {"pairs": [{"jd_source": "jd:L2", "resume_source": "resume:L2", "answer": "I improved revenue 50%"}]},
    {"pairs": [], "claims": ["Candidate is Kubernetes expert"]},
])
def test_llm_unsupported_claims_or_sources_downgrade_to_local(workspace, response):
    pack = build_pack(workspace, url=URL, use_llm=True, llm_client=FakeModel(response))
    assert pack["llm"]["status"] == "downgraded_to_local"
    assert not any(q["kind"] == "evidence_pair" for q in pack["sections"]["questions"])
    assert "improved revenue" not in json.dumps(pack)
    assert "Kubernetes expert" not in json.dumps(pack)


def test_llm_can_only_add_reference_bound_practice_questions(workspace):
    pack = build_pack(workspace, url=URL, use_llm=True,
                      llm_client=FakeModel({"pairs": [{"jd_source": "jd:L2", "resume_source": "resume:L2"}]}))
    assert pack["llm"]["status"] == "validated_reference_selection"
    question = pack["sections"]["questions"][-1]
    assert question["sources"] == ["jd:L2", "resume:L2"]
    assert "限制" in question["prompt"]


def test_output_never_overwrites_existing_or_source(workspace, tmp_path):
    pack = build_pack(workspace, url=URL)
    output = tmp_path / "existing"
    output.mkdir()
    (output / "keep.txt").write_text("keep")
    for target in (output, workspace / "current.txt"):
        with pytest.raises(FileExistsError):
            write_pack(pack, target)
    assert (output / "keep.txt").read_text() == "keep"
    assert (workspace / "current.txt").read_text(encoding="utf-8") == TEXT


def test_windows_source_newlines_and_byte_identity_remain_fixed(workspace, tmp_path):
    raw = TEXT.replace("\n", "\r\n").encode("utf-8")
    (workspace / "current.txt").write_bytes(raw)
    pack = build_pack(workspace, url=URL)
    assert pack["resume"]["text"].encode("utf-8") == raw
    assert pack["resume"]["sha256"] == hashlib.sha256(raw).hexdigest()
    output = tmp_path / "crlf-pack"
    write_pack(pack, output)
    assert b"\r\r\n" not in (output / "pack.md").read_bytes()
    assert json.loads((output / "pack.json").read_text(encoding="utf-8"))["resume"]["text"].encode("utf-8") == raw


def test_cli_workspace_is_resolved_after_callback(workspace, tmp_path, monkeypatch):
    from applypilot import config
    from applypilot.commands.interview import app as interview_app

    app = typer.Typer()

    @app.callback()
    def root(selected: Annotated[Path, typer.Option("--workspace")]):
        monkeypatch.setenv("APPLYPILOT_DIR", str(selected))

    app.add_typer(interview_app, name="interview")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "wrong-default")
    output = tmp_path / "cli-pack"
    result = CliRunner().invoke(app, ["--workspace", str(workspace), "interview", "prepare", "--url", URL,
                                    "--round", "HR", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["binding_status"] == "current_unverified"
    assert (output / "pack.json").is_file()

    second = tmp_path / "second-workspace"
    second.mkdir()
    second_resume = second / "resume.txt"
    second_resume.write_text("- Created a different project for another workspace.", encoding="utf-8")
    conn = sqlite3.connect(second / "applypilot.db")
    conn.execute("CREATE TABLE jobs (url TEXT, title TEXT, full_description TEXT, tailored_resume_path TEXT)")
    conn.execute("INSERT INTO jobs VALUES (?,?,?,?)", (URL, "Second role", "Analyze a different dataset.", str(second_resume)))
    conn.commit()
    conn.close()
    second_output = tmp_path / "second-cli-pack"
    result = CliRunner().invoke(app, ["--workspace", str(second), "interview", "prepare", "--url", URL,
                                    "--output", str(second_output)])
    assert result.exit_code == 0, result.output
    pack = json.loads((second_output / "pack.json").read_text(encoding="utf-8"))
    assert pack["job"]["title"] == "Second role"
    assert pack["resume"]["path"] == str(second_resume)


def test_corrupt_database_is_an_actionable_cli_error(tmp_path, monkeypatch):
    from applypilot.commands.interview import app

    (tmp_path / "applypilot.db").write_bytes(b"not SQLite")
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    result = CliRunner().invoke(app, ["--url", URL, "--output", str(tmp_path / "output")])
    assert result.exit_code == 2
    assert "database" in result.output
    assert "Traceback" not in result.output
    assert not (tmp_path / "output").exists()


def test_model_settings_are_workspace_scoped_without_cached_provider(tmp_path, monkeypatch):
    from applypilot import llm
    from applypilot.commands.interview import _llm_environment

    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "LLM_URL", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    for index in (1, 2):
        workspace = tmp_path / str(index)
        workspace.mkdir()
        (workspace / ".env").write_text(f"LLM_URL=http://localhost:{1000 + index}\nLLM_MODEL=model-{index}\n", encoding="utf-8")
        with _llm_environment(workspace):
            base, model, _ = llm._detect_provider()
            assert base == f"http://localhost:{1000 + index}"
            assert model == f"model-{index}"
        assert "LLM_URL" not in os.environ


def test_sections_filter_company_marketing_and_cover_duties_requirements_bonus():
    jd_lines = [
        "MACHINE LEARNING INTERN", "at Fictional Systems", "ABOUT FICTIONAL SYSTEMS",
        "Our company helps customers around the world and is growing fast.",
        "Our culture celebrates ownership and collaboration.", "ABOUT THE ROLE",
        "We are looking for an intern to join our exciting mission.", "WHAT YOU WILL WORK ON",
        "Build evaluation frameworks for document extraction.", "Support model experiments and report limitations.",
        "ABOUT YOU", "You are proficient in Python and SQL.", "You have strong communication skills.",
        "BONUS POINTS IF", "You have experience with cloud deployment.", "WHAT YOU'll GAIN",
        "Mentorship from industry experts and exposure to our culture.",
    ]
    jd = _lines("\n".join(jd_lines), "jd")
    resume_lines = [
        "Candidate Name", "SUMMARY", "AI student designing data systems.", "EDUCATION",
        "Fictional University, BSc Design and Management | GPA: 3.8/4.0", "TECHNICAL SKILLS",
        "Workflow Automation: Scheduled pipelines, prompt design", "EXPERIENCE",
        "Fictional Design and Planning LLC", "Developer | 2025 - Present",
        "- Built a document-extraction pipeline and a reproducible evaluation report.",
        "- Implemented train-fold preprocessing and documented transfer limitations.", "PROJECTS",
        "- Delivered a Python/SQL data-cleaning tool for a class research project.",
    ]
    resume = _lines("\n".join(resume_lines), "resume")
    sections = _local_sections(jd, resume, "technical")
    assert {item["sources"][0] for item in sections["role_focus"]} == {"jd:L9", "jd:L10", "jd:L12", "jd:L13", "jd:L15"}
    assert {item["category"] for item in sections["role_focus"]} == {"responsibilities", "requirements", "preferred"}
    assert {item["sources"][0] for item in sections["star_evidence"]} == {"resume:L11", "resume:L12", "resume:L14"}
    for section in ("questions", "questions_to_ask", "star_evidence", "gaps"):
        assert all(ref in {"jd:L9", "jd:L10", "jd:L12", "jd:L13", "jd:L15", "resume:L11", "resume:L12", "resume:L14"}
                   for item in sections[section] for ref in item["sources"])


def test_long_responsibilities_leave_room_for_required_and_bonus_evidence():
    jd = _lines("Responsibilities\n" + "\n".join(f"Build deliverable {index}." for index in range(12))
                + "\nRequirements\nRequired: Python.\nBonus points if\nYou have cloud experience.", "jd")
    sections = _local_sections(jd, _lines("- Built a data tool.", "resume"), None)
    assert len(sections["role_focus"]) == 8
    assert any(item["category"] == "preferred" for item in sections["role_focus"])
    assert any(item["category"] == "requirements" for item in sections["role_focus"])


def test_empty_evidence_never_falls_back_to_names_education_or_marketing(workspace):
    conn = sqlite3.connect(workspace / "applypilot.db")
    conn.execute("UPDATE jobs SET full_description=?", ("ABOUT COMPANY\nOur company creates wonderful opportunities.",))
    conn.commit()
    conn.close()
    (workspace / "current.txt").write_text("EDUCATION\nUniversity Design and Management | GPA: 4.0\n", encoding="utf-8")
    pack = build_pack(workspace, url=URL)
    assert not pack["sections"]["role_focus"]
    assert not pack["sections"]["star_evidence"]
    assert any("未识别到" in warning for warning in pack["warnings"])


def test_llm_only_receives_selected_evidence_not_headers_or_skill_inventory(workspace):
    class CheckingModel:
        def chat(self, **kwargs):
            sources = json.loads(kwargs["messages"][1]["content"])["sources"]
            assert "jd:L1" not in sources
            assert "resume:L1" not in sources
            assert sources["resume:L2"] == TEXT.splitlines()[1]
            return '{"pairs": [{"jd_source": "jd:L2", "resume_source": "resume:L2"}]}'

    pack = build_pack(workspace, url=URL, use_llm=True, llm_client=CheckingModel())
    assert pack["llm"]["status"] == "validated_reference_selection"


@pytest.mark.parametrize("content,finish,reason", [
    ("", "length", "response_truncated"),
    ('{"pairs": [', "length", "response_truncated"),
    ("", "stop", "empty_content"),
    ('```json\n{"pairs": []}\n```', "stop", "invalid_json"),
])
def test_actual_client_response_contract_has_safe_downgrade_diagnostics(workspace, monkeypatch, content, finish, reason):
    import httpx

    from applypilot.llm import LLMClient

    client = LLMClient("http://localhost:9999", "fixture", "fixture-only")
    response = httpx.Response(200, request=httpx.Request("POST", "http://localhost:9999/chat/completions"),
                              json={"choices": [{"finish_reason": finish, "message": {
                                  "content": content, "reasoning_content": "private reasoning is never stored"}}],
                                  "usage": {"prompt_tokens": 400, "completion_tokens": 1600, "total_tokens": 2000}})
    monkeypatch.setattr(client._client, "post", lambda *args, **kwargs: response)
    try:
        pack = build_pack(workspace, url=URL, use_llm=True, llm_client=client)
    finally:
        client._client.close()
    assert pack["llm"]["reason"] == reason
    diagnostics = pack["llm"]["response_diagnostics"]
    assert diagnostics["finish_reason"] == finish
    assert diagnostics["content_chars"] == len(content)
    assert diagnostics["completion_tokens"] == 1600
    assert diagnostics["reasoning_chars"] > 0
    assert "private reasoning" not in json.dumps(pack)
    assert "fixture-only" not in json.dumps(pack)
    assert not any(question["kind"] == "evidence_pair" for question in pack["sections"]["questions"])
