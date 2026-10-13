"""Short HTTP acceptance for explicit local reminder edits."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import test_job_progress_browser as progress_fixture

from applypilot import followup, followup_reminders
from applypilot.career.server import PREFIX

progress = progress_fixture.progress
ROUTE = PREFIX + "/job-progress/reminders"


def authorize(client):
    token = client.get(PREFIX + "/bootstrap").json()["token"]
    client.headers.update({"Origin": str(client.base_url).rstrip("/"), "X-Career-Token": token})


def test_set_postpone_and_hide_only_edit_local_reminders(progress):
    client, db = progress
    today = datetime.now(UTC).date()
    tomorrow = (today + timedelta(days=1)).isoformat()
    original = db.read_bytes()
    empty = client.get(ROUTE).json()
    assert empty["counts"] == {"overdue": 0, "today": 0, "future": 0, "undated": 0}
    assert empty["unscheduled_count"] == 103 and empty["limit"] == 100
    assert not (db.parent / "followup-reminders.json").exists()
    record = client.get(PREFIX + "/job-progress").json()["rows"][0]
    payload = {"record_id": record["record_id"], "due_date": today.isoformat(), "next_step": "准备三个面试项目例子"}
    assert client.post(ROUTE, json=payload).status_code == 403
    authorize(client)
    saved = client.post(ROUTE, json=payload)
    assert saved.status_code == 200, saved.text
    identity = saved.json()["reminder_id"]
    current = client.get(ROUTE).json()
    assert current["counts"]["today"] == 1 and current["unscheduled_count"] == 102
    assert current["groups"]["today"][0]["record_id"] == record["record_id"]
    changed = client.post(ROUTE + f"/{identity}/snooze", json={"due_date": tomorrow})
    assert changed.status_code == 200, changed.text
    assert client.get(ROUTE).json()["counts"]["future"] == 1
    too_early = client.post(ROUTE + f"/{identity}/snooze", json={"due_date": today.isoformat()})
    assert too_early.status_code == 422 and too_early.json()["error"]["code"] == "reminder_date_before_current"
    assert client.post(ROUTE + f"/{identity}/dismiss", json={}).status_code == 200
    assert client.get(ROUTE).json()["counts"]["future"] == 0
    assert db.read_bytes() == original
    content = (db.parent / "followup-reminders.json").read_text(encoding="utf-8")
    assert "PRIVATE" not in content and "example.test" not in content
    assert json.loads(content)["items"][identity]["dismissed_at"]


def test_existing_action_override_and_invalid_inputs_are_visible(progress):
    client, db = progress
    today = datetime.now(UTC).date()
    with sqlite3.connect(db) as conn:
        action = followup.add_action(conn, "https://example.test/1?token=PRIVATE", "PRIVATE ACTION SUMMARY", today.isoformat() + "T00:00:00Z")
        action_before = conn.execute("SELECT due_at,completed_at FROM followup_actions WHERE action_id=?", (action["action_id"],)).fetchone()
    original = db.read_bytes()
    authorize(client)
    listed = client.get(ROUTE).json()
    existing = listed["groups"]["today"][0]
    assert existing["source"] == "existing_action"
    assert "PRIVATE" not in json.dumps(listed)
    tomorrow = (today + timedelta(days=1)).isoformat()
    changed = client.post(ROUTE + f'/{existing["reminder_id"]}/snooze', json={"due_date": tomorrow})
    assert changed.status_code == 200, changed.text
    assert client.get(ROUTE).json()["groups"]["future"][0]["due_date"] == tomorrow
    assert db.read_bytes() == original
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT due_at,completed_at FROM followup_actions WHERE action_id=?", (action["action_id"],)).fetchone() == action_before
    for query in ("?limit=0", "?limit=501", "?limit=100&limit=200", "?on=2030-01-01"):
        assert client.get(ROUTE + query).status_code == 422
    assert client.post(ROUTE, json={"record_id": "made-up", "due_date": today.isoformat(), "next_step": "Review"}).status_code == 409
    damaged = db.parent / "followup-reminders.json"
    damaged.write_text("{broken", encoding="utf-8")
    response = client.get(ROUTE)
    assert response.status_code == 409 and response.json()["error"]["code"] == "reminder_store_invalid"
    response = client.post(ROUTE + f'/{existing["reminder_id"]}/dismiss', json={})
    assert response.status_code == 409 and damaged.read_text(encoding="utf-8") == "{broken"
    assert db.read_bytes() == original


def test_reminder_html_download_includes_whole_selected_group(progress):
    client, db = progress
    rows = client.get(PREFIX + "/job-progress").json()["rows"]
    today = datetime.now(UTC).date()
    for row in rows[:2]:
        followup_reminders.set_reminder(db.parent, record_id=row["record_id"], due_date=today.isoformat(), next_step="Synthetic preparation")
    followup_reminders.set_reminder(db.parent, record_id=rows[2]["record_id"], due_date=(today + timedelta(days=2)).isoformat(), next_step="Future preparation")
    original = db.read_bytes()
    sidecar = (db.parent / "followup-reminders.json").read_bytes()
    assert len(client.get(ROUTE + "?limit=1").json()["groups"]["today"]) == 1
    response = client.get(ROUTE + "/download?format=html&bucket=today")
    assert response.status_code == 200, response.text
    assert response.headers["Content-Disposition"] == 'attachment; filename="followup-reminders.html"'
    assert all(row["title"] in response.text for row in rows[:2])
    assert rows[2]["title"] not in response.text and "Future preparation" not in response.text
    assert "无条数截断" in response.text
    assert client.get(ROUTE + "/download?format=html&limit=1").status_code == 422
    assert db.read_bytes() == original and (db.parent / "followup-reminders.json").read_bytes() == sidecar
