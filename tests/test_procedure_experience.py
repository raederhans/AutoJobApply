import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from applypilot.apply.procedure_experience import (
    ADAPTER, NEGATIVE, POLICY, FrozenProcedureMemory, ProcedureExperienceStore,
    ProcedureTemplate, context_template,
)
from applypilot.apply.recipe_experience import RecipeExperienceStore, RecipeExperienceTemplate, RoutineControlTemplate


def observed():
    form = {"coverage": {"scope": "visible_top_document_open_shadow", "iframe_count": 0},
            "question_coverage": {"fields_truncated": False}, "fields": [{
                "field_key": "#school", "control": "select", "value": "PRIVATE_PERSONAL_VALUE",
                "value_source": "dom_read", "record_path": [{"label": "Education", "dom_identity": 1}],
                "semantic": {"identification": "identified", "record": {"status": "identified", "key": "#education"}}}]}
    plan = {"kind": "school_selection", "record_key": "#education", "field_key": "#school", "value": "PRIVATE_PERSONAL_VALUE"}
    report = {"kind": plan["kind"], "record_key": plan["record_key"], "status": "verified", "actions": 1,
              "grants_submission_authority": False, "whole_application_verified": False}
    return form, plan, report


def train(store, run="run-1", verified=True):
    return store.record_outcome(*observed(), run_ref=run, verify=lambda: verified)


def test_candidate_requires_two_independent_runs_and_freeze_is_read_only(tmp_path):
    store = ProcedureExperienceStore(tmp_path / "memory.db")
    assert train(store).state == "candidate"
    assert train(store).validation_count == 1
    assert not store.freeze().templates
    assert train(store, "run-2").state == "validated"
    frozen = store.freeze()
    before = (tmp_path / "memory.db").read_bytes()
    form, plan, _ = observed()
    assert frozen.lookup(form, plan)[0]["grants_authority"] is False
    form["fields"][0]["value"] = "ANOTHER_APPLICANT"
    plan["value"] = "ANOTHER_APPLICANT"
    assert frozen.lookup(form, plan)
    assert (tmp_path / "memory.db").read_bytes() == before
    assert b"PRIVATE_PERSONAL_VALUE" not in before
    assert b"#school" not in before
    assert b"run-1" not in before


def test_failures_are_candidates_not_causal_reflections(tmp_path):
    store = ProcedureExperienceStore(tmp_path / "memory.db")
    form, plan, report = observed()
    report.update(status="parked", reason="ambiguous_target_option")
    assert store.record_outcome(form, plan, report, run_ref="one", verify=lambda: True).state == "candidate"
    assert store.record_outcome(form, plan, report, run_ref="two", verify=lambda: True).state == "validated"
    assert store.freeze().lookup(form, plan)[0]["lesson"] == "ambiguous_target_option"
    report["reason"] = "A company secretly blocked the user"
    assert store.record_outcome(form, plan, report, run_ref="three", verify=lambda: True) is None


def test_false_or_failed_independent_check_invalidates_and_cannot_be_revived(tmp_path):
    store = ProcedureExperienceStore(tmp_path / "memory.db")
    train(store)
    train(store, "two")
    assert train(store, "bad", False).state == "invalidated"
    assert train(store, "later").state == "invalidated"
    assert not store.freeze().templates


@pytest.mark.parametrize("flag", ["tainted", "submit_started", "effect_unknown", "missing_fact"])
def test_blocked_context_cannot_reuse_memory(tmp_path, flag):
    store = ProcedureExperienceStore(tmp_path / "m.db")
    train(store)
    train(store, "two")
    form, plan, _ = observed()
    assert not store.freeze().lookup(form, plan, **{flag: True})


@pytest.mark.parametrize("mutation", [
    lambda f, p: f["fields"][0]["semantic"].update(identification="ambiguous"),
    lambda f, p: f["fields"][0]["record_path"][0].update(dom_identity=None),
    lambda f, p: f["fields"][0]["semantic"]["record"].update(key="#other"),
    lambda f, p: f["coverage"].update(iframe_count=1),
    lambda f, p: f["coverage"].clear(),
    lambda f, p: f["question_coverage"].update(fields_truncated=True),
    lambda f, p: f["fields"][0].update(value_source="unavailable"),
    lambda f, p: f["fields"][0].update(disabled=True),
    lambda f, p: f["fields"][0].update(multiple=True),
    lambda f, p: f["fields"].append(copy.deepcopy(f["fields"][0])),
    lambda f, p: p.update(field_key="#missing"),
])
def test_current_observation_must_be_valid_not_just_cache_hit(tmp_path, mutation):
    store = ProcedureExperienceStore(tmp_path / "m.db")
    train(store)
    train(store, "two")
    form, plan, _ = observed()
    mutation(form, plan)
    assert not store.freeze().lookup(form, plan)


@pytest.mark.parametrize("change", [
    {"lesson": "fill password"}, {"kind": "submit"}, {"controls": ("password",)},
    {"negative_conditions": ()}, {"adapter_version": "other"}, {"policy_version": "other"},
    {"schema_version": "untrusted"}, {"record_kind": "unknown"}, {"controls": tuple("text" for _ in range(5))},
])
def test_closed_vocabulary_cannot_store_page_instructions_or_applicant_values(change):
    data = {"kind": "school_selection", "record_kind": "education", "controls": ("select",), "lesson": "school_selection"}
    with pytest.raises(ValueError):
        ProcedureTemplate(**(data | change))


def test_version_mismatch_rejected(tmp_path):
    store = ProcedureExperienceStore(tmp_path / "m.db")
    train(store)
    train(store, "two")
    form, plan, _ = observed()
    assert not store.freeze().lookup(form, plan, adapter_version=ADAPTER + "x")
    assert not store.freeze().lookup(form, plan, policy_version=POLICY + "x")


def test_structure_alone_cannot_promote_and_saturated_ledger_cannot_forge_second_check(tmp_path):
    store = ProcedureExperienceStore(tmp_path / "m.db")
    template = context_template(*observed()[:2])
    with pytest.raises(ValueError):
        store.record_validation(template, event_id="one", evidence="host_structure")
    for index in range(64):
        store.observe(template, event_id=f"observation-{index}")
    for index in range(3):
        assert store.record_validation(template, event_id=f"check-{index}", evidence="host_postcondition").state == "candidate"
    assert store.invalidate(template, event_id="invalid").state == "invalidated"


def test_original_routine_store_remains_compatible_and_mixed_database_is_safe(tmp_path):
    path = tmp_path / "m.db"
    original = RecipeExperienceStore(path)
    template = RecipeExperienceTemplate("greenhouse", "greenhouse-semantic-recipe/v1", "routine-provider-recipe/v1",
                                        (RoutineControlTemplate("city", "text", True, True, 0),))
    assert original.observe(template, event_id="o").state == "candidate"
    assert original.record_validation(template, event_id="v", evidence="host_structure").state == "validated"
    assert original.lookup(template, adapter_version=template.adapter_version, policy_version=template.policy_version,
                           validate_fresh=lambda _: True) == template
    newer = ProcedureExperienceStore(path)
    train(newer)
    train(newer, "two")
    assert len(newer.freeze().templates) == 1
    assert original.get(template).state == "validated"


def test_corruption_is_not_a_validated_hint(tmp_path):
    path = tmp_path / "m.db"
    store = ProcedureExperienceStore(path)
    train(store)
    train(store, "two")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE recipe_experiences SET validation_count=1")
    with pytest.raises(ValueError):
        store.freeze()


def test_concurrent_duplicate_validations_are_not_two_independent_runs(tmp_path):
    store = ProcedureExperienceStore(tmp_path / "m.db")
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: train(store), range(3)))
    template = context_template(*observed()[:2])
    assert store.get(template).validation_count == 1
    assert store.get(template).state == "candidate"


def test_frozen_snapshot_is_bounded_and_has_no_mutator():
    with pytest.raises(ValueError):
        FrozenProcedureMemory(tuple(["unsafe"]))
    memory = FrozenProcedureMemory(())
    assert not hasattr(memory, "record_validation")
    assert len(memory.digest) == 64
    assert NEGATIVE == ("ambiguous_record", "unobserved_scope", "unknown_effect", "submission_started", "missing_fact")


def test_invalidated_procedure_cannot_be_evicted_and_revived(tmp_path):
    store = ProcedureExperienceStore(tmp_path / "m.db", capacity=1)
    old = context_template(*observed()[:2])
    store.invalidate(old, event_id="bad")
    replacement = ProcedureTemplate("school_selection", "education", ("combobox",), "school_selection")
    with pytest.raises(ValueError, match="capacity"):
        store.observe(replacement, event_id="new")
    assert store.get(old).state == "invalidated"
    assert store.get(replacement) is None
    assert train(store, "later").state == "invalidated"


def test_record_outcome_requires_original_bounded_execution_metadata(tmp_path):
    form, plan, report = observed()
    store = ProcedureExperienceStore(tmp_path / "m.db")
    report.pop("actions")
    with pytest.raises(ValueError):
        store.record_outcome(form, plan, report, run_ref="run", verify=lambda: True)
    assert not (tmp_path / "m.db").exists()


def test_contextual_advice_is_value_free_and_off_switch_preserves_response(tmp_path, monkeypatch):
    from applypilot.apply.procedure_experience import advice_for_form, enrich_bridge_response
    store = ProcedureExperienceStore(tmp_path / "m.db")
    train(store)
    train(store, "two")
    form, _, _ = observed()
    form["fields"][0]["application_question"] = {"text": "School"}
    advice = advice_for_form(store.freeze(), form)
    assert advice and "PRIVATE_PERSONAL_VALUE" not in json.dumps(advice)
    monkeypatch.delenv("APPLYPILOT_PROCEDURE_MEMORY_DB", raising=False)
    response = {"ok": True, "outcome": "completed", "content": []}
    assert enrich_bridge_response(response, tmp_path) is response


def test_mcp_completed_response_gets_advice_without_authority_or_extra_input(tmp_path, monkeypatch):
    from applypilot.apply import visual_bridge_mcp as mcp
    from applypilot.apply.visual_bridge import write_host_metadata
    store = ProcedureExperienceStore(tmp_path / "m.db")
    train(store)
    train(store, "two")
    bridge = tmp_path / "bridge"
    write_host_metadata(bridge, session_id="session", token_epoch="epoch", surface="browser",
                        target={"runtime": "iab", "tab_id": "tab", "application_url": "https://ats.example.test/application"})
    form, _, _ = observed()
    form["fields"][0]["application_question"] = {"text": "School"}
    response = {"ok": True, "outcome": "completed", "session_id": "session", "token_epoch": "epoch",
                "content": [{"type": "text", "text": json.dumps({"form_state": form})}]}
    monkeypatch.setenv("APPLYPILOT_PROCEDURE_MEMORY_DB", str(tmp_path / "m.db"))
    monkeypatch.setenv(mcp.BRIDGE_DIR_ENV, str(bridge))
    calls = []
    def execute(*args, **kwargs):
        calls.append(1)
        return response
    monkeypatch.setattr(mcp, "request_visual_operation", execute)
    reply = mcp._handle({"id": 1, "method": "tools/call", "params": {"name": "visual_operation",
                        "arguments": {"operation": "observe", "arguments": {}}}})
    assert len(calls) == 1
    assert reply["result"]["isError"] is False
    last = json.loads(reply["result"]["content"][-1]["text"])
    assert last["procedure_advice"] and last["authority"] == "none"
    assert last["learning_from_this_response"] is False
    assert "PRIVATE_PERSONAL_VALUE" not in json.dumps(last)
    response.update(ok=False, outcome="outcome_unknown")
    reply = mcp._handle({"id": 2, "method": "tools/call", "params": {"name": "visual_operation",
                        "arguments": {"operation": "observe", "arguments": {}}}})
    assert reply["result"]["isError"] is True and len(reply["result"]["content"]) == 1


def test_mcp_wrong_epoch_or_corrupt_memory_does_not_recast_completed_input(tmp_path, monkeypatch):
    from applypilot.apply.procedure_experience import enrich_bridge_response
    from applypilot.apply.visual_bridge import write_host_metadata
    store = ProcedureExperienceStore(tmp_path / "m.db")
    train(store)
    train(store, "two")
    bridge = tmp_path / "bridge"
    write_host_metadata(bridge, session_id="s", token_epoch="e", surface="browser",
                        target={"runtime": "iab", "tab_id": "t", "application_url": "https://ats.example.test/a"})
    form, _, _ = observed()
    response = {"ok": True, "outcome": "completed", "session_id": "s", "token_epoch": "wrong",
                "content": [{"type": "text", "text": json.dumps({"form_state": form})}]}
    monkeypatch.setenv("APPLYPILOT_PROCEDURE_MEMORY_DB", str(tmp_path / "m.db"))
    assert enrich_bridge_response(response, bridge) is response
    response["token_epoch"] = "e"
    (tmp_path / "m.db").write_bytes(b"invalid sqlite")
    assert enrich_bridge_response(response, bridge) is response
