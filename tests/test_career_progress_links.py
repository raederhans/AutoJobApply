"""The workbench links records by exact saved identity, never display labels."""

import csv
import io
import sqlite3

import test_application_inbox as inbox_fixture

from applypilot.application_progress import register_application
from applypilot.career.server import PREFIX

inbox = inbox_fixture.inbox


def test_record_filter_and_exact_inbox_link_ignore_equal_titles(inbox):
    client, db = inbox
    records = PREFIX + "/job-progress"
    queue = PREFIX + "/application-inbox"
    first = client.get(records).json()["rows"][0]
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO jobs(url,title,company_name) VALUES(?,?,?)",
                     ("https://example.test/different-role", first["title"], first["company"]))
    rows = client.get(records).json()["rows"]
    other = next(row for row in rows if row["record_id"] != first["record_id"])
    exact = client.get(queue, params={"record_id": first["record_id"], "scope": "all"})
    assert exact.status_code == 200, exact.text
    assert [item["id"] for item in exact.json()["items"]] == ["exception-1"]
    assert set(exact.json()["record_filter"]) == {"record_id", "title", "company"}
    assert client.get(queue, params={"record_id": other["record_id"]}).json()["pagination"]["total"] == 0
    selected = client.get(records, params={"record_id": first["record_id"]}).json()
    assert [row["record_id"] for row in selected["rows"]] == [first["record_id"]]
    assert selected["filters"]["record_id"] == first["record_id"]
    download = client.get(records + "/download", params={"format": "csv", "record_id": first["record_id"]})
    assert [row["record_id"] for row in csv.DictReader(io.StringIO(download.content.decode("utf-8-sig")))] == [first["record_id"]]


def test_registry_identity_change_and_missing_entry_require_fresh_selection(inbox):
    client, db = inbox
    before = client.get(PREFIX + "/job-progress").json()["rows"][0]
    with sqlite3.connect(db) as conn:
        url = conn.execute("SELECT url FROM jobs").fetchone()[0]
        register_application(conn, source_key="same-explicit-entry", company=before["company"], title=before["title"],
                             job_url=url, submitted_at=None, submission_basis="reported")
        register_application(conn, source_key="no-entry", company="Fictional", title="No saved entry",
                             job_url=None, submitted_at=None, submission_basis="reported")
    after = client.get(PREFIX + "/job-progress").json()["rows"]
    current = next(row for row in after if row["title"] == before["title"])
    missing = next(row for row in after if row["title"] == "No saved entry")
    assert current["record_id"] != before["record_id"]
    route = PREFIX + "/application-inbox"
    assert client.get(route, params={"record_id": before["record_id"]}).status_code == 409
    assert client.get(route, params={"record_id": current["record_id"]}).json()["pagination"]["total"] == 1
    response = client.get(route, params={"record_id": missing["record_id"]})
    assert response.status_code == 422 and response.json()["error"]["code"] == "progress_source_unavailable"
