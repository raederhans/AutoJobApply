"""Run the production writing CLI against explicit fictional inputs only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner

from applypilot.cli import app
from applypilot.commands import writing

FACT = "I built a Python dashboard for weekly operational reporting."
ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "scripts" / "evals" / "writing-cases.json"


def registry():
    return {
        "schema_version": 1,
        "sources": [
            {"id": "candidate", "kind": "project_evidence", "text": FACT},
            {"id": "jd", "kind": "jd", "text": "Analyst internship: build Python dashboards for operational reporting."},
        ],
        "candidate_evidence": [{"id": "dashboard", "source_id": "candidate", "quote": FACT, "status": "confirmed"}],
        "role": {"job_id": "fictional-job", "title": "Analyst Intern", "company_name": "Example", "jd_source_id": "jd"},
        "company": {"name": "Example", "facts": []}, "voice_examples": [],
    }


def question():
    return {
        "job_id": "fictional-job", "page_id": "step1", "field_key": "experience",
        "text": "Describe your Python dashboard work and your own contribution.",
        "help_text": "At most 150 words.", "language": "en", "required": True,
        "constraints": [{"kind": "max", "unit": "words", "value": 150, "source": "help_text"}],
    }


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    workspace = tmp_path / "uncreated-workspace"
    monkeypatch.setenv("APPLYPILOT_DIR", str(workspace))
    monkeypatch.delenv("APPLYPILOT_DISCOVERY_ONLY", raising=False)
    return {
        "context": save(tmp_path / "context.json", registry()),
        "question": save(tmp_path / "question.json", question()),
        "workspace": workspace,
        "output": tmp_path / "drafts",
    }


def invoke(inputs, *args, code=0):
    result = CliRunner().invoke(app, ["--workspace", str(inputs["workspace"]), "writing", *map(str, args)])
    assert result.exit_code == code, result.output + repr(result.exception)
    assert not inputs["workspace"].exists()
    return result


def subprocess_lazy(inputs, args):
    source_path = str(ROOT / "src")
    env = {**os.environ, "PYTHONPATH": source_path, "PYTHONIOENCODING": "utf-8"}
    env.pop("APPLYPILOT_DISCOVERY_ONLY", None)
    argv = ["--workspace", str(inputs["workspace"]), *map(str, args)]
    code = (
        "import sys; from typer.testing import CliRunner; from applypilot.cli import app; "
        f"result = CliRunner().invoke(app, {argv!r}); "
        "assert result.exit_code == 0, result.output + repr(result.exception); "
        "assert 'applypilot.config' not in sys.modules; "
        "assert 'applypilot.database' not in sys.modules; "
        "assert 'applypilot.llm' not in sys.modules; print(result.stdout)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not inputs["workspace"].exists()
    return result.stdout


@pytest.mark.parametrize("args", [["--help"], ["writing", "--help"]])
def test_root_and_writing_help_do_not_import_config_db_or_llm(inputs, args):
    output = subprocess_lazy(inputs, args)
    assert "writing" in output.lower() or "context-check" in output


def test_context_check_and_plan_are_lazy_and_explicit(inputs):
    checked = json.loads(subprocess_lazy(inputs, ["writing", "context-check", "--file", inputs["context"]]))
    assert checked == {"valid": True, "sources": 2, "candidate_evidence": 1, "job_id": "fictional-job", "authority": "none"}
    plan = json.loads(subprocess_lazy(inputs, [
        "writing", "plan", "--context", inputs["context"], "--question", inputs["question"],
    ]))
    assert plan["task"]["route"] == "application_answer"
    assert plan["task"]["question"]["text"] == question()["text"]
    assert [item["id"] for item in plan["selected_context"]["candidate_evidence"]] == ["dashboard"]
    assert plan["authority"] == "none"


def test_benchmark_default_is_lazy_preview_without_report(inputs, tmp_path):
    output = tmp_path / "new-report.json"
    report = json.loads(subprocess_lazy(inputs, ["writing", "benchmark", "--suite", SUITE, "--output", output]))
    assert report["status"] == "preview_only"
    assert report["model_calls"] == 0
    assert report["cases"]
    assert not output.exists()


def test_existing_benchmark_report_is_preserved(inputs, tmp_path):
    output = save(tmp_path / "existing-report.json", {"preserved": "旧报告"})
    before = output.read_bytes()
    result = invoke(inputs, "benchmark", "--suite", SUITE, "--output", output, code=2)
    assert "already exists" in result.output
    assert output.read_bytes() == before


def test_benchmark_model_run_saves_report_and_evaluate_is_offline(inputs, tmp_path, fake_client):
    suite = save(tmp_path / "fictional-suite.json", {
        "schema_version": 1, "contexts": {"fictional": registry()},
        "cases": [{"id": "one-answer", "genre": "application_answer", "split": "development",
                   "context_id": "fictional", "question": question()}],
    })
    output = tmp_path / "run-report.json"
    ran = json.loads(invoke(inputs, "benchmark", "--suite", suite, "--output", output, "--use-llm").stdout)
    assert ran["summary"]["contract_passed"] == 1
    assert len(fake_client.calls) == 3
    evaluated = json.loads(subprocess_lazy(inputs, ["writing", "evaluate", "--suite", suite, "--results", output]))
    assert evaluated["summary"]["contract_passed"] == 1
    assert evaluated["summary"]["errors"] == 0
    before = output.read_bytes()
    invoke(inputs, "benchmark", "--suite", suite, "--output", output, "--use-llm", code=2)
    assert len(fake_client.calls) == 3
    assert output.read_bytes() == before


def observation(help_text="At most 150 words."):
    return {
        "page_url": "https://example.test/same-url-wizard",
        "fields": [{
            "field_key": "experience", "required": True, "value": "PRIVATE_CURRENT_VALUE",
            "application_question": {
                "text": question()["text"], "language": "en", "help_text": help_text,
                "section_path": ["Experience"], "completeness": "known",
            },
        }],
    }


def test_question_import_accumulates_pages_and_preserves_revisions(inputs, tmp_path):
    observed = save(tmp_path / "observation.json", observation())
    first, second, third = [tmp_path / f"questions-{number}.json" for number in range(3)]
    for page, output, prior in [("step1", first, None), ("step2", second, first)]:
        args = ["questions", "--file", observed, "--job-id", "fictional-job", "--page-id", page, "--output", output]
        if prior:
            args.extend(["--existing", prior])
        data = json.loads(invoke(inputs, *args).stdout)
        assert data["whole_form"] == "unknown"
    save(observed, observation("At most 100 words."))
    invoke(inputs, "questions", "--file", observed, "--job-id", "fictional-job", "--page-id", "step1",
           "--existing", second, "--output", third)
    merged = json.loads(third.read_text(encoding="utf-8"))
    assert set(merged["pages"]) == {"step1", "step2"}
    current = merged["pages"]["step1"][0]
    assert merged["pages"]["step2"][0]["question_id"] != current["question_id"]
    assert len(merged["revisions"][current["question_id"]]) == 2
    assert current["constraints"][0]["value"] == 100
    assert merged["coverage"]["whole_form"] == "unknown"
    assert "PRIVATE_CURRENT_VALUE" not in third.read_text(encoding="utf-8")


def test_question_output_never_overwrites_existing_file(inputs, tmp_path):
    observed = save(tmp_path / "observation.json", observation())
    output = save(tmp_path / "already.json", {"preserved": True})
    before = output.read_bytes()
    invoke(inputs, "questions", "--file", observed, "--job-id", "fictional-job", "--page-id", "step1",
           "--output", output, code=2)
    assert output.read_bytes() == before


class FakeClient:
    model = "offline-fixture-model"

    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        if messages[0]["content"].startswith("Edit application prose"):
            payload = json.loads(messages[1]["content"])
            return json.dumps({"decision": "keep", "edits": [], "draft": payload["draft"]})
        if "Review application writing" in messages[0]["content"]:
            return json.dumps({
                "verdict": "pass", "issues": [], "unsupported_claims": [], "missed_parts": [],
                "voice_fit": "uncalibrated",
                "scores": {"relevance": 2, "specificity": 2, "naturalness": 2, "concision": 2},
            })
        return json.dumps({"text": FACT, "claims": [{"text": FACT, "kind": "candidate", "evidence_ids": ["dashboard"]}], "missing_facts": []})


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()

    @contextmanager
    def supplied_client():
        yield client

    monkeypatch.setattr(writing, "_client", supplied_client)
    return client


def generation_args(inputs, genre):
    args = [genre, "--context", inputs["context"], "--output-dir", inputs["output"], "--max-repairs", "0"]
    if genre == "answer":
        args.extend(["--question", inputs["question"]])
    return args


@pytest.mark.parametrize("genre", ["answer", "cover"])
def test_generation_saves_real_artifact_with_injected_client_no_db(inputs, fake_client, genre):
    output = json.loads(invoke(inputs, *generation_args(inputs, genre)).stdout)
    artifact_path = Path(output["artifact_path"])
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["genre"] == ("application_answer" if genre == "answer" else "cover_letter")
    assert artifact["status"] == "reviewed_draft"
    assert artifact["authority"] == "none" and artifact["submission_ready"] is False
    assert Path(output["text_path"]).read_text(encoding="utf-8") == artifact["text"] == FACT
    assert artifact["generation"]["model"] == "offline-fixture-model"
    assert len(fake_client.calls) == 3
    assert artifact["editing"]["enabled"] is True
    assert artifact["editing"]["passes"][0]["decision"] == "keep"
    assert not list(inputs["output"].rglob("*.db"))
    assert not list(inputs["output"].rglob("*.pdf"))
    assert not list(inputs["output"].rglob("profile.json"))


@pytest.mark.parametrize("genre", ["answer", "cover"])
def test_no_editorial_flag_skips_only_editor_not_final_review(inputs, fake_client, genre):
    output = json.loads(invoke(inputs, *generation_args(inputs, genre), "--no-editorial").stdout)
    artifact = json.loads(Path(output["artifact_path"]).read_text(encoding="utf-8"))
    assert artifact["editing"] == {"enabled": False, "passes": []}
    assert artifact["review"]["verdict"] == "pass"
    assert len(fake_client.calls) == 2


@pytest.mark.parametrize("genre", ["answer", "cover"])
def test_revision_writes_new_uuid_and_preserves_old_bytes(inputs, fake_client, genre):
    args = generation_args(inputs, genre)
    first = json.loads(invoke(inputs, *args).stdout)
    path = Path(first["artifact_path"])
    old_json, old_text = path.read_bytes(), Path(first["text_path"]).read_bytes()
    second = json.loads(invoke(inputs, *args, "--previous", path, "--revision-request", "Keep the supported detail; make it direct.").stdout)
    revised = json.loads(Path(second["artifact_path"]).read_text(encoding="utf-8"))
    assert second["revision_id"] != first["revision_id"]
    assert revised["previous_revision"] == first["revision_id"]
    assert path.read_bytes() == old_json and Path(first["text_path"]).read_bytes() == old_text
    assert len(list(inputs["output"].glob("*/artifact.json"))) == 2


def test_colliding_uuid_does_not_overwrite_existing_artifact(inputs, fake_client, monkeypatch):
    import applypilot.writing_common

    monkeypatch.setattr(applypilot.writing_common, "uuid4", lambda: UUID("00000000-0000-4000-8000-000000000001"))
    args = generation_args(inputs, "cover")
    output = json.loads(invoke(inputs, *args).stdout)
    artifact, text = Path(output["artifact_path"]), Path(output["text_path"])
    before = artifact.read_bytes(), text.read_bytes()
    invoke(inputs, *args, code=2)
    assert (artifact.read_bytes(), text.read_bytes()) == before
    assert len(list(inputs["output"].glob("*/artifact.json"))) == 1


def test_answer_and_plan_accept_prior_answers_only_as_sibling_context(inputs, fake_client):
    first = json.loads(invoke(inputs, *generation_args(inputs, "answer")).stdout)
    previous = Path(first["artifact_path"])
    next_question = question()
    next_question["field_key"] = "second-answer"
    save(inputs["question"], next_question)
    preview = json.loads(invoke(inputs, "plan", "--context", inputs["context"], "--question", inputs["question"], "--sibling", previous).stdout)
    assert preview["task"]["siblings"][0]["usage"] == "avoid_redundancy_only_not_facts"
    second = json.loads(invoke(inputs, *generation_args(inputs, "answer"), "--sibling", previous).stdout)
    artifact = json.loads(Path(second["artifact_path"]).read_text(encoding="utf-8"))
    assert artifact["task"]["siblings"] == preview["task"]["siblings"]
    assert artifact["task"]["siblings"][0]["text"] == FACT


def test_cover_request_does_not_enter_answer_generation(inputs, fake_client):
    value = question()
    value["text"] = "Please provide a cover letter."
    save(inputs["question"], value)
    result = invoke(inputs, *generation_args(inputs, "answer"), code=2)
    assert "cover_letter" in result.output
    assert not fake_client.calls
    assert not inputs["output"].exists()


def test_answer_artifact_cannot_be_revised_by_cover_command(inputs, fake_client):
    answer = json.loads(invoke(inputs, *generation_args(inputs, "answer")).stdout)
    old = Path(answer["artifact_path"])
    before = old.read_bytes()
    invoke(inputs, *generation_args(inputs, "cover"), "--previous", old, "--revision-request", "Shorten.", code=2)
    assert len(fake_client.calls) == 3
    assert old.read_bytes() == before
    assert len(list(inputs["output"].glob("*/artifact.json"))) == 1


def test_no_fact_status_is_saved_with_exit_two(inputs, fake_client):
    value = registry()
    value["candidate_evidence"][0]["status"] = "unresolved"
    save(inputs["context"], value)
    output = json.loads(invoke(inputs, *generation_args(inputs, "cover"), code=2).stdout)
    assert output["status"] == "needs_fact"
    assert Path(output["artifact_path"]).is_file()
    assert not fake_client.calls


@pytest.mark.parametrize("command", ["context-check", "plan", "answer", "cover"])
def test_invalid_registry_exits_without_artifact_or_model(inputs, fake_client, command):
    save(inputs["context"], {"schema_version": 1})
    if command == "context-check":
        args = [command, "--file", inputs["context"]]
    elif command == "plan":
        args = [command, "--context", inputs["context"], "--question", inputs["question"]]
    else:
        args = generation_args(inputs, command)
    invoke(inputs, *args, code=2)
    assert not inputs["output"].exists()
    assert not fake_client.calls
