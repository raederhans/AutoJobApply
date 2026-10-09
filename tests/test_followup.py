"""Followup contracts use fictional records and disposable databases only."""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from applypilot import followup

URL = "https://example.test/jobs/42"
OTHER_URL = "https://example.test/jobs/43"


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, company_name TEXT, title TEXT, apply_status TEXT)")
    connection.executemany("INSERT INTO jobs VALUES (?, 'Example Company', ?, ?)", [
        (URL, "Analyst Intern", "applied"), (OTHER_URL, "Engineering Intern", None),
    ])
    connection.execute("CREATE TABLE submission_receipts (value TEXT)")
    connection.execute("INSERT INTO submission_receipts VALUES ('receipt-sentinel')")
    connection.execute("CREATE TABLE company_priority_events (value TEXT)")
    connection.execute("INSERT INTO company_priority_events VALUES ('priority-sentinel')")
    connection.commit()
    yield connection
    connection.close()


def event(**changes):
    return {"provider": "outlook", "message_id": "message-42", "job_url": URL,
            "company": "Example Company", "title": "Analyst Intern",
            "occurred_at": "2026-10-09T10:00:00+08:00", "event_type": "rejected",
            "evidence_ref": "reviewed-mail:message-42", "summary": "Reviewed feedback", **changes}


def test_exact_url_only_and_unknown_url_pending(conn):
    result = followup.import_events(conn, [event(), event(message_id="ambiguous", job_url=None),
                                          event(message_id="unknown", job_url="https://example.test/missing")])
    assert result == {"imported": 3, "duplicates": 0, "pending": 2}
    assert len(followup.timeline(conn, URL)["events"]) == 1
    assert followup.timeline(conn, OTHER_URL)["events"] == []
    pending = followup.pending_events(conn)
    assert {item["message_id"] for item in pending} == {"ambiguous", "unknown"}
    assert next(item for item in pending if item["message_id"] == "unknown")["source_job_url"].endswith("missing")


def test_unique_company_title_still_requires_exact_url(conn):
    conn.execute("DELETE FROM jobs WHERE url=?", (OTHER_URL,))
    followup.import_events(conn, [event(job_url=None)])
    assert len(followup.pending_events(conn)) == 1


def test_idempotence_equivalent_timezone_and_conflict_does_not_overwrite(conn):
    followup.import_events(conn, [event()])
    original = followup.timeline(conn, URL)
    assert followup.import_events(conn, [event(provider="Outlook", occurred_at="2026-10-09T02:00:00Z")]) == {
        "imported": 0, "duplicates": 1, "pending": 0,
    }
    with pytest.raises(ValueError, match="different content"):
        followup.import_events(conn, [event(summary="Conflicting observation")])
    assert followup.timeline(conn, URL) == original
    followup.import_events(conn, [event(provider="gmail")])
    assert len(followup.timeline(conn, URL)["events"]) == 2


def test_resolve_is_explicit_idempotent_and_preserves_source(conn):
    followup.import_events(conn, [event(job_url="https://example.test/missing")])
    identity = followup.pending_events(conn)[0]["event_id"]
    with pytest.raises(ValueError, match="exact existing"):
        followup.resolve_event(conn, identity, "https://example.test/still-missing")
    result = followup.resolve_event(conn, identity, OTHER_URL)
    assert result["source_job_url"] == "https://example.test/missing"
    assert result["job_url"] == OTHER_URL
    assert result["resolved_at"]
    assert followup.pending_events(conn) == []
    assert followup.resolve_event(conn, identity, OTHER_URL) == result
    with pytest.raises(ValueError, match="cannot be reassigned"):
        followup.resolve_event(conn, identity, URL)


@pytest.mark.parametrize("change", [
    {"evidence_ref": None}, {"occurred_at": "2026-10-09T10:00:00"},
    {"scheduled_at": "2026-10-12T10:00:00+08:00"}, {"round": True},
    {"round": 0}, {"event_type": "submitted"}, {"apply_status": "applied"},
    {"summary": "contains\x00control"},
])
def test_invalid_events_reject_whole_batch_without_tables(conn, change):
    with pytest.raises(ValueError):
        followup.import_events(conn, [event(message_id="good"), event(**change)])
    assert followup.timeline(conn, URL)["events"] == []
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='followup_events'").fetchone()


def test_manual_notes_do_not_require_evidence(conn):
    followup.import_events(conn, [event(event_type="manual_note", evidence_ref=None)])
    result = followup.timeline(conn, URL)["events"][0]
    assert result["event_type"] == "manual_note"
    assert result["evidence_ref"] is None


@pytest.mark.parametrize("payload", [None, {}, [None], [event(occurred_at=None)]])
def test_invalid_input_types_are_rejected_without_writes(conn, payload):
    with pytest.raises(TypeError):
        followup.import_events(conn, payload)
    assert followup.timeline(conn, URL)["events"] == []


def test_batch_conflict_and_sql_failure_roll_back_new_events(conn):
    followup.import_events(conn, [event()])
    with pytest.raises(ValueError, match="different content"):
        followup.import_events(conn, [event(message_id="good"), event(summary="conflict")])
    assert len(followup.timeline(conn, URL)["events"]) == 1
    conn.execute("""CREATE TRIGGER followup_reject_insert BEFORE INSERT ON followup_events
                    WHEN NEW.message_id='bad' BEGIN SELECT RAISE(ABORT, 'injected write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected write failure"):
        followup.import_events(conn, [event(message_id="good"), event(message_id="bad")])
    assert len(followup.timeline(conn, URL)["events"]) == 1


def test_duplicate_inside_batch_and_conflicting_repeat_are_atomic(conn):
    assert followup.import_events(conn, [event(), event()])["duplicates"] == 1
    with pytest.raises(ValueError):
        followup.import_events(conn, [event(message_id="new"), event(message_id="new", summary="conflict")])
    assert len(followup.timeline(conn, URL)["events"]) == 1


def test_savepoint_preserves_outer_transaction(conn):
    conn.execute("UPDATE jobs SET title='temporary' WHERE url=?", (URL,))
    followup.import_events(conn, [event()])
    conn.rollback()
    assert conn.execute("SELECT title FROM jobs WHERE url=?", (URL,)).fetchone()[0] == "Analyst Intern"
    assert followup.timeline(conn, URL)["events"] == []


def test_action_timezone_due_completion_and_unknown_identity(conn):
    action = followup.add_action(conn, URL, "Prepare interview", "2026-10-12T09:00:00+08:00")
    assert action["due_at"] == "2026-10-12T01:00:00+00:00"
    assert followup.due_actions(conn, at="2026-10-12T00:59:59Z") == []
    assert followup.due_actions(conn, at="2026-10-12T01:00:00Z") == [action]
    with pytest.raises(ValueError, match="timezone"):
        followup.add_action(conn, URL, "Bad date", "2026-10-12T09:00:00")
    with pytest.raises(ValueError, match="exact existing"):
        followup.add_action(conn, "missing", "Bad job", "2026-10-12T09:00:00Z")
    with pytest.raises(ValueError, match="Unknown action_id"):
        followup.complete_action(conn, "missing")
    completed = followup.complete_action(conn, action["action_id"])
    assert completed["completed_at"]
    assert followup.complete_action(conn, action["action_id"]) == completed
    assert followup.due_actions(conn, at="2026-10-13T01:00:00Z") == []


def test_does_not_change_application_or_other_ledgers(conn):
    before = {table: conn.execute(f"SELECT * FROM {table}").fetchall()
              for table in ("jobs", "submission_receipts", "company_priority_events")}
    followup.import_events(conn, [event(), event(message_id="pending", job_url=None)])
    followup.resolve_event(conn, followup.pending_events(conn)[0]["event_id"], OTHER_URL)
    action = followup.add_action(conn, OTHER_URL, "Follow up", "2026-10-12T09:00:00+08:00")
    followup.complete_action(conn, action["action_id"])
    after = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in before}
    assert before == after


def test_summary_reads_without_schema_and_orders_interviews(conn):
    empty = followup.followup_summary(conn)
    assert empty == {"pending_count": 0, "open_action_count": 0, "due_action_count": 0,
                     "due_actions": [], "upcoming_interviews": [], "recent_events": []}
    assert followup.followup_summary(None) == empty
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'followup_%'").fetchone()
    followup.import_events(conn, [
        event(event_type="interview", scheduled_at="2026-10-13T14:00:00+08:00", round=2),
        event(message_id="pending", event_type="interview_invited", job_url=None,
              scheduled_at="2026-10-12T14:00:00+08:00", round=1),
    ])
    followup.add_action(conn, URL, "Due", "2026-10-09T09:00:00+08:00")
    summary = followup.followup_summary(conn, now=datetime(2026, 10, 9, 2, tzinfo=UTC))
    assert summary["pending_count"] == summary["open_action_count"] == summary["due_action_count"] == 1
    assert len(summary["upcoming_interviews"]) == 1
    assert summary["upcoming_interviews"][0]["round"] == 2


def test_ics_utc_crlf_escape_folding_stable_uid_and_pending_exclusion(conn):
    text = "Interview; comma, slash\\\r\nATTENDEE:malicious@example.test " + "中文" * 55
    followup.import_events(conn, [
        event(event_type="interview", scheduled_at="2026-10-13T14:00:00+08:00", summary=text),
        event(message_id="pending", event_type="interview", job_url=None,
              scheduled_at="2026-10-14T14:00:00+08:00"),
    ])
    action = followup.add_action(conn, OTHER_URL, "Follow up", "2026-10-12T09:00:00+08:00")
    calendar = followup.export_ics(conn)
    assert calendar.count("BEGIN:VEVENT") == 2
    assert "DTSTART:20261013T060000Z\r\n" in calendar
    assert "DTSTART:20261012T010000Z\r\n" in calendar
    assert "\r\nATTENDEE:" not in calendar
    assert "\n" not in calendar.replace("\r\n", "")
    assert all(len(line.encode("utf-8")) <= 75 for line in calendar.split("\r\n"))
    unfolded = calendar.replace("\r\n ", "")
    assert "Interview\\; comma\\, slash\\\\\\nATTENDEE:" in unfolded
    uid = followup.timeline(conn, URL)["events"][0]["event_id"]
    assert f"UID:{uid}@applypilot.local\r\n" in calendar
    assert f"UID:{uid}@applypilot.local\r\n" in followup.export_ics(conn)
    assert followup.export_ics(conn, job_url=URL).count("BEGIN:VEVENT") == 1
    followup.complete_action(conn, action["action_id"])
    assert followup.export_ics(conn).count("BEGIN:VEVENT") == 1


def test_module_import_does_not_bind_workspace_paths(tmp_path):
    env = {**os.environ, "APPLYPILOT_DIR": str(tmp_path), "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    code = "import sys; import applypilot.commands.followup; assert 'applypilot.config' not in sys.modules; "
    code += "assert 'applypilot.database' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_cli_workspace_isolation_and_all_operations(tmp_path, monkeypatch):
    from applypilot.cli import app
    from applypilot.commands.followup import app as followup_app
    from applypilot.database import close_connection, init_db

    # Parent integration may already register the group; do not register twice.
    if not any(group.name == "followup" for group in app.registered_groups):
        app.add_typer(followup_app, name="followup")
    monkeypatch.delenv("APPLYPILOT_DISCOVERY_ONLY", raising=False)
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path / "unused"))
    workspaces = [tmp_path / "first", tmp_path / "second"]
    for workspace in workspaces:
        db = init_db(workspace / "applypilot.db")
        with db:
            db.execute("INSERT INTO jobs (url, company_name, title, apply_status) VALUES (?, ?, ?, ?)",
                       (URL, "Example Company", "Analyst Intern", "applied"))
        close_connection(workspace / "applypilot.db")
    source = tmp_path / "events.json"
    source.write_text(json.dumps([event(job_url=None)]), encoding="utf-8")
    runner = CliRunner()

    def invoke(workspace, *arguments):
        result = runner.invoke(app, ["--workspace", str(workspace), "followup", *arguments])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    try:
        assert invoke(workspaces[0], "import", "--file", str(source))["pending"] == 1
        assert invoke(workspaces[1], "pending")["events"] == []
        pending = invoke(workspaces[0], "pending")["events"][0]
        invoke(workspaces[0], "resolve", "--event-id", pending["event_id"], "--url", URL)
        assert len(invoke(workspaces[0], "timeline", "--url", URL)["events"]) == 1
        assert invoke(workspaces[0], "import", "--file", str(source))["duplicates"] == 1
        action = invoke(workspaces[1], "add-action", "--url", URL, "--summary", "Prepare",
                        "--due-at", "2026-10-12T09:00:00+08:00")
        assert invoke(workspaces[0], "due", "--at", "2026-10-12T09:00:00+08:00")["actions"] == []
        assert len(invoke(workspaces[1], "due", "--at", "2026-10-12T09:00:00+08:00")["actions"]) == 1
        output = tmp_path / "export.ics"
        invoke(workspaces[1], "export-ics", "--file", str(output))
        assert b"DTSTART:20261012T010000Z\r\n" in output.read_bytes()
        invoke(workspaces[1], "complete-action", "--action-id", action["action_id"])
        assert invoke(workspaces[1], "due", "--at", "2026-10-12T09:00:00+08:00")["actions"] == []
        assert not (tmp_path / "unused").exists()
        for workspace in workspaces:
            with sqlite3.connect(workspace / "applypilot.db") as db:
                assert db.execute("SELECT apply_status FROM jobs WHERE url=?", (URL,)).fetchone()[0] == "applied"
    finally:
        for workspace in workspaces:
            close_connection(workspace / "applypilot.db")


def test_cli_reads_missing_workspace_without_creation(tmp_path, monkeypatch):
    from applypilot.commands.followup import app

    workspace = tmp_path / "absent"
    monkeypatch.setenv("APPLYPILOT_DIR", str(workspace))
    result = CliRunner().invoke(app, ["pending"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"events": []}
    assert not workspace.exists()


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_calendar_cannot_overwrite_database_files(tmp_path, monkeypatch, suffix):
    from applypilot.commands.followup import app

    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    output = tmp_path / f"applypilot.db{suffix}"
    result = CliRunner().invoke(app, ["export-ics", "--file", str(output)])
    assert result.exit_code == 2
    assert "overwrite workspace database files" in result.output
    assert not output.exists()
