"""Small real HTTP checks for the read-only job progress browser entry."""

from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

import httpx
import pytest

from applypilot.career import job_progress
from applypilot.career.server import PREFIX, start_background
from applypilot.database import close_connection, init_db


@pytest.fixture
def progress(tmp_path):
    db = tmp_path / "applypilot.db"
    conn = init_db(db)
    for index in range(103):
        conn.execute("INSERT INTO jobs(url,title,company_name,location,apply_status,last_attempted_at) "
                     "VALUES(?,?,?,?,?,?)", (f"https://example.test/{index}?token=PRIVATE", f"Analyst {index:03}",
                     "Fictional Co", "Singapore", "failed", "2026-10-13T04:00:00Z"))
    conn.execute("UPDATE jobs SET title='=100% Analyst <script>',last_attempted_at=NULL WHERE title='Analyst 000'")
    conn.execute("UPDATE jobs SET apply_status='applied',applied_at='2026-10-12T12:00:00Z' WHERE title='Analyst 001'")
    conn.commit()
    close_connection(db)
    instance = start_background(tmp_path)
    try:
        with httpx.Client(base_url=instance.origin, trust_env=False, timeout=10) as client:
            yield client, db
    finally:
        instance.shutdown()
        instance.server_close()


def test_preview_pagination_and_download_share_filters_without_writes(progress):
    client, db = progress
    before = db.read_bytes()
    route = PREFIX + "/job-progress"
    response = client.get(route)
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["summary"]["record_count"] == 103 and len(first["rows"]) == 100
    assert first["summary"]["open_action_record_count"] == 0
    second = client.get(route, params={"offset": 100}).json()
    assert len(second["rows"]) == 3 and second["pagination"]["previous_offset"] == 0
    assert not ({row["record_id"] for row in first["rows"]} & {row["record_id"] for row in second["rows"]})
    csv_response = client.get(route + "/download", params={"format": "csv"})
    assert csv_response.status_code == 200, csv_response.text
    assert csv_response.headers["Content-Disposition"] == 'attachment; filename="job-progress.csv"'
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    assert len(list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))) == 103

    filters = {"q": "%", "status": "failed"}
    literal = client.get(route, params=filters).json()
    assert literal["summary"]["record_count"] == 1
    assert literal["rows"][0]["updated_at"] == literal["rows"][0]["submitted_at"] == ""
    exported = client.get(route + "/download", params={**filters, "format": "csv"})
    row = next(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert row["title"] == "'=100% Analyst <script>"
    assert row["record_id"] == literal["rows"][0]["record_id"]
    markdown = client.get(route + "/download", params={**filters, "format": "markdown"})
    assert markdown.headers["Content-Disposition"] == 'attachment; filename="job-progress.md"'
    assert "&lt;script&gt;" in markdown.text and "<script>" not in markdown.text
    assert "Literal keyword filter: %" in markdown.text
    assert "PRIVATE" not in exported.text + markdown.text + response.text
    assert "example.test" not in exported.text + markdown.text + response.text
    assert db.read_bytes() == before


def test_utc_status_filters_and_bad_browser_queries(progress):
    client, _ = progress
    route = PREFIX + "/job-progress"
    filtered = client.get(route, params=[("since", "2026-10-13"), ("until", "2026-10-13"),
                                        ("status", "failed"), ("status", "applied")]).json()
    assert filtered["summary"]["record_count"] == 102
    submitted = client.get(route, params={"date_field": "submitted_at", "since": "2026-10-12", "until": "2026-10-12"}).json()
    assert [row["title"] for row in submitted["rows"]] == ["Analyst 001"]
    for query in ("?q=a&q=b", "?offset=-1", "?date_field=private_answer", "?since=2026-10-14&until=2026-10-13",
                  "?status=not_a_status", "/download", "/download?format=html", "/download?format=csv&offset=100"):
        response = client.get(route + query)
        assert response.status_code == 422, (query, response.text)
        assert response.json()["error"]["code"] == "invalid_progress_filter"


def test_absent_progress_tables_keep_unknown_counts_and_no_schema_writes():
    with sqlite3.connect(":memory:") as conn:
        result = job_progress.preview(conn)
        assert result["available"] is False and result["summary"]["record_count"] is None
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []
        with pytest.raises(ValueError, match="progress_report_unavailable"):
            job_progress.download(conn, format="csv")


def test_explicit_followup_workspace_does_not_resolve_home(tmp_path, monkeypatch):
    from applypilot.commands.followup import _database_path

    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))

    def forbidden_home():
        raise AssertionError("An explicit workspace does not need the user's home")

    monkeypatch.setattr(Path, "home", forbidden_home)
    assert _database_path() == tmp_path / "applypilot.db"
