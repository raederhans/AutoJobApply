"""Synthetic reminders stay local and never mutate application state."""

import hashlib
import json
import sqlite3

import pytest

from applypilot.application_progress import register_application, sync_jobs
from applypilot.followup_reminders import (
    STORE_NAME,
    _next_step,
    dismiss_reminder,
    list_reminders,
    set_reminder,
    snooze_reminder,
)

URL = "https://example.test/jobs/42?token=URL_SECRET"
ON = "2026-10-13"


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    with sqlite3.connect(root / "applypilot.db") as conn:
        conn.execute("""CREATE TABLE jobs (
            url TEXT PRIMARY KEY, title TEXT, company_name TEXT, apply_status TEXT, applied_at TEXT,
            apply_error TEXT, tailored_resume_path TEXT, unanswered_questions_json TEXT
        )""")
        conn.execute("INSERT INTO jobs VALUES (?, 'Analyst', 'Fictional Company', 'failed', NULL, ?, ?, ?)",
                     (URL, "RAW_ERROR_SECRET", r"C:\private\RESUME_SECRET.docx",
                      '[{"answer":"ANSWER_SECRET"}]'))
        conn.execute("""CREATE TABLE followup_actions (
            action_id TEXT PRIMARY KEY, job_url TEXT NOT NULL, summary TEXT NOT NULL,
            due_at TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT
        )""")
        conn.execute("CREATE TABLE application_receipts (receipt_id TEXT, job_url TEXT, payload TEXT)")
        conn.execute("INSERT INTO application_receipts VALUES ('synthetic', ?, 'RECEIPT_SECRET')", (URL,))
    return root


def db_state(root):
    database = root / "applypilot.db"
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as conn:
        schema = conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        jobs = conn.execute("SELECT * FROM jobs").fetchall()
        receipts = conn.execute("SELECT * FROM application_receipts").fetchall()
        actions = conn.execute("SELECT * FROM followup_actions ORDER BY action_id").fetchall()
    return hashlib.sha256(database.read_bytes()).hexdigest(), schema, jobs, receipts, actions


def action(root, identity, due, *, completed=None):
    with sqlite3.connect(root / "applypilot.db") as conn:
        conn.execute("INSERT INTO followup_actions VALUES (?, ?, ?, ?, ?, ?)",
                     (identity, URL, "SUMMARY_SECRET token=ACTION_SECRET " + URL,
                      due, "2026-10-01T00:00:00Z", completed))


def hashed(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:16]


def rows(listing):
    return [row for group in listing["groups"].values() for row in group]


def first_record(root):
    return list_reminders(root, on=ON)["unscheduled"][0]["record_id"]


def test_utc_buckets_are_disjoint_and_legacy_completed_actions_and_secrets_are_hidden(workspace):
    action(workspace, "old", "2026-10-12T23:30:00Z")
    action(workspace, "today", "2026-10-12T20:00:00-04:00")
    action(workspace, "future", "2026-10-14T10:00:00+08:00")
    action(workspace, "undated", "INVALID_DATE_SECRET")
    action(workspace, "done", "2026-10-01T00:00:00Z", completed="2026-10-02T00:00:00Z")
    before = db_state(workspace)
    listing = list_reminders(workspace, on=ON)
    assert listing["timezone"] == "UTC"
    assert listing["counts"] == {"overdue": 1, "today": 1, "future": 1, "undated": 1}
    all_rows = rows(listing)
    assert len({row["reminder_id"] for row in all_rows}) == len(all_rows) == 4
    expected = {hashed("action-", name): bucket for name, bucket in
                (("old", "overdue"), ("today", "today"), ("future", "future"), ("undated", "undated"))}
    assert {row["reminder_id"]: row["bucket"] for row in all_rows} == expected
    assert listing["groups"]["today"][0]["due_date"] == ON
    assert all(row["target_available"] for row in all_rows)
    text = json.dumps(listing)
    for secret in (URL, "URL_SECRET", "RAW_ERROR_SECRET", "RESUME_SECRET", "ANSWER_SECRET",
                   "RECEIPT_SECRET", "SUMMARY_SECRET", "ACTION_SECRET", "INVALID_DATE_SECRET"):
        assert secret not in text
    assert db_state(workspace) == before
    assert not (workspace / STORE_NAME).exists()


def test_set_snooze_and_local_dismiss_only_change_sidecar_and_sanitize_short_next_step(workspace):
    before = db_state(workspace)
    rid = first_record(workspace)
    saved = set_reminder(
        workspace, record_id=rid, due_date=ON,
        next_step="Review recruiting status https://private.test/?token=NEXT_URL_SECRET",
    )
    assert saved["source"] == "local_reminder" and saved["target_available"]
    assert saved["record_id"] == rid
    assert "NEXT_URL_SECRET" not in saved["next_step"] and "https://" not in saved["next_step"]
    assert len(saved["next_step"]) <= 240
    reminder_id = saved["reminder_id"]
    assert list_reminders(workspace, on=ON)["counts"]["today"] == 1
    changed = snooze_reminder(workspace, reminder_id=reminder_id, due_date="2026-10-15")
    assert changed["due_date"] == "2026-10-15" and not changed["dismissed"]
    assert list_reminders(workspace, on=ON)["counts"]["future"] == 1
    dismissed = dismiss_reminder(workspace, reminder_id=reminder_id)
    assert dismissed["dismissed"] and "locally" in dismissed["message"]
    assert rows(list_reminders(workspace, on=ON)) == []
    assert db_state(workspace) == before
    stored = (workspace / STORE_NAME).read_text()
    for secret in (URL, "URL_SECRET", "NEXT_URL_SECRET", "RAW_ERROR_SECRET", "RESUME_SECRET"):
        assert secret not in stored
    assert not (workspace / (STORE_NAME + ".lock")).exists()
    assert list(workspace.glob(".followup-reminders-*.tmp")) == []


def test_no_url_registered_application_can_receive_a_reminder_without_creating_a_job(workspace):
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        application = register_application(
            conn, source_key="synthetic-external", company="External Company", title="No URL Role",
            job_url=None, submitted_at=None, submission_basis="reported",
        )
    before = db_state(workspace)
    rid = hashed("application-", application["application_id"])
    saved = set_reminder(workspace, record_id=rid, due_date="2026-10-14", next_step="Review recruiting feedback")
    assert saved["title"] == "No URL Role" and saved["target_available"]
    state = json.loads((workspace / STORE_NAME).read_text())
    assert state["items"][saved["reminder_id"]]["target_key"].startswith("registry-")
    assert rows(list_reminders(workspace, on=ON))[0]["record_id"] == rid
    assert db_state(workspace) == before


def test_url_target_key_survives_jobs_only_to_registry_identity_sync(workspace):
    original_rid = first_record(workspace)
    saved = set_reminder(workspace, record_id=original_rid, due_date=ON, next_step="Review existing feedback")
    state = json.loads((workspace / STORE_NAME).read_text())
    assert state["items"][saved["reminder_id"]]["target_key"] == hashed("url-", URL)
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        conn.execute("UPDATE jobs SET apply_status='applied', applied_at='2026-10-01T00:00:00Z'")
        assert sync_jobs(conn)["created"] == 1
    before = db_state(workspace)
    current = rows(list_reminders(workspace, on=ON))[0]
    assert current["target_available"] and current["record_id"] != original_rid
    assert current["reminder_id"] == saved["reminder_id"]
    refreshed = set_reminder(workspace, record_id=current["record_id"], due_date="2026-10-15",
                             next_step="Review the next update")
    assert refreshed["reminder_id"] == saved["reminder_id"]
    assert len(json.loads((workspace / STORE_NAME).read_text())["items"]) == 1
    assert db_state(workspace) == before


def test_multiple_legacy_actions_for_one_job_have_isolated_overrides(workspace):
    action(workspace, "first", "2026-10-12T00:00:00Z")
    action(workspace, "second", "2026-10-14T00:00:00Z")
    first = hashed("action-", "first")
    second = hashed("action-", "second")
    before = db_state(workspace)
    snooze_reminder(workspace, reminder_id=first, due_date="2026-10-16")
    listing = {row["reminder_id"]: row for row in rows(list_reminders(workspace, on=ON))}
    assert listing[first]["due_date"] == "2026-10-16"
    assert listing[second]["due_date"] == "2026-10-14"
    state = json.loads((workspace / STORE_NAME).read_text())
    assert set(state["overrides"]) == {first}
    response = dismiss_reminder(workspace, reminder_id=second)
    assert response["source"] == "existing_action" and response["dismissed"]
    assert {row["reminder_id"] for row in rows(list_reminders(workspace, on=ON))} == {first}
    assert set(json.loads((workspace / STORE_NAME).read_text())["overrides"]) == {first, second}
    assert db_state(workspace) == before


def test_database_completed_action_wins_over_overlay_and_cannot_be_revived(workspace):
    action(workspace, "done", "2026-10-12T00:00:00Z", completed="2026-10-13T00:00:00Z")
    identity = hashed("action-", "done")
    sidecar = workspace / STORE_NAME
    sidecar.write_text(json.dumps({"version": 1, "items": {}, "overrides": {
        identity: {"due_date": "2026-10-15"},
    }}))
    before = db_state(workspace)
    content = sidecar.read_bytes()
    assert rows(list_reminders(workspace, on=ON)) == []
    with pytest.raises(ValueError, match="inactive"):
        snooze_reminder(workspace, reminder_id=identity, due_date="2026-10-16")
    assert sidecar.read_bytes() == content
    assert db_state(workspace) == before


@pytest.mark.parametrize("content", ["{", '{"version":2,"items":{},"overrides":{}}',
                                   '{"version":1,"items":[],"overrides":{}}'])
def test_invalid_json_is_preserved_and_cannot_be_overwritten(workspace, content):
    rid = first_record(workspace)
    sidecar = workspace / STORE_NAME
    sidecar.write_text(content)
    before = db_state(workspace)
    with pytest.raises(ValueError, match="preserve"):
        list_reminders(workspace, on=ON)
    with pytest.raises(ValueError, match="preserve"):
        set_reminder(workspace, record_id=rid, due_date=ON, next_step="Review status")
    assert sidecar.read_text() == content
    assert db_state(workspace) == before
    assert not (workspace / (STORE_NAME + ".lock")).exists()
    assert list(workspace.glob(".followup-reminders-*.tmp")) == []


def test_concurrent_edit_lock_refuses_changes_and_preserves_existing_files(workspace):
    saved = set_reminder(workspace, record_id=first_record(workspace), due_date=ON, next_step="Review status")
    sidecar = workspace / STORE_NAME
    content = sidecar.read_bytes()
    lock = workspace / (STORE_NAME + ".lock")
    lock.write_text("synthetic concurrent writer")
    before = db_state(workspace)
    with pytest.raises(ValueError, match="edit is active"):
        snooze_reminder(workspace, reminder_id=saved["reminder_id"], due_date="2026-10-15")
    assert sidecar.read_bytes() == content
    assert lock.read_text() == "synthetic concurrent writer"
    assert rows(list_reminders(workspace, on=ON))[0]["due_date"] == ON
    assert db_state(workspace) == before


def test_failed_atomic_replace_keeps_prior_json_and_cleans_own_temporary_files(workspace, monkeypatch):
    from applypilot import followup_reminders

    saved = set_reminder(workspace, record_id=first_record(workspace), due_date=ON, next_step="Review status")
    sidecar = workspace / STORE_NAME
    content = sidecar.read_bytes()

    def fail_replace(*args):
        raise OSError("Synthetic replace failure")

    monkeypatch.setattr(followup_reminders.os, "replace", fail_replace)
    with pytest.raises(OSError, match="Synthetic"):
        snooze_reminder(workspace, reminder_id=saved["reminder_id"], due_date="2026-10-14")
    assert sidecar.read_bytes() == content
    assert not (workspace / (STORE_NAME + ".lock")).exists()
    assert list(workspace.glob(".followup-reminders-*.tmp")) == []


def test_read_only_list_does_not_create_sidecar_or_missing_workspace(workspace, tmp_path):
    before = set(workspace.iterdir())
    listing = list_reminders(workspace, on=ON)
    assert listing["unscheduled_count"] == 1 and rows(listing) == []
    assert set(workspace.iterdir()) == before
    missing = tmp_path / "absent"
    assert rows(list_reminders(missing, on=ON)) == []
    assert not missing.exists()


@pytest.mark.parametrize("due", ["2026-1-01", "2026-02-30", "2026-10-13T00:00:00Z", "DATE_SECRET"])
def test_invalid_dates_do_not_leave_a_sidecar(workspace, due):
    before = db_state(workspace)
    with pytest.raises(ValueError):
        set_reminder(workspace, record_id=first_record(workspace), due_date=due, next_step="Review status")
    assert not (workspace / STORE_NAME).exists()
    assert not (workspace / (STORE_NAME + ".lock")).exists()
    assert db_state(workspace) == before


def test_unknown_ids_and_unsafe_bare_labels_do_not_leave_a_sidecar(workspace):
    before = db_state(workspace)
    rid = first_record(workspace)
    with pytest.raises(ValueError, match="Unknown record_id"):
        set_reminder(workspace, record_id="application-" + "0" * 16, due_date=ON, next_step="Review status")
    with pytest.raises(ValueError, match="Unknown or inactive"):
        snooze_reminder(workspace, reminder_id="reminder-" + "0" * 16, due_date="2026-10-14")
    for label in ("https://example.test/?token=SECRET", "token=SECRET", "Review status token=NEXT_SECRET", "   "):
        with pytest.raises(ValueError, match="short next step"):
            set_reminder(workspace, record_id=rid, due_date=ON, next_step=label)
    assert not (workspace / STORE_NAME).exists()
    assert not (workspace / (STORE_NAME + ".lock")).exists()
    assert db_state(workspace) == before


@pytest.mark.parametrize("stage", ["rejected", "withdrawn", "accepted", "declined", "conflict"])
def test_terminal_next_step_never_replays_a_user_supplied_submission_instruction(stage):
    label = _next_step({"recruiting_stage": stage}, "SUBMIT_AGAIN_SECRET")
    assert "closed or conflicting" in label and "SUBMIT_AGAIN_SECRET" not in label


def test_uncertain_next_step_requires_existing_receipt_review_before_any_action():
    label = _next_step({"application_status": "submission_uncertain"}, "SUBMIT_AGAIN_SECRET")
    assert "existing submission receipt" in label and "SUBMIT_AGAIN_SECRET" not in label
