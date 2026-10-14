"""Isolated synthetic interview domain and loopback HTTP checks."""

import sqlite3

import httpx
import pytest

from applypilot import followup, followup_reminders
from applypilot.career import interviews, server
from applypilot.career import reminders as career_reminders
from applypilot.database import close_connection, init_db

URL = "https://fictional.example/jobs/one"


@pytest.fixture
def workspace(tmp_path):
    conn = init_db(tmp_path / "applypilot.db")
    conn.execute("INSERT INTO jobs(url,title,company_name,full_description) VALUES(?,?,?,?)",
                 (URL, "Analyst", "Fictional Co", "Responsibilities\nBuild models\nRequirements\nPython"))
    conn.commit()
    close_connection(tmp_path / "applypilot.db")
    return tmp_path


def invite(**extra):
    return {"request_key": "invite-one", "job_url": URL, "round": 1,
            "round_name": "Technical", "scheduled_at": "2026-10-20T10:00:00+08:00",
            "timezone": "Asia/Singapore", "mode": "video", "source_text": "Fictional invitation", **extra}


def test_round_actions_reschedule_replay_and_terminal(workspace):
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        created = interviews.mutate(conn, workspace, "create", invite())
        item = created["item"]
        assert interviews.mutate(conn, workspace, "create", invite()) == created
        request = {"request_key": "action-one", "expected_revision": item["revision"],
                   "summary": "Practice real project", "due_at": "2026-10-19T01:00:00Z"}
        item = interviews.mutate(conn, workspace, "actions", request, item["id"])["item"]
        assert followup.actions(conn)[0]["action_id"] == item["actions"][0]["action_id"]
        reminders = followup_reminders.list_reminders(workspace)
        assert sum(reminders["counts"].values()) == 1
        old_revision = item["revision"]
        item = interviews.mutate(conn, workspace, "complete-action", {"request_key": "done-one",
            "expected_revision": old_revision, "action_id": item["actions"][0]["action_id"]}, item["id"])["item"]
        assert item["actions"][0]["completed_at"] and item["revision"] != old_revision
        request = {"request_key": "move-one", "expected_revision": item["revision"],
                   "scheduled_at": "2026-10-21T10:00:00+08:00", "timezone": "Asia/Singapore",
                   "source_text": "User recorded reschedule"}
        moved = interviews.mutate(conn, workspace, "reschedule", request, item["id"])
        assert interviews.mutate(conn, workspace, "reschedule", request, item["id"]) == moved
        assert len(moved["item"]["history"]) == 4
        with pytest.raises(ValueError, match="revision"):
            interviews.mutate(conn, workspace, "cancel", {"request_key": "stale", "expected_revision": item["revision"],
                                                       "source_text": "Cancel"}, item["id"])
        item = moved["item"]
        item = interviews.mutate(conn, workspace, "result", {"request_key": "result", "expected_revision": item["revision"],
                                "source_text": "User outcome", "result": "next_round"}, item["id"])["item"]
        assert item["status"] == "completed" and not followup.scheduled_interviews(followup._events(conn))
        assert item["scheduled_at"] == moved["item"]["scheduled_at"]
        completed_id = item["id"]
        item = interviews.mutate(conn, workspace, "actions", {"request_key": "post-result-followup",
            "expected_revision": item["revision"], "summary": "Prepare next-round questions",
            "due_at": "2026-10-24T01:00:00Z"}, item["id"])["item"]
        assert item["id"] == completed_id and item["status"] == "completed"
        assert len(item["actions"]) == 2
        with pytest.raises(ValueError, match="terminal"):
            interviews.mutate(conn, workspace, "prepare", {"request_key": "terminal-prepare",
                "expected_revision": item["revision"]}, item["id"])
        with pytest.raises(ValueError, match="terminal"):
            interviews.mutate(conn, workspace, "reschedule", {**request, "request_key": "late", "expected_revision": item["revision"]}, item["id"])
        assert conn.execute("SELECT applied_at FROM jobs WHERE url=?", (URL,)).fetchone()[0] is None


def test_cli_round_is_visible_and_reschedule_replaces_original(workspace):
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        followup.import_events(conn, [{"provider": "local", "message_id": "cli", "occurred_at": "2026-10-13T01:00:00Z",
            "event_type": "interview_invited", "evidence_ref": "fictional source", "summary": "CLI invitation",
            "job_url": URL, "round": 2, "scheduled_at": "2026-10-22T01:00:00Z"}])
        item = interviews.list_items(conn, workspace)["items"][0]
        item = interviews.mutate(conn, workspace, "reschedule", {"request_key": "cli-move", "expected_revision": item["revision"],
            "scheduled_at": "2026-10-23T01:00:00Z", "timezone": "UTC", "source_text": "Changed time"}, item["id"])["item"]
        scheduled = followup.scheduled_interviews(followup._events(conn))
        assert len(scheduled) == 1 and scheduled[0]["scheduled_at"] == item["scheduled_at"]
        cancelled = interviews.mutate(conn, workspace, "cancel", {"request_key": "cli-cancel", "expected_revision": item["revision"],
            "source_text": "Cancelled by user"}, item["id"])["item"]
        assert cancelled["status"] == "cancelled" and not followup.scheduled_interviews(followup._events(conn))
        assert cancelled["scheduled_at"] == item["scheduled_at"]


def test_local_preparation_missing_then_bound_and_source_revision(workspace, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No paid model or environment reads")
    monkeypatch.setattr("dotenv.dotenv_values", forbidden)
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        item = interviews.mutate(conn, workspace, "create", invite(scheduled_at=None))["item"]
        assert item["status"] == "pending"
        item = interviews.mutate(conn, workspace, "prepare", {"request_key": "missing", "expected_revision": item["revision"]}, item["id"])["item"]
        assert item["preparation"]["status"] == "missing_materials"
        resume = workspace / "fictional-resume.txt"
        resume.write_text("Experience\n- Built Python models for a fictional project.", encoding="utf-8")
        conn.execute("UPDATE jobs SET tailored_resume_path=? WHERE url=?", (str(resume), URL))
        conn.commit()
        item = interviews.detail(conn, workspace, item["id"])
        item = interviews.mutate(conn, workspace, "prepare", {"request_key": "ready", "expected_revision": item["revision"]}, item["id"])["item"]
        assert item["preparation"]["status"] == "ready"
        assert item["preparation"]["content"]["llm"]["status"] == "not_requested"
        assert item["preparation"]["binding_status"] == "current_unverified"
        resume.write_text("Experience\n- Developed a second fictional model.", encoding="utf-8")
        changed = interviews.detail(conn, workspace, item["id"])
        assert changed["revision"] != item["revision"] and changed["preparation"]["status"] == "source_changed"


def test_http_readonly_session_validation_and_replay(workspace):
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        followup.import_events(conn, [{"provider": "local", "message_id": "pending-invite", "occurred_at": "2026-10-13T01:00:00Z",
            "event_type": "interview_invited", "evidence_ref": "fictional source", "summary": "Pending invitation",
            "job_url": None, "round": 3}])
    host = server.start_background(workspace, port=0)
    try:
        with httpx.Client(base_url=host.origin, trust_env=False) as client:
            state = client.get(server.PREFIX + "/bootstrap").json()
            before = (workspace / "applypilot.db").read_bytes()
            response = client.get(server.PREFIX + "/interviews")
            assert response.status_code == 200 and response.json()["jobs"][0]["job_url"] == URL
            assert before == (workspace / "applypilot.db").read_bytes()
            assert client.post(server.PREFIX + "/interviews", json=invite()).status_code == 403
            client.headers.update({"Origin": host.origin, "X-Career-Token": state["token"]})
            pending = response.json()["pending_invitations"][0]
            endpoint = server.PREFIX + "/interviews/invitations/" + pending["event_id"] + "/resolve"
            resolve = {"request_key": "associate", "expected_revision": pending["revision"], "job_url": URL}
            resolved = client.post(endpoint, json=resolve)
            assert resolved.status_code == 200, resolved.text
            assert resolved.json()["item"]["round"] == 3 and resolved.json()["item"]["status"] == "pending"
            assert client.post(endpoint, json=resolve).json() == resolved.json()
            assert not client.get(server.PREFIX + "/interviews").json()["pending_invitations"]
            created = client.post(server.PREFIX + "/interviews", json=invite())
            assert created.status_code == 200, created.text
            assert client.post(server.PREFIX + "/interviews", json=invite()).json() == created.json()
            assert client.post(server.PREFIX + "/interviews", json=invite(source_text="changed")).status_code == 409
            assert client.post(server.PREFIX + "/interviews", json=invite(request_key="other", job_url="https://missing.example/")).status_code == 422
            assert client.post(server.PREFIX + "/interviews", json=invite(request_key="invalid-zone", round=2, timezone="UTC")).status_code == 422
            assert client.post(server.PREFIX + "/interviews", json={**invite(), "path": "arbitrary"}).status_code == 422
            client.headers["X-Career-Token"] = "expired"
            assert client.post(server.PREFIX + "/interviews", json=invite()).status_code == 403
            assert client.get(server.PREFIX + "/interviews", headers={"Origin": "https://foreign.example"}).status_code == 403
    finally:
        host.shutdown()
        host.server_close()


def test_reminder_interview_link_reads_only_metadata_and_requires_unique_job(workspace, monkeypatch):
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        item = interviews.mutate(conn, workspace, "create", invite())["item"]
        item = interviews.mutate(conn, workspace, "actions", {"request_key": "private-action",
            "expected_revision": item["revision"], "summary": "PRIVATE_ACTION_TEXT",
            "due_at": "2026-10-20T01:00:00Z"}, item["id"])["item"]
        aid = item["actions"][0]["action_id"]
        queries = []
        conn.set_trace_callback(queries.append)
        assert interviews.action_interview_links(conn) == {aid: item["id"]}
        conn.set_trace_callback(None)
        assert all("summary" not in query and "source_text" not in query and "SELECT payload" not in query for query in queries)
    def forbidden(*args, **kwargs):
        raise AssertionError("Reminder bridge must not read interview detail or packs")
    monkeypatch.setattr(interviews, "detail", forbidden)
    original = followup_reminders.list_reminders(workspace)
    bridged = career_reminders.list_items(workspace)
    original_item = next(entry for group in original["groups"].values() for entry in group)
    entry = next(entry for group in bridged["groups"].values() for entry in group)
    assert entry["interview_id"] == item["id"]
    assert entry["next_step"] == original_item["next_step"] and "PRIVATE_ACTION_TEXT" not in str(bridged)
    assert "interview_id" not in original_item
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        # A second real round claims the same action: omit the ambiguous link.
        followup.import_events(conn, [{"provider": "user", "message_id": "ambiguous-link", "occurred_at": "2026-10-13T23:00:00Z",
            "event_type": "interview_invited", "evidence_ref": "fictional source", "summary": "Unrelated round",
            "job_url": URL, "round": 2, "stage_id": "iv-" + "a" * 32, "action_ids": [aid]}])
        assert interviews.action_interview_links(conn) == {}
        conn.execute("DELETE FROM jobs WHERE url=?", (URL,))
        conn.commit()
        assert interviews.action_interview_links(conn) == {}


def test_same_stage_remains_scoped_to_job_and_application(workspace):
    from applypilot.application_progress import register_application

    other = "https://fictional.example/jobs/second"
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        conn.execute("INSERT INTO jobs(url,title,company_name,full_description) VALUES(?,?,?,?)",
                     (other, "Second Analyst", "Second Fictional", "Python models"))
        conn.commit()
        for number, url in enumerate((URL, other)):
            followup.import_events(conn, [{"provider": "local", "message_id": f"scoped-{number}",
                "occurred_at": "2026-10-13T01:00:00Z", "event_type": "interview_invited", "stage_id": "technical",
                "round": 1, "job_url": url, "scheduled_at": "2026-10-21T01:00:00Z",
                "evidence_ref": "fictional source", "summary": "CLI technical round"}])
        items = interviews.list_items(conn, workspace)["items"]
        assert len(items) == 2 and len({item["id"] for item in items}) == 2
        assert len(followup.scheduled_interviews(followup._events(conn))) == 2
        for item in items:
            assert {event["job_url"] for event in item["history"]} == {item["job_url"]}
            updated = interviews.mutate(conn, workspace, "actions", {"request_key": "action-" + item["id"],
                "expected_revision": item["revision"], "summary": "Follow this exact round",
                "due_at": "2026-10-20T01:00:00Z"}, item["id"])["item"]
            assert updated["id"] == item["id"]
            assert interviews.action_interview_links(conn)[updated["actions"][0]["action_id"]] == item["id"]
        first = next(item for item in interviews.list_items(conn, workspace)["items"] if item["job_url"] == URL)
        interviews.mutate(conn, workspace, "cancel", {"request_key": "cancel-scoped", "expected_revision": first["revision"],
            "source_text": "Cancel first exact job only"}, first["id"])
        assert [event["job_url"] for event in followup.scheduled_interviews(followup._events(conn))] == [other]
        application = register_application(conn, source_key="second-application", company="Fictional Co", title="Analyst",
                                           submission_basis="unverified")
        # A historical second application identity can point to the same pipeline job.
        conn.execute("UPDATE followup_applications SET job_url=? WHERE application_id=?", (URL, application["application_id"]))
        followup.import_events(conn, [{"provider": "local", "message_id": "second-application-round",
            "occurred_at": "2026-10-13T02:00:00Z", "event_type": "interview_invited", "stage_id": "technical",
            "round": 1, "job_url": URL, "application_id": application["application_id"],
            "scheduled_at": "2026-10-22T01:00:00Z", "evidence_ref": "fictional source", "summary": "Separate application"}])
        same_job = [item for item in interviews.list_items(conn, workspace)["items"] if item["job_url"] == URL]
        assert len(same_job) == 2 and len({item["id"] for item in same_job}) == 2
        assert sorted(item["status"] for item in same_job) == ["cancelled", "scheduled"]


def test_roundless_cli_invitation_keeps_origin_when_managed(workspace):
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        followup.import_events(conn, [{"provider": "local", "message_id": "roundless", "occurred_at": "2026-10-13T01:00:00Z",
            "event_type": "interview_invited", "job_url": URL, "evidence_ref": "fictional source", "summary": "Unnumbered invite"}])
        item = interviews.list_items(conn, workspace)["items"][0]
        original_id = item["id"]
        item = interviews.mutate(conn, workspace, "actions", {"request_key": "roundless-task", "expected_revision": item["revision"],
            "summary": "Confirm round details", "due_at": "2026-10-20T01:00:00Z"}, item["id"])["item"]
        assert item["id"] == original_id and len(item["history"]) == 2 and item["round"] is None
        assert interviews.action_interview_links(conn)[item["actions"][0]["action_id"]] == original_id
        item = interviews.mutate(conn, workspace, "reschedule", {"request_key": "roundless-move",
            "expected_revision": item["revision"], "scheduled_at": "2026-10-21T01:00:00Z", "timezone": "UTC",
            "source_text": "Confirmed unnumbered interview time"}, item["id"])["item"]
        assert item["id"] == original_id and item["round"] is None and item["status"] == "scheduled"
        cancelled = interviews.mutate(conn, workspace, "cancel", {"request_key": "roundless-cancel",
            "expected_revision": item["revision"], "source_text": "Unnumbered invite cancelled"}, item["id"])["item"]
        assert cancelled["id"] == original_id and cancelled["status"] == "cancelled"
        assert not followup.scheduled_interviews(followup._events(conn))
        origin = next(event for event in item["history"] if event["event_type"] == "interview_invited")["event_id"]
        base = {"provider": "local", "occurred_at": "2026-10-13T02:00:00Z", "event_type": "interview_cancelled",
                "job_url": URL, "evidence_ref": "fictional source", "summary": "Invalid origin probe"}
        with pytest.raises(ValueError, match="origin"):
            followup.import_events(conn, [{**base, "message_id": "unknown-origin", "interview_origin_event_id": "missing"}])
        with pytest.raises(ValueError, match="binding"):
            followup.import_events(conn, [{**base, "message_id": "wrong-origin", "interview_origin_event_id": origin}])
