"""Executable recovery and exception-queue contracts."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from applypilot.apply.application_actor import ApplicationActorState, decide_recovery
from applypilot.apply.contracts import RecoveryCommand, RecoveryExecutionResult, application_actor_id, contract_json
from applypilot.apply.recovery_execution import (
    admit_recovery_decision,
    execute_recovery_command,
)
from applypilot.storage import agent_control


def actor(**changes: object) -> ApplicationActorState:
    values: dict[str, object] = {
        "run_id": "turn-1",
        "attempt_id": "attempt-1",
        "application_id": "application-1",
        "page_id": "page-1",
        "write_owner": "playwright",
        "phase": "verify",
        "same_application_retries_remaining": 1,
        "new_session_retries_remaining": 1,
    }
    values.update(changes)
    return ApplicationActorState(**values)  # type: ignore[arg-type]


def command_for(
    result: str,
    *,
    submit_started: bool = False,
    run_id: str = "turn-1",
):
    decision = decide_recovery(actor(run_id=run_id), result)
    admission = admit_recovery_decision(decision, submit_started=submit_started)
    assert admission.command is not None
    return admission.command


def test_allowlisted_command_executes_once_and_replay_has_no_second_effect() -> None:
    connection = sqlite3.connect(":memory:")
    command = command_for("failed:site_blocked")
    effect_calls: list[str] = []

    def handler(_command):
        effect_calls.append("called")
        return {
            "browser_runtime": "cloak",
            "fallback_applied": True,
            "recovery_turn_completed": True,
        }

    def verifier(_command, details):
        return details.get("fallback_applied") is True

    first = execute_recovery_command(
        connection,
        command,
        handler=handler,
        verifier=verifier,
    )
    replay = execute_recovery_command(
        connection,
        command,
        handler=handler,
        verifier=verifier,
    )

    assert first.stage == replay.stage == "verified"
    assert first.terminal_status == replay.terminal_status == "completed"
    assert effect_calls == ["called"]
    assert [
        result.stage
        for result in agent_control.list_recovery_results(
            connection,
            command_id=command.command_id,
        )
    ] == ["started", "executed", "verified"]


def test_crash_after_started_fails_closed_without_replaying_effect() -> None:
    connection = sqlite3.connect(":memory:")
    command = command_for("failed:site_blocked")

    def crash(_command):
        raise SystemExit("synthetic process crash")

    with pytest.raises(SystemExit, match="synthetic process crash"):
        execute_recovery_command(
            connection,
            command,
            handler=crash,
            verifier=lambda _command, _details: True,
        )

    replay_calls: list[str] = []
    replay = execute_recovery_command(
        connection,
        command,
        handler=lambda _command: replay_calls.append("called") or {},
        verifier=lambda _command, _details: True,
        incomplete_started_timeout_seconds=0,
    )

    assert replay.stage == "failed"
    assert replay.terminal_status == "terminated"
    assert replay.outcome == "indeterminate_effect_after_started_replay"
    assert replay_calls == []
    queued = agent_control.list_exceptions(
        connection,
        command_id=command.command_id,
    )
    assert len(queued) == 1
    assert queued[0].queue_kind == "recovery_execution"
    assert queued[0].context["terminal_status"] == "terminated"


@pytest.mark.parametrize(
    ("error", "terminal_status"),
    [
        (RuntimeError("failed"), "failed"),
        (BrokenPipeError("terminated"), "terminated"),
        (TimeoutError("timed out"), "timed_out"),
        (InterruptedError("canceled"), "canceled"),
    ],
)
def test_execution_failures_have_queryable_terminal_classification(
    error: Exception,
    terminal_status: str,
) -> None:
    connection = sqlite3.connect(":memory:")
    command = command_for(
        "failed:site_blocked",
        run_id=f"turn-{terminal_status}",
    )

    def fail(_command):
        raise error

    result = execute_recovery_command(
        connection,
        command,
        handler=fail,
        verifier=lambda _command, _details: True,
    )
    queued = agent_control.list_exceptions(
        connection,
        command_id=command.command_id,
    )

    assert result.stage == "failed"
    assert result.terminal_status == terminal_status
    assert result.outcome == f"recovery_effect_{terminal_status}"
    assert queued[0].context["terminal_status"] == terminal_status


def test_submission_uncertain_can_only_enqueue_receipt_reconciliation() -> None:
    connection = sqlite3.connect(":memory:")
    command = command_for("submission_uncertain", submit_started=True)

    assert command.command == "enqueue_receipt_reconciliation"
    result = execute_recovery_command(connection, command)
    queued = agent_control.list_exceptions(
        connection,
        command_id=command.command_id,
    )

    assert result.stage == "verified"
    assert len(queued) == 1
    assert queued[0].queue_kind == "receipt_reconciliation"
    assert queued[0].next_action == "reconcile_receipt_without_resubmitting"


@pytest.mark.parametrize(
    "result",
    [
        "captcha",
        "failed:mfa",
        "failed:assessment_required",
        "failed:identity_material_missing:passport",
        "failed:financial_document_required",
    ],
)
def test_sensitive_and_unsupported_fact_boundaries_are_human_only(result: str) -> None:
    decision = decide_recovery(actor(), result)
    admission = admit_recovery_decision(decision, submit_started=False)

    assert decision.recovery_action is not None
    assert decision.recovery_action.action == "human_only"
    assert admission.command is not None
    assert admission.command.command == "enqueue_human_handoff"
    assert admission.command.submit_authority is False
    assert admission.command.page_write_authority is False
    assert admission.command.ledger_write_authority is False


@pytest.mark.parametrize(
    "result",
    [
        "failed:unsupported_skill:required_answer",
        "failed:unknown_required_fact",
        "failed:resume_upload",
        "failed:required_field_empty:country",
    ],
)
def test_ordinary_resolution_failures_get_one_same_application_retry(result: str) -> None:
    decision = decide_recovery(actor(), result)
    admission = admit_recovery_decision(decision, submit_started=False)

    assert decision.human_interruption is None
    assert admission.command is not None
    assert admission.command.command == "retry_same_application"
    assert admission.command.retry_budget_remaining == 0


def test_policy_rejects_any_automatic_retry_after_submit_started() -> None:
    decision = decide_recovery(actor(), "failed:site_blocked")

    admission = admit_recovery_decision(decision, submit_started=True)

    assert admission.admitted is False
    assert admission.command is None
    assert admission.reason == "post_submit_recovery_must_reconcile_receipt"


def _history_at(connection, command, stage):
    calls = []

    def handler(_command):
        calls.append("handler")
        if stage == "started":
            raise SystemExit("crash")
        if stage == "failed":
            raise RuntimeError("failed")
        return {"mock_effect": True}

    def verifier(_command, _details):
        calls.append("verifier")
        if stage == "executed":
            raise SystemExit("crash")
        return True

    if stage in {"started", "executed"}:
        with pytest.raises(SystemExit):
            execute_recovery_command(connection, command, handler=handler, verifier=verifier)
    else:
        execute_recovery_command(connection, command, handler=handler, verifier=verifier)
    return calls


@pytest.mark.parametrize("stage", ["started", "executed", "verified", "failed"])
@pytest.mark.parametrize("drift", ["attempt", "turn"])
def test_recovery_id_collision_rejects_lineage_at_every_boundary_without_writes(stage, drift) -> None:
    connection = sqlite3.connect(":memory:")
    command = command_for("failed:site_blocked")
    calls = _history_at(connection, command, stage)
    changed = (
        replace(command, attempt_id="other-attempt", actor_id=application_actor_id("other-attempt"))
        if drift == "attempt"
        else replace(command, run_id="other-turn", turn_id="other-turn")
    )
    before = connection.total_changes
    before_calls = calls[:]
    with pytest.raises(ValueError, match="lineage collision"):
        execute_recovery_command(
            connection,
            changed,
            handler=lambda _command: calls.append("repeated_handler") or {},
            verifier=lambda _command, _details: calls.append("repeated_verifier") or True,
            incomplete_started_timeout_seconds=0,
        )
    assert connection.total_changes == before
    assert calls == before_calls


@pytest.mark.parametrize("stage", ["started", "executed", "verified", "failed"])
@pytest.mark.parametrize("drift", ["payload", "budget", "policy", "category", "next_action", "command"])
def test_recovery_rejects_same_lineage_envelope_drift_without_writes(stage, drift) -> None:
    connection = sqlite3.connect(":memory:")
    command = command_for("failed:site_blocked")
    _history_at(connection, command, stage)
    changes = {
        "payload": {"payload": {"application_ref": "application:other"}},
        "budget": {"retry_budget_remaining": command.retry_budget_remaining + 1},
        "policy": {"policy_reason": "different_policy"},
        "category": {"failure_category": "different_failure"},
        "next_action": {"next_action": "different_next_action"},
        "command": {
            "command": "retry_same_application",
            "effect_scope": "same_application",
            "recovery_action": "retry_same_application",
        },
    }
    changed = replace(command, **changes[drift])
    calls = []
    before = connection.total_changes
    with pytest.raises(ValueError, match="envelope collision"):
        execute_recovery_command(
            connection,
            changed,
            handler=lambda _command: calls.append("handler") or {},
            verifier=lambda _command, _details: calls.append("verifier") or True,
            incomplete_started_timeout_seconds=0,
        )
    assert calls == []
    assert connection.total_changes == before


@pytest.mark.parametrize("stage", ["started", "executed", "verified", "failed"])
def test_legacy_recovery_history_cannot_be_signed_by_incoming_envelope(stage) -> None:
    connection = sqlite3.connect(":memory:")
    command = command_for("failed:site_blocked")
    terminal_status = "completed" if stage == "verified" else "failed" if stage == "failed" else None
    old = RecoveryExecutionResult(
        result_id=f"{command.command_id}:{stage}",
        command_id=command.command_id,
        run_id=command.run_id,
        turn_id=command.turn_id,
        attempt_id=command.attempt_id,
        actor_id=command.actor_id,
        stage=stage,
        outcome="legacy_result",
        terminal_status=terminal_status,
        details={"command": command.command, "effect_scope": command.effect_scope},
    )
    agent_control.append_recovery_result(connection, old)
    connection.commit()
    before = connection.total_changes
    calls = []
    with pytest.raises(ValueError, match="lacks durable command envelope"):
        execute_recovery_command(
            connection,
            command,
            handler=lambda _command: calls.append("handler") or {},
            verifier=lambda _command, _details: calls.append("verifier") or True,
            incomplete_started_timeout_seconds=0,
        )
    assert calls == []
    assert connection.total_changes == before
    assert agent_control.list_recovery_results(connection, command_id=command.command_id) == [old]


def test_recovery_checks_all_history_even_with_valid_terminal_lineage() -> None:
    connection = sqlite3.connect(":memory:")
    command = command_for("failed:site_blocked")
    _history_at(connection, command, "started")
    foreign = RecoveryExecutionResult(
        result_id=f"{command.command_id}:executed",
        command_id=command.command_id,
        run_id="other-turn",
        turn_id="other-turn",
        attempt_id="other-attempt",
        actor_id=application_actor_id("other-attempt"),
        stage="executed",
        outcome="foreign_effect",
    )
    terminal = replace(
        foreign,
        result_id=f"{command.command_id}:verified",
        run_id=command.run_id,
        turn_id=command.turn_id,
        attempt_id=command.attempt_id,
        actor_id=command.actor_id,
        stage="verified",
        terminal_status="completed",
    )
    agent_control.append_recovery_result(connection, foreign)
    agent_control.append_recovery_result(connection, terminal)
    connection.commit()
    before = connection.total_changes
    with pytest.raises(ValueError, match="lineage collision"):
        execute_recovery_command(connection, command)
    assert connection.total_changes == before


def test_executed_recovery_resumes_verification_without_repeating_effect() -> None:
    connection = sqlite3.connect(":memory:")
    command = command_for("failed:site_blocked")
    _history_at(connection, command, "executed")
    calls = []
    result = execute_recovery_command(
        connection,
        command,
        handler=lambda _command: calls.append("handler") or {},
        verifier=lambda _command, details: calls.append("verifier") or details.get("mock_effect") is True,
    )
    assert result.stage == "verified"
    assert calls == ["verifier"]


def test_started_recovery_exact_replay_retains_owner_and_canonical_payload_order() -> None:
    connection = sqlite3.connect(":memory:")
    command = replace(command_for("failed:site_blocked"), payload={"first": [1, 2], "second": {"ref": "value"}})
    _history_at(connection, command, "started")
    original = agent_control.list_recovery_results(connection, command_id=command.command_id)[0]
    calls = []
    reordered = replace(command, payload={"second": {"ref": "value"}, "first": [1, 2]})
    before = connection.total_changes
    replay = execute_recovery_command(connection, reordered, handler=lambda _command: calls.append("handler") or {})
    assert replay == original
    assert calls == []
    assert connection.total_changes == before


def test_recovery_started_claim_race_returns_persisted_owner_without_effect(monkeypatch) -> None:
    from applypilot.apply import recovery_execution

    connection = sqlite3.connect(":memory:")
    command = command_for("failed:site_blocked")
    append = recovery_execution._append_durable
    persisted = []

    def concurrent_claim(connection, started):
        if started.stage == "started" and not persisted:
            persisted.append(started)
            assert append(connection, started)
        return append(connection, started)

    monkeypatch.setattr(recovery_execution, "_append_durable", concurrent_claim)
    calls = []
    result = execute_recovery_command(connection, command, handler=lambda _command: calls.append("handler") or {})
    assert result == persisted[0]
    assert calls == []
    assert len(agent_control.list_recovery_results(connection, command_id=command.command_id)) == 1


def test_recovery_rebuilt_admission_and_serialized_envelope_replay_exactly() -> None:
    connection = sqlite3.connect(":memory:")
    command = command_for("failed:site_blocked")
    calls = []
    first = execute_recovery_command(
        connection,
        command,
        handler=lambda _command: calls.append("handler") or {"mock_effect": True},
        verifier=lambda _command, _details: True,
    )
    rebuilt = command_for("failed:site_blocked")
    restored = RecoveryCommand(**contract_json(command))
    assert rebuilt is not command and restored is not command
    before = connection.total_changes
    for replay_command in (rebuilt, restored):
        assert execute_recovery_command(connection, replay_command) == first
    assert calls == ["handler"]
    assert connection.total_changes == before


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_recovery_concurrent_sqlite_claim_commits_snapshot_before_single_reread(
    tmp_path,
    monkeypatch,
    journal_mode,
) -> None:
    from applypilot.apply import recovery_execution

    path = tmp_path / "concurrent-recovery.sqlite3"
    connection = sqlite3.connect(path)
    competitor = sqlite3.connect(path)
    try:
        assert connection.execute(f"PRAGMA journal_mode={journal_mode}").fetchone()[0].upper() == journal_mode
        agent_control.ensure_schema(connection)
        agent_control.ensure_schema(competitor)
        command = command_for("failed:site_blocked")
        append = recovery_execution._append_durable
        list_results = agent_control.list_recovery_results
        persisted = []
        reads = []

        def track_read(connection, **filters):
            reads.append(connection.in_transaction)
            return list_results(connection, **filters)

        def competing_claim(connection, started):
            assert not persisted  # There must be exactly one attempted claim.
            assert not connection.in_transaction
            # A real independent SQLite writer commits after our empty-history
            # read, with its own lifecycle timestamp but an identical envelope.
            competing_started = replace(started, occurred_at=started.occurred_at - timedelta(seconds=1))
            assert append(competitor, competing_started)
            persisted.append(competing_started)
            assert append(connection, started) is False
            assert not connection.in_transaction  # No stale SQLite snapshot.
            return False

        monkeypatch.setattr(agent_control, "list_recovery_results", track_read)
        monkeypatch.setattr(recovery_execution, "_append_durable", competing_claim)
        calls = []
        result = execute_recovery_command(
            connection,
            command,
            handler=lambda _command: calls.append("handler") or {},
        )
        assert result == persisted[0]
        assert reads == [False, False]
        assert calls == []
        assert len(list_results(connection, command_id=command.command_id)) == 1
    finally:
        connection.close()
        competitor.close()
