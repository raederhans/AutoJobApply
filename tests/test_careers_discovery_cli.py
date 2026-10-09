"""The public discovery command never activates sources or opens the jobs DB."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from applypilot import cli


def test_careers_cli_is_allowed_in_radar_lane_without_storage(monkeypatch):
    from applypilot.discovery import career_entries

    captured = {}

    def discover(company, **kwargs):
        captured.update(company=company, **kwargs)
        return {"status": "complete", "read_only": True, "candidates": [
            {"key": "example", "status": "pending"}, {"key": "workday", "status": "unsupported"},
        ]}

    monkeypatch.setattr(career_entries, "discover_career_entries", discover)
    monkeypatch.setattr(career_entries, "candidate_to_source_config", lambda candidate, company: {
        "id": company["id"], "active": False, "review_status": "pending",
    })
    monkeypatch.setattr(cli, "_radar_bootstrap", lambda: (_ for _ in ()).throw(AssertionError("DB opened")))
    result = CliRunner().invoke(cli.app, [
        "radar", "discover-careers", "--url", "https://example.com/careers",
        "--name", "Example", "--company-id", "example", "--official-reviewed", "--max-pages", "2",
    ], env={"APPLYPILOT_DISCOVERY_ONLY": "1"})
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["source_configs"] == [{"id": "example", "active": False, "review_status": "pending"}]
    assert captured["official_relationship_reviewed"] is True
    assert captured["max_pages"] == 2


def test_careers_cli_does_not_invent_review_and_rejects_invalid_url(monkeypatch):
    from applypilot.discovery import career_entries

    def discover(company, **kwargs):
        assert kwargs["official_relationship_reviewed"] is False
        raise ValueError("public HTTPS company URL required")

    monkeypatch.setattr(career_entries, "discover_career_entries", discover)
    result = CliRunner().invoke(cli.app, [
        "radar", "discover-careers", "--url", "http://localhost/",
        "--name", "Example", "--company-id", "example",
    ])
    assert result.exit_code == 2
    assert json.loads(result.output)["status"] == "invalid_input"
