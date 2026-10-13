"""Short HTTP acceptance of the application inbox using fictional records."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import test_operator_runtime as operator_fixture

from applypilot.career.application_inbox import ApplicationInbox
from applypilot.career.server import PREFIX, start_background
from applypilot.database import close_connection, init_db
from applypilot.storage import agent_control, application_ledger, runtime_control


def seed_inbox(workspace, monkeypatch):
    path = workspace / "applypilot.db"
    conn = init_db(path)
    agent_control.ensure_schema(conn)
    application_ledger.ensure_schema(conn)
    runtime_control.ensure_schema(conn)
    # Reuse the exact owner-compatible synthetic checkpoint/lease fixture,
    # leaving its response for the HTTP user's explicit save operation.
    with monkeypatch.context() as patch:
        patch.setattr(operator_fixture, "append_human_response", lambda *_: None)
        operator_fixture._resume_setup(conn)
    conn.commit()
    close_connection(path)
    return path


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    path = seed_inbox(tmp_path, monkeypatch)
    instance = start_background(tmp_path)
    try:
        with httpx.Client(base_url=instance.origin, trust_env=False, timeout=10) as client:
            token = client.get(PREFIX + "/bootstrap").json()["token"]
            client.headers.update({"Origin": instance.origin, "X-Career-Token": token})
            yield client, path
    finally:
        instance.shutdown()
        instance.server_close()


def item(client):
    response = client.get(PREFIX + "/application-inbox")
    assert response.status_code == 200, response.text
    return response.json()["items"][0]


def test_http_answer_then_request_original_owner_once(inbox):
    client, db = inbox
    initial = item(client)
    assert initial["can_respond"] and not initial["can_resume"]
    assert initial["job"]["company"] == "Example Co"
    payload = {"request_key": "answer-1", "revision": initial["revision"], "answer": "下周一可以开始。"}
    route = PREFIX + "/application-inbox/exception-1/"
    saved = client.post(route + "respond", json=payload)
    assert saved.status_code == 200, saved.text
    assert client.post(route + "respond", json=payload).json() == saved.json()
    ready = saved.json()["item"]
    assert ready["can_resume"] and ready["response_recorded"] and not ready["can_respond"]
    command = {"request_key": "resume-1", "revision": ready["revision"]}
    queued = client.post(route + "resume", json=command)
    assert queued.status_code == 200, queued.text
    assert queued.json()["item"]["status"] == "resume_requested"
    assert "等待申请进程接续" in queued.json()["message"]
    assert client.post(route + "resume", json=command).json() == queued.json()
    assert not item(client)["can_resume"]
    progress = client.get(route.rstrip("/")).json()
    assert [event["kind"] for event in progress["timeline"]] == [
        "paused", "human_requested", "answer_saved", "operator_requested"]
    assert all(event["time_basis"] == "recorded" for event in progress["timeline"])
    assert "response_ref" not in json.dumps(progress) and payload["answer"] not in json.dumps(progress, ensure_ascii=False)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT stage FROM operator_command_results").fetchall() == [("requested",)]
        assert conn.execute("SELECT COUNT(*) FROM operator_command_envelopes").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM agent_runtime_turns").fetchone()[0] == 1
        assert conn.execute("SELECT status FROM agent_human_requests").fetchone()[0] == "open"
        assert conn.execute("SELECT status,submit_started FROM application_attempts").fetchone() == ("in_progress", 0)
        reference = conn.execute("SELECT response_ref FROM agent_human_responses").fetchone()[0]
        assert Path(reference).is_relative_to(db.parent / "career" / "workbench")
        assert json.loads(Path(reference).read_text(encoding="utf-8"))["answer"] == payload["answer"]
        assert payload["answer"] not in conn.execute("SELECT input_json FROM career_workbench_requests LIMIT 1").fetchone()[0]


def test_expiry_and_stale_click_cannot_save_or_queue(inbox):
    client, db = inbox
    before = item(client)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE application_attempts SET lease_expires_at='2000-01-01T00:00:00+00:00'")
    blocked = item(client)
    assert blocked["status"] == "blocked" and not blocked["can_respond"] and not blocked["can_resume"]
    response = client.post(PREFIX + "/application-inbox/exception-1/respond", json={
        "request_key": "stale-answer", "revision": before["revision"], "answer": "Synthetic answer"})
    assert response.status_code == 409
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM operator_command_envelopes").fetchone()[0] == 0
    assert not list(db.parent.rglob("human-responses/*.json"))


def test_inbox_get_does_not_migrate_or_write(tmp_path):
    path = tmp_path / "empty.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE marker (value TEXT)")
    original = path.read_bytes()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        result = ApplicationInbox(tmp_path).list_items(conn)
    assert result["available"] is False and result["counts"]["open"] is None
    assert path.read_bytes() == original
    assert not (tmp_path / "career").exists()


def test_search_before_pagination_and_history_scope(inbox):
    client, db = inbox
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE agent_human_requests SET prompt='Confirm 100% availability'")
        for index in range(102):
            conn.execute(
                "INSERT INTO agent_exception_queue "
                "SELECT ?,?,run_id,attempt_id,actor_id,turn_id,'parked',failure_category,next_action,'open','{}',?,NULL "
                "FROM agent_exception_queue WHERE exception_id='exception-1'",
                (f"extra-{index:03}", f"extra-command-{index}",
                 (operator_fixture.NOW + timedelta(seconds=index + 1)).isoformat()),
            )
    route = PREFIX + "/application-inbox"
    latest = client.get(route).json()
    assert latest["pagination"]["total"] == 103 and len(latest["items"]) == 100
    assert "exception-1" not in {value["id"] for value in latest["items"]}
    found = client.get(route, params={"q": "%"}).json()
    assert [value["id"] for value in found["items"]] == ["exception-1"]
    oldest = client.get(route, params={"sort": "oldest"}).json()
    assert oldest["items"][0]["id"] == "exception-1"
    following = client.get(route, params={"offset": 100}).json()
    assert len(following["items"]) == 3 and following["pagination"]["previous_offset"] == 0
    assert not ({value["id"] for value in latest["items"]} & {value["id"] for value in following["items"]})
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE agent_exception_queue SET status='resolved',resolved_at=? WHERE exception_id='extra-000'",
                     (operator_fixture.NOW.isoformat(),))
    history = client.get(route, params={"scope": "history"}).json()
    assert [value["id"] for value in history["items"]] == ["extra-000"]
    assert history["items"][0]["status"] == "resolved" and history["counts"]["open"] == 0
    assert client.get(route + "?q=a&q=b").status_code == 422
    assert client.get(route + "?offset=-1").status_code == 422
    assert client.get(route + "?scope=unsupported").status_code == 422


def test_deadline_unknown_expiry_and_failure_are_distinct(inbox):
    client, db = inbox
    route = PREFIX + "/application-inbox/exception-1"
    initial = item(client)
    saved = client.post(route + "/respond", json={"request_key": "timeline-answer", "revision": initial["revision"], "answer": "Synthetic answer"}).json()
    assert client.post(route + "/resume", json={"request_key": "timeline-resume", "revision": saved["item"]["revision"]}).status_code == 200
    with sqlite3.connect(db) as conn:
        command_id = conn.execute("SELECT command_id FROM operator_command_envelopes").fetchone()[0]
        failure_time = (operator_fixture.NOW + timedelta(minutes=1)).isoformat()
        conn.execute("INSERT INTO operator_command_results VALUES(?,?, 'failed','synthetic_block','blocked',NULL,NULL,'1',?)",
                     ("timeline-failed", command_id, failure_time))
        conn.execute("UPDATE application_attempts SET lease_expires_at='2000-01-01T00:00:00+00:00'")
    detail = client.get(route).json()
    assert detail["item"]["status"] == "failed" and not detail["item"]["can_resume"]
    failure = next(event for event in detail["timeline"] if event["kind"] == "operator_failed")
    assert failure["time_basis"] == "recorded" and failure["occurred_at"] == failure_time
    deadline = next(event for event in detail["timeline"] if event["kind"] == "lease_deadline")
    assert deadline["time_basis"] == "deadline" and "不是实际" in deadline["description"]
    with sqlite3.connect(db) as conn:
        # An old record can lack its terminal timestamp; never substitute now.
        conn.execute("UPDATE agent_exception_queue SET status='expired',resolved_at=NULL WHERE exception_id='exception-1'")
    expired = client.get(route).json()
    terminal = next(event for event in expired["timeline"] if event["kind"] == "queue_expired")
    assert terminal["occurred_at"] is None and terminal["time_basis"] == "unknown"
    assert expired["item"]["status"] == "expired" and not expired["item"]["can_respond"]
    assert not any(event["time_basis"] == "deadline" for event in expired["timeline"])
