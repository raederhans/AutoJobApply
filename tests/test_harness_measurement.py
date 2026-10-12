"""Synthetic inputs only; no model endpoint or applicant workspace is used."""
import copy
import json
from pathlib import Path

import pytest
from test_harness_evaluation import Environment, trial

from applypilot.apply.harness_evaluation import run_trial, source_manifest, tasks
from applypilot.apply.harness_experiment_plan import paired_cold_warm, plan_experiment
from applypilot.apply.harness_measurement import (
    TrialMeasurement,
    aggregate_measurements,
    estimate_usage_cost,
    legacy_metric_views,
    metric,
    normalize_usage,
)
from applypilot.apply.performance_attribution import PerformanceTrace
from applypilot.apply.procedure_experience import FrozenProcedureMemory

ROOT = Path(__file__).resolve().parents[1]


def template():
    return json.loads((ROOT / "docs/active/agent-harness-p0-20261011/p1-c0-budget-template.json").read_text())


def test_integration_uses_existing_trial_ids_without_model_or_private_payload():
    row = trial()
    report = row["measurement"]
    assert report["private_trace"]["attempt_id"] == row["run_id"]
    assert report["private_trace"]["events"][0]["turn"] is None
    assert any(e["turn"] == 0 for e in report["private_trace"]["events"])
    for name in ("decision", "browser", "environment_setup", "oracle", "cleanup", "encoding"):
        assert report["timing"][name]["status"] == "measured"
    for name in ("coordination_wait", "execution_slot_wait", "page_load", "recovery"):
        assert report["timing"][name]["value"] is None
        assert report["timing"][name]["status"] == "unknown"
    assert report["timing"]["model_client"]["status"] == "not_occurred"
    assert report["cost"]["estimated_usd"]["value"] is None
    assert not report["accounting"]["additive_wall_time"]
    assert "messages" not in json.dumps(report)
    public = aggregate_measurements([row])
    assert row["run_id"] not in json.dumps(public)
    assert "private_trace" not in json.dumps(public)


def test_failed_browser_call_keeps_time_and_original_failure_behavior():
    class BrokenAction(Environment):
        def call(self, method, **kwargs):
            if method == "act":
                raise RuntimeError("PRIVATE-PAYLOAD-DO-NOT-RETAIN")
            return super().call(method, **kwargs)
    row = trial(BrokenAction(), {"operation": "fill_control", "arguments": {"field_key": "private", "value": "private"}})
    assert row["termination_reason"] == "driver_or_environment_RuntimeError"
    assert row["measurement"]["timing"]["browser"]["observed_samples"] == 2
    assert row["measurement"]["timing"]["browser"]["value"] >= 0
    assert "PRIVATE-PAYLOAD" not in json.dumps(row["measurement"])


def test_failed_decision_keeps_inclusive_time_and_missing_usage_unknown():
    def fail(*_):
        raise TimeoutError("test-only")
    row = run_trial(tasks("holdout")[0], "current_harness", "cold", {"model": "stub"}, FrozenProcedureMemory(()),
                    environment=Environment(), decide=fail, driver_mode="live_model")
    assert row["measurement"]["timing"]["decision"]["value"] >= 0
    assert row["measurement"]["timing"]["model_client"]["value"] is None
    assert row["measurement"]["usage"]["input"]["value"] is None
    assert row["measurement"]["cost"]["estimated_usd"]["value"] is None


def test_observed_client_timer_is_not_inference_and_subsets_are_not_added_twice():
    # This stub exercises live-envelope validation without any client/network call.
    def stub(*_):
        return {"raw": '{"operation":"finish","arguments":{"status":"ready"}}',
                "provider_ms": 12, "model": "stub", "binding": "stub-binding",
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
                          "prompt_tokens_details": {"cached_tokens": 50},
                          "completion_tokens_details": {"reasoning_tokens": 8}}}
    row = run_trial(tasks("holdout")[0], "current_harness", "cold", {"model": "stub", "binding": "stub-binding"},
                    FrozenProcedureMemory(()), environment=Environment(), decide=stub,
                    pricing={"input": 2, "output": 8, "cached": 1})
    report = row["measurement"]
    assert report["timing"]["model_client"]["value"] == 12
    assert "including_network" in report["timing"]["model_client"]["source"]
    assert report["timing"]["model_service"]["value"] is None
    assert report["usage"]["total"]["value"] == 110
    assert report["usage"]["reasoning_output"]["value"] == 8
    assert report["cost"]["estimated_usd"]["value"] == pytest.approx(0.00023)
    assert report["cost"]["reported_usd"]["value"] is None


@pytest.mark.parametrize("bad", [None, -1, True, float("nan"), float("inf"), "12", 10 ** 1000])
def test_invalid_or_absent_measurements_never_become_zero(bad):
    report = metric([0, bad], unit="ms", source="fixture")
    assert report["value"] is None
    assert report["observed_subtotal"] == 0
    assert report["unknown_samples"] == 1
    assert metric([], unit="ms", source="missing")["status"] == "unknown"


def test_missing_or_regressing_clock_does_not_change_execution():
    for stamps in ([2_000_000, 1_000_000], [None, 1_000_000]):
        trace = TrialMeasurement("a", clock=iter(stamps).__next__)
        result = trace.wrap_decide(lambda *_: "unchanged")({}, 1)
        assert result == "unchanged" and trace.events[0]["duration_ms"] is None


def test_external_boundary_join_rejects_wrong_attempt_and_replays():
    trace = TrialMeasurement("attempt-a")
    with pytest.raises(ValueError, match="binding"):
        trace.record_external("recovery", 5, attempt_id="attempt-b", turn=0, boundary_id="b")
    trace.record_external("recovery", 5, attempt_id="attempt-a", turn=0, boundary_id="b")
    with pytest.raises(ValueError, match="duplicate"):
        trace.record_external("recovery", 5, attempt_id="attempt-a", turn=0, boundary_id="b")
    assert trace.events == [{"turn": 0, "component": "recovery", "duration_ms": 5,
                             "source": "explicit_attempt_turn_boundary", "relationship": "inclusive_may_overlap",
                             "clock": "boundary_owner_local_duration;alignment_unknown"}]


def test_bounded_trace_reports_loss_instead_of_silently_complete_totals():
    trace = TrialMeasurement("a", clock=lambda: 1)
    decide = trace.wrap_decide(lambda *_: None)
    for _ in range(257):
        decide({}, 1)
    assert len(trace.events) == 256 and trace.dropped == 1
    report = trace.report({"run_id": "a", "turns": [], "driver_mode": "reference_driver"}, [])
    assert report["timing"]["decision"]["value"] is None
    assert report["private_trace"]["dropped_events"] == 1


def test_context_limit_keeps_last_encoding_even_without_a_decision_record():
    class LargeAfterAction(Environment):
        def call(self, method, **kwargs):
            if method == "act":
                return {"content": [{"type": "text", "text": "x" * 1_000_100}]}
            return super().call(method, **kwargs)
    row = trial(LargeAfterAction(), {"operation": "fill_control", "arguments": {"field_key": "fictional", "value": "test"}})
    assert row["termination_reason"] == "context_budget"
    assert len(row["turns"]) == 1
    encoding = row["measurement"]["timing"]["encoding"]
    assert encoding["observed_samples"] == 2 and encoding["status"] == "measured"
    assert encoding["value"] >= row["context_encode_ms"]


def test_public_aggregation_retains_partial_subtotals_without_claiming_a_total():
    rows = [trial(), trial()]
    rows[0]["measurement"]["timing"]["browser"] = metric([10], unit="ms", source="fixture")
    rows[1]["measurement"]["timing"]["browser"] = metric([5, None], unit="ms", source="fixture")
    report = aggregate_measurements(rows)[0]["timing"]["browser"]
    assert report["value"] is None and report["observed_subtotal"] == 15
    assert report["observed_samples"] == 2 and report["unknown_samples"] == 1
    assert report["trial_status_counts"] == {"measured": 1, "partial": 1, "unknown": 0, "not_occurred": 0}
    trace = TrialMeasurement(rows[1]["run_id"], clock=lambda: 1)
    for _ in range(257):
        trace.wrap_environment(Environment()).call("observe")
    rows[1]["measurement"] = trace.report(rows[1], [])
    report = aggregate_measurements(rows)[0]["timing"]["browser"]
    assert report["value"] is None and report["observed_subtotal"] == 10
    assert report["unknown_samples"] > 0


def test_known_usage_cost_subtotal_survives_a_later_missing_response():
    count = 0
    def stub(*_):
        nonlocal count
        count += 1
        if count == 2:
            raise TimeoutError("synthetic")
        return {"raw": '{"operation":"observe","arguments":{}}', "model": "stub", "binding": "stub",
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 0}}}
    row = run_trial(tasks("holdout")[0], "current_harness", "cold", {"model": "stub", "binding": "stub"},
                    FrozenProcedureMemory(()), environment=Environment(), decide=stub, pricing={"input": 1, "output": 2})
    cost = row["measurement"]["cost"]["estimated_usd"]
    assert cost["value"] is None and cost["observed_subtotal"] == pytest.approx(0.00012)
    assert cost["status"] == "partial" and row["estimated_cost_usd"] is None
    joined = row["measurement"]["private_trace"]["usage_by_turn"]
    assert [record["turn"] for record in joined] == [0, 1]
    assert joined[0]["tokens"]["input"] == 100 and joined[1]["tokens"]["input"] is None


def test_usage_aliases_partial_failures_and_unsupported_billing_categories():
    raw = {"input_tokens": 100, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 0},
           "output_tokens_details": {"reasoning_tokens": 5}}
    assert normalize_usage(raw)["total"] == 120
    rates = {"input": 1, "output": 2}
    assert estimate_usage_cost([raw], rates) == pytest.approx(0.00014)
    assert estimate_usage_cost([raw, None], rates) is None
    assert estimate_usage_cost([{**raw, "prompt_tokens": 99}], rates) is None
    assert estimate_usage_cost([{**raw, "total_tokens": 125}], rates) is None
    for detail in ({"cached_tokens": 0, "cache_write_tokens": 4}, {"cached_tokens": 0, "audio_tokens": 1},
                   {"cached_tokens": 101}, {"cached_tokens": False}, {"cached_tokens": 0, "cache_write_tokens": -1}):
        assert estimate_usage_cost([{**raw, "input_tokens_details": detail}], rates) is None
    assert normalize_usage({"prompt_tokens": 10})["cached_input"] is None


def test_legacy_sources_keep_windows_parallel_work_unknowns_and_no_private_dimensions():
    trace = PerformanceTrace({"domain": "PRIVATE-DOMAIN", "worker_id": "worker-1"})
    trace.record("agent.turn", 100)
    trace.record("recovery.agent", 60)
    views = legacy_metric_views(
        bridge={"recent": {"queue_wait_ms": {"measured": 1, "unavailable": 1, "p50": 20, "p95": 20, "max": 20}},
                "job_id": "PRIVATE-JOB"},
        group={"spans": {"group_slot_wait": {"samples": 0, "unavailable": 1, "total_ms": 0},
                          "host_execute": {"samples": 2, "unavailable": 1, "total_ms": 300}}},
        performance=trace.snapshot())
    assert views["group"]["metrics"]["group_slot_wait"]["value_ms"] is None
    assert views["group"]["metrics"]["host_execute"]["value_ms"] is None
    assert views["group"]["metrics"]["host_execute"]["observed_subtotal_ms"] == 300
    assert not views["performance"]["additive_wall_time"]
    assert "PRIVATE" not in json.dumps(views)


def test_budget_template_is_offline_complete_and_unknown_prices_are_not_zero():
    config = template()
    plan = plan_experiment(config)
    assert plan["model_calls"] == 0 and plan["status"] == "not_run"
    assert plan["evaluation_trials"] == 42 and plan["training_trials"] == 14
    assert plan["maximum_decisions_for_complete_cohort"] == 448
    assert plan["planning_estimate_usd"] is None
    assert not plan["monetary_cap_enforced"]
    assert plan["planned_cells"] == plan_experiment(config)["planned_cells"]
    config["max_decisions"] = 128
    assert "decision_cap_cannot_cover_worst_case_complete_paired_cohort" in plan_experiment(config)["warnings"]


def test_explicit_assumption_budget_does_not_claim_enforced_spend_cap():
    config = template()
    config.update(model="fixture-model", endpoint_binding="fictional-binding", assumed_max_input_tokens_per_decision=2000,
                  output_cap_includes_reasoning=True, proposed_spend_limit_usd=1)
    config["pricing"].update(input=2, output=8, cached=1, source_url="https://example.invalid/pricing", observed_date="2026-10-12")
    plan = plan_experiment(config)
    assert plan["planning_estimate_usd"] == pytest.approx(448 * (2000 * 2 + 1024 * 8) / 1_000_000)
    assert "planning_estimate_exceeds_proposed_limit" in plan["warnings"]
    assert plan["live_authorization"] == "not_granted_by_plan"
    config["pricing"]["source_url"] = None
    assert plan_experiment(config)["planning_estimate_usd"] is None


def paired_rows():
    seed = trial()
    from applypilot.apply.harness_evaluation import ARMS
    return [dict(seed, arm=arm, cohort=cohort, pair_id="pair", repeat=0) for arm in ARMS for cohort in ("cold", "warm")]


def test_cold_warm_keeps_unknown_costs_and_full_pair_denominators():
    rows = paired_rows()
    result = paired_cold_warm(rows)
    assert len(result) == 3
    assert result[0]["metrics"]["estimated_cost_usd"] == {"mean_delta": None, "observed_pairs": 0, "unknown_pairs": 1}
    assert result[0]["metrics"]["elapsed_ms"]["mean_delta"] == 0
    with pytest.raises(ValueError, match="incomplete"):
        paired_cold_warm(rows[:-1])
    with pytest.raises(ValueError, match="duplicate"):
        paired_cold_warm(rows + rows[:1])
    mixed = copy.deepcopy(rows)
    mixed[0]["schema_version"] = "different"
    with pytest.raises(ValueError, match="schemas"):
        paired_cold_warm(mixed)


@pytest.mark.parametrize("field", ["config_digest", "memory_digest", "tools_digest", "initial_state_digest", "browser_version"])
def test_cold_warm_requires_equivalent_initial_conditions(field):
    rows = paired_rows()
    rows[0][field] = "different"
    with pytest.raises(ValueError, match=field):
        paired_cold_warm(rows)


def test_source_identity_includes_measurement_and_plan_implementations():
    manifest = source_manifest(ROOT)
    assert "src/applypilot/apply/harness_measurement.py" in manifest
    assert "src/applypilot/apply/harness_experiment_plan.py" in manifest
