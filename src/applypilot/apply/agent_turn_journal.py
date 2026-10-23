"""Durable Agent turn journal for apply attempts.

Persists turn start/progress/completion, supervisor interventions and
proposal outcomes as ordered control events, and projects turn results onto
allowlisted resumable summaries.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from applypilot.apply import application_actor as application_actor_mod
from applypilot.apply import application_supervisor_loop as supervisor_loop_mod
from applypilot.apply.contracts import (
    AgentCheckpoint,
    AgentRunRequest,
    AgentTurnResult,
    ApplicationEvent,
    HumanRequest,
    contract_json,
)

logger = logging.getLogger(__name__)


_agent_event_clock = lambda: datetime.now(UTC)


def _ordered_agent_event_time(previous: datetime | None = None) -> datetime:
    """Keep one run's durable lifecycle ordered on coarse wall clocks."""
    current = _agent_event_clock()
    if previous is not None and current <= previous:
        return previous + timedelta(microseconds=1)
    return current


def _persist_agent_turn_started(
    request: AgentRunRequest,
    *,
    backend: str,
    model: str,
    runtime_metadata: dict,
) -> datetime:
    """Best-effort control telemetry; never becomes application authority."""
    from applypilot.database import append_agent_event

    occurred_at = _ordered_agent_event_time()
    idempotency_key = f"agent-turn:v2:{request.actor_id}:{request.turn_id}:started"
    event = ApplicationEvent(
        event_id=idempotency_key,
        attempt_id=request.attempt_id,
        run_id=request.run_id,
        phase=request.phase,
        actor=request.agent_role,
        event_type="agent.turn.started",
        payload={
            "backend": backend,
            "model": model,
            "concurrency_mode": request.concurrency_mode,
            "available_tools": list(request.available_tools),
            "runtime": runtime_metadata,
        },
        idempotency_key=idempotency_key,
        actor_id=request.actor_id,
        turn_id=request.turn_id,
        schema_version="2",
        occurred_at=occurred_at,
    )
    try:
        append_agent_event(event)
    except Exception as exc:  # noqa: BLE001 - advisory telemetry must not alter apply outcome
        logger.warning("Could not persist Agent turn start %s: %s", request.run_id, exc)
    return occurred_at


def _persist_agent_turn_progress(
    request: AgentRunRequest,
    *,
    sequence: int,
    last_tool: str,
    tool_call_count: int,
    browser_success_count: int,
    browser_failure_count: int,
    elapsed_ms: int,
    occurred_after: datetime | None,
) -> datetime:
    """Persist a bounded heartbeat without page text, inputs, or tool outputs."""
    from applypilot.database import append_agent_event

    occurred_at = _ordered_agent_event_time(occurred_after)
    safe_tool = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(last_tool or "unknown"))[:80]
    idempotency_key = (
        f"agent-turn:v2:{request.actor_id}:{request.turn_id}:progress:{max(1, sequence)}"
    )
    event = ApplicationEvent(
        event_id=idempotency_key,
        attempt_id=request.attempt_id,
        run_id=request.run_id,
        phase=request.phase,
        actor=request.agent_role,
        event_type="agent.turn.progress",
        payload={
            "sequence": max(1, sequence),
            "last_tool": safe_tool,
            "tool_call_count": max(0, tool_call_count),
            "browser_success_count": max(0, browser_success_count),
            "browser_failure_count": max(0, browser_failure_count),
            "elapsed_ms": max(0, elapsed_ms),
        },
        idempotency_key=idempotency_key,
        actor_id=request.actor_id,
        turn_id=request.turn_id,
        schema_version="2",
        occurred_at=occurred_at,
    )
    try:
        append_agent_event(event)
    except Exception as exc:  # noqa: BLE001 - advisory telemetry must not alter apply outcome
        logger.warning("Could not persist Agent turn progress %s: %s", request.run_id, exc)
    return occurred_at


def _persist_supervisor_intervention(
    request: AgentRunRequest,
    decision: supervisor_loop_mod.SupervisorDecision,
    *,
    sequence: int,
    backend: str,
    stage: str,
    outcome: str | None = None,
    occurred_after: datetime | None,
) -> datetime:
    """Durably persist control intent/outcome; failures must stop the action."""

    from applypilot.database import append_agent_event

    if stage not in {"intent", "outcome"}:
        raise ValueError("supervisor intervention stage must be intent or outcome")
    if stage == "outcome" and not outcome:
        raise ValueError("supervisor intervention outcome is required")
    occurred_at = _ordered_agent_event_time(occurred_after)
    idempotency_key = (
        f"agent-turn:v2:{request.actor_id}:{request.turn_id}:supervisor:{sequence}:{stage}"
    )
    event = ApplicationEvent(
        event_id=idempotency_key,
        attempt_id=request.attempt_id,
        run_id=request.run_id,
        phase=request.phase,
        actor="application_supervisor",
        event_type=f"agent.supervisor.intervention.{stage}",
        payload={
            "sequence": sequence,
            "stage": stage,
            "outcome": outcome,
            "level": decision.level,
            "action": decision.action,
            "reason_code": decision.reason_code,
            "backend": backend,
            "expected_turn_id": decision.expected_turn_id,
            "receipt_only": decision.receipt_only,
            "signals": {
                "meaningful_progress": decision.signals.meaningful_progress,
                "no_progress_window": round(decision.signals.no_progress_window, 3),
                "tool_repeat_count": decision.signals.tool_repeat_count,
                "page_signature": decision.signals.page_signature,
                "unresolved_control_delta": decision.signals.unresolved_control_delta,
                "validation_delta": decision.signals.validation_delta,
                "last_successful_effect": decision.signals.last_successful_effect,
            },
        },
        idempotency_key=idempotency_key,
        actor_id=request.actor_id,
        turn_id=request.turn_id,
        schema_version="2",
        occurred_at=occurred_at,
    )
    append_agent_event(event)
    return occurred_at


def _persist_agent_turn_completed(
    request: AgentRunRequest,
    result: AgentTurnResult,
    *,
    application_status: str,
    duration_ms: int,
    source: str,
    metrics: Mapping[str, object] | None = None,
    conflict_classification: str | None = None,
    conflict_status_families: Mapping[str, object] | None = None,
    occurred_after: datetime | None = None,
    expected_checkpoint_sequence: int | None = None,
) -> datetime:
    """Atomically save the terminal control event and resumable checkpoint."""
    from applypilot.database import (
        current_agent_checkpoint_sequence,
        record_agent_turn_control,
    )

    actor_decision = application_actor_mod.decision_for_turn(
        request,
        result,
        application_status=application_status,
    )
    actor_decision_json = contract_json(actor_decision)
    legacy_actor_decision_json = {
        key: actor_decision_json[key]
        for key in (
            "run_id",
            "attempt_id",
            "phase",
            "disposition",
            "next_phase",
            "recovery_action",
            "human_interruption",
            "shadow_only",
        )
    }
    legacy_actor_decision_json["schema_version"] = "1"
    raw_evidence_refs = result.observations.get("evidence_refs", ())
    evidence_ref_count = (
        len(raw_evidence_refs) if isinstance(raw_evidence_refs, (list, tuple)) else 0
    )
    bounded_metrics: dict[str, int | float] = {}
    for key, value in (metrics or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        bounded_metrics[_bounded_control_text(key, maximum=80)] = max(0, value)
    occurred_at = _ordered_agent_event_time(occurred_after)
    completed_idempotency_key = (
        f"agent-turn:v2:{request.actor_id}:{request.turn_id}:completed"
    )
    payload: dict[str, object] = {
        "reported_status": result.status,
        "application_status": application_status,
        # Free-form Agent/page text stays in the transient turn result. The
        # durable control plane records only bounded workflow metadata.
        "summary_length": len(result.summary),
        "duration_ms": max(0, duration_ms),
        "source": source,
        "proposal_ids": [proposal.proposal_id for proposal in result.proposals],
        "evidence_ref_count": evidence_ref_count,
        "metrics": bounded_metrics,
        # Preserve the established v1 key for existing readers while the
        # native v2 envelope is published alongside it for durable actors.
        "actor_decision": legacy_actor_decision_json,
        "actor_decision_v2": actor_decision_json,
    }
    if conflict_classification in {
        "evidence_mismatch",
        "legacy_result_invalid",
        "status_mismatch",
    }:
        payload["conflict_classification"] = conflict_classification
    if conflict_classification is not None and isinstance(
        conflict_status_families, Mapping
    ):
        allowed_families = {
            "failure",
            "invalid",
            "ready",
            "applied",
            "uncertain",
            "manual_gate",
            "non_submit",
            "other",
        }
        safe_families = {
            key: value
            for key in ("structured", "legacy")
            if (value := str(conflict_status_families.get(key) or ""))
            in allowed_families
        }
        if safe_families:
            payload["conflict_status_families"] = safe_families
    event = ApplicationEvent(
        event_id=completed_idempotency_key,
        attempt_id=request.attempt_id,
        run_id=request.run_id,
        phase=request.phase,
        actor=request.agent_role,
        event_type="agent.turn.completed",
        payload=payload,
        evidence_refs=(),
        idempotency_key=completed_idempotency_key,
        actor_id=request.actor_id,
        turn_id=request.turn_id,
        schema_version="2",
        occurred_at=occurred_at,
    )
    if expected_checkpoint_sequence is None:
        try:
            expected_checkpoint_sequence = current_agent_checkpoint_sequence(request.actor_id)
        except Exception as exc:  # noqa: BLE001 - control state remains advisory
            logger.warning(
                "Could not read Agent actor sequence %s: %s",
                request.actor_id,
                exc,
            )
            return occurred_at
    checkpoint = AgentCheckpoint(
        checkpoint_id=f"{completed_idempotency_key}:checkpoint",
        run_id=request.run_id,
        attempt_id=request.attempt_id,
        phase=request.phase,
        sequence=expected_checkpoint_sequence + 1,
        state={
            "application_status": application_status,
            "result": _durable_agent_result(result),
            "source": source,
            "actor_decision": legacy_actor_decision_json,
            "actor_decision_v2": actor_decision_json,
        },
        actor_id=request.actor_id,
        turn_id=request.turn_id,
        idempotency_key=completed_idempotency_key,
        expected_sequence=expected_checkpoint_sequence,
        fresh_turn_resume_authorized=False,
        schema_version="2",
        created_at=occurred_at,
    )
    human_request = application_actor_mod.human_request_for_decision(actor_decision)
    if (
        human_request is None
        and result.requested_human_input
        and (
            actor_decision.recovery_action is None
            or (
                actor_decision.recovery_action.action == "park"
                and actor_decision.recovery_action.missing_material is not None
            )
        )
    ):
        human_request = HumanRequest(
            request_id=f"{request.run_id}:human:1",
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            request_type="agent_clarification",
            prompt="Agent requested human review; inspect the run evidence before continuing.",
            context={
                "actor_id": request.actor_id,
                "turn_id": request.turn_id,
                "phase": request.phase,
                "application_status": application_status,
                "requested_input_length": len(result.requested_human_input),
            },
        )
    try:
        record_agent_turn_control(event, checkpoint, human_request)
    except Exception as exc:  # noqa: BLE001 - advisory telemetry must not alter apply outcome
        logger.warning("Could not persist Agent turn completion %s: %s", request.run_id, exc)
    return occurred_at


def _bounded_control_text(value: object, *, maximum: int = 200) -> str:
    """Normalize identifier-like control metadata without retaining page text."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()
    return text[:maximum]


def _durable_agent_result(result: AgentTurnResult) -> dict[str, object]:
    """Project a turn result onto an allowlisted, resumable control summary."""
    return {
        "schema_version": "1",
        "run_id": _bounded_control_text(result.run_id),
        "status": _bounded_control_text(result.status),
        "summary_length": len(result.summary),
        "proposals": [
            {
                "proposal_id": _bounded_control_text(proposal.proposal_id),
                "kind": _bounded_control_text(proposal.kind, maximum=100),
                "depends_on": [
                    _bounded_control_text(dependency)
                    for dependency in proposal.depends_on
                ],
                "concurrency_mode": _bounded_control_text(
                    proposal.concurrency_mode,
                    maximum=100,
                ),
                "concurrency_key": (
                    None
                    if proposal.concurrency_key is None
                    else _bounded_control_text(proposal.concurrency_key)
                ),
                "priority": proposal.priority,
            }
            for proposal in result.proposals
        ],
        "observation_keys": sorted(
            _bounded_control_text(key, maximum=100) for key in result.observations
        ),
        "requested_human_input": result.requested_human_input is not None,
    }


def _persist_agent_proposal_outcomes(
    request: AgentRunRequest,
    outcomes: Mapping[str, Mapping[str, object]],
    *,
    max_workers: int,
    occurred_after: datetime | None = None,
) -> datetime:
    """Persist outcome metadata, never specialist return values or page content."""
    from applypilot.database import append_agent_event

    completed = [
        _bounded_control_text(proposal_id)
        for proposal_id, outcome in outcomes.items()
        if outcome.get("status") == "completed"
    ]
    failed = [
        _bounded_control_text(proposal_id)
        for proposal_id, outcome in outcomes.items()
        if outcome.get("status") == "failed"
    ]
    blocked = [
        _bounded_control_text(proposal_id)
        for proposal_id, outcome in outcomes.items()
        if outcome.get("status") == "blocked"
    ]
    occurred_at = _ordered_agent_event_time(occurred_after)
    idempotency_key = (
        f"agent-turn:v2:{request.actor_id}:{request.turn_id}:proposals:1"
    )
    event = ApplicationEvent(
        event_id=idempotency_key,
        attempt_id=request.attempt_id,
        run_id=request.run_id,
        phase=request.phase,
        actor="agent-orchestrator",
        event_type="agent.proposals.executed",
        payload={
            "completed_ids": completed,
            "failed_ids": failed,
            "blocked_ids": blocked,
            "max_workers": max_workers,
        },
        idempotency_key=idempotency_key,
        actor_id=request.actor_id,
        turn_id=request.turn_id,
        schema_version="2",
        occurred_at=occurred_at,
    )
    try:
        append_agent_event(event)
    except Exception as exc:  # noqa: BLE001 - advisory telemetry must not alter apply outcome
        logger.warning("Could not persist Agent proposal outcomes %s: %s", request.run_id, exc)
    return occurred_at
