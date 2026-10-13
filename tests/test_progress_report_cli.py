"""CLI smoke coverage uses only fictional temporary workspaces."""

from __future__ import annotations

import json
import sqlite3

from typer.testing import CliRunner

from applypilot.cli import app


def make_workspace(path, title="Analyst Intern"):
    path.mkdir()
    with sqlite3.connect(path / "applypilot.db") as conn:
        conn.execute("CREATE TABLE jobs (url TEXT, title TEXT, company_name TEXT, apply_status TEXT, last_attempted_at TEXT)")
        conn.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?)",
                     ("https://example.test/jobs/1?token=PRIVATE", title, "Fictional Company", "failed",
                      "2026-10-13T04:00:00Z"))


def test_cli_exports_explicit_workspace_without_bootstrap(tmp_path, monkeypatch):
    from applypilot import cli

    def forbidden():
        raise AssertionError("Read-only export must never bootstrap or load a profile")

    monkeypatch.setattr(cli, "_bootstrap", forbidden)
    monkeypatch.delenv("APPLYPILOT_DISCOVERY_ONLY", raising=False)
    first = tmp_path / "first"
    second = tmp_path / "second"
    make_workspace(first)
    make_workspace(second, "Second Workspace Role")
    runner = CliRunner()
    for workspace in (first, second):
        output = tmp_path / (workspace.name + "-report")
        result = runner.invoke(app, ["--workspace", str(workspace), "followup", "export-progress",
                                    "--output-dir", str(output), "--since", "2026-10-13", "--until", "2026-10-13",
                                    "--status", "failed", "--status", "submission_uncertain"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["count"] == 1
        assert payload["timezone"] == "UTC"
        assert set(payload["files"]) == {"csv", "markdown"}
        csv = (output / "job-progress.csv").read_text(encoding="utf-8-sig")
        assert "PRIVATE" not in csv and "example.test" not in csv
        assert ("Second Workspace Role" in csv) == (workspace == second)
        assert sorted(p.name for p in workspace.iterdir()) == ["applypilot.db"]


def test_cli_missing_database_and_invalid_filters_fail_without_outputs(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLYPILOT_DISCOVERY_ONLY", raising=False)
    runner = CliRunner()
    absent = tmp_path / "missing"
    output = tmp_path / "not-created"
    base = ["--workspace", str(absent), "followup", "export-progress", "--output-dir", str(output)]
    result = runner.invoke(app, base)
    assert result.exit_code != 0 and "applypilot.db" in result.output
    assert not absent.exists() and not output.exists()
    make_workspace(absent)
    for args in (["--since", "2026-11-01", "--until", "2026-10-13"],
                 ["--date-field", "answer_text"], ["--status", "credential-secret"]):
        result = runner.invoke(app, [*base, *args])
        assert result.exit_code != 0
        assert not output.exists()
