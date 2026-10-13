"""Short HTTP acceptance of the application inbox using fictional records."""

from __future__ import annotations

import json
import sqlite3
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
