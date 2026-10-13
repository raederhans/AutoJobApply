"""Offline HTML reports use selected synthetic projections only."""

import csv
import hashlib
import io
import sqlite3
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from applypilot import followup
from applypilot.application_progress import register_application
from applypilot.followup_reminders import STORE_NAME, list_reminders, set_reminder
from applypilot.local_report_html import collect_reminder_report, export_report_html
from applypilot.progress_report import collect_report, render_csv

URL = "https://example.test/jobs/selected?token=URL_SECRET"
OTHER_URL = "https://example.test/jobs/excluded?token=OTHER_URL_SECRET"
ON = "2026-10-13"
TITLE = '\"><img src=x onerror="XSS_SENTINEL">合成职位 <script>XSS_SENTINEL</script>'


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    with sqlite3.connect(root / "applypilot.db") as conn:
        conn.execute("""CREATE TABLE jobs (
            url TEXT PRIMARY KEY, title TEXT, company_name TEXT, location TEXT, apply_status TEXT,
            discovered_at TEXT, last_attempted_at TEXT, applied_at TEXT, apply_error TEXT,
            tailored_resume_path TEXT, cover_letter_path TEXT, unanswered_questions_json TEXT,
            application_evidence TEXT, full_description TEXT
        )""")
        conn.executemany("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            (URL, TITLE, "Selected Company", '\" onmouseover="LOCATION_XSS" <svg onload="LOCATION_XSS">',
             "failed", "2026-10-12T00:00:00Z", "2026-10-12T23:30:00-02:00", None,
             "password=ERROR_SECRET", r"C:\private\RESUME_SECRET.docx", r"C:\private\COVER_SECRET.pdf",
             '[{"answer":"ANSWER_SECRET"}]', "EVIDENCE_SECRET", "DESCRIPTION_SECRET"),
            (OTHER_URL, "UNSELECTED_ROLE_SENTINEL", "UNSELECTED_COMPANY_SENTINEL", "Remote", "applied",
             "2026-09-30T00:00:00Z", "2026-10-11T00:00:00Z", "2026-10-02T01:00:00+08:00",
             None, None, None, None, None, None),
        ])
        conn.execute("""CREATE TABLE followup_actions (
            action_id TEXT PRIMARY KEY, job_url TEXT NOT NULL, summary TEXT NOT NULL,
            due_at TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT
        )""")
        conn.execute("CREATE TABLE application_receipts (receipt_id TEXT, job_url TEXT, payload TEXT)")
        conn.execute("INSERT INTO application_receipts VALUES ('synthetic', ?, 'RECEIPT_SECRET')", (URL,))
    return root


def source_state(root):
    database = root / "applypilot.db"
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as conn:
        schema = conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        protected = {table: conn.execute(f"SELECT * FROM {table}").fetchall()
                     for table in ("jobs", "application_receipts", "followup_actions")}
    sidecar = root / STORE_NAME
    return (hashlib.sha256(database.read_bytes()).hexdigest(), schema, protected,
            hashlib.sha256(sidecar.read_bytes()).hexdigest() if sidecar.exists() else None)


def add_actions(root, identities, *, url=URL, due="2026-10-13T12:00:00Z", completed=None):
    with sqlite3.connect(root / "applypilot.db") as conn:
        conn.executemany("INSERT INTO followup_actions VALUES (?, ?, ?, ?, ?, ?)", [
            (identity, url, "SUMMARY_SECRET token=ACTION_SECRET " + url, due, "2026-10-01T00:00:00Z", completed)
            for identity in identities
        ])


def exported(result):
    path = Path(result["files"]["html"])
    return path, path.read_text(encoding="utf-8"), BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")


def assert_inert(document, soup):
    assert soup.find("script") is None
    assert soup.find(["img", "svg", "iframe", "object", "embed", "link", "form"]) is None
    for element in soup.find_all(True):
        assert not any(name.lower().startswith("on") for name in element.attrs)
        assert "src" not in element.attrs and "srcset" not in element.attrs
        for name in ("href", "action", "poster", "data"):
            if name in element.attrs:
                assert element[name].startswith("#")
    assert "@import" not in document and "url(" not in document
    assert "application/json" not in document


def assert_no_private_values(document):
    for secret in (URL, OTHER_URL, "URL_SECRET", "OTHER_URL_SECRET", "ERROR_SECRET", "RESUME_SECRET",
                   "COVER_SECRET", "ANSWER_SECRET", "EVIDENCE_SECRET", "DESCRIPTION_SECRET",
                   "RECEIPT_SECRET", "SUMMARY_SECRET", "ACTION_SECRET", "MESSAGE_SECRET", "QUOTE_SECRET"):
        assert secret not in document


@pytest.mark.parametrize("filters", [
    {"since": ON, "until": ON, "statuses": ["failed"], "date_field": "updated_at"},
    {"since": "2026-10-01", "until": "2026-10-01", "statuses": ["applied"], "date_field": "submitted_at"},
])
def test_progress_selection_matches_existing_projection_and_hides_unselected_records(workspace, tmp_path, filters):
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        expected = collect_report(conn, **filters)
    before = source_state(workspace)
    result = export_report_html(workspace, tmp_path / "report", **filters)
    _, document, soup = exported(result)
    assert result["kind"] == "progress" and result["timezone"] == "UTC"
    assert result["count"] == len(expected["rows"]) == 1
    assert result["filters"] == expected["filters"]
    assert len(soup.select("#records tbody tr")) == len(expected["rows"])
    expected_ids = {row["record_id"] for row in expected["rows"]}
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        other_rows = [row for row in collect_report(conn)["rows"] if row["record_id"] not in expected_ids]
    for row in expected["rows"]:
        assert row["record_id"] in document and row["title"] in soup.get_text()
    for row in other_rows:
        assert row["record_id"] not in document and row["title"] not in soup.get_text()
    assert soup.select_one(".metric .number").get_text() == "1"
    assert source_state(workspace) == before
    assert_inert(document, soup)
    assert_no_private_values(document)


def test_html_is_utf8_self_contained_and_xss_and_attribute_payloads_stay_text(workspace, tmp_path):
    result = export_report_html(workspace, tmp_path / "report", statuses=["failed"])
    path, document, soup = exported(result)
    assert path.read_bytes().decode("utf-8") == document
    assert document.lower().startswith("<!doctype html>")
    assert soup.html["lang"] == "zh-CN"
    assert soup.find("meta", charset=True)["charset"] == "utf-8"
    csp = soup.find("meta", attrs={"http-equiv": "Content-Security-Policy"})["content"]
    assert "default-src 'none'" in csp and "style-src 'unsafe-inline'" in csp
    assert "base-uri 'none'" in csp and "form-action 'none'" in csp
    assert soup.find("style") is not None
    assert soup.h1.get_text() == "求职进度"
    assert TITLE in soup.get_text() and "合成职位" in document
    assert "LOCATION_XSS" in soup.get_text()
    assert_inert(document, soup)
    assert_no_private_values(document)
    assert sorted(item.name for item in path.parent.iterdir()) == ["job-progress.html"]
    assert not (workspace / STORE_NAME).exists()


def test_reminder_bucket_due_bounds_and_execution_status_recount_without_fifty_row_truncation(workspace, tmp_path):
    add_actions(workspace, [f"today-{i}" for i in range(65)])
    add_actions(workspace, ["yesterday"], due="2026-10-12T23:00:00Z")
    add_actions(workspace, ["tomorrow"], due="2026-10-14T00:00:00Z")
    add_actions(workspace, ["other-status"], url=OTHER_URL)
    add_actions(workspace, ["completed"], completed="2026-10-13T10:00:00Z")
    before = source_state(workspace)
    filters = {"on": ON, "bucket": "today", "since": ON, "until": ON, "statuses": ["failed"]}
    report = collect_reminder_report(workspace, **filters)
    assert report["counts"] == {"today": 65}
    assert len(report["groups"]["today"]) == 65
    result = export_report_html(workspace, tmp_path / "report", kind="reminders", **filters)
    _, document, soup = exported(result)
    assert result["count"] == 65 and result["filters"] == report["filters"]
    assert result["filters"]["date_field"] == "due_date"
    assert len(soup.select("article.today")) == 65
    assert soup.select_one(".metric .number").get_text() == "65"
    for row in report["groups"]["today"]:
        assert row["reminder_id"] in document
    listing = list_reminders(workspace, on=ON, limit=100)
    selected_ids = {row["reminder_id"] for row in report["groups"]["today"]}
    for group in listing["groups"].values():
        for row in group:
            if row["reminder_id"] not in selected_ids:
                assert row["reminder_id"] not in document
    assert "UNSELECTED_ROLE_SENTINEL" not in document
    assert collect_reminder_report(workspace, on=ON, bucket="today", since="2026-10-12",
                                   until="2026-10-12", statuses=())["counts"] == {"today": 0}
    assert source_state(workspace) == before
    assert_inert(document, soup)
    assert_no_private_values(document)


def test_reminder_status_filter_matches_recruiting_stage_and_preserves_sidecar(workspace, tmp_path):
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        application = register_application(conn, source_key="synthetic-registry", company="Selected Company",
                                           title=TITLE, job_url=URL, submitted_at=None, submission_basis="reported")
        followup.import_events(conn, [{
            "provider": "synthetic", "message_id": "MESSAGE_SECRET", "application_id": application["application_id"],
            "job_url": URL, "event_type": "assessment", "occurred_at": "2026-10-12T12:00:00Z",
            "summary": "SUMMARY_SECRET", "evidence_ref": "QUOTE_SECRET",
        }])
    rid = next(row["record_id"] for row in list_reminders(workspace, on=ON)["unscheduled"]
               if row["title"] == TITLE)
    reminder = set_reminder(workspace, record_id=rid, due_date=ON,
                            next_step='<svg onload="STEP_XSS"> Prepare materials https://private.test/?token=STEP_URL_SECRET')
    before = source_state(workspace)
    result = export_report_html(workspace, tmp_path / "report", kind="reminders", on=ON,
                                since=ON, until=ON, statuses=["assessment"])
    _, document, soup = exported(result)
    assert result["count"] == 1
    assert reminder["reminder_id"] in document
    assert "STEP_XSS" in soup.get_text() and "STEP_URL_SECRET" not in document
    assert "UNSELECTED_ROLE_SENTINEL" not in document
    assert source_state(workspace) == before
    assert_inert(document, soup)
    assert_no_private_values(document)


def test_bad_sidecar_is_preserved_and_reminder_export_creates_no_output(workspace, tmp_path):
    sidecar = workspace / STORE_NAME
    sidecar.write_bytes(b'{"version":1,"items":')
    before = source_state(workspace)
    output = tmp_path / "report"
    with pytest.raises(ValueError, match="preserve"):
        export_report_html(workspace, output, kind="reminders", on=ON)
    assert source_state(workspace) == before
    assert not output.exists()


@pytest.mark.parametrize("kind", ["progress", "reminders"])
def test_existing_output_is_never_overwritten_and_sources_stay_unchanged(workspace, tmp_path, kind):
    before = source_state(workspace)
    output = tmp_path / "report"
    result = export_report_html(workspace, output, kind=kind)
    path = Path(result["files"]["html"])
    content = path.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        export_report_html(workspace, output, kind=kind)
    assert path.read_bytes() == content
    assert source_state(workspace) == before


@pytest.mark.parametrize("kind", ["progress", "reminders"])
def test_missing_data_does_not_initialize_workspace_or_output(tmp_path, kind):
    root = tmp_path / "absent"
    output = tmp_path / "report"
    with pytest.raises(ValueError, match="required"):
        export_report_html(root, output, kind=kind)
    assert not root.exists() and not output.exists()


@pytest.mark.parametrize("options", [
    {"kind": "career"}, {"on": ON}, {"bucket": "today"}, {"date_field": "due_date"},
    {"kind": "reminders", "date_field": "submitted_at"},
    {"kind": "reminders", "bucket": "SECRET_BUCKET"},
    {"kind": "reminders", "on": "2026-02-30"},
    {"kind": "reminders", "since": "2026-10-14", "until": ON},
    {"kind": "reminders", "statuses": ["SECRET_STATUS"]},
])
def test_invalid_scope_or_parameters_are_rejected_before_output_creation(workspace, tmp_path, options):
    before = source_state(workspace)
    output = tmp_path / "report"
    with pytest.raises(ValueError):
        export_report_html(workspace, output, **options)
    assert not output.exists()
    assert source_state(workspace) == before


@pytest.mark.parametrize("prefix", ["=1+1", "+SUM(A1)", "-1+2", "@SUM(A1)"])
def test_html_text_does_not_weaken_existing_csv_formula_prefix_guard(workspace, tmp_path, prefix):
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        conn.execute("UPDATE jobs SET title=? WHERE url=?", (prefix, URL))
        report = collect_report(conn, statuses=["failed"])
    csv_row = next(csv.DictReader(io.StringIO(render_csv(report))))
    assert csv_row["title"] == "'" + prefix
    result = export_report_html(workspace, tmp_path / "report", statuses=["failed"])
    _, document, soup = exported(result)
    assert soup.select_one("#records tbody tr strong").get_text() == prefix
    assert_inert(document, soup)
