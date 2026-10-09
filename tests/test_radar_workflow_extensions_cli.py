"""Public radar integration for the four workflow extensions, with isolated storage."""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from applypilot import cli, config, database


@pytest.fixture
def conn(tmp_path, monkeypatch):
    database.close_connection()
    connection = database.init_db(tmp_path / "radar.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "radar.db")
    monkeypatch.setattr(database, "get_connection", lambda *a, **k: connection)
    monkeypatch.setattr(cli, "_radar_bootstrap", lambda: None)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    (tmp_path / "linkedin_searches.yaml").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "load_radar_config", dict)
    monkeypatch.setattr(config, "radar_location_is_accepted", lambda location, *a, **k: location == "Singapore")
    yield connection
    database.close_connection(tmp_path / "radar.db")


def test_collect_passes_unfiltered_inventory_after_finished_run(conn, monkeypatch):
    from applypilot.discovery import official

    company = {"id": "example", "name": "Example", "provider": "greenhouse", "board": "example",
               "active": True, "cadence": "daily"}
    jobs = [{"url": f"https://boards.greenhouse.io/example/jobs/{index}", "title": "Data Intern",
             "company_name": "Example", "external_id": str(index), "location": location,
             "verification_status": "verified_official"}
            for index, location in [(1, "Singapore"), (2, "Other")]]
    monkeypatch.setattr(official, "load_company_watchlist", lambda: [company])
    monkeypatch.setattr(official, "collect_company", lambda c: {
        "status": "complete", "pagination_complete": True, "pages_scanned": 1,
        "raw_count": 2, "normalised_count": 2, "jobs": jobs,
        "metadata": {"inventory_scope": "fixture", "inventory_complete": True},
    })
    seen = []

    def lifecycle(connection, run_id, inventory):
        run = connection.execute("SELECT * FROM radar_fetch_runs WHERE run_id=?", (run_id,)).fetchone()
        assert run["finished_at"] and run["status"] == "complete"
        assert json.loads(run["metadata_json"])["inventory_complete"] is True
        seen.extend(inventory)
        return {"seen": len(inventory)}

    monkeypatch.setattr(database, "reconcile_posting_lifecycle", lifecycle)
    result = CliRunner().invoke(cli.app, ["radar", "collect", "--company", "example"])
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["sources"][0]["accepted"] == 1
    assert output["sources"][0]["lifecycle"]["seen"] == 2
    assert seen == jobs
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_lifecycle_cli_is_inspection_only(conn):
    result = CliRunner().invoke(cli.app, ["radar", "lifecycle", "--url", "https://example.com/jobs/unknown"],
                                env={"APPLYPILOT_DISCOVERY_ONLY": "1"})
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["posting_status"] == "legacy_untracked"
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_report_explains_unavailable_postings_without_changing_application_status():
    from applypilot.radar import render_daily_report

    report = render_daily_report(source_runs=[], posting_lifecycle=[{
        "url": "https://example.com/jobs/1", "title": "Data Intern", "company_name": "Example",
        "posting_status": "needs_reverification", "posting_reason": "verified_presence_expired",
        "possible_repost_hints": [{"url": "https://example.com/jobs/2"}],
    }])
    assert "needs_reverification" in report and "verified_presence_expired" in report
    assert "1 advisory repost hint(s)" in report
    assert "separate from application status" in report


def test_budget_cli_on_missing_workspace_does_not_create_db_or_bootstrap(tmp_path, monkeypatch):
    missing = tmp_path / "uncreated.db"
    monkeypatch.setattr(database, "DB_PATH", missing)
    monkeypatch.setattr(cli, "_radar_bootstrap", lambda: pytest.fail("plan initialized workspace"))
    result = CliRunner().invoke(cli.app, ["radar", "budget", "--budget", "2"],
                                env={"APPLYPILOT_DISCOVERY_ONLY": "1"})
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["selected"]) == 2
    assert not missing.exists()


def test_zero_official_budget_makes_no_network_or_run(conn, monkeypatch):
    from applypilot.discovery import official

    monkeypatch.setattr(official, "load_company_watchlist", lambda: [{
        "id": "example", "name": "Example", "provider": "greenhouse", "board": "example",
        "active": True, "cadence": "daily",
    }])
    monkeypatch.setattr(official, "collect_company", lambda c: pytest.fail("zero budget fetched a source"))
    result = CliRunner().invoke(cli.app, ["radar", "collect", "--budget", "0"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["sources"] == [] and payload["budget_plan"]["selected"] == []
    assert conn.execute("SELECT COUNT(*) FROM radar_fetch_runs").fetchone()[0] == 0
