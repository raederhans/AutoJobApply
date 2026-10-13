"""The reminder CLI operates only on fictional, local workspaces."""

import hashlib
import json
import sqlite3

from typer.testing import CliRunner

from applypilot.cli import app


def invoke(workspace, *arguments):
    result = CliRunner().invoke(app, ["--workspace", str(workspace), "followup", *arguments])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_cli_local_reminder_set_snooze_dismiss_cycle(tmp_path, monkeypatch):
    from applypilot import cli

    monkeypatch.delenv("APPLYPILOT_DISCOVERY_ONLY", raising=False)
    monkeypatch.setattr(cli, "_bootstrap", lambda: (_ for _ in ()).throw(AssertionError("no bootstrap")))
    workspace = tmp_path / "fictional-workspace"
    workspace.mkdir()
    database = workspace / "applypilot.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE jobs (url TEXT, title TEXT, company_name TEXT, apply_status TEXT)")
        conn.execute("INSERT INTO jobs VALUES (?, ?, ?, ?)",
                     ("https://example.test/jobs/1?token=PRIVATE_TOKEN", "Analyst Intern", "Fictional Company", "applied"))
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    initial = invoke(workspace, "reminders", "--on", "2026-10-13")
    assert initial["counts"] == {"overdue": 0, "today": 0, "future": 0, "undated": 0}
    assert sorted(p.name for p in workspace.iterdir()) == ["applypilot.db"]
    record_id = initial["unscheduled"][0]["record_id"]
    saved = invoke(workspace, "set-reminder", "--record-id", record_id, "--due-date", "2026-10-13",
                   "--next-step", "Review three examples for the interview")
    reminder_id = saved["reminder_id"]
    today = invoke(workspace, "reminders", "--on", "2026-10-13", "--bucket", "today")
    assert list(today["groups"]) == ["today"]
    assert today["groups"]["today"][0]["next_step"] == "Review three examples for the interview"
    invoke(workspace, "snooze-reminder", "--reminder-id", reminder_id, "--due-date", "2026-10-16")
    assert invoke(workspace, "reminders", "--on", "2026-10-13")["counts"]["future"] == 1
    invoke(workspace, "dismiss-reminder", "--reminder-id", reminder_id)
    assert sum(invoke(workspace, "reminders")["counts"].values()) == 0
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT apply_status FROM jobs").fetchone() == ("applied",)
    state = (workspace / "followup-reminders.json").read_text(encoding="utf-8")
    assert "PRIVATE_TOKEN" not in state and "example.test" not in state


def test_cli_workspace_isolation_and_invalid_input_do_not_initialize_data(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLYPILOT_DISCOVERY_ONLY", raising=False)
    missing = tmp_path / "missing"
    assert invoke(missing, "reminders")["unscheduled_count"] == 0
    assert not missing.exists()
    for arguments in (["set-reminder", "--record-id", "application-not-real", "--due-date", "2026-10-13", "--next-step", "Review"],
                      ["reminders", "--on", "2026-1-01"], ["reminders", "--bucket", "unknown"]):
        result = CliRunner().invoke(app, ["--workspace", str(missing), "followup", *arguments])
        assert result.exit_code != 0
        assert not missing.exists()
