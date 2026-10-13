"""Crash-window recovery uses synthetic files and real SQLite, never browser input."""
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from test_attended_host_contract import admit, review
from test_attended_host_contract import transport as transport_fixture
from test_attended_recovery_consistency import env, feedback, inspection  # noqa: F401

from applypilot.apply import attended_host

transport = transport_fixture


def saved(transport):
    return json.loads(transport[0].execute("SELECT payload FROM attended_applications").fetchone()[0])


def complete(transport, request_id, review_id, *, outcome="completed", observed_at=None):
    pending = saved(transport)["host_continuity"]["pending"]
    reply = {**feedback(outcome=outcome), "request_id": request_id}
    envelope = {"schema_version": 1, "attempt_id": transport[4]["attempt_id"], "job_url": transport[4]["job_url"],
                "review_id": review_id, "request_sha256": pending["request_sha256"], "feedback": reply,
                "observed_at": observed_at or datetime.now(UTC).isoformat(), "inspection": inspection()}
    path = transport[5] / "continuity/completed" / f"{request_id}.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    return path


def expire(transport):
    transport[0].execute("UPDATE application_attempts SET lease_expires_at='2000-01-01T00:00:00+00:00'")
    transport[0].commit()


def host_run(transport, action, request_id):
    return transport[1](action, bridge_directory=str(transport[5]), request_id=request_id)


def assert_no_renewal(transport):
    assert transport[0].execute("SELECT lease_expires_at FROM application_attempts").fetchone()[0] == (
        "2000-01-01T00:00:00+00:00")
    assert transport[0].execute("SELECT COUNT(*) FROM application_submission_gates").fetchone()[0] == 0


def test_expired_attempt_can_revoke_and_latch_missing_completion_without_renewal(transport):
    request_id, review_id = review(transport)
    admit(transport, review_id)
    expire(transport)
    state = host_run(transport, "host-reconcile", request_id)
    assert state["recovery"]["unknown_effect_latched"] is True
    assert state["browser_write_authority"] is False and state["submit_authority"] is False
    assert not (transport[5] / "continuity/permits" / f"{request_id}.json").exists()
    assert_no_renewal(transport)
    _, another = review(transport)
    with pytest.raises(ValueError, match="lease expired"):
        admit(transport, another)


@pytest.mark.parametrize("action", ["host-complete", "host-reconcile"])
def test_committed_ack_replay_after_expiry_is_digest_verified_and_does_not_renew(transport, action):
    request_id, review_id = review(transport)
    admit(transport, review_id)
    path = complete(transport, request_id, review_id)
    host_run(transport, "host-complete", request_id)
    response = transport[5] / "responses" / f"{request_id}.json"
    response.unlink()
    expire(transport)
    before = saved(transport)
    state = host_run(transport, action, request_id)
    assert state["next_action"] == "already_recorded_no_input_replay"
    assert response.exists()
    assert saved(transport) == before
    assert_no_renewal(transport)
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="completion changed"):
        host_run(transport, action, request_id)


@pytest.mark.parametrize("outcome", ["completed", "outcome_unknown"])
def test_expired_attempt_records_only_historical_feedback_without_preparation_authority(transport, outcome):
    request_id, review_id = review(transport)
    admit(transport, review_id)
    complete(transport, request_id, review_id, outcome=outcome)
    expire(transport)
    state = host_run(transport, "host-complete", request_id)
    assert state["recovery"]["status"] == "parked"
    assert bool(state["recovery"]["unknown_effect_latched"]) == (outcome == "outcome_unknown")
    assert state["browser_write_authority"] is False and state["submit_authority"] is False
    assert state["host_continuity"]["last_completion"] == request_id
    assert "pending" not in state["host_continuity"]
    assert "test@example.test" not in json.dumps(state["recovery"])
    assert_no_renewal(transport)


def test_stale_completion_is_historical_evidence_and_cannot_restore_a_checkpoint(transport):
    request_id, review_id = review(transport)
    admit(transport, review_id)
    timestamp = (datetime.now(UTC) - timedelta(minutes=6)).isoformat()
    complete(transport, request_id, review_id, observed_at=timestamp)
    state = host_run(transport, "host-complete", request_id)
    assert state["recovery"]["status"] == "parked"
    assert state["recovery"]["reconciliation_reason"] == "stale_host_completion"
    assert "checkpoint" not in state and "consistency_report" not in state
    assert state["browser_write_authority"] is False


def test_publication_cleanup_failure_always_rolls_back_and_hides_raw_payload(transport, monkeypatch):
    from pathlib import Path

    conn, _, profile, _, _, root, _ = transport
    request_id, review_id = review(transport)
    deliveries = []
    with monkeypatch.context() as patch:
        patch.setattr(attended_host, "publish", lambda *args: deliveries.append(args[1]))
        admit(transport, review_id)
    path, _ = deliveries[0]
    unlink = Path.unlink
    def fail_cleanup(self, *args, **kwargs):
        if self == path:
            raise PermissionError("secret-applicant-answer")
        return unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    monkeypatch.setattr(attended_host, "_atomic_write_json", lambda *_: (_ for _ in ()).throw(
        OSError("secret-applicant-answer")))
    with pytest.raises(OSError, match="Host permit publication failed") as error:
        attended_host.publish(conn, deliveries[0], profile)
    assert not conn.in_transaction
    assert "secret-applicant-answer" not in str(error.value)
    assert saved(transport)["host_continuity"]["pending"]["request_id"] == request_id
    assert not (root / "continuity/permits" / f"{request_id}.json").exists()


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), True, "private-answer"])
def test_invalid_deadline_is_rejected_before_pending_permission_is_committed(transport, deadline):
    _, review_id = review(transport, {"deadline_at": deadline})
    with pytest.raises(ValueError) as error:
        admit(transport, review_id)
    assert "private-answer" not in str(error.value)
    assert "pending" not in saved(transport)["host_continuity"]
    assert not list((transport[5] / "continuity/permits").glob("*.json"))


def test_invalid_observation_timestamp_has_a_safe_diagnostic(transport):
    _, review_id = review(transport)
    path = transport[5] / "continuity/reviews" / f"{review_id}.json"
    envelope = json.loads(path.read_text())
    envelope["observed_at"] = "private-applicant-answer"
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError) as error:
        admit(transport, review_id)
    assert "private-applicant-answer" not in str(error.value)
    assert "observation timestamp" in str(error.value)
    assert "pending" not in saved(transport)["host_continuity"]


@pytest.mark.parametrize("action", ["host-complete", "host-reconcile"])
def test_lost_previous_ack_is_republished_without_disturbing_a_new_pending_request(transport, action):
    first, first_review = review(transport)
    admit(transport, first_review)
    path = complete(transport, first, first_review)
    host_run(transport, "host-complete", first)
    response = transport[5] / "responses" / f"{first}.json"
    response.unlink()
    second, second_review = review(transport)
    admit(transport, second_review)
    before = saved(transport)
    permit = transport[5] / "continuity/permits" / f"{second}.json"
    permit_bytes = permit.read_bytes()
    state = host_run(transport, action, first)
    assert state["next_action"] == "already_recorded_no_input_replay"
    assert response.exists() and saved(transport) == before
    assert permit.read_bytes() == permit_bytes
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="completion changed"):
        host_run(transport, action, first)
    assert saved(transport) == before and permit.read_bytes() == permit_bytes


def test_late_success_after_missing_completion_cannot_clear_unknown_effect(transport):
    request_id, review_id = review(transport)
    admit(transport, review_id)
    host_run(transport, "host-reconcile", request_id)
    complete(transport, request_id, review_id)
    state = host_run(transport, "host-complete", request_id)
    assert state["recovery"]["unknown_effect_latched"] is True
    assert state["recovery"]["status"] == "parked"
    assert state["host_continuity"]["last_completion"] == request_id
    assert state["browser_write_authority"] is False and state["submit_authority"] is False
    assert transport[1]("resume")["next_action"] == "manual_effect_reconciliation_no_replay"


def test_stale_known_completion_can_only_continue_after_a_new_fresh_review(transport):
    request_id, review_id = review(transport)
    admit(transport, review_id)
    complete(transport, request_id, review_id,
             observed_at=(datetime.now(UTC) - timedelta(minutes=6)).isoformat())
    host_run(transport, "host-complete", request_id)
    _, next_review = review(transport)
    state = admit(transport, next_review)
    assert state["next_action"] == "execute_exact_reviewed_request_once"
    assert state["recovery"]["status"] == "reviewed"


def test_repeated_missing_completion_does_not_inflate_diagnostic_history(transport):
    request_id, review_id = review(transport)
    admit(transport, review_id)
    expire(transport)
    first = host_run(transport, "host-reconcile", request_id)
    second = host_run(transport, "host-reconcile", request_id)
    assert second["recovery"] == first["recovery"]
    assert len(second["recovery"]["transport_history"]) == 1
    assert_no_renewal(transport)


@pytest.mark.parametrize("expiry", ["permit", "attempt"])
def test_delayed_expired_publication_cannot_publish_or_renew_authority(transport, monkeypatch, expiry):
    conn, _, profile, _, _, root, _ = transport
    request_id, review_id = review(transport)
    deliveries = []
    with monkeypatch.context() as patch:
        patch.setattr(attended_host, "publish", lambda *args: deliveries.append(args[1]))
        admit(transport, review_id)
    if expiry == "attempt":
        expire(transport)
    else:
        later = time.time() + 61
        monkeypatch.setattr(attended_host.time, "time", lambda: later)
    lease = conn.execute("SELECT lease_expires_at FROM application_attempts").fetchone()[0]
    with pytest.raises(ValueError, match="before publication"):
        attended_host.publish(conn, deliveries[0], profile)
    assert not conn.in_transaction
    assert conn.execute("SELECT lease_expires_at FROM application_attempts").fetchone()[0] == lease
    assert not (root / "continuity/permits" / f"{request_id}.json").exists()
    assert saved(transport)["host_continuity"]["pending"]["request_id"] == request_id
    state = host_run(transport, "host-reconcile", request_id)
    assert state["recovery"]["unknown_effect_latched"] is True


@pytest.mark.parametrize("barrier", ["submit", "episode", "expired", "terminal", "materials"])
def test_old_ack_replay_revokes_new_pending_permission_when_current_authority_is_blocked(transport, barrier):
    conn = transport[0]
    first, first_review = review(transport)
    admit(transport, first_review)
    complete(transport, first, first_review)
    host_run(transport, "host-complete", first)
    response = transport[5] / "responses" / f"{first}.json"
    response.unlink()
    second, second_review = review(transport)
    admit(transport, second_review)
    if barrier == "submit":
        conn.execute("UPDATE application_attempts SET submit_started=1")
        conn.commit()
    elif barrier == "episode":
        conn.execute("CREATE TABLE application_episodes(attempt_id TEXT,state TEXT,command_sequence INTEGER,result_sequence INTEGER)")
        conn.execute("INSERT INTO application_episodes VALUES (?, 'recovering', 2, 1)", (transport[4]["attempt_id"],))
        conn.commit()
    elif barrier == "expired":
        expire(transport)
    elif barrier == "terminal":
        conn.execute("UPDATE application_attempts SET status='failed'")
        conn.commit()
    else:
        transport[3].write_bytes(b"changed-synthetic-resume")
    state = host_run(transport, "host-complete", first)
    assert state["next_action"] == "already_recorded_no_input_replay" and response.exists()
    assert not (transport[5] / "continuity/permits" / f"{second}.json").exists()
    assert saved(transport)["host_continuity"]["pending"]["request_id"] == second
    ack = json.loads(response.read_text())["attended_continuity"]
    assert ack["submit_authority"] is False
    if barrier == "submit":
        assert ack["next_action"] == "reconcile_receipt_only"
        assert conn.execute("SELECT submit_started FROM application_attempts").fetchone()[0] == 1
    if barrier == "terminal":
        assert conn.execute("SELECT status FROM application_attempts").fetchone()[0] == "failed"
    assert conn.execute("SELECT COUNT(*) FROM application_submission_gates").fetchone()[0] == 0


def test_invalid_completion_cannot_leave_an_expired_attempt_permit_available(transport):
    request_id, review_id = review(transport)
    admit(transport, review_id)
    path = complete(transport, request_id, review_id)
    envelope = json.loads(path.read_text())
    envelope["request_sha256"] = "different-request"
    path.write_text(json.dumps(envelope))
    expire(transport)
    before = saved(transport)
    with pytest.raises(ValueError, match="does not bind"):
        host_run(transport, "host-complete", request_id)
    assert saved(transport) == before
    assert not (transport[5] / "continuity/permits" / f"{request_id}.json").exists()
    assert_no_renewal(transport)
