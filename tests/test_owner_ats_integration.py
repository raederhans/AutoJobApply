"""One local inbox -> durable owner -> mock ATS recovery, without submission."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import test_application_inbox as inbox_fixture
import test_launcher_durable_runtime as launcher_fixture
import test_operator_runtime as operator_fixture

from applypilot import config
from applypilot.apply import launcher
from applypilot.apply.human_handoff import operator_answer_prompt, read_workbench_answer
from applypilot.apply.operator_commands import OperatorCommandError
from applypilot.apply.operator_dispatch import wait_for_requested_resume
from applypilot.apply.operator_runtime import OperatorRuntime
from applypilot.career.server import PREFIX, start_background
from applypilot.storage import agent_control

ANSWER = "Monday, 09:00-12:00 (synthetic availability)"


@pytest.fixture
def queued(tmp_path, monkeypatch):
    db = inbox_fixture.seed_inbox(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    instance = start_background(tmp_path)
    try:
        with httpx.Client(base_url=instance.origin, trust_env=False) as client:
            token = client.get(PREFIX + "/bootstrap").json()["token"]
            client.headers.update({"Origin": instance.origin, "X-Career-Token": token})
            route = PREFIX + "/application-inbox/exception-1/"
            initial = inbox_fixture.item(client)
            answer_payload = {"request_key": "owner-answer", "revision": initial["revision"], "answer": ANSWER}
            saved = client.post(route + "respond", json=answer_payload)
            assert saved.status_code == 200
            assert client.post(route + "respond", json=answer_payload).json() == saved.json()
            # Refresh/reconstruction of the inbox reads the same durable answer.
            ready = inbox_fixture.item(client)
            resume_payload = {"request_key": "owner-resume", "revision": ready["revision"]}
            requested = client.post(route + "resume", json=resume_payload)
            assert requested.status_code == 200
            assert client.post(route + "resume", json=resume_payload).json() == requested.json()
            yield client, db
    finally:
        instance.shutdown()
        instance.server_close()


@pytest.mark.browser
def test_saved_answer_reaches_same_page_owner_after_inbox_refresh(queued):
    from playwright.sync_api import sync_playwright

    client, db = queued
    fixture = Path(__file__).parent / "fixtures" / "apply" / "owner_resume.html"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(fixture.read_bytes())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    calls = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--disable-background-networking"])
            try:
                context = browser.new_context()
                context.route("**/*", lambda route: route.continue_()
                              if route.request.url.startswith(origin + "/") else route.abort())
                page = context.new_page()
                page.goto(origin + "/apply/1")
                evidence = os.environ.get("OWNER_ATS_EVIDENCE")
                if evidence:
                    page.screenshot(path=str(Path(evidence) / "before.png"))
                with sqlite3.connect(db) as conn:
                    # A newly opened control connection sees the persisted request;
                    # only the original page owner is allowed to execute it.
                    durable_owner = operator_fixture._owner(conn, calls)
                    persisted = OperatorRuntime(conn).load_requested_resume("exception-1")

                    def owner(command, resume_context):
                        with launcher._runtime_operator_resume_scope(
                            command, checkpoint_id=resume_context["checkpoint_ref"], resume_context=resume_context
                        ):
                            scope = launcher._scoped_operator_resume()
                            assert scope is not None
                            assert page.url == origin + "/apply/1"
                            assert page.locator("main").get_attribute("data-job-url") == operator_fixture.JOB
                            assert page.locator("h1").inner_text() == "Engineer at Example Co"
                            prompt = operator_answer_prompt(scope["answer"])
                            # Deterministic local agent reads the same JSON data the
                            # production launcher now passes to its fresh child.
                            answer = json.loads(prompt.split("BOUND HUMAN ANSWER (JSON string):\n")[1])
                            page.get_by_label("Availability").fill(answer)
                            assert page.get_by_label("Availability").input_value() == ANSWER
                            assert page.locator("#submit-count").inner_text() == "0"
                            result = durable_owner(command, resume_context)
                        assert "answer" not in scope
                        assert launcher._scoped_operator_resume() is None
                        return result

                    result = wait_for_requested_resume(
                        conn, exception_id="exception-1", request_id="request-1", resume_owner=owner,
                        heartbeat=lambda: True, stop_wait=lambda _seconds: False, timeout_seconds=10,
                    )
                    assert result.status == "resumed"
                    replay = OperatorRuntime(conn, resume_owner=owner).resume(persisted, request_id="request-1")
                    assert replay.replayed and replay.resolved
                    assert conn.execute("SELECT status FROM agent_human_requests").fetchone()[0] == "consumed"
                    assert conn.execute("SELECT COUNT(*) FROM operator_command_envelopes").fetchone()[0] == 1
                    assert conn.execute("SELECT COUNT(*) FROM agent_runtime_turns").fetchone()[0] == 2
                    assert conn.execute("SELECT submit_started FROM application_attempts").fetchone()[0] == 0
                    command = conn.execute("SELECT command_id FROM operator_command_envelopes").fetchone()[0]
                    assert conn.execute("SELECT stage FROM operator_command_results WHERE command_id=? ORDER BY occurred_at",
                                        (command,)).fetchall() == [("requested",), ("started",), ("verified",)]
                    assert ANSWER not in json.dumps(calls)
                    for table, column in [("agent_runtime_turns", "request_json"), ("agent_checkpoints", "state_json")]:
                        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                        if column in columns:
                            assert ANSWER not in str(conn.execute(f"SELECT {column} FROM {table}").fetchall())
                assert client.get(PREFIX + "/application-inbox/exception-1").json()["item"]["status"] == "resolved"
                page.reload()
                assert page.get_by_label("Availability").input_value() == ANSWER
                assert page.locator("#submit-count").inner_text() == "0"
                assert len(calls) == 1
                if evidence:
                    page.screenshot(path=str(Path(evidence) / "after.png"))
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("mutation", ["expired", "stale_owner", "checkpoint"])
def test_queued_answer_cannot_cross_fresh_binding(queued, mutation):
    _client, db = queued
    calls = []
    with sqlite3.connect(db) as conn:
        if mutation == "expired":
            conn.execute("UPDATE application_attempts SET lease_expires_at='2000-01-01T00:00:00+00:00'")
        elif mutation == "stale_owner":
            conn.execute("UPDATE browser_resource_leases SET runtime_id='reconstructed-owner'")
        else:
            previous = agent_control.latest_actor_checkpoint(conn, operator_fixture.ACTOR)
            agent_control.append_checkpoint(conn, replace(
                previous, checkpoint_id="newer-checkpoint", sequence=2, expected_sequence=1,
                idempotency_key="newer-checkpoint",
            ))
        conn.commit()
        runtime = OperatorRuntime(conn, resume_owner=operator_fixture._owner(conn, calls))
        command = runtime.load_requested_resume("exception-1")
        with pytest.raises(OperatorCommandError, match="drifted"):
            runtime.resume(command, request_id="request-1")
        assert calls == []
        assert conn.execute("SELECT COUNT(*) FROM agent_runtime_turns").fetchone()[0] == 1


def test_unknown_owner_effect_remains_started_and_cannot_replay(queued):
    _client, db = queued
    effects = []
    with sqlite3.connect(db) as conn:
        def owner(command, resume_context):
            with launcher._runtime_operator_resume_scope(
                command, checkpoint_id=resume_context["checkpoint_ref"], resume_context=resume_context
            ):
                effects.append("local effect dispatched")
                raise RuntimeError("synthetic observation lost after dispatch")

        runtime = OperatorRuntime(conn, resume_owner=owner)
        command = runtime.load_requested_resume("exception-1")
        with pytest.raises(OperatorCommandError, match="unknown"):
            runtime.resume(command, request_id="request-1")
        with pytest.raises(OperatorCommandError):
            runtime.resume(command, request_id="request-1")
        assert effects == ["local effect dispatched"]
        assert conn.execute("SELECT stage FROM operator_command_results ORDER BY occurred_at").fetchall() == [
            ("requested",), ("started",)]
        assert agent_control.get_human_request(conn, "request-1").status == "open"
        assert launcher._scoped_operator_resume() is None


def test_reconstructed_process_expires_old_page_instead_of_inheriting_it(queued):
    _client, db = queued
    with sqlite3.connect(db) as conn:
        result = wait_for_requested_resume(
            conn, exception_id="exception-1", request_id="request-1",
            resume_owner=lambda *_: pytest.fail("lost owner must not run"),
            heartbeat=lambda: False, stop_wait=lambda _seconds: False, timeout_seconds=10,
        )
        assert result.status == "lease_lost"
        assert agent_control.get_human_request(conn, "request-1").status == "expired"
        assert conn.execute("SELECT COUNT(*) FROM agent_runtime_turns").fetchone()[0] == 1


@pytest.mark.parametrize("mutation", ["bytes", "outside", "request", "schema"])
def test_bound_artifact_rejects_changed_bytes_paths_or_request(queued, mutation):
    _client, db = queued
    with sqlite3.connect(db) as conn:
        command = OperatorRuntime(conn).load_requested_resume("exception-1")
        prepared = OperatorRuntime(conn)._prepare_resume(command, "request-1")
        response = dict(prepared["resume_context"]["human_response"])
        path = Path(response["response_ref"])
        if mutation == "bytes":
            path.write_text("tampered", encoding="utf-8")
        elif mutation == "outside":
            response["response_ref"] = str(db.parent / "another-answer.json")
        else:
            payload = json.loads(path.read_bytes())
            payload["request_id" if mutation == "request" else "schema_version"] = "other"
            raw = json.dumps(payload).encode()
            digest = hashlib.sha256(raw).hexdigest()
            path = path.with_name(digest + ".json")
            path.write_bytes(raw)
            response.update(response_ref=str(path), response_digest=digest)
        with pytest.raises(ValueError, match="workbench response artifact"):
            read_workbench_answer(response, workspace=db.parent)
        assert launcher._scoped_operator_resume() is None


def test_production_launcher_passes_bound_data_and_keeps_control_reference_only(tmp_path, monkeypatch):
    attempt = "owner-answer-launch"
    parent, db, _events = launcher_fixture._prepared_parent(attempt, monkeypatch, tmp_path)
    parent_turn = parent["_parent_agent_run_id"]
    checkpoint = parent["_parent_agent_checkpoint_id"]
    request = parent_turn + ":human:1"
    raw = json.dumps({"schema_version": "1", "request_id": request, "answer": ANSWER}).encode()
    digest = hashlib.sha256(raw).hexdigest()
    root = config.APP_DIR / "career" / "workbench" / "human-responses"
    root.mkdir(parents=True)
    path = root / (digest + ".json")
    path.write_bytes(raw)
    command = replace(launcher_fixture._operator_command(attempt_id=attempt, parent_turn_id=parent_turn),
                      input_ref="human-response:" + hashlib.sha256(request.encode()).hexdigest(), input_sha256=digest)
    prompts = []
    actions = []
    original_action = launcher._record_worker_action

    def record_action(worker_id, description):
        actions.append(description)
        return original_action(worker_id, description)

    monkeypatch.setattr(launcher, "_record_worker_action", record_action)
    original = launcher_fixture._FakeStdin.write

    def write(stream, value):
        prompts.append(value)
        return original(stream, value)

    monkeypatch.setattr(launcher_fixture._FakeStdin, "write", write)
    original_process = launcher_fixture._FakeProcess.__post_init__

    def echo_answer(process):
        original_process(process)
        process.stdout = io.StringIO(process.stdout.getvalue() + "\n" + json.dumps({
            "type": "item.completed", "item": {"type": "agent_message", "text": ANSWER},
        }))

    monkeypatch.setattr(launcher_fixture._FakeProcess, "__post_init__", echo_answer)
    with launcher._runtime_operator_resume_scope(command, checkpoint_id=checkpoint, resume_context={
        "resume_mode": "fresh_agent_turn", "parent_run_id": parent_turn, "checkpoint_ref": checkpoint,
        "human_response": {"request_id": request, "response_type": "availability", "resolved_by": "career-workbench",
                           "response_ref": str(path), "response_digest": digest},
    }):
        scope = launcher._scoped_operator_resume()
        child = launcher_fixture._job(attempt, _parent_agent_run_id=parent_turn, _parent_agent_checkpoint_id=checkpoint)
        assert launcher.run_job(child, port=9432, worker_id=0, model="model", agent_backend="codex",
                                resume_existing_page=True, submission_phase="prepare")[0] == "ready_to_submit"
    assert len(prompts) == 1 and json.dumps(ANSWER) in prompts[0]
    assert "does not authorize Submit" in prompts[0]
    assert "answer" not in scope
    with sqlite3.connect(db) as conn:
        assert ANSWER not in str(conn.execute("SELECT * FROM agent_runtime_turns").fetchall())
        assert ANSWER not in str(conn.execute("SELECT * FROM agent_checkpoints").fetchall())
    assert ANSWER not in "".join(p.read_text(encoding="utf-8") for p in config.LOG_DIR.rglob("*.log"))
    assert ANSWER not in "".join(p.read_text(encoding="utf-8") for p in config.APP_DIR.rglob("*.log"))
    # Whole-answer checks alone missed the former 50-character description
    # truncation. Also reject a distinctive prefix in logs and progress sinks.
    marker = ANSWER[:20]
    assert marker not in "".join(p.read_text(encoding="utf-8") for p in config.APP_DIR.rglob("*.log"))
    assert marker not in str(actions)


@pytest.mark.parametrize("input_kind,answer", [
    ("text", "SYNTH-OAR-42"),
    ("text", "SYNTH-OAR-LONG-PRIVATE-ANSWER-" + "private-fragment-" * 8),
    ("element", "SYNTH-OAR-ELEMENT-42"),
    ("url", "SYNTH-OAR-URL-42"),
])
def test_operator_answer_tool_input_never_enters_logs_or_progress(tmp_path, monkeypatch, input_kind, answer):
    monkeypatch.setattr(__import__(__name__), "ANSWER", answer)
    original_process = launcher_fixture._FakeProcess.__post_init__

    def tool_answer(process):
        original_process(process)
        value = {"ref": "e1", "text": answer}
        if input_kind == "element":
            value["element"] = answer
        elif input_kind == "url":
            value = {"url": "https://example.test/?answer=" + answer}
        process.stdout = io.StringIO(process.stdout.getvalue() + "\n" + json.dumps({
            "type": "assistant", "message": {"content": [{
                "type": "tool_use", "id": "synthetic-private-field",
                "name": "mcp__playwright__browser_type", "input": value,
            }]},
        }))

    monkeypatch.setattr(launcher_fixture._FakeProcess, "__post_init__", tool_answer)
    test_production_launcher_passes_bound_data_and_keeps_control_reference_only(tmp_path, monkeypatch)
