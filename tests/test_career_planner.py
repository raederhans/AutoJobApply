"""Route behavior is tested with finite synthetic tasks, never personal grades."""

from copy import deepcopy

import pytest

from applypilot.career.diagnosis import build_diagnosis
from applypilot.career.planner import build_route
from applypilot.career.resources import resources_for


def catalog(**dependencies):
    return {"schema_version": "fixture", "skills": [
        {"id": sid, "name_zh": sid, "prerequisites": deps, "anchors": [
            {"id": f"{sid}.{stage}", "stage": stage, "task": f"Finite {sid} {stage} task",
             "context": f"Local {sid} fixture", "deliverable": f"{sid} artifact",
             "acceptance": [f"{sid} valid result", f"{sid} handles errors"],
             "support": "Templates and documented assistance allowed", "limits": "Only this environment"}
            for stage in ("guided", "routine")]}
        for sid, deps in dependencies.items()]}


def row(rid="r1", sid="programming", status="needs_verification", strength="required", **fields):
    return {"id": rid, "skills": [sid], "status": status, "strength": strength,
            "quote": f"A finite {sid} requirement", **fields}


def diagnosis(*rows):
    return {"diagnosis_id": "diagnosis:a", "target_id": "target:a", "requirements": list(rows)}


def by_id(route):
    return {t["task_id"]: t for t in route["tasks"]}


def assert_dag(route):
    positions = {tid: i for i, tid in enumerate(route["topological_order"])}
    assert len(positions) == len(route["tasks"])
    assert list(positions) == [task["task_id"] for task in route["tasks"]]
    for task in route["tasks"]:
        for dep in task["depends_on"]:
            assert positions[dep] < positions[task["task_id"]]


def test_transitive_dependencies_dedup_and_exact_anchor_contract():
    rubrics = catalog(programming=[], testing=["programming"], web=["testing", "programming"])
    route = build_route(diagnosis(row(sid="web"), row("r2", "web")), rubrics)
    tasks = by_id(route)
    assert set(tasks) == {"anchor:web.guided", "anchor:testing.guided", "anchor:programming.guided"}
    assert all(t["requirement_ids"] == ["r1", "r2"] for t in tasks.values())
    anchor = rubrics["skills"][2]["anchors"][0]
    task = tasks["anchor:web.guided"]
    assert task["title"] == anchor["task"]
    assert task["context"] == anchor["context"]
    assert task["deliverable"] == anchor["deliverable"]
    assert task["acceptance"] == anchor["acceptance"]
    assert task["assistance_policy"] == anchor["support"]
    assert task["limits"] == [anchor["limits"]]
    assert_dag(route)


def test_unknown_budget_is_order_only_deterministic_and_does_not_mutate_inputs():
    rubrics = catalog(programming=[])
    original = diagnosis(row())
    before = deepcopy((original, rubrics))
    a = build_route(original, rubrics)
    assert a == build_route(original, rubrics)
    assert (original, rubrics) == before
    assert a["budget"]["total_hours"] is None
    assert a["budget"]["reserved_max_hours"] is None
    assert all(not t["scheduled"] and t["deferred_reason"] == "budget_unknown_order_only" for t in a["tasks"])
    assert "calendar" not in a
    assert a["diagnosis_id"] == "diagnosis:a" and a["target_id"] == "target:a"
    assert a["catalog_id"].startswith("catalog:")


def test_known_budget_respects_max_estimates_and_deferred_dependencies():
    rubrics = catalog(programming=[], web=["programming"], writing=[])
    route = build_route(diagnosis(row(sid="web"), row("r2", "writing", strength="preferred")),
                        rubrics, weekly_hours=2, weeks=4)
    tasks = by_id(route)
    assert tasks["anchor:programming.guided"]["scheduled"]
    assert not tasks["anchor:web.guided"]["scheduled"]
    assert tasks["anchor:web.guided"]["deferred_reason"] == "exceeds_remaining_budget"
    assert route["budget"]["reserved_max_hours"] == 8
    assert all(t["estimate_hours"]["max"] == 8 for t in tasks.values())
    zero = build_route(diagnosis(row(sid="web")), rubrics, weekly_hours=0)
    assert by_id(zero)["anchor:web.guided"]["deferred_reason"] == "dependency_deferred"
    assert_dag(route)


def test_large_task_deferred_without_shrinking_allows_independent_small_task():
    rubrics = catalog(programming=[], writing=[])
    route = build_route(diagnosis(row(), row("r2", "writing", "evidence_missing", "preferred")),
                        rubrics, weekly_hours=1, weeks=4)
    tasks = by_id(route)
    assert tasks["anchor:programming.guided"]["estimate_hours"]["max"] == 8
    assert tasks["anchor:programming.guided"]["deferred_reason"] == "exceeds_remaining_budget"
    assert tasks["evidence:writing"]["scheduled"]
    assert route["budget"]["reserved_max_hours"] == 3


@pytest.mark.parametrize("hours,weeks", [(float("nan"), 4), (float("inf"), 4), (-1, 4),
                                        (True, 4), (1, 0), (1, 1.5), (1, True)])
def test_invalid_budget_rejected(hours, weeks):
    with pytest.raises(ValueError):
        build_route(diagnosis(row()), catalog(programming=[]), weekly_hours=hours, weeks=weeks)


def test_qualification_group_is_separate_and_never_learning_prerequisite():
    route = build_route(diagnosis(row(), row("degree", status="qualification_unknown"),
                                 row("timing", status="qualification_unknown")), catalog(programming=[]))
    tasks = by_id(route)
    assert tasks["qualification"]["requirement_ids"] == ["degree", "timing"]
    assert tasks["qualification"]["skill_id"] is None
    assert tasks["qualification"]["kind"] == "qualification_review"
    assert tasks["qualification"]["priority"] == 0
    assert "qualification_feasibility_first" in tasks["qualification"]["priority_reasons"]
    assert route["topological_order"][0] == "qualification"
    assert not tasks["qualification"]["depends_on"]
    assert all("qualification" not in t["depends_on"] for t in route["tasks"])


def test_evidence_dedup_precedes_verification_including_prerequisites():
    route = build_route(diagnosis(row(status="related_evidence"),
                                 row("r2", "programming", "evidence_missing"),
                                 row("r3", "web", "transferable_evidence")),
                        catalog(programming=[], web=["programming"]))
    tasks = by_id(route)
    assert tasks["evidence:programming"]["requirement_ids"] == ["r1", "r2"]
    assert tasks["anchor:programming.guided"]["depends_on"] == ["evidence:programming"]
    assert "evidence:web" in tasks["anchor:web.guided"]["depends_on"]
    assert_dag(route)


@pytest.mark.parametrize("status", ["related_evidence", "evidence_missing"])
def test_existing_evidence_only_does_not_force_redundant_practice(status):
    route = build_route(diagnosis(row(status=status)), catalog(programming=[]))
    assert list(by_id(route)) == ["evidence:programming"]


def test_unknown_uncovered_and_unresolved_are_system_coverage_issues():
    route = build_route(diagnosis(row(sid="invented"), row("r2", status="uncovered"),
                                 row("r3", status="unresolved")), catalog(programming=[]))
    assert not route["tasks"]
    assert {e["requirement_id"] for e in route["excluded_requirements"]} == {"r1", "r2", "r3"}
    assert all(e["reason"] == "system_coverage_issue" for e in route["excluded_requirements"])


def test_actual_diagnosis_or_language_requires_one_branch_not_all():
    rubrics = catalog(programming=[], sql=[])
    d = build_diagnosis(target={"id": "target:a"}, rubrics=rubrics,
                       jobs=[{"id": "job:a", "requirements": [
                           {"id": "r1", "text": "Experience with SQL or Python", "source_section": "Required"}]}],
                       evidence={"materials": [], "evidence": []})
    route = build_route(d, rubrics)
    assert len(route["tasks"]) == 1
    choice = route["branch_choices"][0]
    assert choice["selected_children"] == [0]
    assert len(choice["alternatives"]) == 2
    assert list(by_id(route)) == ["anchor:sql.guided"]
    preferred = build_route(d, rubrics, preferred_skills=["programming"])
    assert list(by_id(preferred)) == ["anchor:programming.guided"]
    explicit = build_route(d, rubrics, selected_anchor_ids=["programming.routine"])
    assert list(by_id(explicit)) == ["anchor:programming.routine"]


def test_nested_and_or_keeps_other_required_skill_and_mixed_strength():
    tree = {"op": "all", "children": [
        {"op": "any", "children": [{"op": "atom", "skills": ["sql"]},
                                     {"op": "atom", "skills": ["programming"]}]},
        {"op": "atom", "skills": ["statistics"], "strength": "required"},
        {"op": "atom", "skills": ["writing"], "strength": "preferred"}]}
    rubrics = catalog(sql=[], programming=[], statistics=[], writing=[])
    route = build_route(diagnosis(row(sid="sql", strength="mixed", condition_tree=tree)), rubrics)
    tasks = by_id(route)
    assert set(tasks) == {"anchor:sql.guided", "anchor:statistics.guided", "anchor:writing.guided"}
    assert tasks["anchor:statistics.guided"]["priority"] < tasks["anchor:writing.guided"]["priority"]


def test_explicit_selection_restricts_roots_but_keeps_dependency_closure():
    rubrics = catalog(programming=[], web=["programming"], writing=[])
    route = build_route(diagnosis(row(sid="web"), row("r2", "writing")), rubrics,
                        selected_anchor_ids=["web.routine"])
    assert set(by_id(route)) == {"anchor:web.routine", "anchor:programming.guided"}
    assert route["excluded_requirements"][0]["reason"] == "outside_explicit_task_selection"
    assert_dag(route)
    empty = build_route(diagnosis(row(sid="web")), rubrics, selected_anchor_ids=[])
    assert not empty["tasks"]


def test_exact_pass_bypasses_only_corresponding_prerequisite_and_is_annotated():
    rubrics = catalog(programming=[], web=["programming"])
    obs = {"anchor_id": "programming.guided", "outcome": "passed", "method": "runtime_check",
           "subject": "candidate", "compatible": True, "id": "trusted-observation"}
    route = build_route(diagnosis(row(sid="web")), rubrics, prior_task_observations=[obs])
    assert list(by_id(route)) == ["anchor:web.guided"]
    bypass = route["evidence_bypasses"][0]
    assert bypass["prerequisite_anchor_id"] == "programming.guided"
    assert bypass["observations"] == [obs]
    assert "evidence_bypass:programming.guided" in route["tasks"][0]["priority_reasons"]


def test_intermediate_pass_does_not_silently_bypass_unobserved_transitive_skills():
    rubrics = catalog(programming=[], api=["programming"], web=["api"])
    obs = {"anchor_id": "api.guided", "outcome": "passed", "method": "task_review",
           "subject": "candidate", "compatible": True}
    route = build_route(diagnosis(row(sid="web")), rubrics, prior_task_observations=[obs])
    assert set(by_id(route)) == {"anchor:web.guided", "anchor:programming.guided"}
    assert by_id(route)["anchor:web.guided"]["depends_on"] == ["anchor:programming.guided"]
    assert route["evidence_bypasses"][0]["prerequisite_anchor_id"] == "api.guided"
    assert_dag(route)


def test_at_least_selects_k_branches_and_preserves_all_alternatives():
    tree = {"op": "at_least", "k": 2, "children": [
        {"op": "atom", "skills": [sid]} for sid in ["programming", "sql", "writing"]]}
    route = build_route(diagnosis(row(condition_tree=tree)), catalog(programming=[], sql=[], writing=[]),
                        preferred_skills=["writing"])
    assert set(by_id(route)) == {"anchor:programming.guided", "anchor:writing.guided"}
    assert route["branch_choices"][0]["selected_children"] == [0, 2]
    assert len(route["branch_choices"][0]["alternatives"]) == 3


def test_completed_root_anchor_not_resurrected_by_default_fallback_or_explicit_selection():
    obs = {"anchor_id": "programming.guided", "outcome": "passed", "method": "runtime_check",
           "subject": "candidate", "compatible": True}
    d = diagnosis(row(next_action={"anchor_ids": []}))
    route = build_route(d, catalog(programming=[]), prior_task_observations=[obs])
    assert not route["tasks"] and not route["topological_order"]
    assert route["excluded_requirements"][0]["reason"] == "exact_completed_target_task"
    assert route["excluded_requirements"][0]["anchor_ids"] == ["programming.guided"]
    explicit = build_route(d, catalog(programming=[]), prior_task_observations=[obs],
                           selected_anchor_ids=["programming.guided"])
    assert not explicit["tasks"]


def test_completed_root_still_retains_related_evidence_and_qualification_review():
    obs = {"anchor_id": "programming.guided", "outcome": "passed", "method": "self_task_review",
           "subject": "candidate", "compatible": True}
    route = build_route(diagnosis(row(status="transferable_evidence"),
                                 row("degree", status="qualification_unknown")),
                        catalog(programming=[]), prior_task_observations=[obs])
    assert set(by_id(route)) == {"qualification", "evidence:programming"}


@pytest.mark.parametrize("status", ["related_evidence", "evidence_missing"])
def test_reviewed_evidence_collection_advances_to_task_verification_without_skill_pass(status):
    obs = {"anchor_id": None, "skill_id": "programming", "task_id": "evidence:programming",
           "task_kind": "evidence_collection", "outcome": "passed", "method": "artifact_review",
           "subject": "artifact", "compatible": True}
    route = build_route(diagnosis(row(status=status)), catalog(programming=[]), prior_task_observations=[obs])
    assert set(by_id(route)) == {"evidence:programming", "anchor:programming.guided"}
    anchor = by_id(route)["anchor:programming.guided"]
    assert anchor["kind"] == "verification"
    assert anchor["depends_on"] == ["evidence:programming"]
    assert any(r.startswith("evidence_collection_reviewed:") for r in anchor["priority_reasons"])
    assert not route["evidence_bypasses"]


@pytest.mark.parametrize("changes", [{"compatible": False}, {"outcome": "completed"},
                                    {"task_kind": "qualification_review"}, {"task_id": "evidence:other"},
                                    {"subject": "team"}, {"method": "course_completion"}])
def test_unreviewed_or_unscoped_collection_does_not_advance_to_verification(changes):
    obs = {"anchor_id": None, "skill_id": "programming", "task_id": "evidence:programming",
           "task_kind": "evidence_collection", "outcome": "passed", "method": "artifact_review",
           "subject": "artifact", "compatible": True, **changes}
    route = build_route(diagnosis(row(status="evidence_missing")), catalog(programming=[]),
                        prior_task_observations=[obs])
    assert list(by_id(route)) == ["evidence:programming"]


@pytest.mark.parametrize("changes", [{"method": "course_completion"}, {"subject": "team"},
                                    {"compatible": False}, {"outcome": "completed"},
                                    {"anchor_id": "programming.routine"}, {"subject": None}])
def test_course_related_or_incompatible_pass_does_not_remove_prerequisite(changes):
    obs = {"anchor_id": "programming.guided", "outcome": "passed", "method": "runtime_check",
           "subject": "candidate", "compatible": True, **changes}
    route = build_route(diagnosis(row(sid="web")), catalog(programming=[], web=["programming"]),
                        prior_task_observations=[obs])
    assert "anchor:programming.guided" in by_id(route)
    assert not route["evidence_bypasses"]


def test_observed_failure_selects_exact_failed_anchor_and_preserves_acceptance():
    rubrics = catalog(programming=[])
    failure = {"anchor_id": "programming.routine", "criterion": "programming handles errors"}
    route = build_route(diagnosis(row(status="observed_task_gap", observed_task_failures=[failure],
                                     next_action={"anchor_ids": ["programming.guided"]})), rubrics)
    task = route["tasks"][0]
    assert task["task_id"] == "anchor:programming.routine" and task["kind"] == "practice"
    assert task["acceptance"] == rubrics["skills"][0]["anchors"][1]["acceptance"]
    assert task["estimate_hours"]["max"] == 20
    assert any(r.startswith("observed_failure:") for r in task["priority_reasons"])


def test_priority_monotonicity_and_topology_override_priority():
    rubrics = catalog(programming=[])
    baseline = build_route(diagnosis(row(strength="preferred")), rubrics)["tasks"][0]["priority"]
    required = build_route(diagnosis(row()), rubrics)["tasks"][0]["priority"]
    preferred = build_route(diagnosis(row()), rubrics, preferred_skills=["programming"])["tasks"][0]["priority"]
    assert preferred < required < baseline
    merged = build_route(diagnosis(row(), row("r2", strength="preferred")), rubrics)["tasks"][0]
    assert merged["priority"] == required
    failure = {"anchor_id": "programming.guided", "criterion": "programming handles errors"}
    gap = build_route(diagnosis(row(status="observed_task_gap", observed_task_failures=[failure])), rubrics)
    assert gap["tasks"][0]["priority"] < required
    # Required task waits for its preferred-skill evidence prerequisite.
    route = build_route(diagnosis(row(), row("r2", status="evidence_missing", strength="preferred")), rubrics)
    assert route["tasks"][0]["priority"] > route["tasks"][1]["priority"]
    assert_dag(route)


def test_stable_ids_across_budget_preferences_and_practice_kind():
    rubrics = catalog(programming=[])
    plain = build_route(diagnosis(row()), rubrics)
    failure = {"anchor_id": "programming.guided", "criterion": "programming handles errors"}
    practice = build_route(diagnosis(row(status="observed_task_gap", observed_task_failures=[failure])), rubrics,
                           weekly_hours=5, preferred_skills=["programming"])
    assert plain["topological_order"] == practice["topological_order"]
    assert plain["input_digest"] != practice["input_digest"]


@pytest.mark.parametrize("dependencies,error", [
    ({"programming": ["missing"]}, "missing_prerequisite_skill"),
    ({"programming": ["web"], "web": ["programming"]}, "prerequisite_cycle"),
    ({"programming": ["programming"]}, "prerequisite_cycle"),
])
def test_invalid_dependency_catalog_rejected(dependencies, error):
    with pytest.raises(ValueError, match=error):
        build_route(diagnosis(row()), catalog(**dependencies))


def test_missing_anchor_refs_and_duplicate_ids_rejected():
    rubrics = catalog(programming=[])
    with pytest.raises(ValueError, match="missing_recommended_anchor"):
        build_route(diagnosis(row(next_action={"anchor_ids": ["unknown"]})), rubrics)
    with pytest.raises(ValueError, match="unknown_selected_anchor"):
        build_route(diagnosis(row()), rubrics, selected_anchor_ids=["unknown"])
    with pytest.raises(ValueError, match="selected_anchor_not_in_target"):
        build_route(diagnosis(row()), catalog(programming=[], web=[]), selected_anchor_ids=["web.guided"])
    with pytest.raises(ValueError, match="invalid_or_duplicate_requirement"):
        build_route(diagnosis(row(), row()), rubrics)
    rubrics["skills"][0]["anchors"].append(deepcopy(rubrics["skills"][0]["anchors"][0]))
    with pytest.raises(ValueError, match="invalid_or_duplicate_anchor"):
        build_route(diagnosis(row()), rubrics)


def test_official_resource_metadata_and_empty_fallback():
    for sid in ["programming", "web-ui", "sql", "data-quality", "ml-evaluation", "llm-applications"]:
        refs = resources_for(sid, f"{sid}.guided")
        assert refs and all(ref["skill_id"] == sid and ref["anchor_id"] == f"{sid}.guided" for ref in refs)
        assert all(ref["verified_at"] == "2026-10-11" and ref["language"] == "en"
                   and ref["foundation"] and ref["type"] == "official_documentation" for ref in refs)
        refs[0]["title"] = "mutation"
        assert resources_for(sid, f"{sid}.guided")[0]["title"] != "mutation"
    route = build_route(diagnosis(row(sid="writing")), catalog(writing=[]))
    assert route["tasks"][0]["resources"] == []
    assert "暂无" in route["tasks"][0]["resources_note"]
