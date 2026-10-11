"""Synthetic clauses exercise the logic without replaying personal curation."""

import json
from copy import deepcopy

import pytest

from applypilot.career.catalog import map_evidence, map_topics, technical_mentions
from applypilot.career.diagnosis import STATUSES, build_diagnosis
from applypilot.career.requirements import iter_atoms, parse_requirement


def rubric(*skills):
    return {"schema_version": "test-v1", "skills": [
        {"id": sid, "name_zh": sid, "anchors": [
            {"id": f"{sid}.guided", "stage": "guided", "task": "A finite task",
             "acceptance": ["handles invalid input", "documents result"]}]} for sid in skills]}


def fact(eid="record-a", claim="Implemented Python input validation", *, support="user_confirmed_document",
         ownership="personal", skills=None, limits=None):
    return {"id": eid, "claim": claim, "source_refs": [{"material_id": "material-a", "quote": claim,
            "line_start": 1, "line_end": 1}], "ownership": ownership, "support_type": support,
            "demonstrates": [], "skills": skills or [], "does_not_prove": limits or []}


def bundle(*facts):
    return {"materials": [{"id": "material-a", "kind": "project", "path": "chosen.txt",
                            "snapshot_digest": "fixture", "text": "\n".join(item["claim"] for item in facts)}],
            "evidence": list(facts), "fingerprint": "test-evidence-v1", "boundaries": []}


def diagnose(text, *, facts=(), skills=("programming",), requirements=None, artifacts=None, **kwargs):
    reqs = requirements or [{"id": "clause-a", "text": text, "source_section": "Required Qualifications"}]
    return build_diagnosis(target={"id": "target-a", "revision": 1},
                           jobs=[{"id": "job-a", "title": "Example role", "snapshot_id": "snapshot-a",
                                  "snapshot_path": "jd.txt", "requirements": reqs}],
                           rubrics=rubric(*skills), evidence={**bundle(*facts), "assessment_artifacts": artifacts or {}},
                           **kwargs)


def parse(text, **fields):
    return parse_requirement({"id": "clause-a", "text": text, "source_section": "Required Qualifications", **fields})


def test_technical_aliases_do_not_collide():
    tags = [item["tag"] for item in technical_mentions("JavaScript TypeScript C++ C# NoSQL SystemVerilog Node.js")]
    assert tags == ["JavaScript", "TypeScript", "C++", "C#", "NoSQL", "SystemVerilog", "Node.js"]
    assert [item["tag"] for item in technical_mentions("Java C SQL Verilog")] == ["Java", "C", "SQL", "Verilog"]
    assert technical_mentions("We go into details") == []
    assert map_topics("", ["Spanish", "English"])["skills"] == []


def test_six_states_and_unknown_levels():
    assert diagnose("Experience with Python", facts=[fact()])["requirements"][0]["status"] == "related_evidence"
    assert diagnose("Experience with C++", facts=[fact()])["requirements"][0]["status"] == "transferable_evidence"
    assert diagnose("Experience with Python", facts=[fact(support="resume_only")])["requirements"][0]["status"] == "evidence_missing"
    assert diagnose("Experience with Python")["requirements"][0]["status"] == "needs_verification"
    qualification = diagnose("Must hold a Bachelor's degree")
    assert qualification["requirements"][0]["status"] == "qualification_unknown"
    observation = {"id": "task-a", "requirement_id": "clause-a", "skill_id": "programming",
                   "anchor_id": "programming.guided", "method": "independent_task_review", "context": "Python fixture",
                   "assistance": [], "quality_by_acceptance": [
                       {"criterion": "handles invalid input", "status": "fail", "evidence_refs": ["record-a"]}],
                   "limits": ["one input, one environment"]}
    result = diagnose("Experience with Python", facts=[fact()], assessments=[observation])
    assert result["requirements"][0]["status"] == "observed_task_gap"
    assert result["requirements"][0]["observed_task_failures"][0]["limits"] == observation["limits"]
    assert result["skills"][0]["level"] is None
    assert result["summary"]["status_counts"]["observed_task_gap"] == 1
    assert set(result["summary"]["status_counts"]) == set(STATUSES)
    assert "score" not in result


@pytest.mark.parametrize("changes", [
    {"method": "curated_document_review"}, {"requirement_id": "other-clause"},
    {"anchor_id": "invented-anchor"}, {"assistance": None}, {"context": None},
    {"quality_by_acceptance": [{"criterion": "handles invalid input", "status": "fail", "evidence_refs": []}]},
    {"quality_by_acceptance": [{"criterion": "handles invalid input", "status": "fail", "evidence_refs": ["unknown"]}]},
    {"quality_by_acceptance": [{"criterion": "invented-criterion", "status": "fail", "evidence_refs": ["record-a"]}]},
    {"quality_by_acceptance": [{"criterion": "handles invalid input", "status": "pass", "evidence_refs": ["record-a"]}]},
])
def test_unobserved_or_unbound_failure_does_not_create_gap(changes):
    observation = {"id": "task-a", "requirement_id": "clause-a", "anchor_id": "programming.guided",
                   "method": "self_task_review", "context": "finite task", "assistance": [],
                   "quality_by_acceptance": [{"criterion": "handles invalid input", "status": "fail", "evidence_refs": ["record-a"]}]}
    observation.update(changes)
    result = diagnose("Experience with Python", facts=[fact()], assessments=[observation])
    assert result["requirements"][0]["status"] == "related_evidence"
    assert result["skills"][0]["level"] is None


def test_and_languages_cannot_be_covered_by_one_language():
    result = diagnose("Experience with Python, C++ and CUDA", facts=[fact()], skills=("programming", "performance"))
    row = result["requirements"][0]
    assert row["condition_tree"]["op"] == "all"
    assert row["related_conditions_supported"] is False
    assert row["status"] == "transferable_evidence"
    missing = {tag for item in row["condition_observations"] for tag in item["missing_technical_tags"]}
    assert missing == {"C++", "CUDA"}


def test_local_or_is_kept_under_three_and_tasks():
    row = parse("Relevant practical or internship experience articulating product questions, "
                "pulling data from datasets (SQL or Python), and using statistics to arrive at an answer.")
    tree = row["condition_tree"]
    assert row["parse_status"] == "parsed"
    assert tree["op"] == "all" and len(tree["children"]) == 3
    assert tree["experience_sources"] == ["practical", "internship"]
    middle = tree["children"][1]
    assert middle["op"] == "all"
    assert middle["children"][1]["op"] == "any"
    assert [atom["technical_tags"] for atom in iter_atoms(middle["children"][1])] == [["SQL"], ["Python"]]
    result = diagnose(row["quote"], facts=[fact(claim="Used SQL to pull datasets")],
                      skills=("sql", "programming", "requirements", "statistics"))
    assert result["requirements"][0]["related_conditions_supported"] is False


def test_mixed_language_strength_preserves_required_and_preferred():
    row = parse("Fluency in German and English; French is highly desirable.")
    assert row["kind"] == "qualification"
    assert row["strength"] == "mixed"
    required, preferred = row["condition_tree"]["children"]
    assert required["op"] == "all"
    assert required["strength"] == "required"
    assert preferred["strength"] == "preferred"
    assert row["skills"] == []


def test_degree_and_years_alternative_keeps_grouping():
    row = parse("3+ years technology sales experience OR Bachelor's Degree in Computing AND "
                "1+ year technology sales experience OR equivalent experience")
    assert row["kind"] == "qualification"
    assert row["condition_tree"]["op"] == "any"
    assert len(row["condition_tree"]["children"]) == 3
    assert row["condition_tree"]["children"][1]["op"] == "all"
    assert row["skills"] == []


def test_related_field_with_conferral_date_is_not_independent_degree_route():
    row = parse("Currently working towards a Bachelor's degree in Computing, Statistics or other equivalent "
                "technical discipline with an expected conferral date between May 2030 and May 2032.")
    assert row["kind"] == "qualification"
    assert row["condition_tree"]["op"] == "atom"
    alternate = parse("Expected graduation in 2030 or graduated within the past 18 months")
    assert alternate["condition_tree"]["op"] == "any"


def test_generated_reference_cannot_establish_observed_failure():
    item = {**fact(), "authoritative": False}
    assessment = {"id": "task-a", "requirement_id": "clause-a", "anchor_id": "programming.guided",
                  "method": "task_review", "context": "Python", "assistance": [],
                  "quality_by_acceptance": [{"criterion": "handles invalid input", "status": "fail", "evidence_refs": ["record-a"]}]}
    result = diagnose("Python experience", facts=[item], assessments=[assessment])
    assert result["requirements"][0]["status"] == "evidence_missing"


def test_cardinality_and_numbered_local_alternatives():
    row = parse("Experience in at least two of the following areas: 1/ Python or Java. "
                "2/ SQL. 3/ testing.")
    assert row["parse_status"] == "parsed"
    assert row["condition_tree"]["op"] == "at_least"
    assert row["condition_tree"]["k"] == 2
    assert row["condition_tree"]["children"][0]["op"] == "any"
    result = diagnose("Experience with at least one of the following languages: Python, Java, or Ruby.",
                      facts=[fact()])
    assert result["requirements"][0]["status"] == "related_evidence"


def test_two_track_choice_and_conditional_track_requirement():
    row = parse("Focus on one of two specialized tracks: TensorRT-LLM – inference (Python / PyTorch) "
                "or TensorRT Compiler – graph optimization (C++).")
    assert row["condition_tree"]["op"] == "at_least" and row["condition_tree"]["k"] == 1
    assert len(row["condition_tree"]["children"]) == 2
    assert row["parse_status"] == "parsed"
    track = parse("TensorRT-LLM: Python programming and experience with PyTorch")
    assert track["condition_tree"]["op"] == "conditional"
    assert track["condition_tree"]["applies_if"]["text"] == "TensorRT-LLM"


def test_examples_do_not_become_all_framework_gates():
    row = parse("Experience with modern frontend frameworks (e.g., React, TypeScript, Angular, Vue).",
                requirement_strength="preferred")
    assert row["strength"] == "preferred"
    assert row["technical_conditions"] == []
    assert row["condition_tree"]["examples"][0]["mandatory"] is False
    result = diagnose(row["quote"], facts=[fact(claim="Implemented a React user interface")], skills=("web-ui", "programming"))
    assert result["requirements"][0]["status"] == "related_evidence"
    assert result["requirements"][0]["condition_observations"][0]["missing_technical_tags"] == []


def test_local_examples_preserve_second_required_domain():
    row = parse("Experience with visualization tools (e.g. Excel, Tableau) and statistical methods (e.g. t-test).")
    assert row["condition_tree"]["op"] == "all"
    assert [child["op"] for child in row["condition_tree"]["children"]] == ["atom", "atom"]
    assert all(child["examples"][0]["mandatory"] is False for child in row["condition_tree"]["children"])


def test_responsibility_example_retains_strength():
    row = parse("Design agentic workflows with advanced techniques like Retrieval-Augmented Generation (RAG).",
                source_section="Responsibilities", requirement_kind="responsibility")
    assert row["kind"] == "responsibility" and row["strength"] == "responsibility"
    assert row["condition_tree"]["examples"][0]["mandatory"] is False


def test_lower_bound_acceptance_date_is_not_exact_deadline():
    row = parse("Applications will be accepted at least until November 15, 2031.")
    assert row["temporal_condition"]["kind"] == "acceptance_lower_bound"
    assert row["temporal_condition"]["operator"] == "at_least_until"
    assert parse("Please complete your application before November 15, 2031.")["temporal_condition"]["kind"] == "deadline"


def test_eligibility_misclassification_is_retained_and_corrected():
    row = parse("Programming experience using Python or C++", requirement_kind="eligibility")
    assert row["raw_kind"] == "eligibility"
    assert row["kind"] == "skill"


def test_unscoped_mixed_conditions_and_uncovered_do_not_create_gaps():
    result = diagnose("Python and Java or C++")
    assert result["requirements"][0]["status"] == "unresolved"
    assert result["requirements"][0]["next_action"]["anchor_ids"] == []
    uncovered = diagnose("Experience in underwater archaeology")
    assert uncovered["requirements"][0]["status"] == "uncovered"


def test_ownership_and_negative_boundaries_are_preserved():
    item = fact(claim="Implemented React interface", ownership="team_with_scoped_role",
                limits=["Does not prove C++ implementation or backend work"])
    result = diagnose("Experience with React", facts=[item], skills=("web-ui",))
    ref = result["requirements"][0]["evidence_refs"][0]
    assert ref["ownership"] == "team_with_scoped_role"
    assert ref["does_not_prove"] == item["does_not_prove"]
    unscoped = diagnose("Experience with Python", facts=[fact(ownership="team_only")])
    assert unscoped["requirements"][0]["status"] == "evidence_missing"
    excluded = diagnose("Experience with C++", facts=[fact(claim="Python project with C++ listed as a future target",
                        limits=["C++ capability not demonstrated"])])
    assert excluded["requirements"][0]["related_conditions_supported"] is False


@pytest.mark.parametrize("kind", ["generated", "generated_text", "resume", "resume_copy"])
def test_material_kind_cannot_be_promoted_by_strong_evidence_label(kind):
    evidence = bundle(fact(support="user_confirmed_document"))
    evidence["materials"][0]["kind"] = kind
    result = build_diagnosis(target={}, jobs=[{"id": "job-a", "requirements": [
        {"id": "clause-a", "text": "Python experience"}]}], rubrics=rubric("programming"), evidence=evidence)
    assert result["requirements"][0]["status"] == "evidence_missing"


def test_normalized_authority_false_cannot_be_promoted():
    item = {**fact(), "authoritative": False}
    assert diagnose("Python experience", facts=[item])["requirements"][0]["status"] == "evidence_missing"


@pytest.mark.parametrize("claim", [
    "Implemented React frontend, but teammate implemented C++ backend development",
    "Implemented React frontend; C++ backend development implemented by teammate",
    "本人负责React前端；C++后端由队友实现",
    "Implemented React frontend, but did not implement C++ backend development",
    "Implemented React frontend; C++ programming not demonstrated",
])
def test_peer_and_negative_scope_cannot_prove_personal_backend(claim):
    item = fact(claim=claim, skills=["programming", "web-ui"])
    item["source_refs"][0]["quote"] = "Team delivered React frontend and C++ backend development"
    mapped = map_evidence(item)
    assert "C++" not in mapped["technical_tags"]
    assert "programming" not in mapped["skills"]
    result = diagnose("C++ backend development", facts=[item], skills=("programming", "web-ui"))
    assert result["requirements"][0]["status"] == "needs_verification"
    assert result["requirements"][0]["evidence_refs"] == []


def test_model_suggestions_are_annotations_only_and_validate_ids():
    suggestion = {"requirement_id": "clause-a", "evidence_id": "record-a", "relation": "related", "reason": "Topic association"}
    result = diagnose("Experience with C++", facts=[fact()], model_suggestions=[suggestion])
    assert result["requirements"][0]["status"] == "transferable_evidence"
    assert result["requirements"][0]["model_suggestions"] == [suggestion]
    for invalid in [{**suggestion, "evidence_id": "unknown"}, {**suggestion, "relation": "passed"},
                    {**suggestion, "status": "related_evidence"}]:
        with pytest.raises(ValueError, match="invalid_model_suggestion"):
            diagnose("Experience with Python", facts=[fact()], model_suggestions=[invalid])


def test_determinism_serialization_and_no_input_mutation():
    kwargs = {"target": {"id": "target-a"}, "jobs": [
        {"id": "job-b", "requirements": [{"id": "req-b", "text": "Experience with Python"}]},
        {"id": "job-a", "requirements": [{"id": "req-a", "text": "Experience with Python"}]}],
        "rubrics": rubric("programming"), "evidence": bundle(fact())}
    before = deepcopy(kwargs)
    first = build_diagnosis(**kwargs)
    second = build_diagnosis(**{**kwargs, "jobs": list(reversed(kwargs["jobs"]))})
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(second, ensure_ascii=False, sort_keys=True)
    assert kwargs == before
    assert [row["job_id"] for row in first["requirements"]] == ["job-a", "job-b"]
    assert all(row["next_action"]["anchor_ids"] == [] for row in first["requirements"])


def test_duplicate_source_identity_rejected():
    with pytest.raises(ValueError, match="duplicate_requirement"):
        diagnose("", requirements=[{"id": "same", "text": "Python"}, {"id": "same", "text": "C++"}])


@pytest.mark.parametrize("text", [
    "French proficiency is a requirement for this placement",
    "Excellent written and verbal Japanese communication skills",
    "Working knowledge of Spanish is required",
])
def test_language_proficiency_is_a_qualification_even_in_skill_section(text):
    result = diagnose(text, facts=[fact(claim="Communicated software changes to colleagues")],
                      skills=("technical-communication",))
    row = result["requirements"][0]
    assert row["kind"] == "qualification" and row["status"] == "qualification_unknown"
    assert row["qualification_conditions"][0]["kind"] == "language"
    if "communication" in text:
        assert row["skills"] == ["technical-communication"]
        assert any(atom["kind"] == "skill" for atom in iter_atoms(row["condition_tree"]))
        assert any(atom["kind"] == "qualification" for atom in iter_atoms(row["condition_tree"]))
    else:
        assert row["skills"] == []


def test_chinese_team_context_and_peer_api_do_not_erase_owned_frontend():
    item = fact(claim="三人团队中负责 React/TypeScript 前端和用户体验，接入队友 API，呈现失败状态；"
                      "C++后端由队友实现", ownership="team_with_scoped_role",
                skills=["web-ui", "api-integration"], limits=["本人未实现后端采集与索引"])
    mapped = map_evidence(item)
    assert "web-ui" in mapped["skills"] and "api-integration" in mapped["skills"]
    assert {"React", "TypeScript"} <= set(mapped["technical_tags"])
    assert "C++" not in mapped["technical_tags"]
    result = diagnose("Experience with React", facts=[item], skills=("web-ui",))
    assert result["requirements"][0]["status"] == "related_evidence"


@pytest.mark.parametrize("claim,ownership,signals", [
    ("Master's student in AI", "candidate_record", []),
    ("AI Solutions Lead at Example Company", "candidate_record", ["consulting_role_context"]),
    ("Enrolled in an artificial intelligence degree", "personal", ["ai_study"]),
    ("AI research department employment history", "personal", ["research_role_context"]),
])
def test_education_role_and_context_only_are_not_engineering_experience(claim, ownership, signals):
    item = fact(claim=claim, ownership=ownership)
    item["demonstrates"] = signals
    mapped = map_evidence(item)
    assert mapped["evidence_scope"] == "qualification_context"
    assert mapped["skills"] == []
    result = diagnose("Experience with AI", facts=[item], skills=("llm-applications",))
    assert result["requirements"][0]["status"] == "needs_verification"
    assert result["requirements"][0]["evidence_refs"] == []


def test_example_followed_by_deployment_environment_keeps_unresolved_scope():
    result = diagnose("Expertise in deploying LLM inference frameworks like vLLM or Triton, "
                      "including on Kubernetes or edge devices to deliver AI at scale",
                      facts=[fact(claim="Implemented an LLM application")],
                      skills=("llm-applications", "cloud-basics"))
    row = result["requirements"][0]
    assert row["status"] == "unresolved"
    assert "Kubernetes" in row["quote"]
    assert row["condition_tree"]["reason"] == "example_scope_ambiguous"
    assert row["related_conditions_supported"] is None


@pytest.mark.parametrize("text", [
    "Build AI inference services for deployment across multiple hardware environments",
    "Experience operating cloud systems under disaster recovery constraints",
    "Implement Python data processing under strict latency constraints",
])
def test_one_topic_in_complex_task_does_not_establish_complete_condition_relevance(text):
    result = diagnose(text, facts=[fact(claim="Implemented Python AI scripts using cloud services")],
                      skills=("programming", "llm-applications", "cloud-basics"))
    row = result["requirements"][0]
    assert row["parse_status"] == "parsed"
    assert row["status"] == "transferable_evidence"
    assert row["related_conditions_supported"] is None
    assert row["condition_observations"][0]["coverage_scope"] == "partial_task"
    assert row["condition_observations"][0]["mapped_topics_supported"] is True


@pytest.mark.parametrize("text", [
    "Release new products and features, evaluate their performance, and iterate on customer feedback.",
    "Review business performance using retention and revenue before prioritizing the next release.",
    "发布产品、核对业务表现并迭代交付。",
])
def test_product_performance_maps_to_outcomes_and_product_delivery(text):
    mapped = map_topics(text, ["performance"])
    assert "outcome-metrics" in mapped["skills"]
    assert "product-prioritization" in mapped["skills"]
    assert "performance" not in mapped["skills"]
    result = diagnose(text, skills=("performance", "outcome-metrics", "product-prioritization"))
    row = result["requirements"][0]
    assert "performance" not in row["skills"]
    assert "performance.guided" not in row["next_action"]["anchor_ids"]


@pytest.mark.parametrize("text", [
    "Measure CPU performance with runtime profiling under a latency budget.",
    "Improve product runtime performance by profiling memory allocation.",
    "分析运行时性能与内存限制，比较优化方案。",
])
def test_explicit_technical_performance_keeps_engineering_domain(text):
    mapped = map_topics(text)
    assert "performance" in mapped["skills"]
    assert "outcome-metrics" not in mapped["skills"]


@pytest.mark.parametrize("text", ["Evaluate their performance", "Strong performance optimization skills", "核对表现与优化"])
def test_ambiguous_performance_does_not_guess_domain(text):
    mapped = map_topics(text, ["performance"])
    assert "performance" not in mapped["skills"]
    assert "outcome-metrics" not in mapped["skills"]


def test_separate_business_and_runtime_sentences_keep_both_scopes():
    mapped = map_topics("Review business performance and revenue; profile CPU runtime performance.")
    assert {"outcome-metrics", "performance"} <= set(mapped["skills"])
    assert mapped["mapping_version"] == "career-controlled-topics-v3"


def test_business_benchmarking_and_mixed_case_hint_do_not_imply_cpu_optimization():
    mapped = map_topics("Benchmarking business performance against competing products", ["Performance"])
    assert "outcome-metrics" in mapped["skills"]
    assert "performance" not in mapped["skills"]


@pytest.mark.parametrize("text", ["Are 18 years of age or older", "Must be 18 years old", "Minimum age of 18", "年满18岁"])
def test_age_requirements_are_qualifications_not_missing_skills(text):
    row = diagnose(text)["requirements"][0]
    assert row["kind"] == "qualification" and row["status"] == "qualification_unknown"
    assert not row["skills"] and not row["next_action"]["anchor_ids"]


def test_objective_c_aliases_consume_whole_language_name():
    mentions = technical_mentions("Objective C and Objective-C, followed by plain C")
    assert [item["tag"] for item in mentions] == ["Objective-C", "Objective-C", "C"]
    assert [item["quote"] for item in mentions] == ["Objective C", "Objective-C", "C"]
    assert map_topics("Objective C")["skills"] == ["programming"]


def m3_review(*, outcome="passed", **changes):
    review = {
        "id": "review-a", "schema_version": "career-assessment-v1", "compatible": True,
        "route_id": "route-a", "task_id": "task-a", "task_kind": "verification",
        "anchor_id": "programming.guided", "skill_id": "programming", "requirement_ids": ["clause-a"],
        "target_id": "target-a", "target_revision": 1, "catalog_id": "catalog-a",
        "evidence_snapshot_id": "facts-snapshot-a", "artifact_evidence_snapshot_id": "artifact-a",
        "artifact_fingerprint": "artifact-fingerprint-a", "method": "self_task_review", "reviewer": "candidate",
        "subject": "candidate", "context": {"runtime": "fictional Python fixture"},
        "assistance": ["guided instructions"], "outcome": outcome,
        "acceptance_results": [
            {"criterion": "handles invalid input", "status": "fail" if outcome == "failed" else "pass",
             "evidence_refs": [{"material_id": "material-a", "quote": "invalid input result", "line_start": 1, "line_end": 1}]},
            {"criterion": "documents result", "status": "pass",
             "evidence_refs": [{"material_id": "material-a", "quote": "documented result", "line_start": 2, "line_end": 2}]},
        ], "limits": ["fictional runtime only"],
    }
    review.update(changes)
    return review


def m3_artifacts():
    return {"artifact-a": {"fingerprint": "artifact-fingerprint-a", "materials": [
        {"id": "material-a", "kind": "project_evidence", "snapshot_digest": "artifact-digest-a",
         "text": "invalid input result\ndocumented result"}], "evidence": []}}


def test_m3_pass_closes_only_exact_task_preserving_requirement_evidence_state():
    observation = m3_review()
    result = diagnose("Experience with Python and SQL", skills=("programming", "sql"),
                      assessments=[observation], artifacts=m3_artifacts())
    row = result["requirements"][0]
    assert row["status"] == "needs_verification"
    assert row["related_conditions_supported"] is False
    assert row["next_action"]["anchor_ids"] == ["sql.guided"]
    assert [item["task_id"] for item in row["observed_task_passes"]] == ["task-a"]
    review = row["task_observations"][0]
    assert review["candidate_task_result"] == "passed"
    assert review["method"] == "self_task_review" and review["reviewer"] == "candidate"
    assert review["assistance"] == ["guided instructions"]
    assert review["acceptance_results"][0]["evidence_refs"][0]["snapshot_digest"] == "artifact-digest-a"
    assert all(skill["level"] is None for skill in result["skills"])
    assert row["candidate_level"] is None and row["skill_level_verified"] is False
    assert row["evidence_refs"] == []


def test_m3_pass_preserves_partial_jd_and_existing_material_status():
    result = diagnose("Experience with Python and C++", facts=[fact()], assessments=[m3_review()],
                      artifacts=m3_artifacts())
    row = result["requirements"][0]
    assert row["status"] == "transferable_evidence"
    assert row["related_conditions_supported"] is False
    assert row["observed_task_passes"] and not row["observed_task_failures"]
    assert row["next_action"]["anchor_ids"] == []


@pytest.mark.parametrize("changes", [
    {"compatible": False}, {"compatible": None}, {"compatible": 1},
    {"schema_version": "invented-v1"}, {"method": "marked_completed"}, {"method": {}},
    {"task_kind": "course"}, {"subject": "assistant"}, {"reviewer": ""},
    {"assistance": None}, {"context": None}, {"target_revision": 2}, {"target_id": "other-target"},
    {"artifact_evidence_snapshot_id": "unselected-artifact"}, {"artifact_fingerprint": "changed"},
    {"acceptance_results": [None]}, {"limits": "unbounded"},
    {"acceptance_results": [{"criterion": "handles invalid input", "status": [], "evidence_refs": []}]},
])
def test_m3_unknown_or_malformed_review_does_not_elevate(changes):
    review = m3_review(**changes)
    row = diagnose("Python experience", assessments=[review], artifacts=m3_artifacts())["requirements"][0]
    assert row["status"] == "needs_verification"
    assert row["observed_task_passes"] == [] and row["observed_task_failures"] == []
    assert row["next_action"]["anchor_ids"] == ["programming.guided"]


def test_m3_missing_compatible_cannot_enter_legacy_failure_path():
    review = m3_review(outcome="failed", requirement_id="clause-a", quality_by_acceptance=[
        {"criterion": "handles invalid input", "status": "fail", "evidence_refs": ["record-a"]}])
    del review["compatible"]
    row = diagnose("Python experience", facts=[fact()], assessments=[review],
                   artifacts=m3_artifacts())["requirements"][0]
    assert row["status"] == "related_evidence" and not row["task_observations"]


@pytest.mark.parametrize("ref_changes", [
    {"material_id": "unknown"}, {"quote": "invented result"}, {"line_start": 2, "line_end": 2},
    {"line_start": True}, {"line_end": 50}, {"snapshot_digest": "other-digest"},
])
def test_m3_review_requires_quote_in_exact_artifact_namespace(ref_changes):
    review = m3_review()
    review["acceptance_results"][0]["evidence_refs"][0].update(ref_changes)
    row = diagnose("Python experience", facts=[fact(claim="invented result")], assessments=[review],
                   artifacts=m3_artifacts())["requirements"][0]
    assert not row["observed_task_passes"] and not row["task_observations"]


@pytest.mark.parametrize("kind,subject,method", [
    ("evidence_collection", "artifact", "artifact_review"),
    ("verification", "artifact", "runtime_check"),
    ("qualification_review", "candidate", "independent_task_review"),
])
def test_m3_non_capability_reviews_are_visible_without_granting_task_pass(kind, subject, method):
    review = m3_review(task_kind=kind, subject=subject, method=method)
    row = diagnose("Python experience", assessments=[review], artifacts=m3_artifacts())["requirements"][0]
    assert row["status"] == "needs_verification" and row["task_observations"]
    assert row["task_observations"][0]["candidate_task_result"] is None
    assert not row["observed_task_passes"] and not row["verified_capability_gap"]
    assert row["next_action"]["anchor_ids"] == ["programming.guided"]


@pytest.mark.parametrize("text,status", [
    ("Bachelor's degree required", "qualification_unknown"),
    ("Python and Java or C++", "unresolved"), ("Underwater archaeology", "uncovered"),
])
def test_m3_pass_never_promotes_qualification_unresolved_or_uncovered(text, status):
    row = diagnose(text, assessments=[m3_review()], artifacts=m3_artifacts())["requirements"][0]
    assert row["status"] == status and not row["observed_task_passes"]


def test_m3_fail_is_scoped_and_latest_same_task_pass_removes_failure():
    fail = m3_review(outcome="failed", sequence=1)
    other_req = {"id": "clause-b", "text": "Python experience"}
    result = diagnose("", requirements=[{"id": "clause-a", "text": "Python experience"}, other_req],
                      assessments=[fail], artifacts=m3_artifacts())
    assert result["requirements"][0]["status"] == "observed_task_gap"
    assert result["requirements"][1]["status"] == "needs_verification"
    passed = m3_review(id="review-b", sequence=2)
    for reviews in ([fail, passed], [passed, fail], [passed]):
        row = diagnose("Python experience", assessments=reviews, artifacts=m3_artifacts())["requirements"][0]
        assert row["status"] == "needs_verification"
        assert not row["observed_task_failures"] and row["observed_task_passes"]
        assert not row["verified_capability_gap"] and not row["next_action"]["anchor_ids"]


def test_m3_distinct_tasks_keep_both_observations_and_ambiguous_duplicates_do_not_win():
    passed = m3_review()
    failed = m3_review(id="review-b", task_id="task-b", outcome="failed")
    row = diagnose("Python experience", assessments=[passed, failed], artifacts=m3_artifacts())["requirements"][0]
    assert row["status"] == "observed_task_gap"
    assert len(row["task_observations"]) == 2 and len(row["observed_task_passes"]) == 1
    assert row["next_action"]["anchor_ids"] == ["programming.guided"]
    failed["task_id"] = "task-a"
    row = diagnose("Python experience", assessments=[passed, failed], artifacts=m3_artifacts())["requirements"][0]
    assert row["status"] == "needs_verification" and not row["task_observations"]


def test_m3_partial_criteria_pass_does_not_close_anchor_and_does_not_mutate_inputs():
    review, artifacts = m3_review(), m3_artifacts()
    review["acceptance_results"] = review["acceptance_results"][:1]
    before = deepcopy((review, artifacts))
    row = diagnose("Python experience", assessments=[review], artifacts=artifacts)["requirements"][0]
    assert row["task_observations"][0]["candidate_task_result"] == "pending"
    assert not row["observed_task_passes"] and row["next_action"]["anchor_ids"] == ["programming.guided"]
    assert (review, artifacts) == before


def test_m3_current_target_bindings_and_not_observed_criteria_are_required_for_pass():
    review = m3_review()
    kwargs = {"target": {"target_id": "target-a", "revision": 1,
                         "bindings": {"catalog_id": "catalog-a", "evidence_snapshot_id": "facts-snapshot-a"}},
              "jobs": [{"id": "job-a", "requirements": [{"id": "clause-a", "text": "Python experience"}]}],
              "rubrics": rubric("programming"), "evidence": {**bundle(), "assessment_artifacts": m3_artifacts()}}
    for key in ("catalog_id", "evidence_snapshot_id"):
        row = build_diagnosis(**kwargs, assessments=[{**review, key: "old-input"}])["requirements"][0]
        assert not row["task_observations"] and not row["observed_task_passes"]
    review["acceptance_results"][1].update(status="not_observed", evidence_refs=[])
    row = build_diagnosis(**kwargs, assessments=[review])["requirements"][0]
    assert not row["observed_task_passes"]  # A claimed pass contradicts unchecked criteria.
    review["outcome"] = "pending"
    row = build_diagnosis(**kwargs, assessments=[review])["requirements"][0]
    assert row["task_observations"][0]["candidate_task_result"] == "pending"
    assert row["next_action"]["anchor_ids"] == ["programming.guided"]
