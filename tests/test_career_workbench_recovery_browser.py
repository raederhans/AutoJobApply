"""Real local workbench interactions after empty results, token rotation and conflicts."""

from __future__ import annotations

import secrets
import sqlite3

import pytest
import test_application_inbox as inbox_fixture

from applypilot import application_progress, followup
from applypilot.career import server

pytestmark = pytest.mark.browser


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    db = inbox_fixture.seed_inbox(tmp_path, monkeypatch)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO jobs(url,title,company_name,apply_status) VALUES(?,?,?,?)",
                     ("https://example.test/other", "Other role", "Example Co", "applied"))
        for index, url in enumerate(("https://example.test/jobs/1", "https://example.test/other"), 1):
            identity = application_progress.application_for_url(conn, url)
            if not identity:
                identity = application_progress.register_application(
                    conn, source_key=f"ui-{index}", company="Example Co", title=f"Role {index}",
                    job_url=url, submission_basis="reported",
                )["application_id"]
            followup.import_events(conn, [{
                "provider": "synthetic", "message_id": f"ui-{index}", "application_id": identity,
                "job_url": url, "event_type": "interview_invited", "round": index,
                "occurred_at": "2026-10-13T00:00:00Z", "evidence_ref": "synthetic-evidence",
                "summary": "Synthetic interview invitation",
            }])
    instance = server.start_background(tmp_path)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--disable-background-networking"])
            try:
                context = browser.new_context(viewport={"width": 1400, "height": 1100})
                context.route("**/*", lambda route: route.continue_()
                              if route.request.url.startswith(instance.origin + "/") else route.abort())
                page = context.new_page()
                requests = []
                page.on("request", lambda request: requests.append({
                    "path": request.url.removeprefix(instance.origin), "method": request.method,
                }))
                page.goto(instance.origin)
                page.wait_for_function("() => document.querySelector('#workspace-label').textContent !== '正在连接…'")
                page.locator('[data-stage="job-progress"]').click()
                page.locator('#job-progress-status-options input[value="interview_1"]').wait_for()
                yield page, instance, db, requests
            finally:
                browser.close()
    finally:
        instance.shutdown()
        instance.server_close()


def apply_filter(page):
    with page.expect_response(lambda response: "/job-progress?" in response.url):
        page.locator("#job-progress-apply").click()
    page.wait_for_function("() => !document.querySelector('#job-progress-apply').disabled")


@pytest.mark.parametrize("field,value", [("job-progress-query", "NO_MATCH_SYNTHETIC"),
                                         ("job-progress-since", "2099-01-01")])
def test_empty_results_preserve_round_selection_and_other_round_choices(workbench, field, value):
    page, _instance, _db, requests = workbench
    first = page.locator('#job-progress-status-options input[value="interview_1"]')
    second = page.locator('#job-progress-status-options input[value="interview_2"]')
    first.check()
    page.locator("#" + field).fill(value)
    apply_filter(page)
    assert first.is_checked() and second.count() == 1
    assert page.locator(".job-progress-table tbody tr").count() == 0
    page.locator("#" + field).fill("")
    apply_filter(page)
    assert first.is_checked() and not second.is_checked()
    assert page.locator(".job-progress-table tbody tr").count() == 1
    assert "第 1 轮面试" in page.locator(".job-progress-table tbody tr").inner_text()
    progress_requests = [request for request in requests if "/job-progress?" in request["path"]]
    assert "status=interview_1" in progress_requests[-1]["path"]
    # A deliberate selection change still takes effect; preserving drafts is not sticky filtering.
    first.uncheck()
    second.check()
    apply_filter(page)
    assert not first.is_checked() and second.is_checked()
    assert "第 2 轮面试" in page.locator(".job-progress-table tbody tr").inner_text()


def save_reminder(page, suffix="/job-progress/reminders"):
    with page.expect_response(lambda response: response.request.method == "POST"
                              and response.url.endswith(suffix)) as observed:
        page.locator('#job-reminder-form button[type="submit"]').click()
    page.wait_for_function("() => !document.querySelector('#reminder-due-date').disabled")
    return observed.value


def start_reminder(page):
    page.locator("[data-reminder-create]").first.click()
    page.locator("#reminder-due-date").fill("2026-12-01")
    page.locator("#reminder-next-step").fill("Review synthetic interview preparation")


@pytest.mark.parametrize("action", ["create", "snooze", "dismiss"])
def test_reminder_session_recovery_preserves_draft_and_requires_new_user_write(workbench, action):
    page, instance, db, requests = workbench
    start_reminder(page)
    identity = None
    due_date = "2026-12-01"
    suffix = "/job-progress/reminders"
    if action != "create":
        initial = save_reminder(page)
        assert initial.status == 200
        identity = initial.json()["reminder_id"]
        page.wait_for_function("() => !document.querySelector('#job-reminder-dialog').open")
        page.locator("#job-reminders-section > summary").click()
        suffix += "/" + identity + "/" + action
        if action == "snooze":
            page.locator(f'[data-reminder-snooze="{identity}"]').click()
            due_date = "2027-01-01"
            page.locator("#reminder-due-date").fill(due_date)
    sidecar = db.parent / "followup-reminders.json"
    original = sidecar.read_bytes() if sidecar.exists() else None
    instance.token = secrets.token_urlsafe(32)
    if action == "dismiss":
        with page.expect_response(lambda response: response.request.method == "POST"
                                  and response.url.endswith(suffix)) as observed:
            page.locator(f'[data-reminder-dismiss="{identity}"]').click()
        failed = observed.value
    else:
        failed = save_reminder(page, suffix)
    assert failed.status == 403
    page.locator('#feedback button').filter(has_text="刷新会话").wait_for()
    before = len(requests)
    if action == "dismiss":
        page.locator("#refresh").click()
    else:
        page.locator("#job-reminder-dialog [data-dialog-feedback] button").click()
    page.locator("#feedback").filter(has_text="会话已刷新。").wait_for()
    page.wait_for_function("() => !document.querySelector('#job-progress-apply').disabled")
    assert any(request["path"].endswith("/bootstrap") for request in requests[before:])
    assert all(request["method"] == "GET" for request in requests[before:])
    assert (sidecar.read_bytes() if sidecar.exists() else None) == original
    if action == "dismiss":
        with page.expect_response(lambda response: response.request.method == "POST"
                                  and response.url.endswith(suffix)) as observed:
            page.locator(f'[data-reminder-dismiss="{identity}"]').click()
        saved = observed.value
    else:
        assert page.locator("#job-reminder-dialog").evaluate("dialog => dialog.open")
        assert page.locator("#reminder-due-date").input_value() == due_date
        if action == "create":
            assert page.locator("#reminder-next-step").input_value() == "Review synthetic interview preparation"
        saved = save_reminder(page, suffix)
    assert saved.status == 200
    assert len([request for request in requests[before:] if request["method"] == "POST"]) == 1


def test_inbox_conflict_refresh_closes_stale_form_and_reopen_retains_draft(workbench):
    page, _instance, db, requests = workbench
    page.locator('[data-stage="inbox"]').click()
    page.locator('[data-inbox-action="respond"]').first.click()
    draft = "Synthetic availability: Monday"
    page.locator("#inbox-answer").fill(draft)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE jobs SET company_name='Updated Example Co' WHERE url='https://example.test/jobs/1'")

    def submit():
        with page.expect_response(lambda response: response.request.method == "POST"
                                  and response.url.endswith("/application-inbox/exception-1/respond")) as observed:
            page.locator('#inbox-answer-form button[type="submit"]').click()
        return observed.value

    assert submit().status == 409
    page.locator("#inbox-dialog [data-dialog-feedback] button").wait_for()
    before = len(requests)
    with page.expect_response(lambda response: "/application-inbox?" in response.url):
        page.locator("#inbox-dialog [data-dialog-feedback] button").click()
    page.wait_for_function("() => !document.querySelector('#inbox-search-submit').disabled")
    assert not page.locator("#inbox-dialog").evaluate("dialog => dialog.open")
    assert all(request["method"] == "GET" for request in requests[before:])
    page.locator('[data-inbox-action="respond"]').first.click()
    assert page.locator("#inbox-answer").input_value() == draft
    assert "Updated Example Co" in page.locator("#inbox-dialog-content").inner_text()
    assert submit().status == 200
    assert len([request for request in requests[before:] if request["method"] == "POST"]) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_human_responses").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM operator_command_envelopes").fetchone()[0] == 0
