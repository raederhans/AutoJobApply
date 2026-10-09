"""Exercise the production root CLI against disposable, fictional workspaces."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

URL = "https://example.test/jobs/extensions-smoke"


def invoke(workspace, *args, code=0, extra_env=None):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "APPLYPILOT_DIR": str(workspace.parent / "wrong-default")}
    env.pop("APPLYPILOT_DISCOVERY_ONLY", None)
    env.update(extra_env or {})
    result = subprocess.run([sys.executable, "-m", "applypilot", "--workspace", str(workspace), *args],
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
    assert result.returncode == code, result.stdout + result.stderr
    return result.stdout


def test_production_cli_import_is_lazy_and_new_groups_are_registered(tmp_path):
    result = subprocess.run([sys.executable, "-c", ("import sys; import applypilot.cli; "
                             "assert 'applypilot.config' not in sys.modules")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    workspace = tmp_path / "not-created"
    output = invoke(workspace, "--help")
    for group in ("followup", "interview", "json-resume", "browser-prepare", "quality"):
        assert group in output
    assert not workspace.exists()


def test_followup_resume_interview_share_only_the_selected_workspace(tmp_path):
    from applypilot.database import close_connection, init_db

    workspace = tmp_path / "selected"
    workspace.mkdir()
    database = workspace / "applypilot.db"
    conn = init_db(database)
    conn.execute("INSERT INTO jobs (url, title, company_name, full_description, apply_status) VALUES (?, ?, ?, ?, ?)",
                 (URL, "Analyst Intern", "Fictional Company", "Requirements\nUse Python to analyze data.", "applied"))
    conn.commit()
    close_connection(database)
    external = tmp_path / "external-resume.json"
    external.write_text(json.dumps({"basics": {"name": "Example Candidate", "summary": "Python analysis"},
                                    "projects": [{"name": "Demo", "highlights": ["Built a Python report."]}],
                                    "x-test-preserved": {"language": "中文"}}), encoding="utf-8")
    imported = json.loads(invoke(workspace, "json-resume", "import", "--input", str(external)))
    draft = Path(imported["draft_dir"])
    assert draft.is_relative_to(workspace)
    assert imported["status"] == "unvalidated_draft"
    assert json.loads((draft / "resume.json").read_text(encoding="utf-8"))["x-test-preserved"] == {"language": "中文"}
    events = tmp_path / "events.json"
    events.write_text(json.dumps([{"provider": "reviewed-test", "message_id": "m1", "job_url": URL,
                                   "occurred_at": "2026-10-09T10:00:00+08:00", "event_type": "interview_invited",
                                   "evidence_ref": "fixture:m1", "summary": "Synthetic invitation", "round": 1,
                                   "scheduled_at": "2026-10-13T14:00:00+08:00"}]), encoding="utf-8")
    assert json.loads(invoke(workspace, "followup", "import", "--file", str(events)))["imported"] == 1
    assert json.loads(invoke(workspace, "followup", "import", "--file", str(events)))["duplicates"] == 1
    timeline = json.loads(invoke(workspace, "followup", "timeline", "--url", URL))
    assert len(timeline["events"]) == 1
    output = tmp_path / "pack"
    pack_result = json.loads(invoke(workspace, "interview", "prepare", "--url", URL, "--resume", str(draft / "resume.txt"),
                                    "--output", str(output), "--round", "technical"))
    assert pack_result["binding_status"] == "explicit_unverified"
    pack = json.loads((output / "pack.json").read_text(encoding="utf-8"))
    assert pack["job"]["url"] == URL
    assert pack["llm"]["status"] == "not_requested"
    assert "Built a Python report" in (output / "pack.md").read_text(encoding="utf-8")
    with sqlite3.connect(database) as read:
        assert read.execute("SELECT apply_status FROM jobs WHERE url=?", (URL,)).fetchone()[0] == "applied"
    assert not (workspace / "profile.json").exists()
    assert not (tmp_path / "wrong-default").exists()


def test_root_browser_plan_is_value_free_and_quality_rejects_negative_cases(tmp_path):
    workspace = tmp_path / "uncreated"
    inputs = {
        "actions": {"data": [{"selector": "external-city", "method": "fill", "arguments": ["wrong city"]}]},
        "bindings": [{"selector": "external-city", "field_key": "#city", "label": "City", "semantic": "city"}],
        "facts": {"city": "SYNTHETIC_PRIVATE_VALUE"},
        "observation": {"ok": True, "outcome": "completed", "content": [{"type": "text", "text": json.dumps({
            "form_state": {"page_url": URL, "fields": [{"field_key": "#city", "label": "City", "control": "text",
                                                          "disabled": False, "readonly": False}]}})}]},
    }
    args = ["browser-prepare", "plan", "--url", URL]
    for key, value in inputs.items():
        path = tmp_path / f"{key}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        args.extend([f"--{key}", str(path)])
    output = invoke(workspace, *args)
    assert json.loads(output)["live_revalidation_required"] is True
    assert "SYNTHETIC_PRIVATE_VALUE" not in output
    assert "wrong city" not in output
    fixtures = Path(__file__).resolve().parents[1] / "scripts" / "evals"
    passing = json.loads(invoke(workspace, "quality", "run", "--file", str(fixtures / "synthetic-pass.json")))
    failing = json.loads(invoke(workspace, "quality", "run", "--file", str(fixtures / "synthetic-fail.json"), code=1))
    assert passing["passed"] is True and failing["failed_count"] > 0
    exported = json.loads(invoke(workspace, "quality", "export-promptfoo", "--file", str(fixtures / "synthetic-pass.json"),
                                 "--output-dir", str(tmp_path / "promptfoo")))
    assert exported["model_calls"] == 0
    assert Path(exported["config"]).is_file()
    assert not workspace.exists()


@pytest.mark.parametrize("command", ["followup", "interview", "json-resume", "browser-prepare", "quality"])
def test_discovery_only_mode_does_not_gain_new_command_authority(tmp_path, command):
    workspace = tmp_path / "uncreated"
    # A real subcommand reaches the root authorization callback before argument validation.
    sub = {"followup": "pending", "interview": "prepare", "json-resume": "check",
           "browser-prepare": "plan", "quality": "run"}[command]
    output = invoke(workspace, command, sub, code=2, extra_env={"APPLYPILOT_DISCOVERY_ONLY": "1"})
    assert "Discovery-only mode blocks" in output
    assert not workspace.exists()
