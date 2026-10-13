"""Filtered offline HTML CLI tests use fictional temporary workspaces."""

import hashlib
import json
import sqlite3

from typer.testing import CliRunner

from applypilot.cli import app
from applypilot.followup_reminders import list_reminders, set_reminder


def make_workspace(path):
    path.mkdir()
    with sqlite3.connect(path / "applypilot.db") as conn:
        conn.execute("CREATE TABLE jobs (url TEXT, title TEXT, company_name TEXT, apply_status TEXT, last_attempted_at TEXT)")
        conn.executemany("INSERT INTO jobs VALUES (?, ?, ?, ?, ?)", [
            ("https://example.test/jobs/1?token=PRIVATE", "Selected Role", "Fictional Company", "failed", "2026-10-13T01:00:00Z"),
            ("https://example.test/jobs/2", "UNSELECTED_ROLE", "UNSELECTED_COMPANY", "applied", "2026-10-11T01:00:00Z"),
        ])


def test_real_cli_progress_and_reminder_html_entries(tmp_path, monkeypatch):
    from applypilot import cli

    monkeypatch.delenv("APPLYPILOT_DISCOVERY_ONLY", raising=False)

    def forbidden():
        raise AssertionError("HTML export cannot bootstrap or load a profile")

    monkeypatch.setattr(cli, "_bootstrap", forbidden)
    workspace = tmp_path / "workspace"
    make_workspace(workspace)
    rid = next(row["record_id"] for row in list_reminders(workspace)["unscheduled"] if row["title"] == "Selected Role")
    set_reminder(workspace, record_id=rid, due_date="2026-10-13", next_step="Review <script>literal</script> task")
    database = workspace / "applypilot.db"
    sidecar = workspace / "followup-reminders.json"
    before = hashlib.sha256(database.read_bytes()).hexdigest(), sidecar.read_bytes()
    runner = CliRunner()
    for kind, options in (
        ("progress", ["--since", "2026-10-13", "--until", "2026-10-13"]),
        ("reminders", ["--on", "2026-10-13", "--bucket", "today", "--since", "2026-10-13"]),
    ):
        output = tmp_path / kind
        result = runner.invoke(app, ["--workspace", str(workspace), "followup", "export-html",
                                    "--kind", kind, "--output-dir", str(output), "--status", "failed", *options])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["count"] == 1 and payload["kind"] == kind
        assert payload["timezone"] == "UTC"
        assert len(list(output.iterdir())) == 1
        document = next(output.iterdir()).read_text(encoding="utf-8")
        assert "Selected Role" in document
        for secret in ("UNSELECTED_ROLE", "UNSELECTED_COMPANY", "PRIVATE", "https://example.test", "<script>"):
            assert secret not in document
        assert "Content-Security-Policy" in document
    assert (hashlib.sha256(database.read_bytes()).hexdigest(), sidecar.read_bytes()) == before


def test_cli_invalid_scope_and_missing_workspace_do_not_initialize_data(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLYPILOT_DISCOVERY_ONLY", raising=False)
    workspace = tmp_path / "workspace"
    output = tmp_path / "not-created"
    runner = CliRunner()
    base = ["--workspace", str(workspace), "followup", "export-html", "--output-dir", str(output)]
    assert runner.invoke(app, base).exit_code != 0
    assert not workspace.exists() and not output.exists()
    make_workspace(workspace)
    for options in (["--kind", "dashboard"], ["--on", "2026-10-13"],
                    ["--kind", "reminders", "--date-field", "submitted_at"],
                    ["--kind", "reminders", "--bucket", "not-a-bucket"]):
        result = runner.invoke(app, [*base, *options])
        assert result.exit_code != 0
        assert not output.exists()
