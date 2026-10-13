"""The comparison entry uses safe IDs and fictional data without bootstrap."""

import hashlib
import json
import sqlite3

from typer.testing import CliRunner

from applypilot.cli import app


def setup_workspace(path):
    path.mkdir()
    with sqlite3.connect(path / "applypilot.db") as conn:
        conn.execute("CREATE TABLE jobs (url TEXT, platform_job_id TEXT, title TEXT, company_name TEXT, salary TEXT)")
        conn.executemany("INSERT INTO jobs VALUES (?, ?, ?, ?, ?)", [
            ("https://example.test/one?token=PRIVATE", "one", "First role", "Fictional Company", "SGD 1,800/month"),
            ("https://example.test/two", "two", "Second role", "Fictional Company", None),
        ])


def invoke(workspace, *args):
    result = CliRunner().invoke(app, ["--workspace", str(workspace), "compare-jobs", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_cli_list_and_export_preserve_exact_order_and_source(tmp_path, monkeypatch):
    from applypilot import cli

    monkeypatch.delenv("APPLYPILOT_DISCOVERY_ONLY", raising=False)

    def forbidden():
        raise AssertionError("Comparison must not bootstrap or call a model")

    monkeypatch.setattr(cli, "_bootstrap", forbidden)
    workspace = tmp_path / "workspace"
    setup_workspace(workspace)
    before = hashlib.sha256((workspace / "applypilot.db").read_bytes()).hexdigest()
    listing = invoke(workspace, "--list", "--query", "role", "--limit", "1")
    assert listing["count"] == 2 and len(listing["jobs"]) == 1
    assert "PRIVATE" not in json.dumps(listing) and "https://" not in json.dumps(listing)
    output = tmp_path / "comparison"
    result = invoke(workspace, "--job-id", "platform:two", "--job-id", "platform:one", "--output-dir", str(output))
    assert result["count"] == 2 and set(result["files"]) == {"html", "markdown"}
    assert result["job_ids"][0] != result["job_ids"][1]
    html = (output / "job-comparison.html").read_text(encoding="utf-8")
    assert html.index("Second role") < html.index("First role")
    assert "SGD 1,800/month" in html and "PRIVATE" not in html and "https://" not in html
    assert hashlib.sha256((workspace / "applypilot.db").read_bytes()).hexdigest() == before
    assert sorted(p.name for p in workspace.iterdir()) == ["applypilot.db"]


def test_cli_invalid_inputs_do_not_initialize_or_partially_export(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLYPILOT_DISCOVERY_ONLY", raising=False)
    workspace = tmp_path / "missing"
    runner = CliRunner()
    base = ["--workspace", str(workspace), "compare-jobs"]
    assert runner.invoke(app, [*base, "--list"]).exit_code != 0
    assert not workspace.exists()
    setup_workspace(workspace)
    output = tmp_path / "not-created"
    for args in (["--job-id", "one", "--output-dir", str(output)],
                 ["--job-id", "one", "--job-id", "missing", "--output-dir", str(output)],
                 ["--list", "--job-id", "one"],
                 ["--job-id", "one", "--job-id", "two", "--query", "role", "--output-dir", str(output)]):
        result = runner.invoke(app, [*base, *args])
        assert result.exit_code != 0
        assert not output.exists()
