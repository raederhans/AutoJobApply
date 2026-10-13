"""Real SQLite/attended CLI transitions. Controlled Host evidence, no browser writes."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from applypilot.apply import attended_application as attended
from applypilot.apply.authorization import build_bound_manifest
from applypilot.database import init_db


@pytest.fixture
def env(tmp_path):
    conn = init_db(tmp_path / "jobs.db")
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 controlled-fixture")
    job = {"url": "https://boards.greenhouse.io/example/jobs/123", "application_url": "https://boards.greenhouse.io/example/jobs/123",
        "company_name": "Example", "title": "Engineer", "full_description": "Engineer role", "fit_score": 8,
        "tailor_status": "machine_validated", "tailored_resume_path": str(pdf), "cover_letter_status": "not_required",
        "eligibility_status": "eligible"}
    conn.execute("INSERT INTO jobs ("+",".join(job)+") VALUES ("+",".join("?" for _ in job)+")", tuple(job.values()))
    conn.commit()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(build_bound_manifest([job])))
    profile = {"personal": {"email": "test@example.test", "city": "Example City"},
               "resume_facts": {"preserved_projects": ["Example Project"]},
               "submission_policy": {"allow_runtime_readiness_review": True}}
    base = {"job_url": job["url"], "host_session_id": "host-1", "tab_id": "tab-1"}
    def run(action, **extra):
        return attended.execute(conn, {**base, "action": action, **extra}, profile)
    state = run("begin", manifest_path=str(manifest))
    base["attempt_id"] = state["attempt_id"]
    yield conn, run, profile, pdf, base
    conn.close()


def observation():
    return {"source": "attending_host", "observed_at": datetime.now(UTC).isoformat(), "evidence_refs": ["controlled-host-capture"]}


def form():
    return {"page_url": "https://boards.greenhouse.io/example/jobs/123", "fields": [
        {"field_key": "email", "value": "test@example.test", "control": "email", "value_source": "live_dom_snapshot",
         "invalid": False, "record_path": [], "semantic": {"identification": "identified"},
         "application_question": {"text": "Email", "help_text": "", "constraints": [], "section_path": [], "language": "en", "completeness": "known"}}],
         "coverage": {"scope": "visible_top_document_open_shadow", "iframe_count": 0},
         "question_coverage": {"fields_truncated": False}}


def inspection(f=None):
    f = f or form()
    return {"session_id": "host-1", "phase": "prepare", "submission_authorized": False,
            "target": {"runtime": "iab", "tab_id": "tab-1", "application_url": form()["page_url"]},
            "content": [{"type": "text", "text": json.dumps({"page_url": f["page_url"], "tab_id": "tab-1"})},
                        {"type": "text", "text": json.dumps({"form_state": f})}]}


def feedback(status="verified", outcome="completed"):
    content = {"subgoal_result": {"status": status, "reason": "record_postconditions_verified", "progress": []}}
    if outcome == "failed":
        content = {"host_state": "active", "reobserve_before_retry": True}
    return {"schema_version": 1, "request_id": str(uuid4()), "session_id": "host-1", "token_epoch": "epoch-1",
            "ok": outcome == "completed", "outcome": outcome, "content": [{"type": "text", "text": json.dumps(content)}]}


def snapshot(consistency=False):
    data = {"url": form()["page_url"], "required_unfilled": [], "sensitive_required_unknown": [], "file_fields": [],
        "form_fields": [], "text_fields": [], "select_fields": [], "full_name_values": [], "email_values": [],
        "captcha_visible": False, "verification_visible": False, "assessment_visible": False,
        "resume_field_present": True, "resume_uploaded": True, "submit_control_count": 1}
    if consistency:
        data["consistency"] = {"form_state": form()}
    return data


def bind(run):
    return run("bind-consistency", bindings=[{"field_key": "email", "source": {"kind": "profile", "path": ["personal", "email"]}}],
               inspection=inspection(), host_token_epoch="epoch-1", **observation())


def test_bound_consistency_cannot_be_omitted_at_checkpoint_claim_or_intent(env):
    conn, run, *_ = env
    bind(run)
    with pytest.raises(ValueError, match="bound consistency"):
        run("checkpoint", snapshot=snapshot(), **observation())
    current = snapshot(True)
    run("checkpoint", snapshot=current, **observation())
    with pytest.raises(ValueError, match="independent"):
        run("claim", snapshot=current)  # A fresh host observation is now required for the bound checks.
    claimed = run("claim", snapshot=current, **observation())
    assert claimed["consistency_report"]["status"] == "consistent"
    assert claimed["consistency_report"]["whole_application_verified"] is False
    run("submit-intent", snapshot=current, checkpoint_digest=claimed["checkpoint_digest"], **observation())
    assert run("recover")["next_action"] == "reconcile_receipt_only"
    assert conn.execute("SELECT COUNT(*) FROM application_submission_gates").fetchone()[0] == 1


def test_mismatched_field_never_creates_prepared_checkpoint_or_gate(env):
    conn, run, *_ = env
    bind(run)
    current = snapshot(True)
    current["consistency"]["form_state"]["fields"][0]["value"] = "overwritten@example.test"
    with pytest.raises(ValueError, match="bound_value_mismatch"):
        run("checkpoint", snapshot=current, **observation())
    state = json.loads(conn.execute("SELECT payload FROM attended_applications").fetchone()[0])
    assert "checkpoint" not in state
    assert conn.execute("SELECT COUNT(*) FROM application_submission_gates").fetchone()[0] == 0
    assert "overwritten@example.test" not in str(state)
    assert "test@example.test" not in str(state)


def test_same_attempt_recovery_preserves_binding_and_invalidates_old_checkpoint(env):
    conn, run, _, _, base = env
    bound = bind(run)
    run("checkpoint", snapshot=snapshot(True), **observation())
    response = feedback("waiting")
    recovered = run("recover", inspection=inspection(), feedback=response, host_token_epoch="epoch-1", **observation())
    assert recovered["attempt_id"] == base["attempt_id"]
    assert recovered["recovery"]["status"] == "waiting"
    assert "checkpoint" not in recovered and "historical_checkpoint_digest" in recovered
    assert recovered["consistency_contract"] == bound["consistency_contract"]
    # New connection simulates process restart. No second attempt, no lost binding.
    db = conn.execute("PRAGMA database_list").fetchone()[2]
    other = init_db(db)
    try:
        profile = {"personal": {"email": "test@example.test", "city": "Example City"},
                   "resume_facts": {"preserved_projects": ["Example Project"]},
                   "submission_policy": {"allow_runtime_readiness_review": True}}
        resumed = attended.execute(other, {**base, "action": "resume"}, profile)
        assert resumed["next_action"] == "fresh_host_inspection_before_input"
        assert resumed["recovery"]["status"] == "waiting"
        assert other.execute("SELECT COUNT(*) FROM application_attempts").fetchone()[0] == 1
    finally:
        other.close()


@pytest.mark.parametrize("key,value", [("source", "child_worker"), ("observed_at", "2000-01-01T00:00:00+00:00")])
def test_recovery_rejects_untrusted_or_stale_observation(env, key, value):
    _, run, *_ = env
    obs = observation(); obs[key] = value
    with pytest.raises(ValueError):
        run("recover", inspection=inspection(), feedback=feedback(), host_token_epoch="epoch-1", **obs)


@pytest.mark.parametrize("mutation", ["other_job", "query_job", "tab", "session", "epoch"])
def test_recovery_revalidates_current_page_and_binding_before_continuation(env, mutation):
    _, run, *_ = env
    bind(run)
    view = inspection()
    epoch = "epoch-1"
    if mutation in {"other_job", "query_job"}:
        f = form(); f["page_url"] = f["page_url"].replace("123", "456") if mutation == "other_job" else f["page_url"] + "?gh_jid=another"
        view = inspection(f)
    elif mutation == "tab": view["target"]["tab_id"] = "other-tab"
    elif mutation == "session": view["session_id"] = "other-session"
    else: epoch = "other-epoch"
    with pytest.raises(ValueError):
        run("recover", inspection=view, feedback=feedback(), host_token_epoch=epoch, **observation())


def test_stale_attended_payload_cannot_clear_real_ledger_submit_latch(env):
    conn, run, *_ = env
    conn.execute("UPDATE application_attempts SET submit_started=1")
    conn.commit()
    result = run("resume")
    assert result["next_action"] == "reconcile_receipt_only"
    assert conn.execute("SELECT submit_started FROM application_attempts").fetchone()[0] == 1
    assert run("recover")["next_action"] == "reconcile_receipt_only"
    with pytest.raises(ValueError, match="already started"):
        run("checkpoint", snapshot=snapshot(), **observation())


def test_unknown_post_input_feedback_latches_without_impossible_inspection(env):
    conn, run, *_ = env
    run("checkpoint", snapshot=snapshot(), **observation())
    value = run("recover", feedback=feedback(outcome="outcome_unknown"), host_token_epoch="epoch-1", **observation())
    assert value["recovery"]["unknown_effect_latched"]
    assert "checkpoint" not in value
    again = run("recover", feedback=feedback(), inspection=inspection(), host_token_epoch="epoch-1", **observation())
    assert again["next_action"] == "manual_effect_reconciliation_no_replay"
    with pytest.raises(ValueError, match="unresolved recovery effect"):
        run("checkpoint", snapshot=snapshot(), **observation())
    assert conn.execute("SELECT COUNT(*) FROM application_submission_gates").fetchone()[0] == 0


def test_changed_resume_blocks_recovery_before_any_plan(env):
    _, run, _, pdf, _ = env
    pdf.write_bytes(b"changed-material")
    result = run("recover", feedback=feedback(), inspection=inspection(), host_token_epoch="epoch-1", **observation())
    assert result["phase"] == "material_changed" and "recovery" not in result


def test_existing_claim_cannot_be_reopened_for_recovery(env):
    _, run, *_ = env
    run("checkpoint", snapshot=snapshot(), **observation())
    claimed = run("claim", snapshot=snapshot())
    result = run("recover", feedback=feedback(), inspection=inspection(), host_token_epoch="epoch-1", **observation())
    assert result["gate_id"] == claimed["gate_id"] and "immutable" in result["next_action"]
    with pytest.raises(ValueError, match="immutable"):
        bind(run)


def test_bound_checks_cannot_be_dropped_in_same_attempt(env):
    _, run, *_ = env
    bind(run)
    f = form(); f["fields"][0]["field_key"] = "another-email-field"
    with pytest.raises(ValueError, match="cannot drop"):
        run("bind-consistency", bindings=[{"field_key": "another-email-field", "source": {"kind": "profile", "path": ["personal", "email"]}}],
            inspection=inspection(f), host_token_epoch="epoch-1", **observation())


def test_legacy_resume_is_not_advertised_as_browser_or_submission_authority(env):
    _, run, *_ = env
    value = run("resume")
    assert value["browser_write_authority"] is False
    assert value["next_action"] == "fresh_host_inspection_before_input"


@pytest.mark.parametrize("action", ["recover", "bind-consistency"])
def test_cli_routes_new_actions_through_existing_attended_command(env, tmp_path, monkeypatch, action):
    from typer.testing import CliRunner
    from applypilot import cli, config

    conn, _, profile, _, base = env
    req = {**base, "action": action, "inspection": inspection(), "host_token_epoch": "epoch-1", **observation()}
    if action == "recover":
        req["feedback"] = feedback("waiting")
    else:
        req["bindings"] = [{"field_key": "email", "source": {"kind": "profile", "path": ["personal", "email"]}}]
    request = tmp_path / "request.json"
    request.write_text(json.dumps(req), encoding="utf-8")
    monkeypatch.setattr(config, "load_profile", lambda: profile)
    db = conn.execute("PRAGMA database_list").fetchone()[2]
    result = CliRunner().invoke(cli.app, ["attended-application", "--db", db, "--request-file", str(request)])
    assert result.exit_code == 0, result.output
    state = json.loads(conn.execute("SELECT payload FROM attended_applications").fetchone()[0])
    assert state["attempt_id"] == base["attempt_id"]
    assert (state["recovery"]["status"] == "waiting" if action == "recover" else bool(state["consistency_contract"]))
    assert conn.execute("SELECT COUNT(*) FROM application_submission_gates").fetchone()[0] == 0
