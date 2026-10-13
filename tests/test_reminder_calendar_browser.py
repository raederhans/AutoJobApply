"""Short wire-format and real HTTP checks for the existing reminder download UI."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta

import test_job_progress_browser as progress_fixture

from applypilot import followup, followup_reminders
from applypilot.career.reminder_calendar import render_calendar
from applypilot.career.server import PREFIX

progress = progress_fixture.progress
ROUTE = PREFIX + "/job-progress/reminders"


def unfolded(payload: bytes) -> str:
    assert payload.endswith(b"\r\n")
    assert b"\n" not in payload.replace(b"\r\n", b"")
    assert all(len(line) <= 75 for line in payload.split(b"\r\n"))
    # Independently unfold the wire representation before decoding UTF-8.
    return re.sub(rb"\r\n[ \t]", b"", payload).decode("utf-8")


def test_calendar_text_stays_inside_properties_and_whole_utf8_lines():
    report = {"groups": {"today": [{"target_available": True, "due_date": "9999-12-31",
        "title": "中文岗位" * 30, "company": "Example, Inc; Co\\Lab",
        "next_step": "Prepare\r\nBEGIN:VALARM\nTRIGGER:-PT1M\x00"}]}}
    body, counts = render_calendar(report)
    text = unfolded(body)
    assert counts == {"events": 1, "omitted_undated": 0, "omitted_unavailable": 0}
    assert text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
    assert "DTSTART;VALUE=DATE:99991231\r\n" in text
    assert "中文岗位" * 30 in text
    assert "Example\\, Inc\\; Co\\\\Lab" in text
    assert r"Prepare\nBEGIN:VALARM\nTRIGGER:-PT1M" in text
    assert "\r\nBEGIN:VALARM" not in text and "\x00" not in text
    assert "DTEND" not in text and "DURATION" not in text
    assert "METHOD:" not in text and "ATTENDEE" not in text and "ORGANIZER" not in text
    assert text.count("BEGIN:VEVENT\r\n") == text.count("END:VEVENT\r\n") == 1
    assert "CLASS:PRIVATE\r\nTRANSP:TRANSPARENT\r\n" in text
    next_body, _ = render_calendar(report)
    assert re.search(r"UID:([^\r]+)", text)[1] != re.search(r"UID:([^\r]+)", unfolded(next_body))[1]


def test_unknown_dates_and_unavailable_targets_are_counted_without_invented_events():
    shared = {"title": "Fictional", "company": "Example", "next_step": "Review", "target_available": True}
    report = {"groups": {"undated": [
        {**shared, "due_date": ""}, {**shared, "due_date": "2026-02-30"},
        {**shared, "due_date": None}, {**shared, "due_date": "", "target_available": False},
    ]}}
    body, counts = render_calendar(report)
    assert counts == {"events": 0, "omitted_undated": 3, "omitted_unavailable": 1}
    text = unfolded(body)
    assert "BEGIN:VEVENT" not in text and "DTSTART" not in text
    assert "X-APPLYPILOT-OMITTED-UNDATED:3\r\n" in text
    assert "X-APPLYPILOT-OMITTED-UNAVAILABLE:1\r\n" in text


def test_http_calendar_uses_complete_selected_group_and_local_overrides_without_writes(progress):
    client, db = progress
    rows = client.get(PREFIX + "/job-progress").json()["rows"]
    today = datetime.now(UTC).date()
    for row in rows[:3]:
        followup_reminders.set_reminder(db.parent, record_id=row["record_id"],
                                       due_date=today.isoformat(), next_step="Prepare fictional examples")
    listing = followup_reminders.list_reminders(db.parent)
    reminder_id = listing["groups"]["today"][0]["reminder_id"]
    future_day = (today + timedelta(days=3)).isoformat()
    followup_reminders.snooze_reminder(db.parent, reminder_id=reminder_id, due_date=future_day)
    with sqlite3.connect(db) as conn:
        action = followup.add_action(conn, "https://example.test/1?token=PRIVATE", "PRIVATE SUMMARY", today.isoformat() + "T00:00:00Z")
        conn.execute("UPDATE followup_actions SET due_at='invalid-date' WHERE action_id=?", (action["action_id"],))
    before = db.read_bytes(), (db.parent / "followup-reminders.json").read_bytes()
    assert len(client.get(ROUTE + "?limit=1").json()["groups"]["today"]) == 1
    response = client.get(ROUTE + "/download?format=ics&bucket=all")
    assert response.status_code == 200, response.text
    assert response.headers["Content-Disposition"] == 'attachment; filename="followup-reminders.ics"'
    assert response.headers["Content-Type"] == "text/calendar; charset=utf-8"
    assert response.headers["X-ApplyPilot-Calendar-Events"] == "3"
    assert response.headers["X-ApplyPilot-Calendar-Omitted-Undated"] == "1"
    assert response.headers["X-ApplyPilot-Calendar-Omitted-Unavailable"] == "0"
    text = unfolded(response.content)
    assert text.count("BEGIN:VEVENT\r\n") == 3
    assert "DTSTART;VALUE=DATE:" + future_day.replace("-", "") in text
    assert "PRIVATE" not in text.replace("CLASS:PRIVATE", "") and "example.test" not in text
    selected = client.get(ROUTE + "/download?format=ics&bucket=future")
    assert selected.headers["X-ApplyPilot-Calendar-Events"] == "1"
    assert unfolded(selected.content).count("BEGIN:VEVENT\r\n") == 1
    empty = client.get(ROUTE + "/download?format=ics&bucket=undated")
    assert empty.headers["X-ApplyPilot-Calendar-Events"] == "0"
    assert empty.headers["X-ApplyPilot-Calendar-Omitted-Undated"] == "1"
    assert "BEGIN:VEVENT" not in unfolded(empty.content)
    assert (db.read_bytes(), (db.parent / "followup-reminders.json").read_bytes()) == before
    followup_reminders.dismiss_reminder(db.parent, reminder_id=reminder_id)
    assert client.get(ROUTE + "/download?format=ics&bucket=future").headers["X-ApplyPilot-Calendar-Events"] == "0"


def test_calendar_bad_scopes_and_unavailable_target_are_explicit(progress):
    client, db = progress
    today = datetime.now(UTC).date().isoformat()
    row = client.get(PREFIX + "/job-progress").json()["rows"][0]
    followup_reminders.set_reminder(db.parent, record_id=row["record_id"], due_date=today, next_step="Fictional preparation")
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM jobs WHERE title=?", (row["title"],))
    before = db.read_bytes(), (db.parent / "followup-reminders.json").read_bytes()
    response = client.get(ROUTE + "/download?format=ics&bucket=all")
    assert response.status_code == 200 and response.headers["X-ApplyPilot-Calendar-Events"] == "0"
    assert response.headers["X-ApplyPilot-Calendar-Omitted-Unavailable"] == "1"
    for query in ("format=ics&bucket=invalid", "format=ics&limit=1", "format=ics&format=html", "format=ics&record_id=made-up"):
        assert client.get(ROUTE + "/download?" + query).status_code == 422
    assert (db.read_bytes(), (db.parent / "followup-reminders.json").read_bytes()) == before
