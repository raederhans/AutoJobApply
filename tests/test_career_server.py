"""Real loopback HTTP contracts against fictional isolated career data only."""

from __future__ import annotations

import http.client
import json
import socket
import sqlite3
import time
from copy import deepcopy

import httpx
import pytest
from test_career_learning import artifact_registry, review_for
from test_career_learning import prepared as learning_prepared  # noqa: F401
from test_career_service import prepared as base_prepared  # noqa: F401

from applypilot.career import server as host


@pytest.fixture
def running(learning_prepared):  # noqa: F811
    conn, _target, _source, _registry, _bundle, db = learning_prepared
    jobs = [tuple(row) for row in conn.execute("SELECT * FROM jobs ORDER BY url")]
    instance = host.start_background(db.parent)
    with httpx.Client(base_url=instance.origin, trust_env=False, timeout=10) as client:
        state = client.get(host.PREFIX + "/bootstrap").json()
        client.headers.update({"Origin": instance.origin, "X-Career-Token": state["token"]})
        try:
            yield instance, client, state, db, jobs
        finally:
            instance.shutdown()
            instance.server_close()


def get(client, endpoint):
    response = client.get(host.PREFIX + endpoint)
    assert response.status_code == 200, response.text
    return response.json()


def post(client, endpoint, data, status=200):
    response = client.post(host.PREFIX + endpoint, json=data)
    assert response.status_code == status, response.text
    return response.json()


def job_result(client, job):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        value = get(client, "/runs/" + job["job_id"])
        if value["status"] in {"completed", "failed", "interrupted", "cancelled"}:
            assert value["status"] == "completed", value
            return value["result_refs"]
        time.sleep(.01)
    pytest.fail("HTTP background job did not finish")


def test_http_pdf_extraction_and_invalid_document(running):
    import base64
    import io

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    _instance, client, _state, _db, _jobs = running
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=100)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 60 Td (Synthetic project artifact.) Tj ET")
    page[NameObject("/Contents")] = stream
    output = io.BytesIO()
    writer.write(output)
    data = {"name": "fictional.pdf", "kind": "resume", "claim": "Synthetic project artifact.",
            "ownership": "Fictional candidate", "quote": "Synthetic project artifact.",
            "data_base64": base64.b64encode(output.getvalue()).decode()}
    result = post(client, "/materials", data, 201)["material"]
    assert result["text"].strip() == "Synthetic project artifact."
    assert result["kind"] == "resume"
    bad = post(client, "/materials", {**data, "data_base64": base64.b64encode(b"invalid PDF").decode()}, 422)
    assert bad["error"]["code"] == "text_export_required"


def test_dashboard_navigation_can_open_html_but_not_read_api(running):
    _instance, client, _state, _db, _jobs = running
    headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    entry = client.get("/", headers=headers)
    assert entry.status_code == 200 and "career.js" in entry.text
    response = client.get(host.PREFIX + "/bootstrap", headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "cross_site_request"


def create_route(client, state):
    target_data = {"job_id": "example-one", "label": "Fictional HTTP target",
                   "material_ids": [m["id"] for m in state["materials"]], "request_key": "target"}
    created = post(client, "/targets", target_data, 201)
    target = created["target"]
    diagnosis_job = post(client, f"/targets/{target['target_id']}/diagnoses",
                         {"expected_revision": target["revision"], "request_key": "diagnose"}, 202)
    diagnosis_id = job_result(client, diagnosis_job)["diagnosis_id"]
    route_job = post(client, f"/diagnoses/{diagnosis_id}/routes",
                     {"expected_revision": target["revision"], "request_key": "route"}, 202)
    route_id = job_result(client, route_job)["route_id"]
    return target, diagnosis_id, get(client, "/routes/" + route_id)


def upload_artifact(client, tmp_path):
    _registry, source, _mid = artifact_registry(tmp_path)
    text = source.read_text(encoding="utf-8")
    data = {"name": "fictional-observation.md", "kind": "project_evidence", "text": text,
            "claim": "Fictional report task observation", "ownership": "Fictional candidate authored this report",
            "quote": "The input is a list of report rows."}
    result = post(client, "/materials", data, 201)
    assert post(client, "/materials", data, 201) == result
    return result["material"]["id"], data


def submit_review(client, progress, task, material_id, *, key, candidate=False):
    route_id = progress["route"]["route_id"]
    submitted = post(client, f"/routes/{route_id}/submissions",
                     {"task_id": task["task_id"], "material_ids": [material_id],
                      "expected_revision": progress["revision"], "request_key": "submit-" + key})["progress"]
    artifact = get(client, f"/routes/{route_id}/tasks/{task['task_id']}/submission")
    assert "path" not in artifact["materials"][0]
    source_id = artifact["materials"][0]["id"]
    review = review_for(task, source_id, candidate=candidate)
    data = {"task_id": task["task_id"], "review": review,
            "expected_revision": submitted["revision"], "request_key": "review-" + key}
    return post(client, f"/routes/{route_id}/reviews", data)["progress"], data


def test_full_http_learning_flow_and_restart_preserve_jobs(running, tmp_path):
    instance, client, state, db, jobs = running
    assert state["catalog"]["jobs"] and state["catalog"]["companies"]
    assert "token" in state and state["materials"]
    target, diagnosis_id, progress = create_route(client, state)
    diagnosis = get(client, "/diagnoses/" + diagnosis_id)
    assert diagnosis["mode"] == "rules_only" and diagnosis["requirements"]
    assert all(s["level"] is None for s in diagnosis["skills"])
    material_id, _data = upload_artifact(client, tmp_path)
    blocker = next(t for t in progress["tasks"] if t["kind"] == "evidence_collection")
    progress, _review = submit_review(client, progress, blocker, material_id, key="evidence")
    assert next(t for t in progress["tasks"] if t["task_id"] == blocker["task_id"])["state"] == "passed"
    evidence_route_id = progress["route"]["route_id"]
    evidence_refresh_job = post(client, f"/routes/{evidence_route_id}/rediagnose",
                                {"expected_revision": progress["revision"], "request_key": "evidence-rediagnose"}, 202)
    evidence_refresh = job_result(client, evidence_refresh_job)
    progress = get(client, "/routes/" + evidence_refresh["route_id"])
    assert progress["route"]["parent_route_id"] == evidence_route_id
    task = next(t for t in progress["tasks"] if t["anchor_id"] and t["ready"])
    assert task["ready"] and task["acceptance"] == ["Names the input"]
    progress, _review = submit_review(client, progress, task, material_id, key="candidate", candidate=True)
    route_id = progress["route"]["route_id"]
    assert next(t for t in progress["tasks"] if t["task_id"] == task["task_id"])["state"] == "passed"
    updated_job = post(client, f"/routes/{route_id}/rediagnose",
                       {"expected_revision": progress["revision"], "request_key": "rediagnose"}, 202)
    refreshed = job_result(client, updated_job)
    updated = get(client, "/diagnoses/" + refreshed["diagnosis_id"])
    assert any(row["observed_task_passes"] for row in updated["requirements"])
    child = get(client, "/routes/" + refreshed["route_id"])
    assert child["route"]["parent_route_id"] == route_id
    assert all(s["level"] is None for s in updated["skills"])
    supplemented_job = post(client, f"/targets/{target['target_id']}/supplement",
                            {"material_ids": [material_id], "expected_revision": target["revision"],
                             "parent_route_id": refreshed["route_id"], "request_key": "supplement"}, 202)
    supplemented = job_result(client, supplemented_job)
    current = get(client, "/targets/" + target["target_id"])["target"]
    assert current["revision"] == target["revision"] + 1
    assert current["latest_diagnosis_id"] == supplemented["diagnosis_id"]
    assert current["latest_route_id"] == supplemented["route_id"]
    latest = get(client, "/routes/" + supplemented["route_id"])
    assert latest["route"]["parent_route_id"] == refreshed["route_id"]
    assert get(client, "/routes/" + route_id)["stale"]
    runs = get(client, "/runs")["runs"]
    assert len(runs) == 5 and all(r["status"] == "completed" for r in runs)
    with sqlite3.connect(db) as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM jobs ORDER BY url")] == jobs
        assert conn.execute("SELECT COUNT(*) FROM career_model_attempts").fetchone()[0] == 0
        publications = {row[0]: json.loads(row[1]) for row in
                        conn.execute("SELECT job_id,result_json FROM career_workbench_publications")}
        assert publications == {run["job_id"]: run["result_refs"] for run in runs}
    instance.shutdown()
    instance.server_close()
    restarted = host.start_background(db.parent)
    try:
        with httpx.Client(base_url=restarted.origin, trust_env=False, timeout=10) as fresh:
            reopened = get(fresh, "/bootstrap")
            assert reopened["token"] != state["token"]
            assert get(fresh, "/runs")["runs"] == runs
            assert get(fresh, "/targets/" + target["target_id"])["target"] == current
            assert get(fresh, "/routes/" + supplemented["route_id"]) == latest
            assert material_id in {m["id"] for m in reopened["materials"]}
        with sqlite3.connect(db) as conn:
            assert [tuple(row) for row in conn.execute("SELECT * FROM jobs ORDER BY url")] == jobs
    finally:
        restarted.shutdown()
        restarted.server_close()


def test_http_host_origin_session_and_fetch_site_rejected(running):
    instance, client, _state, db, _jobs = running
    cases = [("Host", "attacker.example", "invalid_host"),
             ("Origin", "https://attacker.example", "invalid_origin"),
             ("Sec-Fetch-Site", "cross-site", "cross_site_request"),
             ("X-Career-Token", "invalid", "session_required")]
    for header, value, code in cases:
        response = client.post(host.PREFIX + "/targets", json={}, headers={header: value})
        assert response.status_code == 403 and response.json()["error"]["code"] == code
    with httpx.Client(base_url=instance.origin, trust_env=False) as unauthenticated:
        response = unauthenticated.post(host.PREFIX + "/targets", json={})
        assert response.status_code == 403 and response.json()["error"]["code"] == "session_required"
        assert unauthenticated.get(host.PREFIX + "/bootstrap").status_code == 200
    response = client.options(host.PREFIX + "/targets")
    assert response.status_code == 405
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM career_workbench_requests").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM career_workbench_jobs").fetchone()[0] == 0


def test_http_rejection_drains_unread_request_body_without_connection_reset(running):
    # Replying 403 and closing with unread request bytes makes the kernel send RST, which
    # intermittently surfaced as WinError 10053/10054 on the client instead of the JSON error.
    instance, _client, _state, _db, _jobs = running
    payload = {"request_key": "drain", "pad": "a" * 200_000}
    with httpx.Client(base_url=instance.origin, trust_env=False, timeout=10) as unauthenticated:
        for _ in range(50):
            response = unauthenticated.post(host.PREFIX + "/targets", json=payload)
            assert response.status_code == 403 and response.json()["error"]["code"] == "session_required"


def test_http_rejection_with_stalled_upload_is_answered_within_the_drain_bound(running):
    # A client that declares a body but stops sending must not hold the error reply
    # (and a server thread) until the 10s socket timeout.
    instance, _client, _state, _db, _jobs = running
    netloc = instance.origin.split("://", 1)[1]
    head = (
        f"POST {host.PREFIX}/targets HTTP/1.1\r\nHost: {netloc}\r\n"
        "Content-Type: application/json\r\nContent-Length: 200000\r\n\r\n"
    )
    with socket.create_connection(("127.0.0.1", instance.server_port), timeout=8) as raw:
        raw.sendall(head.encode("ascii") + b'{"pad": "' + b"a" * 1000)
        started = time.monotonic()
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = raw.recv(65536)
            if not chunk:
                break
            reply += chunk
        elapsed = time.monotonic() - started
    assert reply.split(b"\r\n", 1)[0].split(b" ")[1] == b"403"
    assert elapsed < host.DRAIN_SECONDS + 2


def test_http_material_and_body_input_boundaries(running):
    instance, client, state, db, _jobs = running
    base = {"name": "fictional.txt", "kind": "project_evidence", "text": "Exact fictional quote.",
            "claim": "Fictional contribution", "ownership": "Fictional candidate", "quote": "Exact fictional quote."}
    for name in ("../escape.txt", r"..\escape.txt", "C:escape.txt", "/absolute.txt"):
        response = client.post(host.PREFIX + "/materials", json={**base, "name": name})
        assert response.status_code == 422
    for field in ("path", "workspace", "allowed_roots", "registry", "use_model", "retry_unknown", "_input_lease"):
        response = client.post(host.PREFIX + "/materials", json={**base, field: "arbitrary"})
        assert response.status_code == 422
    bad_quote = client.post(host.PREFIX + "/materials", json={**base, "quote": "Absent quotation."})
    assert bad_quote.status_code == 422 and bad_quote.json()["error"]["code"] == "quote_not_found"
    missing = client.post(host.PREFIX + "/targets", json={"label": "Fictional", "job_id": "example-one",
                          "material_ids": ["unknown-material"], "request_key": "unknown"})
    assert missing.status_code == 404
    assert client.get(host.PREFIX + "/materials/%2e%2e%2fprivate.txt").status_code == 404
    for body in ("[]", "{\"n\":NaN}", "invalid json"):
        response = client.post(host.PREFIX + "/materials", content=body,
                               headers={"Content-Type": "application/json"})
        assert response.status_code == 422
    # A deliberately incomplete oversized declaration is rejected before the
    # server reads it. http.client permits this malformed probe without h11's
    # client-side Content-Length enforcement obscuring the server behavior.
    oversized_request = http.client.HTTPConnection("127.0.0.1", instance.server_port, timeout=3)
    try:
        oversized_request.request("POST", host.PREFIX + "/materials", headers={
            "Content-Type": "application/json", "Content-Length": str(host.MAX_BODY + 1),
            "Origin": instance.origin, "X-Career-Token": state["token"]})
        response = oversized_request.getresponse()
        assert response.status == 413
        response.read()
    finally:
        oversized_request.close()
    oversized = client.post(host.PREFIX + "/materials", json={**base, "text": "x" * 2_000_001})
    assert oversized.status_code == 413
    assert get(client, "/materials")["materials"] == state["materials"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM career_workbench_materials").fetchone()[0] == 0


def test_http_request_idempotency_revision_conflict_and_exact_review_quotes(running, tmp_path):
    _instance, client, state, db, _jobs = running
    target, diagnosis_id, progress = create_route(client, state)
    target_data = {"job_id": "example-one", "label": "Fictional HTTP target",
                   "material_ids": [m["id"] for m in state["materials"]], "request_key": "target"}
    assert post(client, "/targets", target_data, 201)["target"] == target
    assert client.post(host.PREFIX + "/targets", json={**target_data, "label": "different"}).status_code == 409
    same_run = post(client, f"/targets/{target['target_id']}/diagnoses",
                    {"expected_revision": target["revision"], "request_key": "diagnose"}, 202)
    assert job_result(client, same_run)["diagnosis_id"] == diagnosis_id
    assert client.post(host.PREFIX + f"/targets/{target['target_id']}/diagnoses",
                       json={"expected_revision": 0, "request_key": "stale-diagnosis"}).status_code == 409
    route_id = progress["route"]["route_id"]
    task = next(t for t in progress["tasks"] if t["ready"])
    start = {"task_id": task["task_id"], "event": "started", "reason": "Fictional start",
             "expected_revision": 0, "request_key": "started"}
    started = post(client, f"/routes/{route_id}/events", start)
    assert started["progress"]["revision"] == 1
    assert post(client, f"/routes/{route_id}/events", start) == started
    assert client.post(host.PREFIX + f"/routes/{route_id}/events",
                       json={**start, "request_key": "stale-start"}).status_code == 409
    material_id, _ = upload_artifact(client, tmp_path)
    submit = {"task_id": task["task_id"], "material_ids": [material_id],
              "expected_revision": 1, "request_key": "submitted"}
    submitted = post(client, f"/routes/{route_id}/submissions", submit)
    assert post(client, f"/routes/{route_id}/submissions", submit) == submitted
    artifact = get(client, f"/routes/{route_id}/tasks/{task['task_id']}/submission")
    review = review_for(task, artifact["materials"][0]["id"])
    bad = deepcopy(review)
    bad["acceptance_results"][0]["evidence_refs"][0]["quote"] = "This was never in the submitted text."
    data = {"task_id": task["task_id"], "review": bad, "expected_revision": 2, "request_key": "bad-quote"}
    response = client.post(host.PREFIX + f"/routes/{route_id}/reviews", json=data)
    assert response.status_code == 422 and response.json()["error"]["code"] == "quote_not_found"
    assert get(client, "/routes/" + route_id)["revision"] == 2
    partial = deepcopy(review)
    partial["acceptance_results"].pop()
    assert client.post(host.PREFIX + f"/routes/{route_id}/reviews",
                       json={**data, "review": partial, "request_key": "partial"}).status_code == 422
    valid = {**data, "review": review, "request_key": "reviewed"}
    reviewed = post(client, f"/routes/{route_id}/reviews", valid)
    assert reviewed["progress"]["revision"] == 3
    assert post(client, f"/routes/{route_id}/reviews", valid) == reviewed
    assert client.post(host.PREFIX + f"/routes/{route_id}/reviews",
                       json={**valid, "review": {**review, "context": "Changed"}}).status_code == 409
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM career_assessments").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM career_workbench_jobs").fetchone()[0] == 2


def test_all_http_get_projections_are_readonly_and_do_not_disclose_paths(running, monkeypatch):
    _instance, client, state, db, _jobs = running
    target, diagnosis_id, progress = create_route(client, state)
    route_id = progress["route"]["route_id"]
    with sqlite3.connect(db) as conn:
        before = tuple(conn.iterdump())
    original = sqlite3.connect
    statements = []

    def tracked(*args, **kwargs):
        conn = original(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked)
    endpoints = ["/bootstrap", "/materials", "/materials/" + state["materials"][0]["id"],
                 "/targets", "/targets/" + target["target_id"], "/diagnoses/" + diagnosis_id,
                 "/routes/" + route_id, "/runs", "/health"]
    for _ in range(2):
        for endpoint in endpoints:
            result = get(client, endpoint)
            assert str(db.parent) not in json.dumps(result)
    assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
    with original(db) as conn:
        assert tuple(conn.iterdump()) == before
