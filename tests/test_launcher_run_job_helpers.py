"""Pure run_job setup helpers extracted from the launcher."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from applypilot.apply import launcher

_JOB = {"_parent_agent_run_id": "turn-1", "_parent_agent_checkpoint_id": "ckpt-1"}


def _bind(**overrides):
    values = dict(
        submission_phase="prepare",
        actor_id="actor",
        attempt_id="attempt",
        recovery_command=None,
        operator_resume=None,
        audit_verification=None,
        submit_continuation=None,
    )
    job = overrides.pop("job", _JOB)
    values.update(overrides)
    return launcher._bound_runtime_parent(job, **values)


def test_unscoped_turn_never_binds_the_job_carried_parent() -> None:
    assert _bind() == (None, None, None)


def test_matching_operator_resume_binds_parent_and_authorization() -> None:
    resume = {"actor_id": "actor", "attempt_id": "attempt", "parent_turn_id": "turn-1",
              "checkpoint_id": "ckpt-1", "authorization_id": "auth-9"}
    assert _bind(operator_resume=resume) == ("turn-1", "ckpt-1", "auth-9")


@pytest.mark.parametrize(
    ("scope", "phase", "message"),
    [
        ("operator_resume", "submit", "operator resume"),
        ("audit_verification", "prepare", "audit verification"),
        ("submit_continuation", "prepare", "submit continuation"),
    ],
)
def test_stale_or_wrong_phase_binding_raises(scope, phase, message) -> None:
    binding = {"actor_id": "actor", "attempt_id": "attempt", "parent_turn_id": "turn-1",
               "checkpoint_id": "ckpt-1", "authorization_id": "auth"}
    # audit verification additionally requires the verification child flag.
    with pytest.raises(RuntimeError, match=message):
        _bind(submission_phase=phase, **{scope: binding})


def test_recovery_command_requires_the_stored_checkpoint() -> None:
    command = SimpleNamespace(actor_id="actor", attempt_id="attempt", turn_id="turn-1", command_id="cmd-1")
    assert _bind(recovery_command=command) == ("turn-1", "ckpt-1", "cmd-1")
    with pytest.raises(RuntimeError, match="runtime recovery"):
        _bind(recovery_command=command, job={"_parent_agent_run_id": "turn-1"})


def _admission(disposition, parent="turn-1"):
    return SimpleNamespace(disposition=disposition, parent_turn_id=parent, reason_code="R",
                           requires_fresh_observation=True)


@pytest.mark.parametrize(
    ("disposition", "expected"),
    [
        ("live_owner", "failed:runtime_owner_active"),
        ("blocked", "failed:runtime_recovery_blocked"),
        ("receipt_only", "submission_uncertain"),
        ("fresh", None),
    ],
)
def test_restart_admission_records_and_maps_dispositions(disposition, expected) -> None:
    job: dict = {}
    status = launcher._restart_admission_status(
        job, _admission(disposition), scoped_recovery_requested=False, runtime_parent_turn_id=None,
    )
    assert status == expected
    assert job["_runtime_recovery_admission"]["disposition"] == disposition


def test_recovery_required_continues_only_for_the_matching_scoped_parent() -> None:
    admission = _admission("recovery_required")
    assert launcher._restart_admission_status(
        {}, admission, scoped_recovery_requested=True, runtime_parent_turn_id="turn-1",
    ) is None
    assert launcher._restart_admission_status(
        {}, admission, scoped_recovery_requested=True, runtime_parent_turn_id="other",
    ) == "failed:runtime_recovery_required"
    assert launcher._restart_admission_status(
        {}, admission, scoped_recovery_requested=False, runtime_parent_turn_id="turn-1",
    ) == "failed:runtime_recovery_required"


@pytest.mark.parametrize(
    ("multiplier", "expected"),
    [(None, 900), (1, 900), (True, 900), (1.5, 1350), (5, 1800), ("2", 900)],
)
def test_agent_timeout_multiplier_is_bounded(multiplier, expected) -> None:
    job = {} if multiplier is None else {"_agent_timeout_multiplier": multiplier}
    assert launcher._agent_timeout_for_job(900, job) == expected
    assert launcher._agent_timeout_for_job(3000, {"_agent_timeout_multiplier": 2}) == 3600


def test_initial_runtime_state_reflects_phase_preview_and_observation() -> None:
    assert launcher._initial_runtime_state({}, submission_phase="prepare", dry_run=True) == {"prepare", "preview"}
    observed = {"_browser_observation": {"verification_resume": True, "submission_gate": True}}
    assert launcher._initial_runtime_state(observed, submission_phase="submit", dry_run=False) == {
        "submit", "verification_resumed", "reserved",
    }
    assert launcher._initial_runtime_state(
        {"_submission_gate": True}, submission_phase="submit", dry_run=False,
    ) == {"submit", "reserved"}
    assert launcher._initial_runtime_state(
        {"_submission_gate": True}, submission_phase="prepare", dry_run=False,
    ) == {"prepare"}
