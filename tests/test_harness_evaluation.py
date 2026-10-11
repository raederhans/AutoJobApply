import copy
import json
from pathlib import Path

import pytest

from applypilot.apply.harness_evaluation import (
    ARMS, BrowserRPC, digest, estimate_cost, extract_form, observed_plan, paired_comparisons,
    parse_action, run_trial, schedule, summarize, task_goal, tasks, validate_splits,
)
from applypilot.apply.procedure_experience import FrozenProcedureMemory, ProcedureExperienceStore


class Environment:
    def __init__(self, *, complete=True, unknown=False, oracle=True):
        self.complete, self.unknown, self.oracle = complete, unknown, oracle
        self.calls, self.closed = [], False

    def call(self, method, **kwargs):
        self.calls.append(method)
        if method == "init":
            return {"initial_state_digest": digest(kwargs["task"]), "browser_version": "test-only"}
        if method == "oracle":
            if not self.oracle:
                raise RuntimeError("no terminal evidence")
            return {"complete": self.complete, "substantive_errors": 0, "recovery_triggered": False,
                    "expected_handoff": None, "reads": 1, "writes": 0}
        if method == "act" and self.unknown:
            return {"outcome": "outcome_unknown", "ok": False}
        return {"ok": True, "observation_id": "observation", "content": []}

    def close(self):
        self.closed = True


def driver(action):
    return lambda payload, timeout: {"raw": json.dumps(action), "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def trial(env=None, action=None, **kwargs):
    return run_trial(tasks("holdout")[0], "current_harness", "cold", {"model": "reference"}, FrozenProcedureMemory(()),
                     environment=env or Environment(), decide=driver(action or {"operation": "finish", "arguments": {"status": "ready"}}),
                     driver_mode="reference_driver", **kwargs)


def test_independent_terminal_not_agent_ready_claim_controls_success():
    env = Environment(complete=False)
    result = trial(env)
    assert result["false_ready"] is True and not result["autonomous_complete"]
    assert env.calls[-1] == "oracle" and env.closed
    assert result["estimated_cost_usd"] is None


def test_unavailable_oracle_is_unknown_not_false_success():
    result = trial(Environment(oracle=False))
    assert result["false_ready"] is None
    assert not result["autonomous_complete"]


def test_unknown_effect_stops_even_if_next_call_would_succeed():
    env = Environment(unknown=True)
    result = trial(env, {"operation": "fill_control", "arguments": {"field_key": "f", "value": "x"}})
    assert result["termination_reason"] == "unknown_effect_no_replay"
    assert env.calls.count("act") == 1
    assert not result["autonomous_complete"]


@pytest.mark.parametrize("operation", ["submit", "navigate", "upload_artifact", "evaluate", "click", "shell"])
def test_shared_preparation_boundary_excludes_irreversible_or_arbitrary_tools(operation):
    with pytest.raises(ValueError):
        parse_action({"operation": operation, "arguments": {}})


def test_driver_error_and_failed_trials_stay_in_denominator():
    result = trial(action={"operation": "submit", "arguments": {}})
    assert not result["autonomous_complete"]
    rows = summarize([result])
    assert rows[0]["trials"] == 1 and rows[0]["completion_denominator"] == 1
    assert rows[0]["autonomous_rate"] == 0


def test_handoff_is_not_autonomous_completion_and_wait_time_is_unknown():
    result = trial(action={"operation": "finish", "arguments": {"status": "needs_fact"}})
    assert not result["autonomous_complete"]
    assert result["user_intervention_requests"] == 1 and result["user_intervention_ms"] is None


def test_measured_human_intervention_is_assisted_not_autonomous():
    count = 0
    def decide(payload, timeout):
        nonlocal count
        count += 1
        return {"raw": json.dumps({"operation": "finish", "arguments": {"status": "needs_fact" if count == 1 else "ready"}})}
    result = run_trial(tasks("holdout")[0], "current_harness", "cold", {"model": "reference"}, FrozenProcedureMemory(()),
        environment=Environment(), decide=decide, driver_mode="reference_driver", human=lambda _: {"School": "Cedar Institute"})
    assert result["assisted_complete"] and not result["autonomous_complete"]
    assert result["user_intervention_ms"] >= 0


def test_training_split_disjoint_on_employer_and_page_family():
    validate_splits(tasks("train"), tasks("holdout"))
    for key in ("task_id", "employer", "family"):
        changed = copy.deepcopy(tasks("holdout"))
        changed[0][key] = tasks("train")[0][key]
        with pytest.raises(ValueError, match=key):
            validate_splits(tasks("train"), changed)


def test_holdout_cannot_write_memory(tmp_path):
    with pytest.raises(ValueError, match="held-out"):
        trial(train_store=ProcedureExperienceStore(tmp_path / "m.db"))
    assert not (tmp_path / "m.db").exists()


def test_schedule_is_reproducible_complete_and_counterbalanced():
    plan = schedule(tasks("holdout"), repeats=2)
    assert plan == schedule(tasks("holdout"), repeats=2)
    assert len(plan) == 84
    assert set((a, c) for _, a, c, _, _ in plan[:6]) == {(a, c) for a in ARMS for c in ("cold", "warm")}
    assert plan != schedule(tasks("holdout"), repeats=2, seed=18)


def test_cost_unknown_is_not_zero_and_cache_requires_separate_rate():
    usage = {"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 50}}
    assert estimate_cost([usage], {"input": 2, "output": 8}) is None
    assert estimate_cost([None], {"input": 2, "output": 8}) is None
    assert estimate_cost([usage], {"input": 2, "output": 8, "cached": 1}) == pytest.approx(0.00023)
    assert estimate_cost([], {"input": 2, "output": 8}) is None
    with pytest.raises(ValueError):
        estimate_cost([usage], {"input": -1, "output": 8})


def paired_rows():
    seed = trial()
    return [dict(seed, arm=arm, pair_id="pair", cohort="cold") for arm in ARMS]


@pytest.mark.parametrize("key", ["config_digest", "safety_digest", "tools_digest", "initial_state_digest", "memory_digest", "browser_version"])
def test_non_equivalent_pairs_cannot_publish_a_gain(key):
    rows = paired_rows()
    rows[1][key] = "different"
    with pytest.raises(ValueError, match=key):
        paired_comparisons(rows)


def test_missing_or_duplicate_arm_is_not_silently_dropped():
    rows = paired_rows()
    with pytest.raises(ValueError, match="incomplete"):
        paired_comparisons(rows[:-1])
    with pytest.raises(ValueError, match="duplicate"):
        paired_comparisons(rows + rows[:1])
    result = paired_comparisons(rows)
    assert result[0]["autonomous_rate_delta"] == 0
    assert result[0]["task_bootstrap_95_interval"] is None


def reference_driver(payload, timeout):
    """Test driver reads only public goal/observations, never the independent oracle."""
    current = json.loads(payload["messages"][-1]["content"])
    assert "oracle" not in current
    block = extract_form(current["observation"])
    report = block.get("subgoal_result") or {}
    if report.get("status") == "verified":
        action = {"operation": "finish", "arguments": {"status": "ready"}}
    elif report.get("status") == "needs_fact":
        action = {"operation": "finish", "arguments": {"status": "needs_fact"}}
    elif report.get("status") == "parked" and report.get("reason") not in {"dependencies_changed", "preinput_rejected"}:
        action = {"operation": "finish", "arguments": {"status": "blocked"}}
    else:
        form = block.get("form_state", {})
        # Baseline lacks optional semantic annotation; use its existing explicit path.
        for field in form.get("fields", []):
            if "semantic" not in field and field.get("record_path"):
                field["semantic"] = {"record": {"key": field["record_path"][-1]["key"]}}
        plan = observed_plan(form, current["goal"])
        action = {"operation": "prepare_subgoal", "arguments": plan} if plan else {"operation": "finish", "arguments": {"status": "blocked"}}
    return {"raw": json.dumps(action), "usage": None}


@pytest.mark.browser
def test_real_production_controls_train_freeze_and_unseen_dom_holdout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    store = ProcedureExperienceStore(tmp_path / "m.db")
    memory = FrozenProcedureMemory(())
    training = [t for t in tasks("train") if t["scenario"] in {"native", "async", "record"}]
    for repeat in range(2):
        for task in training:
            result = run_trial(task, "current_harness", "cold", {"model": "reference"}, memory,
                environment=BrowserRPC(root), decide=reference_driver, driver_mode="reference_driver", train_store=store)
            assert result["autonomous_complete"], result
    memory = store.freeze()
    assert len(memory.templates) == 3
    frozen_bytes = (tmp_path / "m.db").read_bytes()
    for task in tasks("holdout"):
        result = run_trial(task, "harness_plus_procedure", "warm", {"model": "reference"}, memory,
            environment=BrowserRPC(root), decide=reference_driver, driver_mode="reference_driver", max_turns=6)
        assert result["oracle_available"], result
        assert result["autonomous_complete"] if task["completable"] else result["correct_handoff"], result
        assert result["false_ready"] is False
        if task["scenario"] == "overwrite":
            assert result["recovered"], result
    assert (tmp_path / "m.db").read_bytes() == frozen_bytes
    # A false ready assertion is independently rejected on the actual empty DOM.
    result = run_trial(tasks("holdout")[0], "basic_skill", "cold", {"model": "reference"}, memory,
        environment=BrowserRPC(root), decide=driver({"operation": "finish", "arguments": {"status": "ready"}}),
        driver_mode="reference_driver")
    assert result["false_ready"] is True and not result["autonomous_complete"]


def test_missing_cache_evidence_keeps_cost_unknown():
    assert estimate_cost([{"prompt_tokens": 100, "completion_tokens": 10}], {"input": 2, "output": 8}) is None


def test_cleanup_failure_retains_independently_scored_trial():
    class BrokenClose(Environment):
        def close(self):
            raise OSError("fixture cleanup failure")
    result = trial(BrokenClose())
    assert result["cleanup_failed"] is True
    assert result["oracle_available"] is True
    assert summarize([result])[0]["trials"] == 1


def test_a_handoff_after_wrong_writes_is_not_a_correct_handoff():
    class WrongBeforeHandoff(Environment):
        def call(self, method, **kwargs):
            result = super().call(method, **kwargs)
            if method == "oracle":
                result.update(expected_handoff="blocked", substantive_errors=1)
            return result
    result = trial(WrongBeforeHandoff(), {"operation": "finish", "arguments": {"status": "blocked"}})
    assert result["correct_handoff"] is False
