from __future__ import annotations

import json
from pathlib import Path

from applypilot.database import close_connection, init_db
from applypilot.view import collect_dashboard_data, render_dashboard


def test_dashboard_followup_missing_tables_is_read_only_and_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "workspace.db"
    conn = init_db(db_path)
    before = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert not {"followup_events", "followup_actions"} & before
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    data = collect_dashboard_data(conn)

    after = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert data["followup"] == {
        "pending_count": 0,
        "open_action_count": 0,
        "due_action_count": 0,
        "due_actions": [],
        "upcoming_interviews": [],
    }
    assert after == before
    assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    close_connection(db_path)


def test_followup_board_shows_due_action_and_applied_job_without_email_evidence_or_html_injection(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "followup.db"
    conn = init_db(db_path)
    job_url = "https://careers.example.test/jobs/applied"
    conn.execute(
        "INSERT INTO jobs (url, title, company_name, apply_status, applied_at) VALUES (?, ?, ?, 'applied', ?)",
        (job_url, "Applied Data Analyst", "Example Employer", "2026-08-27T01:00:00+00:00"),
    )
    conn.execute("""CREATE TABLE followup_events (
        event_id TEXT PRIMARY KEY, provider TEXT NOT NULL, message_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL,
        job_url TEXT, resolved_at TEXT, created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE followup_actions (
        action_id TEXT PRIMARY KEY, job_url TEXT NOT NULL, summary TEXT NOT NULL,
        due_at TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT
    )""")
    interview_payload = {
        "provider": "mailbox",
        "message_id": "message-private-identifier",
        "event_type": "interview_invited",
        "occurred_at": "2026-08-27T00:00:00+00:00",
        "job_url": job_url,
        "company": "Observed Employer",
        "title": "Observed Role",
        "summary": "PRIVATE EMAIL BODY should stay out of the dashboard",
        "evidence_ref": "private-evidence-reference",
        "scheduled_at": "2099-01-02T09:00:00+00:00",
        "round": 2,
    }
    conn.execute(
        "INSERT INTO followup_events VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
        (
            "event-interview",
            "mailbox",
            "message-private-identifier",
            interview_payload["occurred_at"],
            "interview_invited",
            json.dumps(interview_payload),
            job_url,
            "2026-08-27T00:01:00+00:00",
        ),
    )
    pending_payload = {
        "provider": "mailbox",
        "message_id": "message-pending-private",
        "event_type": "recruiter_feedback",
        "occurred_at": "2026-08-28T00:00:00+00:00",
        "job_url": None,
        "summary": "another private mail excerpt",
        "evidence_ref": "pending-evidence-reference",
        "scheduled_at": None,
        "round": None,
    }
    conn.execute(
        "INSERT INTO followup_events VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
        (
            "event-pending",
            "mailbox",
            "message-pending-private",
            pending_payload["occurred_at"],
            "recruiter_feedback",
            json.dumps(pending_payload),
            "2026-08-28T00:01:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO followup_actions VALUES (?, ?, ?, ?, ?, NULL)",
        (
            "action-due",
            job_url,
            '<img src=x onerror="alert(1)"> Confirm interview availability',
            "2000-01-01T00:00:00+00:00",
            "2026-08-27T00:00:00+00:00",
        ),
    )
    conn.commit()
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    data = collect_dashboard_data(conn)
    html = render_dashboard(data)

    assert data["followup"]["pending_count"] == 1
    assert data["followup"]["open_action_count"] == 1
    assert data["followup"]["due_action_count"] == 1
    assert data["followup"]["due_actions"][0]["title"] == "Applied Data Analyst"
    assert data["followup"]["upcoming_interviews"][0]["title"] == "Applied Data Analyst"
    serialized_followup = json.dumps(data["followup"], ensure_ascii=False)
    assert "PRIVATE EMAIL BODY" not in serialized_followup
    assert "evidence_ref" not in serialized_followup
    assert "private-evidence-reference" not in serialized_followup
    assert "Applied Data Analyst" in html
    assert "\\u003cimg src=x" in html
    assert "<img src=x" not in html
    assert "PRIVATE EMAIL BODY" not in html
    assert "message-private-identifier" not in html
    assert "private-evidence-reference" not in html
    assert "safeUrl(item.job_url)" in html
    assert "link.href = href" in html
    assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    close_connection(db_path)
