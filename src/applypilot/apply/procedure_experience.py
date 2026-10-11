"""Bounded, value-free preparation advice on the existing recipe evidence store.

Trusted hosts register independently checked outcomes. Page/model text cannot
supply rules, answers, selectors or authorization. Frozen lookups never learn.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass

from applypilot.apply.application_consistency import _record_kind
from applypilot.apply.recipe_experience import RecipeExperienceStore, _digest

SCHEMA = "preparation-procedure/v1"
ADAPTER = "observed-controls/v1"
POLICY = "bounded-preparation/v1"
KINDS = frozenset({"school_selection", "record_fields"})
CONTROLS = frozenset({"text", "textarea", "email", "tel", "url", "number", "date", "month", "select", "combobox"})
# Text is maintained code, never learned page text. Advice cannot dispatch input.
ADVICE = {
    "school_selection": "Use the observed school record and exact current option. A query is not a committed selection. Consider the bounded school_selection subgoal; verify its fresh result.",
    "record_fields": "Use one identity-bound existing record and current authoritative facts. Retain verified values; consider record_fields for up to four fields. Recheck affected dependencies.",
    "ambiguous_target_option": "Do not choose an arbitrary duplicate option. Obtain disambiguating evidence or request review.",
    "business_postcondition_unproven": "A completed input or matching query is not commitment proof. Inspect actual selected state; do not replay the prior operation automatically.",
    "dependencies_changed": "Discard the old action plan after dependency drift. Preserve historical progress and reobserve before planning new input.",
}
NEGATIVE = ("ambiguous_record", "unobserved_scope", "unknown_effect", "submission_started", "missing_fact")


@dataclass(frozen=True, slots=True)
class ProcedureTemplate:
    kind: str
    record_kind: str
    controls: tuple[str, ...]
    lesson: str
    negative_conditions: tuple[str, ...] = NEGATIVE
    adapter_version: str = ADAPTER
    policy_version: str = POLICY
    schema_version: str = SCHEMA

    def __post_init__(self):
        if self.kind not in KINDS or self.record_kind not in {"education", "employment", "project"}:
            raise ValueError("unsupported procedure scope")
        if (not isinstance(self.controls, tuple) or not 1 <= len(self.controls) <= 4
                or any(c not in CONTROLS for c in self.controls)):
            raise ValueError("unsupported procedure controls")
        if self.kind == "school_selection" and (len(self.controls) != 1 or self.controls[0] not in {"select", "combobox"}):
            raise ValueError("school advice requires one selector")
        if self.lesson not in ADVICE or (self.lesson in KINDS and self.lesson != self.kind):
            raise ValueError("unrecognized procedure or failure lesson")
        if (self.negative_conditions != NEGATIVE or self.adapter_version != ADAPTER
                or self.policy_version != POLICY or self.schema_version != SCHEMA):
            raise ValueError("procedure policy or schema mismatch")

    def as_dict(self):
        # Only closed vocabularies and bounded control kinds. No hashes of answers.
        return asdict(self)

    @property
    def content_key(self):
        return _digest(self.as_dict())


def _parse(raw: str) -> ProcedureTemplate:
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != set(ProcedureTemplate.__dataclass_fields__):
        raise ValueError("invalid procedure template")
    if not isinstance(data["controls"], list) or not isinstance(data["negative_conditions"], list):
        raise TypeError("invalid procedure lists")
    return ProcedureTemplate(**{**data, "controls": tuple(data["controls"]),
                                "negative_conditions": tuple(data["negative_conditions"])})


def context_template(form: Mapping, plan: Mapping, lesson: str | None = None) -> ProcedureTemplate:
    """Project one fresh host observation to public structure, not candidate data."""
    if not isinstance(form, Mapping) or not isinstance(plan, Mapping) or plan.get("kind") not in KINDS:
        raise ValueError("current form and supported preparation plan required")
    coverage = form.get("coverage", {})
    if (coverage.get("scope") != "visible_top_document_open_shadow" or type(coverage.get("iframe_count")) is not int
            or coverage["iframe_count"] != 0 or form.get("question_coverage", {}).get("fields_truncated") is not False):
        raise ValueError("observation scope is incomplete")
    keys = [plan.get("field_key")] if plan["kind"] == "school_selection" else [s.get("field_key") for s in plan.get("steps", [])]
    if not 1 <= len(keys) <= 4 or len(set(keys)) != len(keys):
        raise ValueError("bounded distinct fields required")
    fields = form.get("fields", [])
    selected = []
    for key in keys:
        matches = [f for f in fields if isinstance(f, Mapping) and f.get("field_key") == key]
        if len(matches) != 1:
            raise ValueError("field is not uniquely observed")
        f = matches[0]
        semantic = f.get("semantic", {})
        record = semantic.get("record", {})
        if (semantic.get("identification") != "identified" or record.get("status") != "identified"
                or not plan.get("record_key") or record.get("key") != plan["record_key"]
                or f.get("disabled") or f.get("readonly") or f.get("multiple")
                or f.get("value_source") == "unavailable" or not f.get("record_path")
                or any(r.get("dom_identity") is None for r in f["record_path"])):
            raise ValueError("fresh identified record is required")
        selected.append(f)
    kinds = {_record_kind(f) for f in selected}
    if len(kinds) != 1:
        raise ValueError("conflicting record purposes")
    return ProcedureTemplate(plan["kind"], kinds.pop(), tuple(f["control"] for f in selected), lesson or plan["kind"])


class ProcedureExperienceStore(RecipeExperienceStore):
    """Same SQLite lifecycle; two distinct host checks before procedural reuse."""
    _template_type = ProcedureTemplate
    _minimum_validations = 2

    def _decode_template(self, raw):
        return _parse(raw)

    def record_validation(self, template, *, event_id, evidence):
        if evidence != "host_postcondition":
            raise ValueError("procedure promotion requires independent postcondition evidence")
        return super().record_validation(template, event_id=event_id, evidence=evidence)

    def record_outcome(self, form, plan, report, *, run_ref: str, verify: Callable[[], bool]):
        """Called by the trusted host/oracle, not a worker-supplied verification flag.

        Failure lessons describe observed symptoms, not speculative causal stories.
        A failed independent check invalidates an existing lesson; it never promotes.
        """
        if not isinstance(report, Mapping) or not callable(verify):
            raise TypeError("host result and independent verifier required")
        if (not isinstance(run_ref, str) or not run_ref.strip() or len(run_ref) > 400
                or type(report.get("actions")) is not int or not 0 <= report["actions"] <= 8):
            raise ValueError("bounded execution identity and action evidence required")
        if (report.get("kind") != plan.get("kind") or report.get("record_key") != plan.get("record_key")
                or report.get("grants_submission_authority") is not False
                or report.get("whole_application_verified") is not False):
            raise ValueError("unbound or authorizing report")
        status, reason = report.get("status"), report.get("reason")
        lesson = plan.get("kind") if status == "verified" else reason
        if lesson not in ADVICE or (status != "verified" and status != "parked"):
            return None  # Waiting, missing facts and unknown effects are not reusable skills.
        template = context_template(form, plan, lesson)
        self.observe(template, event_id="observe:" + run_ref)
        try:
            checked = verify() is True
        except Exception:  # noqa: BLE001 - independent verifier failure is not evidence
            checked = False
        if not checked:
            return self.invalidate(template, event_id="reject:" + run_ref)
        return self.record_validation(template, event_id="check:" + run_ref, evidence="host_postcondition")

    def _prune(self, connection):
        # Do not resurrect an invalidated procedure merely by evicting its tombstone.
        count, invalidated = connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(state='invalidated'),0) FROM recipe_experiences"
        ).fetchone()
        if count > self._capacity and invalidated >= self._capacity:
            raise ValueError("procedure capacity reserved by invalidations; use a new policy version")
        if count > self._capacity:
            connection.execute(
                "DELETE FROM recipe_experiences WHERE content_key IN ("
                "SELECT content_key FROM recipe_experiences WHERE state!='invalidated' "
                "ORDER BY touched_ns,content_key LIMIT ?)", (count - self._capacity,)
            )

    def freeze(self):
        """Copy validated advice into immutable process memory using one DB snapshot."""
        if not self._path.is_file():
            return FrozenProcedureMemory(())
        with self._connect_read_only() as connection:
            rows = connection.execute(
                "SELECT content_key,template_json,validation_count FROM recipe_experiences "
                "WHERE state='validated' ORDER BY content_key"
            ).fetchall()
        templates = []
        for key, raw, count in rows:
            # Mixed v1 routine rows remain owned by their original decoder.
            data = json.loads(raw)
            if data.get("schema_version") != SCHEMA:
                continue
            item = _parse(raw)
            if item.content_key != key or type(count) is not int or count < 2:
                raise ValueError("invalid validated procedure evidence")
            templates.append(item)
        if len(templates) > self._capacity:
            raise ValueError("procedure snapshot exceeds capacity")
        return FrozenProcedureMemory(tuple(templates))


@dataclass(frozen=True, slots=True)
class FrozenProcedureMemory:
    templates: tuple[ProcedureTemplate, ...]

    def __post_init__(self):
        if (not isinstance(self.templates, tuple) or len(self.templates) > 128
                or any(not isinstance(t, ProcedureTemplate) for t in self.templates)
                or len({t.content_key for t in self.templates}) != len(self.templates)):
            raise ValueError("invalid frozen procedure snapshot")

    @property
    def digest(self):
        return _digest([t.as_dict() for t in self.templates])

    def lookup(self, form, plan, *, adapter_version=ADAPTER, policy_version=POLICY,
               tainted=False, submit_started=False, effect_unknown=False, missing_fact=False):
        if (tainted is not False or submit_started is not False or effect_unknown is not False
                or missing_fact is not False or adapter_version != ADAPTER or policy_version != POLICY):
            return ()
        try:
            current = context_template(form, plan)
        except (TypeError, ValueError, KeyError):
            return ()
        found = []
        for item in self.templates:
            if (item.kind, item.record_kind, item.controls) == (current.kind, current.record_kind, current.controls):
                found.append({"ref": item.content_key, "lesson": item.lesson, "advice": ADVICE[item.lesson],
                              "scope": "advisory_only", "grants_authority": False})
        return tuple(found)


def advice_for_form(memory: FrozenProcedureMemory, form: Mapping):
    """Bounded contextual suggestions for the current Worker observation, without answers."""
    fields = form.get("fields", [])
    if not isinstance(fields, list) or len(fields) > 512:
        return ()
    groups = {}
    for field in fields:
        key = (field.get("semantic", {}).get("record") or {}).get("key")
        if key:
            groups.setdefault(key, []).append(field)
    suggestions = []
    for key, record in list(groups.items())[:16]:
        plans = []
        for field in record:
            question = field.get("application_question", {}).get("text", "").strip().casefold()
            if field.get("control") in {"select", "combobox"} and question in {"school", "university", "college", "institution", "学校", "大学", "院校"}:
                plans.append({"kind": "school_selection", "record_key": key, "field_key": field["field_key"]})
        if 1 <= len(record) <= 4:
            plans.append({"kind": "record_fields", "record_key": key,
                          "steps": [{"field_key": f["field_key"]} for f in record]})
        for plan in plans:
            for advice in memory.lookup(form, plan):
                suggestions.append({**advice, "record_key": key, "kind": plan["kind"]})
                if len(suggestions) >= 16:
                    return tuple(suggestions)
    return tuple(suggestions)


def enrich_bridge_response(response: dict, bridge_root):
    """Opt-in read-only advice in the EXISTING worker MCP; never change an outcome.

    The attending process supplies an absolute APPLYPILOT_PROCEDURE_MEMORY_DB.
    Tool/page arguments cannot enable learning, choose a DB or grant trust.
    """
    import os
    import sqlite3
    from pathlib import Path

    configured = os.environ.get("APPLYPILOT_PROCEDURE_MEMORY_DB", "")
    if not configured or response.get("ok") is not True or response.get("outcome") != "completed":
        return response
    from applypilot.apply.visual_bridge import VisualBridgeError, read_active_host

    try:
        path = Path(configured)
        if not path.is_absolute() or not path.is_file() or path.stat().st_size > 8_000_000:
            return response
        host = read_active_host(bridge_root)
        if (host.phase != "prepare" or host.target.get("runtime") != "iab"
                or response.get("session_id") != host.session_id or response.get("token_epoch") != host.token_epoch):
            return response
        blocks = []
        for block in response.get("content", []):
            if block.get("type") != "text" or len(block.get("text", "")) > 4_000_000:
                continue
            try:
                payload = json.loads(block["text"])
            except ValueError:
                continue
            if isinstance(payload, dict) and isinstance(payload.get("form_state"), dict):
                blocks.append(payload)
        if len(blocks) != 1 or (blocks[0].get("subgoal_result") or {}).get("status") == "needs_fact":
            return response
        memory = ProcedureExperienceStore(path).freeze()
        advice = advice_for_form(memory, blocks[0]["form_state"])
        if not advice:
            return response
        return {**response, "content": [*response["content"], {"type": "text", "text": json.dumps({
            "procedure_advice": advice, "memory_digest": memory.digest,
            "authority": "none", "learning_from_this_response": False})}]}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error, VisualBridgeError):
        # Losing optional advice cannot recast a completed input as an error/retry.
        return response
