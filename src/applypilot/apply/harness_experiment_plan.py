"""Offline-only planning and cold/warm comparisons for the existing E evaluator.

The entry point cannot run a model, load a profile, or resolve endpoint credentials.
Budget estimates describe assumptions, never an enforced monetary spending cap.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from applypilot.apply.harness_measurement import _integer, _number, validate_pricing

SCHEMA = "harness-experiment-plan/v1"
FIELDS = frozenset({"schema_version", "model", "endpoint_binding", "temperature", "reasoning_effort", "repeats",
                    "seed", "max_turns", "max_decisions", "max_output_tokens", "trial_seconds", "train",
                    "assumed_max_input_tokens_per_decision", "output_cap_includes_reasoning", "pricing",
                    "proposed_spend_limit_usd"})


def plan_experiment(config):
    from applypilot.apply.harness_evaluation import ARMS, digest, schedule, tasks, validate_splits
    if not isinstance(config, dict) or set(config) - FIELDS or config.get("schema_version") != SCHEMA:
        raise ValueError("unsupported experiment plan fields/schema")
    for key, low, high in (("repeats", 1, 10), ("max_turns", 1, 32), ("max_decisions", 1, 2048),
                           ("max_output_tokens", 128, 8192), ("trial_seconds", 5, 300)):
        if type(config.get(key)) is not int or not low <= config[key] <= high:
            raise ValueError("invalid " + key)
    if type(config.get("seed")) is not int or type(config.get("train")) is not bool:
        raise ValueError("explicit seed and training choice required")
    if _number(config.get("temperature")) is None or config["temperature"] > 2:
        raise ValueError("invalid temperature")
    training, holdout = tasks("train"), tasks("holdout")
    validate_splits(training, holdout)
    plan = schedule(holdout, repeats=config["repeats"], seed=config["seed"])
    train_count = 2 * len(training) if config["train"] else 0
    full_decisions = (len(plan) + train_count) * config["max_turns"]
    admitted_decisions = min(config["max_decisions"], full_decisions)
    missing = []
    for key in ("model", "endpoint_binding"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            missing.append(key)
    pricing = config.get("pricing")
    if pricing is not None and (not isinstance(pricing, dict) or set(pricing) - {
            "input", "output", "cached", "source_url", "observed_date", "billing_categories"}):
        raise ValueError("unsupported pricing fields; credentials do not belong in experiment plans")
    rates_available = False
    if isinstance(pricing, dict):
        try:
            validate_pricing(pricing)
            rates_available = True
        except ValueError:
            pass
    if not rates_available:
        missing.append("explicit_input_output_rates")
    if not isinstance(pricing, dict) or not pricing.get("source_url") or not pricing.get("observed_date"):
        missing.append("dated_pricing_source")
    if not isinstance(pricing, dict) or pricing.get("billing_categories") != "text_input_cached_input_output":
        missing.append("supported_billing_categories")
    input_cap = _integer(config.get("assumed_max_input_tokens_per_decision"))
    if input_cap is None or input_cap == 0:
        missing.append("assumed_max_input_tokens_per_decision")
    if config.get("output_cap_includes_reasoning") is not True:
        missing.append("provider_output_cap_semantics")
    proposed_limit = _number(config.get("proposed_spend_limit_usd"))
    if proposed_limit is None:
        missing.append("proposed_spend_limit_usd")
    estimate = None
    estimate_requirements = {"explicit_input_output_rates", "dated_pricing_source", "supported_billing_categories",
                             "assumed_max_input_tokens_per_decision", "provider_output_cap_semantics"}
    if not set(missing) & estimate_requirements:
        # No cache-discount claim. A larger supplied cache rate is conservative.
        input_rate = max(pricing["input"], pricing.get("cached") or 0)
        estimate = _number(admitted_decisions * (input_cap * input_rate
                           + config["max_output_tokens"] * pricing["output"]) / 1_000_000)
    warnings = []
    if config["max_decisions"] < full_decisions:
        warnings.append("decision_cap_cannot_cover_worst_case_complete_paired_cohort")
    if not config["train"]:
        warnings.append("existing_E_has_no_frozen_memory_import;warm_has_no_learned_advice_without_training")
    if proposed_limit is not None and estimate is not None and estimate > proposed_limit:
        warnings.append("planning_estimate_exceeds_proposed_limit")
    return {"schema_version": SCHEMA, "status": "not_run", "model_calls": 0,
            "live_authorization": "not_granted_by_plan", "config": dict(config), "config_digest": digest(config),
            "arms": list(ARMS), "training_trials": train_count, "evaluation_trials": len(plan),
            "maximum_decisions_for_complete_cohort": full_decisions, "configured_decision_cap": config["max_decisions"],
            "maximum_output_tokens_under_declared_cap": admitted_decisions * config["max_output_tokens"],
            "planning_estimate_usd": estimate, "missing_configuration": missing, "warnings": warnings,
            "monetary_cap_enforced": False,
            "budget_scope": "assumed_input_limit_not_enforced_by_E;provider_timeouts_may_still_be_billed",
            "browser_cold_warm": "both_fresh_contexts;warm_means_frozen_procedure_advice_only",
            "provider_prompt_cache": "uncontrolled_record_reported_hits;not_equivalent_to_procedure_warmth",
            "planned_cells": [{"task_id": task["task_id"], "arm": arm, "cohort": cohort,
                               "pair_id": pair, "repeat": repeat} for task, arm, cohort, pair, repeat in plan]}


def paired_cold_warm(rows):
    """Supplement E's within-cohort arm comparisons with matched warm-minus-cold.

    Retain unknown outcomes, failures and costs. Repeats are paired samples, not
    independent employers; this intentionally makes no significance/gain claim.
    """
    from applypilot.apply.harness_evaluation import ARMS
    groups = defaultdict(list)
    for row in rows:
        if row.get("pair_id"):
            groups[(row["schema_version"], row["driver_mode"], row["split"])].append(row)
    comparisons = []
    # Mixing schema revisions for the same logical experiment is not a valid pair.
    if len({key[0] for key in groups}) > 1:
        raise ValueError("mixed evaluation schemas")
    for group, samples in sorted(groups.items()):
        pairs = defaultdict(dict)
        for row in samples:
            key = (row["arm"], row["cohort"])
            if key in pairs[row["pair_id"]]:
                raise ValueError("duplicate cold/warm cell")
            pairs[row["pair_id"]][key] = row
        expected = {(arm, cohort) for arm in ARMS for cohort in ("cold", "warm")}
        for cells in pairs.values():
            if set(cells) != expected:
                raise ValueError("incomplete cold/warm pair; never drop missing cells")
            for field in ("task_id", "repeat", "config_digest", "safety_digest", "tools_digest", "memory_digest",
                          "initial_state_digest", "browser_version", "completable"):
                values = [row.get(field) for row in cells.values()]
                if None in values or len(set(values)) != 1:
                    raise ValueError("non-equivalent cold/warm " + field)
            if any(row.get("termination_reason") == "configuration_drift" for row in cells.values()):
                raise ValueError("model configuration drift")
        for arm in ARMS:
            matched = [(cells[(arm, "cold")], cells[(arm, "warm")]) for cells in pairs.values()]
            metrics = {}
            for field in ("autonomous_complete", "false_ready", "elapsed_ms", "estimated_cost_usd",
                          "input_tokens", "output_tokens", "user_intervention_requests", "user_intervention_ms"):
                selected = [(a, b) for a, b in matched if field != "autonomous_complete" or a["completable"]]
                deltas = []
                for cold, warm in selected:
                    left, right = cold.get(field), warm.get(field)
                    if field in {"autonomous_complete", "false_ready"}:
                        left = int(left) if type(left) is bool else None
                        right = int(right) if type(right) is bool else None
                    deltas.append(right - left if _number(left) is not None and _number(right) is not None else None)
                known = [d for d in deltas if d is not None and math.isfinite(d)]
                metrics[field] = {"mean_delta": sum(known) / len(known) if known and len(known) == len(deltas) else None,
                                  "observed_pairs": len(known), "unknown_pairs": len(deltas) - len(known)}
            recovery_pairs = [(a, b) for a, b in matched if a.get("recovery_triggered") is True
                              and b.get("recovery_triggered") is True]
            recovery_delta = (sum(int(b["recovered"]) - int(a["recovered"]) for a, b in recovery_pairs)
                              if recovery_pairs and all(type(r.get("recovered")) is bool
                                                        for pair in recovery_pairs for r in pair) else None)
            comparisons.append({"evaluation_schema": group[0], "driver_mode": group[1], "split": group[2],
                                "arm": arm, "paired_trials": len(matched),
                                "task_clusters": len({a["task_id"] for a, _ in matched}),
                                "warm_advice_hit_trials": sum(b.get("advice_hits", 0) > 0 for _, b in matched),
                                "joint_recovery_denominator": len(recovery_pairs),
                                "recovery_delta": recovery_delta,
                                "metrics": metrics, "contrast": "warm_minus_cold",
                                "scope": "controlled_fixtures_not_population_generalization"})
    return comparisons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.template.read_text(encoding="utf-8"))
    plan = plan_experiment(config)
    from applypilot.apply.harness_evaluation import digest, source_manifest
    source = source_manifest(args.root.resolve())
    plan.update(source_hashes=source, source_digest=digest(source))
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "experiment-plan.json").write_text(json.dumps(plan, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"status": "not_run", "model_calls": 0, "evaluation_trials": plan["evaluation_trials"],
                      "planning_estimate_usd": plan["planning_estimate_usd"],
                      "missing_configuration": plan["missing_configuration"]}))


if __name__ == "__main__":
    main()
