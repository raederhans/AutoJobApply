"""Small synthetic checks for private, read-only progress exports."""

import csv
import hashlib
import io
import json
import sqlite3
from pathlib import Path

import pytest

from applypilot import followup
from applypilot.application_progress import register_application
from applypilot.progress_report import collect_report, export_progress, render_csv, render_markdown


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def jobs(connection, extra_columns=""):
    connection.execute(
        "CREATE TABLE jobs (url TEXT PRIMARY KEY, title TEXT, company_name TEXT, apply_status TEXT"
        + extra_columns + ")"
    )


def insert_job(connection, **fields):
    names = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    connection.execute(f"INSERT INTO jobs ({names}) VALUES ({placeholders})", tuple(fields.values()))


def application(connection, key, *, url=None, basis="user_confirmed"):
    record = register_application(
        connection, source_key=key, company="Synthetic Company", title="Analyst " + key,
        job_url=url, submitted_at="2026-10-01T09:00:00Z", submission_basis=basis,
    )
    connection.execute("UPDATE followup_applications SET created_at='2026-10-01T09:01:00Z'")
    connection.commit()
    return record["application_id"]


def test_minimal_legacy_jobs_and_empty_database_are_readable(conn):
    empty = collect_report(conn)
    assert empty["rows"] == []
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    jobs(conn)
    insert_job(conn, url="https://example.test/jobs/1", title="Analyst", company_name="Example",
               apply_status=None)
    row = collect_report(conn)["rows"][0]
    assert (row["title"], row["company"], row["application_status"]) == ("Analyst", "Example", "not_started")
    assert row["updated_at"] == row["updated_at_source"] == row["submitted_at"] == ""
    assert collect_report(conn, since="2026-10-01")["rows"] == []
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("jobs",)]


def test_followup_only_and_exact_url_merge_keep_recruiting_semantics(conn):
    external = application(conn, "external")
    assert len(collect_report(conn)["rows"]) == 1
    assert collect_report(conn)["rows"][0]["submission_basis"] == "user_confirmed"
    jobs(conn)
    url = "https://example.test/jobs/42?token=URL_SECRET"
    insert_job(conn, url=url, title="Merged role", company_name="Job company", apply_status="failed")
    merged = application(conn, "merged", url=url)
    # Same employer/title is deliberately insufficient to match a different URL.
    application(conn, "different-url", url="https://example.test/jobs/42")
    followup.import_events(conn, [{
        "provider": "synthetic", "message_id": "SECRET_MESSAGE", "application_id": merged,
        "job_url": url, "event_type": "rejected", "occurred_at": "2026-10-03T12:00:00Z",
        "evidence_ref": "SECRET_EVIDENCE", "summary": "SECRET_SUMMARY",
    }])
    data = collect_report(conn)
    assert len(data["rows"]) == 3
    row = next(row for row in data["rows"] if row["title"] == "Merged role")
    assert row["application_status"] == "failed"
    assert row["recruiting_stage"] == "rejected"
    assert row["updated_at"] == "2026-10-03T12:00:00+00:00"
    assert row["updated_at_source"] == "followup_events.occurred_at"
    assert collect_report(conn, statuses=["rejected"])["rows"] == [row]
    serialized = json.dumps(data)
    assert external not in serialized and merged not in serialized
    for secret in (url, "URL_SECRET", "SECRET_MESSAGE", "SECRET_EVIDENCE", "SECRET_SUMMARY"):
        assert secret not in serialized


def test_fixed_todo_and_secret_material_fields_are_omitted(tmp_path):
    database = tmp_path / "applypilot.db"
    url = "https://example.test/jobs/42?token=URL_SECRET"
    with sqlite3.connect(database) as connection:
        jobs(connection, ", location TEXT, apply_retry_reason TEXT, apply_error TEXT, "
             "application_readiness_reason TEXT, tailored_resume_path TEXT, unanswered_questions_json TEXT, "
             "application_evidence TEXT, submission_observation_json TEXT, discovered_at TEXT")
        insert_job(
            connection, url=url, title="Analyst Bearer TITLE_SECRET",
            company_name="Company token=COMPANY_SECRET www.example.test/?token=COMPANY_URL_SECRET",
            location="Singapore", apply_status="failed",
            apply_retry_reason="assessment_required token=RETRY_SECRET",
            apply_error="password=ERROR_SECRET", application_readiness_reason="ANSWER_SECRET",
            tailored_resume_path=r"C:\private\RESUME_SECRET.docx",
            unanswered_questions_json='[{"answer":"ANSWER_SECRET"}]',
            application_evidence="EVIDENCE_SECRET", submission_observation_json='{"cookie":"COOKIE_SECRET"}',
            discovered_at="2026-10-01T00:00:00Z",
        )
        identity = application(connection, "private", url=url)
        followup.import_events(connection, [{
            "provider": "synthetic", "message_id": "MESSAGE_SECRET", "application_id": identity,
            "job_url": url, "event_type": "manual_note", "occurred_at": "2026-10-02T00:00:00Z",
            "evidence_ref": "EVIDENCE_REF_SECRET", "summary": "SUMMARY_SECRET",
        }])
        followup.add_action(connection, url, "ACTION_SUMMARY_SECRET", "2026-10-15T09:00:00Z")
        report = collect_report(connection)
        row = report["rows"][0]
        assert row["todo_reason"] == "assessment_required; followup_action_pending"
        assert row["todo_due_at"] == "2026-10-15T09:00:00+00:00"
        assert row["open_action_count"] == 1
    result = export_progress(database, tmp_path / "reports")
    output = "\n".join(Path(path).read_text(encoding="utf-8-sig") for path in result["files"].values())
    for secret in (url, "URL_SECRET", "TITLE_SECRET", "COMPANY_SECRET", "COMPANY_URL_SECRET",
                   "RETRY_SECRET", "ERROR_SECRET", "ANSWER_SECRET", "RESUME_SECRET", "EVIDENCE_SECRET",
                   "COOKIE_SECRET", "MESSAGE_SECRET", "EVIDENCE_REF_SECRET", "SUMMARY_SECRET",
                   "ACTION_SUMMARY_SECRET"):
        assert secret not in output
    assert "assessment_required" in output


def test_confirmed_readiness_does_not_create_a_review_todo(conn):
    jobs(conn, ", application_readiness_status TEXT")
    insert_job(conn, url="https://example.test/jobs/ready", title="Ready role", company_name="Example",
               apply_status=None, application_readiness_status="confirmed")
    assert collect_report(conn)["rows"][0]["todo_reason"] == ""


def test_reported_identity_preserves_unknown_submission_date(conn):
    jobs(conn, ", applied_at TEXT")
    url = "https://example.test/jobs/platform-export"
    insert_job(conn, url=url, title="Reported role", company_name="Example", apply_status="applied",
               applied_at="2026-10-12T10:00:00Z")
    register_application(
        conn, source_key="synthetic-platform-observation", company="Example", title="Reported role",
        job_url=url, submitted_at=None, submission_basis="reported",
    )
    row = collect_report(conn)["rows"][0]
    assert row["submission_basis"] == "reported"
    assert row["submitted_at"] == ""
    assert collect_report(conn, date_field="submitted_at", since="2026-10-12", until="2026-10-12")["rows"] == []


def test_utc_dates_status_filter_unknown_date_and_timestamp_source(conn):
    jobs(conn, ", discovered_at TEXT, last_attempted_at TEXT, applied_at TEXT")
    insert_job(conn, url="https://example.test/jobs/failed", title="Failed", company_name="Example",
               apply_status="failed", last_attempted_at="2026-10-12T23:30:00-02:00",
               discovered_at="2026-10-10T12:00:00Z")
    insert_job(conn, url="https://example.test/jobs/applied", title="Applied", company_name="Example",
               apply_status="applied", applied_at="2026-10-12T01:00:00+08:00")
    insert_job(conn, url="https://example.test/jobs/unknown", title="Unknown", company_name="Example",
               apply_status="SECRET_STATUS", discovered_at="not-a-date")
    selected = collect_report(conn, since="2026-10-13", until="2026-10-13", statuses=["failed"])
    assert [row["title"] for row in selected["rows"]] == ["Failed"]
    row = selected["rows"][0]
    assert row["updated_at"] == "2026-10-13T01:30:00+00:00"
    assert row["updated_at_source"] == "jobs.last_attempted_at"
    assert selected["timezone"] == "UTC"
    assert selected["application_status_counts"] == {"failed": 1}
    submitted = collect_report(conn, date_field="submitted_at", since="2026-10-11", until="2026-10-11")
    assert [row["title"] for row in submitted["rows"]] == ["Applied"]
    discovered = collect_report(conn, date_field="discovered_at", since="2026-10-10", until="2026-10-10")
    assert [row["title"] for row in discovered["rows"]] == ["Failed"]
    unknown = next(row for row in collect_report(conn)["rows"] if row["title"] == "Unknown")
    assert unknown["application_status"] == "unknown"
    assert unknown["discovered_at"] == unknown["updated_at"] == unknown["updated_at_source"] == ""
    assert [row["title"] for row in collect_report(conn, statuses=["unknown"])["rows"]] == ["Unknown"]


@pytest.mark.parametrize("filters", [
    {"date_field": "private_answer"}, {"statuses": ["SECRET_STATUS"]},
    {"since": "2026-1-01"}, {"since": "2026-10-13", "until": "2026-10-12"},
])
def test_invalid_filters_are_rejected(conn, filters):
    with pytest.raises(ValueError):
        collect_report(conn, **filters)


@pytest.mark.parametrize("prefix", ["=1+1", "+SUM(A1)", "-1+2", "@SUM(A1)"])
def test_csv_formula_prefix_and_markdown_markup_are_safe(conn, prefix):
    jobs(conn)
    title = prefix + " | [link](javascript:run) <script>"
    insert_job(conn, url="https://example.test/jobs/formula", title=title, company_name="Example",
               apply_status=None)
    report = collect_report(conn)
    exported_row = next(csv.DictReader(io.StringIO(render_csv(report))))
    assert exported_row["title"] == "'" + title
    markdown = render_markdown(report)
    assert "\\| \\[link\\]\\(javascript:run\\)" in markdown
    assert "&lt;script&gt;" in markdown
    assert "<script>" not in markdown and "[link](javascript:run)" not in markdown


def test_export_keeps_database_hash_and_schema_and_refuses_existing_files(tmp_path):
    database = tmp_path / "applypilot.db"
    with sqlite3.connect(database) as connection:
        jobs(connection)
        insert_job(connection, url="https://example.test/jobs/42", title="Analyst", company_name="Example",
                   apply_status="failed")
        before_schema = connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    before_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    result = export_progress(database, tmp_path / "reports")
    assert result["count"] == 1
    assert set(result["files"]) == {"csv", "markdown"}
    assert Path(result["files"]["csv"]).read_bytes().startswith(b"\xef\xbb\xbf")
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before_hash
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall() == before_schema
        assert connection.execute("SELECT apply_status FROM jobs").fetchone() == ("failed",)
    saved = {kind: Path(path).read_bytes() for kind, path in result["files"].items()}
    with pytest.raises(ValueError, match="already exist"):
        export_progress(database, tmp_path / "reports")
    assert {kind: Path(path).read_bytes() for kind, path in result["files"].items()} == saved


@pytest.mark.parametrize("name", ["job-progress.csv", "job-progress.md"])
def test_export_cannot_overwrite_a_database_named_like_a_report(tmp_path, name):
    database = tmp_path / name
    with sqlite3.connect(database) as connection:
        jobs(connection)
    before = database.read_bytes()
    with pytest.raises(ValueError, match="already exist"):
        export_progress(database, tmp_path)
    assert database.read_bytes() == before


def test_missing_database_does_not_initialize_workspace_or_output(tmp_path):
    database = tmp_path / "absent" / "applypilot.db"
    output = tmp_path / "reports"
    with pytest.raises(ValueError, match="Existing applypilot.db"):
        export_progress(database, output)
    assert not database.parent.exists() and not output.exists()
