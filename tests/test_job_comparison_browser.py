"""Short HTTP checks for selecting existing progress records to compare."""

import sqlite3

import test_job_progress_browser as progress_fixture

from applypilot.application_progress import register_application
from applypilot.career.server import PREFIX

progress = progress_fixture.progress
ROUTE = PREFIX + "/job-progress/compare"


def test_selected_progress_ids_preserve_order_and_unknown_fields_without_writes(progress):
    client, db = progress
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE jobs SET salary='SGD 1,000 monthly',description='<p>Public role description</p>' "
                     "WHERE url='https://example.test/1?token=PRIVATE'")
        register_application(conn, source_key="fictional-comparison", company="Fictional Co", title="Analyst 001",
                             job_url="https://example.test/1?token=PRIVATE", submission_basis="unverified")
    rows = client.get(PREFIX + "/job-progress").json()["rows"]
    selected = [next(row for row in rows if row["title"] == title) for title in ("Analyst 002", "Analyst 001")]
    before = db.read_bytes()
    params = [("record_id", row["record_id"]) for row in selected]
    response = client.get(ROUTE, params=params)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["records"] == [{key: row[key] for key in ("record_id", "title", "company")} for row in selected]
    fields = {row["key"]: row["values"] for row in result["fields"]}
    assert fields["salary"] == ["未知（未记录或不可安全展示）", "SGD 1,000 monthly"]
    assert fields["deadline"] == ["未知（未记录或不可安全展示）"] * 2
    assert fields["description_excerpt"][1] == "Public role description"
    assert "PRIVATE" not in response.text and "example.test" not in response.text and "fit_score" not in response.text
    assert all(len(row["values"]) == 2 for row in result["fields"])
    for format, extension in (("html", "html"), ("markdown", "md")):
        downloaded = client.get(ROUTE + "/download", params=[("format", format), *params])
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.headers["Content-Disposition"] == f'attachment; filename="job-comparison.{extension}"'
        assert downloaded.text.index("Analyst 002") < downloaded.text.index("Analyst 001")
        assert "未知" in downloaded.text and "SGD 1,000 monthly" in downloaded.text
        assert "PRIVATE" not in downloaded.text and "example.test" not in downloaded.text and "<script" not in downloaded.text
    assert db.read_bytes() == before


def test_invalid_stale_and_unavailable_comparison_selections_fail_without_writes(progress):
    client, db = progress
    rows = client.get(PREFIX + "/job-progress").json()["rows"]
    ids = [row["record_id"] for row in rows[:5]]
    before = db.read_bytes()
    for formats in ([], [("format", "csv")], [("format", "html")] * 2):
        response = client.get(ROUTE + "/download", params=[*formats, *(("record_id", rid) for rid in ids[:2])])
        assert response.status_code == 422, response.text
    for params in ([], [("record_id", ids[0])], [("record_id", ids[0])] * 2,
                   [("record_id", rid) for rid in ids], [("record_id", "not-a-public-id"), ("record_id", ids[0])],
                   [("record_id", rid) for rid in ids[:2]] + [("q", "ignored")]):
        response = client.get(ROUTE, params=params)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "comparison_selection_invalid"
    assert db.read_bytes() == before
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM jobs WHERE title=?", (rows[0]["title"],))
    before = db.read_bytes()
    stale = client.get(ROUTE, params=[("record_id", rid) for rid in ids[:2]])
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "comparison_record_changed"
    assert db.read_bytes() == before
