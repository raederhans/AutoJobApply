"""Advisory, value-free E measurements; inclusive work is never additive wall time.

This module has no model client, credentials, browser launcher or execution authority.
Private attempt/turn correlation stays in trial records, outside aggregate reports.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Mapping

SCHEMA = "harness-measurement/v1"
SOURCES = {
    "decision": "E.decide_including_worker_startup_transport_and_parse",
    "model_client": "E.provider_ms_client_chat_once_including_network_and_provider_wait",
    "model_service": "provider_server_inference_boundary_unavailable",
    "encoding": "E.context_encode_ms_json_serialization_only",
    "coordination_wait": "explicit_coordinator_boundary_required",
    "execution_slot_wait": "explicit_attempt_bound_group_slot_boundary_required",
    "browser": "E.environment_observe_or_act_rpc_including_readback_and_transport",
    "page_load": "explicit_page_load_boundary_required",
    "recovery": "explicit_recovery_boundary_required",
    "environment_setup": "E.environment_init_including_browser_and_fixture_setup",
    "oracle": "E.independent_terminal_check_rpc",
    "cleanup": "E.environment_close_including_process_cleanup",
}
EXTERNAL_COMPONENTS = frozenset({"coordination_wait", "execution_slot_wait", "page_load", "recovery"})


def _number(value):
    try:
        return value if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _integer(value):
    return value if type(value) is int and _number(value) is not None else None


def metric(values, *, unit, source, not_occurred=False):
    """A partial observed subtotal is not a complete total. Empty means unknown."""
    values = list(values)
    if not values and not_occurred:
        return {"status": "not_occurred", "value": 0, "observed_subtotal": 0,
                "observed_samples": 0, "unknown_samples": 0, "unit": unit, "source": source}
    normalized = [_number(value) for value in values] or [None]
    known = [value for value in normalized if value is not None]
    missing = len(normalized) - len(known)
    subtotal = _number(sum(known)) if known else None
    return {"status": "measured" if not missing and subtotal is not None else "partial" if known else "unknown",
            "value": subtotal if not missing else None, "observed_subtotal": subtotal,
            "observed_samples": len(known), "unknown_samples": missing,
            "unit": unit, "source": source}


def _alias(data, *names):
    values = [data[name] for name in names if name in data]
    return values[0] if values and all(value == values[0] for value in values) else None


def normalize_usage(usage):
    """OpenAI-compatible totals and subsets only; other provider schemas stay unknown."""
    data = usage if isinstance(usage, Mapping) else {}
    incoming = _integer(_alias(data, "prompt_tokens", "input_tokens"))
    outgoing = _integer(_alias(data, "completion_tokens", "output_tokens"))
    prompt = _alias(data, "prompt_tokens_details", "input_tokens_details")
    completion = _alias(data, "completion_tokens_details", "output_tokens_details")
    prompt = prompt if isinstance(prompt, Mapping) else {}
    completion = completion if isinstance(completion, Mapping) else {}
    cached = _integer(prompt.get("cached_tokens"))
    reasoning = _integer(completion.get("reasoning_tokens"))
    cache_write = _integer(prompt.get("cache_write_tokens"))
    if incoming is None or (cached is not None and cached > incoming):
        cached = None
    if outgoing is None or (reasoning is not None and reasoning > outgoing):
        reasoning = None
    if incoming is None or (cache_write is not None and cache_write > incoming):
        cache_write = None
    if cached is not None and cache_write is not None and cached + cache_write > incoming:
        cached = cache_write = None
    total = incoming + outgoing if incoming is not None and outgoing is not None else None
    if "total_tokens" in data and (_integer(data["total_tokens"]) is None or data["total_tokens"] != total):
        incoming = outgoing = cached = reasoning = cache_write = total = None
    return {"input": incoming, "output": outgoing, "cached_input": cached,
            "reasoning_output": reasoning, "cache_write_input": cache_write, "total": total}


def validate_pricing(pricing):
    if not isinstance(pricing, Mapping) or any(_number(pricing.get(k)) is None for k in ("input", "output")):
        raise ValueError("explicit nonnegative input/output pricing required")
    if pricing.get("cached") is not None and _number(pricing["cached"]) is None:
        raise ValueError("invalid cached pricing")


def estimate_usage_cost(usages, pricing):
    """USD estimate, never an invoice; unsupported billing categories fail unknown."""
    if not pricing or not usages:
        return None
    validate_pricing(pricing)
    costs = []
    for raw in usages:
        data = normalize_usage(raw)
        if any(data[k] is None for k in ("input", "output", "cached_input")):
            return None
        # A new usage category needs a billing contract before these text rates
        # can cover it. Preserve known token totals, but do not guess its price.
        if set(raw) - {"prompt_tokens", "input_tokens", "completion_tokens", "output_tokens", "total_tokens",
                       "prompt_tokens_details", "input_tokens_details", "completion_tokens_details",
                       "output_tokens_details"}:
            return None
        for key, allowed in (
                ("prompt_tokens_details", {"cached_tokens", "cache_write_tokens", "audio_tokens"}),
                ("input_tokens_details", {"cached_tokens", "cache_write_tokens", "audio_tokens"}),
                ("completion_tokens_details", {"reasoning_tokens", "audio_tokens"}),
                ("output_tokens_details", {"reasoning_tokens", "audio_tokens"})):
            details = raw.get(key)
            if details is not None and (not isinstance(details, Mapping) or set(details) - allowed):
                return None
        prompt = _alias(raw, "prompt_tokens_details", "input_tokens_details")
        # Older usage omitted cache-write fields. Explicit writes need another
        # billing contract/rate; never silently charge them as ordinary input.
        if isinstance(prompt, Mapping) and "cache_write_tokens" in prompt and data["cache_write_input"] != 0:
            return None
        completion = _alias(raw, "completion_tokens_details", "output_tokens_details")
        for details in (prompt, completion):
            if isinstance(details, Mapping) and "audio_tokens" in details and _integer(details["audio_tokens"]) != 0:
                return None
        if data["cached_input"] and pricing.get("cached") is None:
            return None
        costs.append(((data["input"] - data["cached_input"]) * pricing["input"]
                      + data["cached_input"] * (pricing.get("cached") or 0)
                      + data["output"] * pricing["output"]) / 1_000_000)
    return _number(sum(costs))


class TrialMeasurement:
    """Bounded per-attempt spans; failures retain time, never error text or arguments."""

    def __init__(self, attempt_id, *, clock=time.perf_counter_ns):
        self.attempt_id, self.clock = attempt_id, clock
        self.turn = None
        self.events = []
        self.dropped = 0
        self._external_ids = set()

    def _stamp(self):
        try:
            return _number(self.clock())
        except Exception:  # noqa: BLE001 - unavailable telemetry cannot change behavior
            return None

    def _append(self, component, duration, source, *, turn=None, clock="python_perf_counter_ns"):
        if len(self.events) >= 256:
            self.dropped += 1
            return
        self.events.append({"turn": self.turn if turn is None else turn, "component": component,
                            "duration_ms": _number(duration), "source": source, "clock": clock,
                            "relationship": "inclusive_may_overlap"})

    def _call(self, component, function, *args, **kwargs):
        started = self._stamp()
        try:
            return function(*args, **kwargs)
        finally:
            ended = self._stamp()
            elapsed = (ended - started) / 1_000_000 if started is not None and ended is not None else None
            self._append(component, elapsed, SOURCES[component])

    def wrap_decide(self, decide):
        def measured(*args, **kwargs):
            self.turn = 0 if self.turn is None else self.turn + 1
            return self._call("decision", decide, *args, **kwargs)
        return measured

    def wrap_environment(self, environment):
        trace = self

        class MeasuredEnvironment:
            def call(self, method, **kwargs):
                component = {"init": "environment_setup", "oracle": "oracle"}.get(method, "browser")
                return trace._call(component, environment.call, method, **kwargs)

            def close(self):
                return trace._call("cleanup", environment.close)

        return MeasuredEnvironment()

    def record_encoding(self, duration_ms, *, turn):
        self._append("encoding", duration_ms, SOURCES["encoding"], turn=turn, clock="E_monotonic_duration")

    def record_external(self, component, duration_ms, *, attempt_id, turn, boundary_id):
        """Trusted boundary-owner seam. Aggregate windows cannot be imported here.

        IDs establish association only, not shared clocks or exclusive durations.
        Callers must not pass provider/page payloads; source metadata is allowlisted.
        """
        if (component not in EXTERNAL_COMPONENTS or attempt_id != self.attempt_id or _integer(turn) is None
                or not isinstance(boundary_id, str) or not 1 <= len(boundary_id) <= 128):
            raise ValueError("invalid attempt/turn timing binding")
        key = (component, turn, boundary_id)
        if key in self._external_ids:
            raise ValueError("duplicate timing boundary")
        if len(self._external_ids) >= 256:
            raise ValueError("timing boundary limit")
        self._external_ids.add(key)
        self._append(component, duration_ms, "explicit_attempt_turn_boundary", turn=turn,
                     clock="boundary_owner_local_duration;alignment_unknown")

    def report(self, row, usages, pricing=None):
        if row["run_id"] != self.attempt_id:
            raise ValueError("measurement attempt differs from trial")
        turns = row["turns"]
        live = row["driver_mode"] == "live_model"
        timing = {}
        for component, source in SOURCES.items():
            values = [e["duration_ms"] for e in self.events if e["component"] == component]
            absent = component in {"decision", "browser", "oracle"} and not values and not self.dropped
            if component == "model_client":
                values = [t.get("provider_ms") for t in turns] if live else []
                absent = not live or not turns
            elif component == "model_service":
                absent = not live or not turns
            if self.dropped:
                values.append(None)
            timing[component] = metric(values, unit="ms", source=source, not_occurred=absent)
        usage_source = "provider_response_usage" if live else "reference_driver_fixture_usage_not_provider_evidence"
        normalized = [normalize_usage(u) for u in usages]
        if len(usages) != len(turns):
            normalized.extend(normalize_usage(None) for _ in range(max(1, len(turns) - len(usages))))
        usage = {key: metric([u[key] for u in normalized], unit="tokens", source=usage_source,
                             not_occurred=not turns and not usages)
                 for key in normalize_usage(None)}
        costs = [estimate_usage_cost([u], pricing) for u in usages] if live else []
        if live and len(usages) != len(turns):
            costs.extend(None for _ in range(max(1, len(turns) - len(usages))))
        usage_by_turn = [{"turn": t["turn"], "source": usage_source,
                          "tokens": normalized[index] if index < len(normalized) else normalize_usage(None),
                          "estimated_usd": costs[index] if index < len(costs) else None}
                         for index, t in enumerate(turns)]
        return {"schema_version": SCHEMA, "driver_mode": row["driver_mode"],
                "timing": timing, "usage": usage,
                "cost": {"estimated_usd": metric(costs, unit="USD", source="explicit_rates_times_supported_usage"),
                         "reported_usd": metric([], unit="USD", source="per_attempt_provider_bill_unavailable"),
                         "pricing_provenance": "caller_supplied_rates_not_provider_invoice" if pricing else "unknown"},
                "accounting": {"additive_wall_time": False, "sum_scope": "inclusive_call_work_per_component",
                               "parallel_work": "may_overlap_or_exceed_elapsed", "exclusive_time": "unknown",
                               "critical_path": "unknown", "residual_attribution": "not_inferred",
                               "token_subsets": "cached/cache_write_in_input;reasoning_in_output;do_not_add_again"},
                "private_trace": {"attempt_id": self.attempt_id, "events": [dict(e) for e in self.events],
                                  "usage_by_turn": usage_by_turn,
                                  "clock": "source_local_durations_not_a_shared_timeline", "dropped_events": self.dropped}}


def legacy_metric_views(*, bridge=None, group=None, performance=None):
    """Reuse existing metrics without inventing request IDs, totals or clock joins."""
    views = {}
    if isinstance(bridge, Mapping):
        recent = bridge.get("recent", {})
        views["bridge"] = {"scope": "recent_bounded_host_window_not_attempt_joinable", "additive_wall_time": False,
                           "metrics": {}}
        for key in ("queue_wait_ms", "action_ms", "observation_ms", "host_service_ms"):
            sample = recent.get(key, {}) if isinstance(recent, Mapping) else {}
            sample = sample if isinstance(sample, Mapping) else {}
            views["bridge"]["metrics"][key] = {name: _number(sample.get(name)) for name in ("p50", "p95", "max")}
            views["bridge"]["metrics"][key].update(
                measured=_integer(sample.get("measured")), unknown=_integer(sample.get("unavailable")))
    if isinstance(group, Mapping):
        spans = group.get("spans", {})
        views["group"] = {"scope": "group_lifetime_inclusive_parallel_work_not_attempt_joinable",
                          "additive_wall_time": False, "metrics": {}}
        for key in ("group_slot_wait", "host_execute"):
            sample = spans.get(key, {}) if isinstance(spans, Mapping) else {}
            sample = sample if isinstance(sample, Mapping) else {}
            count, missing = _integer(sample.get("samples")), _integer(sample.get("unavailable"))
            known = _number(sample.get("total_ms")) if count is not None and count > 0 else None
            views["group"]["metrics"][key] = {
                "value_ms": known if missing == 0 else None, "observed_subtotal_ms": known,
                "observed_samples": count, "unknown_samples": missing,
                "status": "measured" if known is not None and missing == 0 else "partial" if known is not None else "unknown"}
    if performance is not None:
        from applypilot.apply.performance_attribution import normalize_attribution
        normalized = normalize_attribution(performance)
        views["performance"] = {"scope": "legacy_nested_advisory_spans_not_turn_joinable", "additive_wall_time": False,
                                "spans": [{k: s[k] for k in ("name", "group", "duration_ms")}
                                          for s in normalized["spans"]] if normalized else None}
    return views


def _aggregate_metric(reports, *, unit, source):
    summary = metric([r.get("value") for r in reports], unit=unit, source=source,
                     not_occurred=not reports)
    subtotals = [_number(r.get("observed_subtotal")) for r in reports]
    known = [value for value in subtotals if value is not None]
    summary["observed_subtotal"] = _number(sum(known)) if known else None
    summary["observed_samples"] = sum(_integer(r.get("observed_samples")) or 0 for r in reports)
    summary["unknown_samples"] = sum(r["unknown_samples"] if _integer(r.get("unknown_samples")) is not None
                                     else 1 for r in reports)
    statuses = [r.get("status", "unknown") for r in reports]
    summary["trial_status_counts"] = {s: statuses.count(s) for s in ("measured", "partial", "unknown", "not_occurred")}
    if summary["value"] is None and known:
        summary["status"] = "partial"
    elif statuses and all(s == "not_occurred" for s in statuses):
        summary["status"] = "not_occurred"
    return summary


def aggregate_measurements(rows):
    """Public aggregates omit attempt IDs, event IDs, task identities and payloads."""
    groups = defaultdict(list)
    for row in rows:
        groups[(row["schema_version"], row["driver_mode"], row["split"], row["arm"], row["cohort"])].append(row)
    result = []
    for key, group in sorted(groups.items()):
        timings = {name: _aggregate_metric([r.get("measurement", {}).get("timing", {}).get(name, {}) for r in group],
                                          unit="ms", source=source) for name, source in SOURCES.items()}
        usage = {name: _aggregate_metric([r.get("measurement", {}).get("usage", {}).get(name, {}) for r in group],
                                         unit="tokens", source="provider_usage" if key[1] == "live_model" else "reference_fixture")
                 for name in normalize_usage(None)}
        cost = _aggregate_metric([r.get("measurement", {}).get("cost", {}).get("estimated_usd", {}) for r in group],
                                 unit="USD", source="explicit_rate_estimate_not_invoice")
        result.append({"evaluation_schema": key[0], "driver_mode": key[1], "split": key[2], "arm": key[3],
                       "cohort": key[4], "trials": len(group), "timing": timings, "usage": usage,
                       "estimated_usd": cost, "additive_wall_time": False})
    return result
