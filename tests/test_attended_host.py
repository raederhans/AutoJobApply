"""Production Host/bridge -> real SQLite C/D owner -> reviewed input, synthetic data only."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from applypilot.apply import attended_application as attended
from applypilot.apply.authorization import build_bound_manifest
from applypilot.apply.visual_bridge import VisualBridgeError, request_visual_operation
from applypilot.database import init_db

REPO = Path(__file__).resolve().parents[1]
URL = "https://boards.greenhouse.io/example/jobs/123"
pytestmark = pytest.mark.browser


class Lab:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.bridge = tmp_path / "bridge"
        self.db = tmp_path / "jobs.db"
        self.conn = init_db(self.db)
        pdf = tmp_path / "resume.pdf"
        pdf.write_bytes(b"%PDF-1.4 controlled-fixture")
        job = {"url": URL, "application_url": URL, "company_name": "Example", "title": "Engineer",
            "full_description": "Engineer role", "fit_score": 8, "tailor_status": "machine_validated",
            "tailored_resume_path": str(pdf), "cover_letter_status": "not_required", "eligibility_status": "eligible"}
        self.conn.execute("INSERT INTO jobs (" + ",".join(job) + ") VALUES (" + ",".join("?" for _ in job) + ")",
                          tuple(job.values()))
        self.conn.commit()
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps(build_bound_manifest([job])))
        self.profile = {"personal": {"email": "test@example.test"},
            "experience": [{"company": "Example Corp", "title": "Engineer"}],
            "resume_facts": {"preserved_projects": ["Example Project"]}}
        self.node = subprocess.Popen(["node", str(REPO / "tests/fixtures/apply/attended-host-lab.mjs")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONPATH": str(REPO / "src"), "APPLYPILOT_DIR": str(tmp_path),
                 "APPLYPILOT_TEST_PYTHON": os.environ.get("APPLYPILOT_TEST_PYTHON", sys.executable)},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.futures = []
        try:
            host = self.rpc("init", directory=str(self.bridge), url=URL)
            self.base = {"job_url": URL, "host_session_id": host["session_id"], "tab_id": host["target"]["tab_id"]}
            state = attended.execute(self.conn, {**self.base, "action": "begin", "manifest_path": str(manifest)}, self.profile)
            self.base.update(attempt_id=state["attempt_id"], bridge_directory=str(self.bridge))
            self.run("host-attach")
            inspection = self.rpc("inspect")
            self.form = next(json.loads(b["text"])["form_state"] for b in inspection["content"]
                             if b["type"] == "text" and b["text"].startswith('{"form_state"'))
            self.keys = {f["label"]: f["field_key"] for f in self.form["fields"]}
        except Exception:
            self.close()
            raise

    def rpc(self, method, **kwargs):
        self.node.stdin.write(json.dumps({"method": method, **kwargs}) + "\n")
        self.node.stdin.flush()
        line = self.node.stdout.readline()
        if not line:
            raise RuntimeError("Controlled browser exited: " + self.node.stderr.read())
        response = json.loads(line)
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]

    def run(self, action, **kwargs):
        return attended.execute(self.conn, {**self.base, "action": action, **kwargs}, self.profile)

    def restarted_owner(self, action, **kwargs):
        request = self.root / "request.json"
        profile = self.root / "profile.json"
        request.write_text(json.dumps({**self.base, "action": action, **kwargs}), encoding="utf-8")
        profile.write_text(json.dumps(self.profile), encoding="utf-8")
        script = ("import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[4]);"
                  "from applypilot.database import get_connection;"
                  "from applypilot.apply import attended_application as owner;"
                  "assert Path(owner.__file__).resolve().is_relative_to(Path(sys.argv[4]).resolve());"
                  "print(json.dumps(owner.execute(get_connection(Path(sys.argv[1])),"
                  "json.loads(Path(sys.argv[2]).read_text()),json.loads(Path(sys.argv[3]).read_text()))))")
        result = subprocess.run([sys.executable, "-c", script, str(self.db), str(request), str(profile), str(REPO / "src")],
            cwd=REPO, capture_output=True, text=True, timeout=20, check=False,
            env={**os.environ, "PYTHONPATH": str(REPO / "src")},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def queue(self, operation="observe", arguments=None):
        future = self.pool.submit(request_visual_operation, self.bridge, operation=operation,
            arguments=arguments or {}, observation_id=None if operation == "observe" else "operator-must-review-fresh",
            timeout_seconds=15)
        self.futures.append(future)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pending = list((self.bridge / "pending").glob("*.json"))
            if pending:
                request_id = pending[0].stem
                return request_id, future
            if future.done():
                future.result()
            time.sleep(0.01)
        raise AssertionError("production bridge did not queue a request")

    def bindings(self, *labels):
        paths = {"Email": ["personal", "email"], "Company": ["experience", 0, "company"],
                 "Job title": ["experience", 0, "title"]}
        return [{"field_key": self.keys[label], "source": {"kind": "profile", "path": paths[label]}} for label in labels]

    def review(self, request_id, bindings=None):
        review = self.rpc("review", request_id=request_id)
        return self.run("host-review", review_id=review["review_id"], **({"bindings": bindings} if bindings else {}))

    def step(self, label, value):
        return {"operation": "fill_control", "field_key": self.keys[label], "value": value}

    def complete(self, request_id, future):
        delivery = self.rpc("execute", request_id=request_id)
        assert delivery["awaiting_owner"] is True
        assert not future.done() and not (self.bridge / "responses" / f"{request_id}.json").exists()
        state = self.run("host-complete", request_id=request_id)
        response = future.result(timeout=3)
        assert response["attended_continuity"]["attempt_id"] == self.base["attempt_id"]
        assert response["attended_continuity"]["submit_authority"] is False
        return state, response

    def close(self):
        if self.node.poll() is None:
            try:
                self.rpc("close")
                self.node.stdin.close()
                self.node.wait(timeout=5)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                self.node.kill()
                self.node.wait(timeout=5)
        self.pool.shutdown(wait=True)
        self.conn.close()


@pytest.fixture
def lab(tmp_path):
    value = Lab(tmp_path)
    try:
        yield value
    finally:
        value.close()


def test_partial_parser_overwrite_is_recovered_through_same_owner_before_reviewed_correction(lab):
    lab.rpc("parser")
    request_id, future = lab.queue("fill_batch", {"steps": [lab.step("Company", "Example Corp"), lab.step("Job title", "Engineer")]})
    reviewed = lab.review(request_id, lab.bindings("Email", "Company", "Job title"))
    assert reviewed["next_action"] == "execute_exact_reviewed_request_once"
    state, _ = lab.complete(request_id, future)
    assert state["recovery"]["status"] == "needs_correction"
    assert state["recovery"]["historical_verified_field_refs"]
    assert state["consistency_report"]["status"] == "blocked"
    assert lab.rpc("oracle")["values"] == {"email": "parser@example.test", "company": "Example Corp", "title": ""}
    request_id, future = lab.queue("fill_batch", {"steps": [lab.step("Email", "test@example.test"), lab.step("Job title", "Engineer")]})
    lab.review(request_id)
    state, _ = lab.complete(request_id, future)
    assert state["consistency_report"]["status"] == "consistent"
    assert state["consistency_report"]["whole_application_verified"] is False
    assert lab.rpc("oracle")["values"] == {"email": "test@example.test", "company": "Example Corp", "title": "Engineer"}
    assert lab.conn.execute("SELECT COUNT(*) FROM application_attempts").fetchone()[0] == 1
    assert lab.conn.execute("SELECT COUNT(*) FROM application_submission_gates").fetchone()[0] == 0


def test_needs_fact_continues_only_with_new_confirmed_source_bindings(lab):
    record = next(f["semantic"]["record"]["key"] for f in lab.form["fields"] if f["label"] == "Company")
    args = {"kind": "record_fields", "record_key": record, "steps": [lab.step("Company", "Example Corp")]}
    request_id, future = lab.queue("prepare_subgoal", args)
    lab.review(request_id, lab.bindings("Company"))
    state, _ = lab.complete(request_id, future)
    assert state["recovery"]["status"] == "needs_fact" and lab.rpc("oracle")["writes"] == 0
    args["steps"].append(lab.step("Job title", "Engineer"))
    request_id, future = lab.queue("prepare_subgoal", args)
    with pytest.raises(ValueError, match="source binding"):
        lab.review(request_id)
    assert lab.rpc("oracle")["writes"] == 0
    # The previously omitted field now has an explicit confirmed source. A
    # change to the frozen applicant facts instead requires a fresh attempt.
    lab.review(request_id, lab.bindings("Company", "Job title"))
    state, _ = lab.complete(request_id, future)
    assert state["recovery"]["status"] == "reviewed"
    assert state["consistency_report"]["status"] == "consistent"


def test_committed_completion_survives_owner_process_restart_and_lost_ack(lab, monkeypatch):
    from applypilot.apply import attended_host
    request_id, future = lab.queue("fill_control", {"field_key": lab.keys["Email"], "value": "test@example.test"})
    lab.review(request_id, lab.bindings("Email"))
    lab.rpc("execute", request_id=request_id)
    with monkeypatch.context() as patch:
        patch.setattr(attended_host, "publish", lambda *_: (_ for _ in ()).throw(OSError("controlled lost ack")))
        with pytest.raises(OSError, match="lost ack"):
            lab.run("host-complete", request_id=request_id)
    assert not future.done()
    state = lab.restarted_owner("host-complete", request_id=request_id)
    assert state["next_action"] == "already_recorded_no_input_replay"
    assert future.result(timeout=3)["ok"] is True
    assert lab.rpc("oracle")["events"] == ["email"]
    assert lab.conn.execute("SELECT COUNT(*) FROM application_attempts").fetchone()[0] == 1


@pytest.mark.parametrize("failure", ["lost_completion", "stopped_before_input", "stopped_after_input"])
def test_missing_or_stopped_host_effect_never_becomes_retry_permission(lab, failure):
    request_id, future = lab.queue("fill_control", {"field_key": lab.keys["Email"], "value": "test@example.test"})
    lab.review(request_id, lab.bindings("Email"))
    if failure == "stopped_before_input":
        lab.rpc("stop")
    else:
        if failure == "stopped_after_input":
            lab.rpc("stop-on-fill")
        lab.rpc("execute", request_id=request_id)
        if failure == "lost_completion":
            (lab.bridge / "continuity/completed" / f"{request_id}.json").unlink()
    state = lab.restarted_owner("host-reconcile", request_id=request_id)
    assert state["recovery"]["unknown_effect_latched"] is True
    assert lab.run("resume")["next_action"] == "manual_effect_reconciliation_no_replay"
    assert not (lab.bridge / "continuity/permits" / f"{request_id}.json").exists()
    if failure == "stopped_after_input":
        # The stopped-Host watchdog may beat the restarted owner's durable ack.
        # Either transport result must preserve the same unknown-effect state.
        try:
            assert future.result(timeout=3)["outcome"] == "outcome_unknown"
        except VisualBridgeError as exc:
            assert exc.code == "outcome_unknown"
    else:
        with pytest.raises(VisualBridgeError):
            future.result(timeout=20)


def test_actual_page_change_after_review_is_rejected_before_input(lab):
    request_id, future = lab.queue("fill_control", {"field_key": lab.keys["Company"], "value": "Example Corp"})
    lab.review(request_id, lab.bindings("Company"))
    lab.rpc("change")
    state, response = lab.complete(request_id, future)
    assert response["outcome"] == "failed"
    assert state["recovery"]["category"] == "no_effect"
    assert lab.rpc("oracle")["writes"] == 0


def test_actual_coverage_loss_cannot_authorize_continuation(lab):
    request_id, future = lab.queue()
    lab.review(request_id)
    lab.complete(request_id, future)
    lab.rpc("coverage")
    request_id, future = lab.queue("fill_control", {"field_key": lab.keys["Company"], "value": "Example Corp"})
    with pytest.raises(ValueError, match="coverage"):
        lab.review(request_id, lab.bindings("Company"))
    assert lab.rpc("oracle")["writes"] == 0
    lab.rpc("stop")
    with pytest.raises(VisualBridgeError):
        future.result(timeout=3)


def test_durable_submit_latch_blocks_fresh_host_review(lab):
    request_id, future = lab.queue("fill_control", {"field_key": lab.keys["Company"], "value": "Example Corp"})
    # Adversarial durable-latch injection, not a fabricated successful checkpoint.
    lab.conn.execute("UPDATE application_attempts SET submit_started=1")
    lab.conn.commit()
    state = lab.review(request_id, lab.bindings("Company"))
    assert state["next_action"] == "reconcile_receipt_only"
    assert lab.rpc("oracle")["writes"] == 0
    assert not list((lab.bridge / "continuity/permits").glob("*.json"))
    lab.rpc("stop")
    with pytest.raises(VisualBridgeError):
        future.result(timeout=3)
