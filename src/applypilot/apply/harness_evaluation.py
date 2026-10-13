"""Interactive, isolated browser-preparation comparisons with an independent oracle.

A model drives live production controls, never a scripted transcript replay.
Reference-driver tests are labelled separately and are NOT model evidence.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
import queue
import random
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict
from itertools import pairwise
from pathlib import Path
from uuid import uuid4

from applypilot.apply.harness_measurement import (
    TrialMeasurement,
    aggregate_measurements,
    estimate_usage_cost,
    normalize_usage,
)
from applypilot.apply.procedure_experience import FrozenProcedureMemory, ProcedureExperienceStore

ARMS = ("basic_skill", "current_harness", "harness_plus_procedure")
OPERATIONS = ("observe", "fill_control", "select_control", "open_control", "search_control", "fill_batch", "prepare_subgoal")
SAFETY = {
    "scope": "synthetic_preparation_only", "network": "denied", "submission": False,
    "facts": "task_supplied_fictional_values_only", "unknown_effect": "stop_no_replay",
    "tools": OPERATIONS,
}
TOOL_CONTRACT = {
    "observe": {}, "fill_control": {"field_key": "observed", "value": "authoritative scalar"},
    "select_control": {"field_key": "observed", "value": "exact observed option"},
    "open_control": {"field_key": "observed"}, "search_control": {"field_key": "observed", "value": "query, not selection"},
    "fill_batch": {"steps": "1-4 objects with operation,field_key,value"},
    "prepare_subgoal": {"kind": "school_selection|record_fields", "record_key": "observed",
                        "school": "field_key,value,optional query", "record": "steps"},
    "finish": {"status": "ready|needs_fact|blocked"},
}
BASIC = "Complete the supplied application-preparation task using observed controls and supplied facts."
HARNESS = BASIC + (
    " Use explicit record ownership and uncertainty. Prefer bounded preparation subgoals where appropriate."
    " A query is not a selection. Inspect fresh readbacks and dependency changes. Keep verified partial progress;"
    " reobserve after a parked plan, then change only the unresolved fields. Never claim whole-application readiness."
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                     allow_nan=False).encode()).hexdigest()


def tasks(split):
    if split not in {"train", "holdout"}:
        raise ValueError("unknown split")
    employer, family = ("lab-a", "fieldset") if split == "train" else ("lab-b", "aria")
    return [{"task_id": f"{split}-{scenario}", "scenario": scenario, "split": split,
             "employer": employer, "family": family,
             "completable": scenario not in {"duplicate", "missing", "no-commit"}}
            for scenario in ("native", "async", "record", "overwrite", "duplicate", "missing", "no-commit")]


def validate_splits(training, holdout):
    for key in ("task_id", "employer", "family"):
        if {t[key] for t in training} & {t[key] for t in holdout}:
            raise ValueError(f"train/holdout leakage in {key}")


def task_goal(task):
    school = task["scenario"] in {"native", "async", "duplicate", "no-commit"}
    record = "Education 1" if task["scenario"] == "native" else "Education 2" if school else "Employment 1"
    facts = {"School" if task["scenario"] == "native" else "University":
             "Example University" if task["employer"] == "lab-a" else "Cedar Institute"} if school else {
        "Company": "Example Corp" if task["employer"] == "lab-a" else "Maple Studio",
        "Job title": "Engineer", "Start date": "2021-01-01", "End date": "2022-01-01"}
    if task["scenario"] == "missing":
        facts.pop("End date")
    return {"target_record": record, "kind": "school_selection" if school else "record_fields", "facts": facts,
            "instruction": "Prepare only this record using these fictional facts. Keep all other fields unchanged. Do not submit."}


def extract_form(observation):
    forms = []
    for block in observation.get("content", []):
        if block.get("type") != "text":
            continue
        try:
            data = json.loads(block["text"])
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("form_state"), dict):
            forms.append(data)
    return forms[0] if len(forms) == 1 else {}


def observed_plan(form, goal):
    """Match task-owned visible labels, not fixture selectors or oracle answers."""
    selected = []
    for label, value in goal["facts"].items():
        matches = [f for f in form.get("fields", []) if f.get("application_question", {}).get("text", "").strip() == label
                   and f.get("record_path") and f["record_path"][-1].get("label") == goal["target_record"]]
        if len(matches) != 1:
            return None
        selected.append((matches[0], value))
    if not selected:
        return None
    record_key = selected[0][0].get("semantic", {}).get("record", {}).get("key")
    if not record_key:
        return None
    if goal["kind"] == "school_selection":
        return {"kind": goal["kind"], "record_key": record_key, "field_key": selected[0][0]["field_key"], "value": selected[0][1]}
    return {"kind": goal["kind"], "record_key": record_key, "steps": [
        {"operation": "select_control" if f["control"] == "select" else "fill_control", "field_key": f["field_key"], "value": value}
        for f, value in selected]}


class BrowserRPC:
    """Line protocol to an isolated laboratory process, bounded on Windows too."""
    def __init__(self, root: Path):
        self.messages = queue.Queue()
        self.process = subprocess.Popen(["node", str(root / "scripts/harness-evaluation-browser.mjs")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", cwd=root, start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        def reader():
            try:
                while True:
                    line = self.process.stdout.readline(4_000_001)
                    if not line or len(line) > 4_000_000:
                        break
                    self.messages.put(json.loads(line))
            except (OSError, ValueError):
                pass
            finally:
                self.messages.put(None)
        self.reader = threading.Thread(target=reader, daemon=True)
        self.reader.start()

    def call(self, method, *, timeout=30, **kwargs):
        if timeout <= 0:
            raise TimeoutError("trial deadline reached")
        key = str(uuid4())
        self.process.stdin.write(json.dumps({"id": key, "method": method, **kwargs}, allow_nan=False) + "\n")
        self.process.stdin.flush()
        try:
            response = self.messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("laboratory deadline") from exc
        if not response or response.get("id") != key or response.get("error"):
            raise RuntimeError("laboratory unavailable or response mismatch")
        return response["result"]

    def close(self):
        try:
            if self.process.poll() is None:
                self.call("close", timeout=3)
                self.process.wait(timeout=3)
        except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired):
            if self.process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                                   capture_output=True, timeout=10, check=False)
                else:
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        finally:
            for stream in (self.process.stdin, self.process.stdout):
                if stream:
                    stream.close()


def parse_action(raw):
    if (not isinstance(raw, dict) or set(raw) != {"operation", "arguments"}
            or not isinstance(raw["arguments"], dict)):
        raise ValueError("invalid decision shape")
    operation, args = raw["operation"], raw["arguments"]
    if operation == "finish":
        if set(args) != {"status"} or args["status"] not in {"ready", "needs_fact", "blocked"}:
            raise ValueError("invalid finish")
    elif operation not in OPERATIONS:
        raise ValueError("tool outside shared safety boundary")
    return raw


def model_decision(payload, timeout, *, runtime_identity=None):
    """One isolated existing-client call; no hidden retry or model fallback."""
    root = verify_runtime_identity(runtime_identity)
    # A fresh -m lookup can select a different editable install. The parent has
    # already checked this root; pin it explicitly, then recheck in the worker.
    bootstrap = ("import runpy, sys; sys.path.insert(0, sys.argv[1]); "
                 "sys.argv = ['applypilot.apply.harness_evaluation', '--model-worker']; "
                 "runpy.run_module('applypilot.apply.harness_evaluation', run_name='__main__')")
    try:
        result = subprocess.run([sys.executable, "-I", "-B", "-c", bootstrap, str(root / "src")],
                                input=json.dumps({**payload, "runtime_identity": runtime_identity}),
                                text=True, encoding="utf-8", capture_output=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("model deadline") from exc
    if result.returncode:
        raise RuntimeError("model driver unavailable")
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise TypeError("invalid model envelope")
    return data


def model_worker():
    payload = json.load(sys.stdin)
    verify_runtime_identity(payload.get("runtime_identity"))
    from applypilot import llm
    # Check the loaded client as well as its pre-import resolution, before any
    # provider configuration/credentials are read or a client is constructed.
    verify_runtime_identity(payload.get("runtime_identity"))
    client = llm.get_client()
    config = payload["config"]
    if client.model != config["model"]:
        raise ValueError("configured model differs from frozen experiment")
    binding = digest({"model": client.model, "endpoint": client.base_url})
    if payload.get("probe"):
        print(json.dumps({"model": client.model, "binding": binding}))
        return
    started = time.monotonic()
    reply = client.chat_once(payload["messages"], temperature=config["temperature"],
                            max_tokens=config["max_tokens"], response_format={"type": "json_object"},
                            reasoning_effort=config.get("reasoning_effort"))
    # Parsing happens outside the provider call so malformed answers retain known usage.
    print(json.dumps({"raw": reply["content"], "usage": reply.get("usage"),
                      "model": reply["model"], "binding": binding,
                      "provider_ms": (time.monotonic() - started) * 1000}))


def _known_sum(values):
    return sum(values) if values and all(type(v) in {int, float} and math.isfinite(v) and v >= 0 for v in values) else None


def token_usage(usage):
    data = normalize_usage(usage)
    return {"input": data["input"], "output": data["output"], "cached": data["cached_input"]}


def estimate_cost(usages, pricing):
    return estimate_usage_cost(usages, pricing)


def _observation_view(observation, arm):
    if arm != "basic_skill":
        return observation
    view = copy.deepcopy(observation)
    for block in view.get("content", []):
        if block.get("type") != "text":
            continue
        try:
            data = json.loads(block["text"])
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and "form_state" in data:
            data["form_state"].pop("semantic_coverage", None)
            for field in data["form_state"].get("fields", []):
                field.pop("semantic", None)
            block["text"] = json.dumps(data)
    return view


def run_trial(task, arm, cohort, config, memory, *, environment, decide=model_decision,
              driver_mode="live_model", max_turns=16, deadline_seconds=90, pricing=None, train_store=None, human=None):
    """Execute actions selected from fresh observations; score only at termination."""
    if arm not in ARMS or cohort not in {"cold", "warm"} or driver_mode not in {"live_model", "reference_driver"}:
        raise ValueError("invalid experimental arm/cohort/driver")
    if task["split"] != "train" and train_store is not None:
        raise ValueError("held-out trials cannot write memory")
    start = time.monotonic()
    deadline = start + deadline_seconds
    goal = task_goal(task)
    run_id = str(uuid4())
    measurement = TrialMeasurement(run_id)
    environment = measurement.wrap_environment(environment)
    decide = measurement.wrap_decide(decide)
    terminal, reason, oracle = None, "turn_budget", None
    observations = None
    initial_hash = None
    browser_version = None
    turns = []
    usages = []
    unknown = False
    cleanup_failed = False
    interventions = 0
    intervention_ms = 0
    intervention_ms_status = "not_requested"
    assisted = False
    candidates = []
    memory_before = memory.digest
    messages = [{"role": "system", "content": (BASIC if arm == "basic_skill" else HARNESS) +
                 " Return exactly JSON {operation,arguments}. Finish with status ready, needs_fact or blocked. " +
                 json.dumps({"safety": SAFETY, "tool_contract": TOOL_CONTRACT})}]
    try:
        initialized = environment.call("init", task={k: task[k] for k in ("scenario", "family", "employer")}, timeout=min(30, deadline_seconds))
        initial_hash = initialized.get("initial_state_digest")
        browser_version = initialized.get("browser_version")
        if not isinstance(initial_hash, str) or len(initial_hash) != 64:
            raise ValueError("independent initial-state digest unavailable")
        observations = environment.call("observe", timeout=max(0.01, deadline - time.monotonic()))
        for turn in range(max_turns):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = "trial_deadline"
                break
            block = extract_form(observations)
            form = block.get("form_state", {})
            plan = observed_plan(form, goal)
            hints = memory.lookup(form, plan) if arm == "harness_plus_procedure" and cohort == "warm" and plan else ()
            payload = {"goal": goal, "observation": _observation_view(observations, arm), "procedure_advice": hints}
            encoded_at = time.monotonic()
            try:
                messages.append({"role": "user", "content": json.dumps(payload, ensure_ascii=False)})
                context_bytes = len(json.dumps(messages, ensure_ascii=False).encode())
            finally:
                encode_ms = (time.monotonic() - encoded_at) * 1000
                measurement.record_encoding(encode_ms, turn=turn)
            if context_bytes > 1_000_000:
                reason = "context_budget"
                break
            started = time.monotonic()
            record = {"turn": turn, "context_bytes": context_bytes, "context_encode_ms": encode_ms,
                      "advice_hits": len(hints), "decision_wall_ms": None, "browser_wall_ms": None}
            turns.append(record)
            try:
                reply = decide({"config": config, "messages": messages}, remaining)
            finally:
                record["decision_wall_ms"] = (time.monotonic() - started) * 1000
            usages.append(reply.get("usage"))
            provider_ms = reply.get("provider_ms")
            record["provider_ms"] = provider_ms if type(provider_ms) in {int, float} and math.isfinite(provider_ms) and provider_ms >= 0 else None
            if driver_mode == "live_model" and (reply.get("model") != config["model"] or reply.get("binding") != config["binding"]):
                reason = "configuration_drift"
                break
            action = parse_action(json.loads(reply["raw"]))
            messages.append({"role": "assistant", "content": json.dumps(action)})
            record["operation"] = action["operation"]
            if time.monotonic() >= deadline:
                reason = "trial_deadline"
                break  # Never perform late input, even if a driver ignored timeout.
            if action["operation"] == "finish":
                terminal = action["arguments"]["status"]
                reason = "agent_finished"
                if terminal == "needs_fact":
                    interventions += 1
                    if human is None or interventions > 2:
                        intervention_ms = None
                        intervention_ms_status = "not_observed"
                        break
                    previous_ms = intervention_ms
                    # Until the callback returns, its complete wait is unknown.
                    # An exception must not leave a fabricated zero or partial total.
                    intervention_ms = None
                    intervention_ms_status = "unknown"
                    human_start = time.monotonic()
                    supplied = human(copy.deepcopy(goal))
                    intervention_ms = previous_ms + (time.monotonic() - human_start) * 1000
                    intervention_ms_status = "measured"
                    if (not isinstance(supplied, dict) or not supplied or len(supplied) > 4
                            or any(k not in {"Company", "Job title", "Start date", "End date", "School", "University"}
                                   or not isinstance(v, str) or not v.strip() or len(v) > 400 for k, v in supplied.items())):
                        reason = "invalid_human_facts"
                        break
                    goal["facts"].update(supplied)
                    assisted = True
                    terminal = None
                    continue
                break
            started = time.monotonic()
            if action["operation"] == "observe":
                observations = environment.call("observe", timeout=max(0.01, deadline - started))
            else:
                before = form
                observations = environment.call("act", operation=action["operation"], arguments=action["arguments"],
                    observation_id=observations.get("observation_id"), timeout=max(0.01, deadline - started))
                record["outcome"] = observations.get("outcome", "completed")
                if observations.get("outcome") == "outcome_unknown":
                    unknown = True
                    reason = "unknown_effect_no_replay"
                    record["browser_wall_ms"] = (time.monotonic() - started) * 1000
                    break
                if action["operation"] == "prepare_subgoal":
                    report = extract_form(observations).get("subgoal_result")
                    if report:
                        candidates.append((before, action["arguments"], report))
            record["browser_wall_ms"] = (time.monotonic() - started) * 1000
        try:
            oracle = environment.call("oracle", timeout=10)
        except (RuntimeError, TimeoutError, OSError):
            oracle = None
    except Exception as exc:  # noqa: BLE001 - failed trials remain in the denominator; no error text persists
        reason = "driver_or_environment_" + type(exc).__name__
        # A decision failure must not discard already applied browser effects.
        try:
            oracle = environment.call("oracle", timeout=10)
        except (RuntimeError, TimeoutError, OSError):
            oracle = None
    finally:
        try:
            environment.close()
        except Exception:  # noqa: BLE001 - cleanup failure must not delete a trial from the report
            cleanup_failed = True
    if memory.digest != memory_before:
        raise RuntimeError("frozen memory changed during evaluation")
    elapsed_ms = (time.monotonic() - start) * 1000
    complete = oracle.get("complete") is True if oracle else False
    correct_handoff = bool(oracle and oracle.get("expected_handoff") and terminal == oracle["expected_handoff"]
                           and oracle.get("substantive_errors") == 0 and not unknown)
    if terminal == "blocked":
        # A blocked status alone cannot distinguish a human handoff from an
        # environment/tool stop. Use independent task evidence, not correctness:
        # a harmful/incorrect handoff still imposes a request on the user.
        expected_handoff = oracle.get("expected_handoff", "unknown") if oracle else "unknown"
        if expected_handoff in ("blocked", "needs_fact"):
            interventions += 1
            intervention_ms = None
            intervention_ms_status = "not_observed"
        elif expected_handoff is not None:
            interventions = None
            intervention_ms = None
            intervention_ms_status = "unknown"
    autonomous = bool(task["completable"] and terminal == "ready" and complete and not unknown and not assisted)
    false_ready = (not complete) if oracle and terminal == "ready" else None if terminal == "ready" else False
    if train_store and oracle and not unknown:
        for before, plan, report in candidates:
            symptom_verified = report.get("reason") == "dependencies_changed" and oracle.get("recovery_triggered")
            duplicate_verified = report.get("reason") == "ambiguous_target_option" and oracle.get("expected_handoff") == "blocked" and not oracle.get("collateral_errors")
            checked = complete if report.get("status") == "verified" else bool(symptom_verified or duplicate_verified)
            try:
                train_store.record_outcome(before, plan, report, run_ref=run_id, verify=lambda checked=checked: checked)
            except (TypeError, ValueError):
                pass  # Unsupported/ambiguous evidence is not a learnable procedure.
    result = {"schema_version": "interactive-preparation-evaluation/v2", "run_id": run_id,
            "task_id": task["task_id"], "split": task["split"], "employer": task["employer"], "family": task["family"],
            "arm": arm, "cohort": cohort, "driver_mode": driver_mode, "config_digest": digest(config),
            "safety_digest": digest(SAFETY), "tools_digest": digest(TOOL_CONTRACT), "initial_state_digest": initial_hash,
            "prompt_digest": digest({"system": messages[0], "goal": task_goal(task)}),
            "memory_digest": memory_before, "browser_version": browser_version, "terminal": terminal, "termination_reason": reason, "cleanup_failed": cleanup_failed,
            "completable": task["completable"], "autonomous_complete": autonomous, "assisted_complete": bool(assisted and complete and terminal == "ready" and not unknown),
            "correct_handoff": correct_handoff, "false_ready": false_ready, "oracle_available": oracle is not None,
            "substantive_errors": oracle.get("substantive_errors") if oracle else None,
            "recovery_triggered": oracle.get("recovery_triggered") if oracle else None,
            "recovered": bool(oracle and oracle.get("recovery_triggered") and autonomous),
            "user_intervention_requests": interventions, "user_intervention_ms": intervention_ms,
            "user_intervention_requests_status": "unknown" if interventions is None else "observed" if interventions else "not_requested",
            "user_intervention_ms_status": intervention_ms_status,
            "elapsed_ms": elapsed_ms, "estimated_cost_usd": estimate_cost(usages, pricing) if len(usages) == len(turns) else None,
            "cost_basis": "priced_successful_responses_only; any_missing_response_makes_total_unknown", "usage_responses": len(usages), "decision_attempts": len(turns),
            "input_tokens": _known_sum([token_usage(u)["input"] for u in usages]) if len(usages) == len(turns) else None,
            "output_tokens": _known_sum([token_usage(u)["output"] for u in usages]) if len(usages) == len(turns) else None,
            "reads": oracle.get("reads") if oracle else None, "writes": oracle.get("writes") if oracle else None,
            "decision_wall_ms": _known_sum([t["decision_wall_ms"] for t in turns]),
            "context_encode_ms": sum(t["context_encode_ms"] for t in turns),
            "advice_hits": sum(t["advice_hits"] for t in turns),
            "coordination_wait_ms": None, "page_loading_ms": None,
            "timing_scope": "wall_spans_include_nested_work; do_not_add_to_elapsed",
            "turns": turns, "whole_application_evaluated": False}
    result["measurement"] = measurement.report(result, usages, pricing)
    if driver_mode == "reference_driver":
        result["estimated_cost_usd"] = None
        result["cost_basis"] = "reference_driver_fixture_not_provider_billing_evidence"
    return result


def summarize(results):
    """Keep unknown metrics unknown, keep failed tasks, and do not pool driver modes."""
    grouped = defaultdict(list)
    for result in results:
        grouped[(result["driver_mode"], result["split"], result["arm"], result["cohort"], result.get("schema_version", "unknown"))].append(result)
    output = []
    for group, rows in sorted(grouped.items()):
        eligible = [r for r in rows if r["completable"]]
        recovery = [r for r in rows if r["recovery_triggered"] is True]
        handoff = [r for r in rows if not r["completable"]]
        output.append({"driver_mode": group[0], "split": group[1], "arm": group[2], "cohort": group[3], "schema_version": group[4], "trials": len(rows),
            "completion_denominator": len(eligible), "autonomous_complete": sum(r["autonomous_complete"] for r in eligible),
            "assisted_complete": sum(r["assisted_complete"] for r in rows),
            "autonomous_rate": sum(r["autonomous_complete"] for r in eligible) / len(eligible) if eligible else None,
            "false_ready": sum(r["false_ready"] is True for r in rows), "unknown_false_ready": sum(r["false_ready"] is None for r in rows),
            "recovery_denominator": len(recovery), "recovered": sum(r["recovered"] for r in recovery),
            "handoff_denominator": len(handoff), "correct_handoff": sum(r["correct_handoff"] for r in handoff),
            "total_elapsed_ms": sum(r["elapsed_ms"] for r in rows),
            "total_cost_usd": _known_sum([r["estimated_cost_usd"] for r in rows]),
            "unknown_cost_trials": sum(r["estimated_cost_usd"] is None for r in rows),
            "total_user_intervention_requests": _known_sum([r["user_intervention_requests"] for r in rows]),
            "unknown_intervention_request_trials": sum(r["user_intervention_requests"] is None for r in rows),
            "total_user_intervention_ms": _known_sum([r["user_intervention_ms"] for r in rows]),
            "user_intervention_ms_status_counts": {
                status: sum(r.get("user_intervention_ms_status", "unknown") == status for r in rows)
                for status in ("not_requested", "measured", "not_observed", "unknown")},
            "unknown_oracle_trials": sum(not r["oracle_available"] for r in rows)})
    return output



def paired_comparisons(results):
    """Complete matched triples only, with same settings/tools/initial state.

    Bootstrap over task clusters rather than pretending repeated trials of one
    fixture are independent employers. Results remain limited to this small lab.
    """
    groups = defaultdict(list)
    for row in results:
        if row.get("pair_id"):
            groups[(row["driver_mode"], row["split"], row["cohort"])].append(row)
    schemas = {row.get("schema_version") for rows in groups.values() for row in rows}
    if len(schemas) > 1:
        raise ValueError("mixed evaluation schemas (schema_version)")
    comparisons = []
    for group, rows in sorted(groups.items()):
        pairs = defaultdict(dict)
        for row in rows:
            pair = pairs[row["pair_id"]]
            if row["arm"] in pair:
                raise ValueError("duplicate arm in paired trial")
            pair[row["arm"]] = row
        for pair in pairs.values():
            if set(pair) != set(ARMS):
                raise ValueError("incomplete paired cohort; do not drop missing trials")
            for key in ("task_id", "schema_version", "config_digest", "safety_digest", "tools_digest", "initial_state_digest", "memory_digest", "browser_version"):
                values = [row.get(key) for row in pair.values()]
                if None in values or len(set(values)) != 1:
                    raise ValueError("non-equivalent paired " + key)
            if any(row["termination_reason"] == "configuration_drift" for row in pair.values()):
                raise ValueError("model configuration drift invalidates comparison")
        for left, right in pairwise(ARMS):
            clusters = defaultdict(list)
            for pair in pairs.values():
                a, b = pair[left], pair[right]
                if a["completable"]:
                    clusters[a["task_id"]].append(int(b["autonomous_complete"]) - int(a["autonomous_complete"]))
            cluster_means = [sum(v) / len(v) for v in clusters.values()]
            rng = random.Random(17)
            samples = sorted(sum(rng.choice(cluster_means) for _ in cluster_means) / len(cluster_means)
                             for _ in range(1000)) if cluster_means else []
            comparisons.append({"driver_mode": group[0], "split": group[1], "cohort": group[2],
                "evaluation_schema": rows[0]["schema_version"],
                "baseline": left, "candidate": right, "paired_trials": len(pairs), "task_clusters": len(clusters),
                "autonomous_rate_delta": sum(cluster_means) / len(cluster_means) if cluster_means else None,
                "task_bootstrap_95_interval": [samples[24], samples[974]] if len(cluster_means) > 1 else None,
                "false_ready_delta": sum(int(p[right]["false_ready"]) - int(p[left]["false_ready"]) for p in pairs.values())
                    if all(p[a]["false_ready"] is not None for p in pairs.values() for a in (left, right)) else None,
                "mean_elapsed_delta_ms": sum(p[right]["elapsed_ms"] - p[left]["elapsed_ms"] for p in pairs.values()) / len(pairs),
                "scope": "small_controlled_task_set_not_population_generalization"})
    return comparisons


def schedule(task_list, *, repeats=2, seed=17):
    if type(repeats) is not int or not 1 <= repeats <= 10:
        raise ValueError("repeats must be between one and ten")
    result = []
    rng = random.Random(seed)
    for repeat in range(repeats):
        for task in task_list:
            cells = [(arm, cohort) for arm in ARMS for cohort in ("cold", "warm")]
            rng.shuffle(cells)
            pair_id = digest({"task": task, "repeat": repeat, "seed": seed})
            result.extend((task, arm, cohort, pair_id, repeat) for arm, cohort in cells)
    return result


PYTHON_SOURCES = {
    "applypilot": "src/applypilot/__init__.py",
    "applypilot.apply": "src/applypilot/apply/__init__.py",
    "applypilot.apply.harness_evaluation": "src/applypilot/apply/harness_evaluation.py",
    "applypilot.apply.harness_measurement": "src/applypilot/apply/harness_measurement.py",
    "applypilot.apply.harness_experiment_plan": "src/applypilot/apply/harness_experiment_plan.py",
    "applypilot.apply.procedure_experience": "src/applypilot/apply/procedure_experience.py",
    "applypilot.apply.recipe_experience": "src/applypilot/apply/recipe_experience.py",
    "applypilot.llm": "src/applypilot/llm.py",
    "applypilot.apply.application_consistency": "src/applypilot/apply/application_consistency.py",
    "applypilot.apply.visual_bridge_mcp": "src/applypilot/apply/visual_bridge_mcp.py",
}


def source_manifest(root):
    """Hash public fixture sources; execution entry points must also bind paths."""
    paths = ["scripts/harness-evaluation-browser.mjs", "scripts/browser-form-state.mjs", "scripts/browser-form-semantics.mjs",
             "scripts/browser-field-batch.mjs", "scripts/browser-preparation-subgoals.mjs", "scripts/visual-bridge-host.mjs",
             "scripts/attended-host-journal.mjs", "scripts/browser-observation-feedback.mjs",
             "tests/fixtures/apply/semantic-preparation-lab.html", *PYTHON_SOURCES.values()]
    return {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths}


def bound_source_manifest(root, *, entry_module="applypilot.apply.harness_evaluation", entry_file=__file__):
    """Require E's source checkout, including already-loaded and lazy modules.

    Resolving specs does not import llm or load provider/profile configuration.
    The explicit entry file also covers modules executed as __main__ by -m.
    """
    root = Path(root).resolve()
    for name, filename in (("applypilot.apply.harness_evaluation", __file__), (entry_module, entry_file)):
        if name not in PYTHON_SOURCES or Path(filename).resolve() != (root / PYTHON_SOURCES[name]).resolve():
            raise ValueError("source root/runtime mismatch: " + name)
    for name, relative in PYTHON_SOURCES.items():
        expected = (root / relative).resolve()
        module = sys.modules.get(name)
        spec = importlib.util.find_spec(name)
        paths = [getattr(spec, "origin", None)]
        if module is not None:
            paths.append(getattr(module, "__file__", None))
        if any(not path or Path(path).resolve() != expected for path in paths):
            raise ValueError("source root/runtime mismatch: " + name)
    return source_manifest(root)


def verify_runtime_identity(identity):
    """Fail closed before any model probe/call if the frozen source changed."""
    if (not isinstance(identity, dict) or set(identity) != {"schema_version", "source_root", "source_digest"}
            or identity.get("schema_version") != "harness-source-runtime/v1"
            or not isinstance(identity.get("source_root"), str)
            or not Path(identity["source_root"]).is_absolute()):
        raise ValueError("bound source runtime identity required")
    root = Path(identity["source_root"]).resolve()
    if digest(bound_source_manifest(root)) != identity["source_digest"]:
        raise ValueError("source runtime digest changed")
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--max-decisions", type=int, default=256)
    parser.add_argument("--max-turns", type=int, default=16)
    parser.add_argument("--trial-seconds", type=int, default=90)
    parser.add_argument("--train", action="store_true", help="Run two training trials per task before freezing memory")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    parser.add_argument("--cached-usd-per-million", type=float)
    parser.add_argument("--run-model", action="store_true", help="Explicitly execute billable model calls; otherwise preflight only")
    args = parser.parse_args()
    if args.model_worker:
        model_worker()
        return
    if not args.root or not args.output or not args.model:
        parser.error("--root, --output and explicit --model are required")
    if not 1 <= args.max_decisions <= 2048 or not 1 <= args.max_turns <= 32 or not 5 <= args.trial_seconds <= 300 or not 128 <= args.max_tokens <= 8192:
        parser.error("invalid bounded trial budget")
    pricing = None
    if args.input_usd_per_million is not None or args.output_usd_per_million is not None:
        pricing = {"input": args.input_usd_per_million, "output": args.output_usd_per_million,
                   "cached": args.cached_usd_per_million}
        estimate_cost([{"prompt_tokens": 1, "completion_tokens": 1}], pricing)
    if not math.isfinite(args.temperature) or not 0 <= args.temperature <= 2:
        parser.error("invalid temperature")
    root = args.root.resolve()
    try:
        source = bound_source_manifest(root)
    except (ValueError, OSError, ImportError) as exc:
        parser.error(str(exc))
    runtime_identity = {"schema_version": "harness-source-runtime/v1", "source_root": str(root),
                        "source_digest": digest(source)}
    training, holdout = tasks("train"), tasks("holdout")
    validate_splits(training, holdout)
    plan = schedule(holdout, repeats=args.repeats, seed=args.seed)
    args.output.mkdir(parents=True, exist_ok=False)
    config = {"model": args.model, "temperature": args.temperature, "max_tokens": args.max_tokens,
              "reasoning_effort": args.reasoning_effort}
    manifest = {"source_hashes": source, "source_digest": digest(source), "runtime_identity": runtime_identity,
                "config": config, "safety": SAFETY,
                "safety_digest": digest(SAFETY), "tools_digest": digest(TOOL_CONTRACT), "seed": args.seed,
                "planned_trials": len(plan), "maximum_model_decisions": args.max_decisions, "training": training, "holdout": holdout,
                "max_turns": args.max_turns, "trial_seconds": args.trial_seconds, "driver_mode": "live_model", "pricing": pricing,
                "status": "not_run", "model_calls": 0,
                "evaluation_scope": "browser_preparation; shared guards retained; C/D orchestration not ablated"}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if not args.run_model:
        print(json.dumps({"status": "not_run", "model_calls": 0, "planned_trials": len(plan), "maximum_model_decisions": args.max_decisions, "reason": "explicit_run_model_required"}))
        return
    try:
        probe = model_decision({"config": config, "probe": True}, 15, runtime_identity=runtime_identity)
    except (RuntimeError, TimeoutError, TypeError, ValueError):
        manifest.update(status="not_run", reason="configured_model_driver_unavailable")
        (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(json.dumps({"status": "not_run", "reason": "configured_model_driver_unavailable", "model_calls": 0}))
        raise SystemExit(2) from None
    config["binding"] = probe["binding"]
    manifest["config"] = config
    manifest["status"] = "running"
    manifest.pop("model_calls")  # Only the not_run state establishes zero calls.
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    memory = FrozenProcedureMemory(())
    store = ProcedureExperienceStore(args.output / "training-procedures.db")
    rows = []
    decisions_used = 0
    def bounded_decide(payload, timeout):
        nonlocal decisions_used
        if decisions_used >= args.max_decisions:
            raise RuntimeError("experiment decision budget exhausted")
        decisions_used += 1
        return model_decision(payload, timeout, runtime_identity=runtime_identity)
    with (args.output / "trials.jsonl").open("x", encoding="utf-8") as stream:
        def record(row):
            stream.write(json.dumps(row, allow_nan=False) + "\n")
            stream.flush()
            rows.append(row)
        if args.train:
            for repeat in range(2):
                for task in training:
                    if decisions_used >= args.max_decisions:
                        break
                    row = run_trial(task, "current_harness", "cold", config, memory,
                        environment=BrowserRPC(root), train_store=store, decide=bounded_decide,
                        max_turns=args.max_turns, deadline_seconds=args.trial_seconds, pricing=pricing)
                    record({**row, "repeat": repeat, "pair_id": None})
            memory = store.freeze()
        (args.output / "frozen-procedures.json").write_text(json.dumps({"digest": memory.digest,
            "templates": [t.as_dict() for t in memory.templates],
            "training_employers": ["lab-a"] if args.train else [],
            "training_families": ["fieldset"] if args.train else []}, indent=2), encoding="utf-8")
        for task, arm, cohort, pair_id, repeat in plan:
            if decisions_used >= args.max_decisions:
                break
            row = run_trial(task, arm, cohort, config, memory, environment=BrowserRPC(root), decide=bounded_decide,
                            max_turns=args.max_turns, deadline_seconds=args.trial_seconds, pricing=pricing)
            record({**row, "pair_id": pair_id, "repeat": repeat})
    (args.output / "frozen-procedures.json").write_text(json.dumps({"digest": memory.digest,
        "templates": [t.as_dict() for t in memory.templates], "training_employers": ["lab-a"] if args.train else [],
        "training_families": ["fieldset"] if args.train else []}, indent=2), encoding="utf-8")
    complete_cohort = len([r for r in rows if r.get("pair_id")]) == len(plan)
    manifest.update(config=config, memory_digest=memory.digest, frozen_template_count=len(memory.templates),
                    model_decisions=decisions_used, status="completed" if complete_cohort else "incomplete_budget_exhausted")
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (args.output / "summary.json").write_text(json.dumps(summarize(rows), indent=2), encoding="utf-8")
    (args.output / "measurements.json").write_text(json.dumps(aggregate_measurements(rows), indent=2), encoding="utf-8")
    try:
        if not complete_cohort:
            raise ValueError("incomplete planned cohort")
        comparisons = paired_comparisons(rows)
        from applypilot.apply.harness_experiment_plan import paired_cold_warm
        cold_warm = paired_cold_warm(rows)
    except ValueError:
        comparisons = {"status": "invalid_comparison", "reason": "incomplete_or_non_equivalent_cohort"}
        cold_warm = comparisons
    (args.output / "comparisons.json").write_text(json.dumps(comparisons, indent=2), encoding="utf-8")
    (args.output / "cold-warm-comparisons.json").write_text(json.dumps(cold_warm, indent=2), encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "trials": len(rows), "frozen_template_count": len(memory.templates)}))


if __name__ == "__main__":
    main()
