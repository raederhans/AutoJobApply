"""Recovery classifications and existing Supervisor/Episode boundaries."""
import json
import sqlite3
from copy import deepcopy
from uuid import uuid4

import pytest

from applypilot.apply.attended_recovery import assess_recovery, episode_barrier, extract_form, summarize_feedback

STATE = {"host_session_id": "host-1", "tab_id": "tab-1"}
URL = "https://boards.greenhouse.io/example/jobs/123"


def form():
    return {"page_url": URL, "fields": [{"field_key": "city", "value": "Synthetic City", "semantic": {"identification": "identified"}}],
            "coverage": {"scope": "visible_top_document_open_shadow", "iframe_count": 0},
            "question_coverage": {"fields_truncated": False}}


def inspection():
    return {"session_id": "host-1", "target": {"runtime": "iab", "tab_id": "tab-1", "application_url": URL},
            "phase": "prepare", "submission_authorized": False,
            "content": [{"type": "text", "text": json.dumps({"page_url": URL, "tab_id": "tab-1"})},
                        {"type": "text", "text": json.dumps({"form_state": form()})}]}


def feedback(status="verified", reason="record_postconditions_verified", outcome="completed"):
    return {"schema_version": 1, "request_id": str(uuid4()), "session_id": "host-1", "token_epoch": "epoch-1",
            "ok": outcome == "completed", "outcome": outcome,
            "content": [{"type": "text", "text": json.dumps({"subgoal_result": {"status": status, "reason": reason,
                        "progress": [{"field_key": "city", "verified": status == "verified"}]}})}]}


def test_extract_actual_host_form_requires_both_page_and_session_identity():
    assert extract_form(inspection(), STATE) == form()
    for change in ({"session_id": "other"}, {"phase": "submit"}, {"submission_authorized": True}, {"target": {"runtime": "cdp"}}):
        with pytest.raises(ValueError):
            extract_form({**inspection(), **change}, STATE)


def test_extract_cannot_choose_a_lucky_form_or_invent_missing_coverage():
    value = inspection()
    value["content"].append(value["content"][-1])
    with pytest.raises(ValueError):
        extract_form(value, STATE)
    with pytest.raises(ValueError):
        extract_form({**inspection(), "content": []}, STATE)


@pytest.mark.parametrize("change", [{"token_epoch": "other"}, {"session_id": "other"}, {"outcome": "unknown"},
                                   {"ok": "true"}, {"ok": False}, {"schema_version": 2}])
def test_feedback_rejects_wrong_epoch_session_schema_or_outcome(change):
    with pytest.raises(ValueError):
        summarize_feedback({**feedback(), **change}, STATE, "epoch-1")


@pytest.mark.parametrize("status,category", [("waiting", "waiting"), ("needs_fact", "needs_fact"),
                                            ("verified", "verified"), ("parked", "needs_correction")])
def test_feedback_distinguishes_categories_without_parsing_model_explanations(status, category):
    summary = summarize_feedback(feedback(status), STATE, "epoch-1")
    assert summary["category"] == category
    assert "Synthetic City" not in str(summary)
    assert "city" not in str(summary)


def test_feedback_no_effect_requires_explicit_active_host_preinput_rejection():
    value = feedback(outcome="failed")
    value["content"] = [{"type": "text", "text": json.dumps({"host_state": "active", "reobserve_before_retry": True})}]
    assert summarize_feedback(value, STATE, "epoch-1")["category"] == "no_effect"
    value["content"] = [{"type": "text", "text": "Agent says nothing was clicked"}]
    assert summarize_feedback(value, STATE, "epoch-1")["category"] == "effect_unknown"


def test_feedback_unknown_effect_dominates_claimed_verified_subgoal():
    value = feedback(outcome="outcome_unknown")
    assert summarize_feedback(value, STATE, "epoch-1")["category"] == "effect_unknown"


def test_existing_episode_pending_effect_and_receipt_only_are_read_without_schema_mutation():
    conn = sqlite3.connect(":memory:")
    assert episode_barrier(conn, "attempt") is None
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    conn.execute("CREATE TABLE application_episodes(attempt_id TEXT,state TEXT,command_sequence INTEGER,result_sequence INTEGER)")
    conn.execute("INSERT INTO application_episodes VALUES('attempt','recovering',2,1)")
    assert episode_barrier(conn, "attempt") == "episode_effect_unresolved"
    conn.execute("UPDATE application_episodes SET state='receipt_only'")
    assert episode_barrier(conn, "attempt") == "reconcile_receipt_only"
    assert conn.in_transaction  # No implicit commit/executescript during the borrowed transaction.
    conn.close()


def test_supervisor_repeated_response_read_does_not_count_as_new_action():
    f = feedback("parked", "business_postcondition_unproven")
    summary = summarize_feedback(f, STATE, "epoch-1")
    one = assess_recovery(form(), summary, None, attempt_id="attempt", observed_at=10)
    two = assess_recovery(form(), summary, one, attempt_id="attempt", observed_at=12)
    assert len(two["history"]) == 1
    assert two["supervisor_level"] == one["supervisor_level"]
    assert two["browser_write_authority"] is False


def test_supervisor_distinct_repeated_no_progress_actions_escalate_without_model_or_retry():
    state = None
    for i in range(3):
        summary = summarize_feedback(feedback("parked", "business_postcondition_unproven"), STATE, "epoch-1")
        state = assess_recovery(form(), summary, state, attempt_id="attempt", observed_at=10+i)
    assert state["supervisor_level"] >= 2
    assert state["status"] == "parked"
    assert state["submit_authority"] is False
    assert "Synthetic City" not in str(state)


def test_supervisor_normal_waits_do_not_turn_into_retries():
    state = None
    for i in range(2):
        summary = summarize_feedback(feedback("waiting", "target_option_not_observed"), STATE, "epoch-1")
        state = assess_recovery(form(), summary, state, attempt_id="attempt", observed_at=10+i*100)
    assert state["status"] == "waiting"
    assert state["next_action"] == "observe_pending_options_without_repeating_input"
    assert state["browser_write_authority"] is False


def test_supervisor_unknown_effect_latch_cannot_be_cleared_by_later_success():
    summary = summarize_feedback(feedback(outcome="outcome_unknown"), STATE, "epoch-1")
    state = assess_recovery(form(), summary, None, attempt_id="attempt", observed_at=10)
    success = summarize_feedback(feedback(), STATE, "epoch-1")
    state = assess_recovery(form(), success, state, attempt_id="attempt", observed_at=11)
    assert state["status"] == "parked" and state["unknown_effect_latched"]


def test_supervisor_incomplete_observation_and_missing_fact_are_distinct():
    summary = summarize_feedback(feedback(), STATE, "epoch-1")
    incomplete = form()
    incomplete["fields"][0]["semantic"]["identification"] = "ambiguous"
    assert assess_recovery(incomplete, summary, None, attempt_id="attempt", observed_at=10)["status"] == "needs_observation"
    summary = summarize_feedback(feedback("needs_fact"), STATE, "epoch-1")
    assert assess_recovery(incomplete, summary, None, attempt_id="attempt", observed_at=10)["status"] == "needs_fact"


def test_supervisor_same_request_id_with_different_reply_is_rejected():
    summary = summarize_feedback(feedback(), STATE, "epoch-1")
    state = assess_recovery(form(), summary, None, attempt_id="attempt", observed_at=10)
    with pytest.raises(ValueError, match="replay conflict"):
        assess_recovery(form(), {**summary, "response_digest": "different"}, state, attempt_id="attempt", observed_at=11)


def test_supervisor_waiting_budget_and_arriving_options():
    state = None
    for i in range(3):
        summary = summarize_feedback(feedback("waiting", "target_option_not_observed"), STATE, "epoch-1")
        state = assess_recovery(form(), summary, state, attempt_id="attempt", observed_at=10+i)
    assert state["status"] == "parked"
    arrived = form()
    arrived["fields"][0]["semantic"]["options"] = {"status": "identified"}
    resumed = assess_recovery(arrived, summary, state, attempt_id="attempt", observed_at=14)
    assert resumed["status"] == "reviewed" and resumed["browser_write_authority"] is False


def test_feedback_noncanonical_request_id_is_not_a_new_identity():
    value = feedback()
    value["request_id"] = value["request_id"].replace("-", "")
    with pytest.raises(ValueError):
        summarize_feedback(value, STATE, "epoch-1")


def test_supervisor_fresh_ambiguity_overrides_old_waiting_and_preserves_historical_progress():
    summary = summarize_feedback(feedback(), STATE, "epoch-1")
    state = assess_recovery(form(), summary, None, attempt_id="attempt", observed_at=10)
    waiting = summarize_feedback(feedback("waiting", "target_option_not_observed"), STATE, "epoch-1")
    uncertain = form(); uncertain["coverage"]["iframe_count"] = 1
    result = assess_recovery(uncertain, waiting, state, attempt_id="attempt", observed_at=11)
    assert result["status"] == "needs_observation"
    assert result["historical_verified_field_refs"] == summary["verified_field_refs"]
    assert result["progress_evidence"].startswith("historical_")
