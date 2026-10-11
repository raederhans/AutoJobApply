"""Pure evidence-relation diagnosis for selected JDs, with no hiring score."""

from __future__ import annotations

from copy import deepcopy

from .catalog import map_evidence
from .requirements import parse_requirements

ALGORITHM_VERSION = "career-diagnosis-rules-v3"
STATUSES = ("related_evidence", "transferable_evidence", "evidence_missing", "needs_verification",
            "observed_task_gap", "qualification_unknown", "unresolved", "uncovered")
_WEAK_SUPPORT = {"resume_only", "self_report", "generated", "model_generated", "unverified"}
_UNSCOPED_OWNERSHIP = {"team", "team_only", "unknown", "not_recorded", "resume_claim_not_independently_checked"}
_WEAK_MATERIAL_KINDS = {"generated", "generated_text", "model_generated", "ai_generated", "resume", "resume_copy",
                        "resume_variant", "cv", "historical_resume"}
_REASONS = {
    "related_evidence": "可回源材料与该条件有相关经历；这只说明证据关联，不表示整条要求已满足或任务测评通过。",
    "transferable_evidence": "已有相近领域或部分条件的证据；目标任务、技术环境及未覆盖条件仍需单独验证。",
    "evidence_missing": "已有自述或相近经历记录，但产物、本人贡献或可靠来源仍待补充；不能据此认定不会。",
    "needs_verification": "当前所选材料没有足够相关观察；能力保持未知，需用有限任务验证。",
    "observed_task_gap": "绑定本条要求的任务验收记录包含有证据的失败项；结论仅限该任务、环境和实际协助条件。",
    "qualification_unknown": "这是资格或申请时间条件；保持原句及替代路径，需独立核对事实，不转换为技能差距。",
    "unresolved": "原句包含尚不能可靠划定作用范围的条件；需先核对原句逻辑，不据此生成确定能力缺口。",
    "uncovered": "当前受控能力目录尚未覆盖此条要求；保留原句，不能临时编造正式能力标准。",
}
_ACTIONS = {
    "related_evidence": ("organize_evidence", "整理本人贡献、产物和边界；需要时核对具体任务。"),
    "transferable_evidence": ("verify_target_context", "用目标环境的小任务核对未覆盖条件，并记录协助。"),
    "evidence_missing": ("collect_existing_evidence", "优先补已有材料、产物和本人职责范围。"),
    "needs_verification": ("verify_task", "选择目录中最小相关任务，记录实际环境与验收。"),
    "observed_task_gap": ("practice_failed_criteria", "围绕明确未通过项补练并复测，保留失败情境。"),
    "qualification_unknown": ("verify_qualification", "核对原句所列资格、时间和替代项；必要时调整目标。"),
    "unresolved": ("clarify_source_condition", "人工核对条件作用范围和原句后再判断。"),
    "uncovered": ("review_catalog_coverage", "人工核对目录覆盖范围；保留未覆盖要求。"),
}


def _unique(items: list[dict], label: str) -> dict[str, dict]:
    result = {}
    for item in items:
        key = str(item.get("id", ""))
        if not key or key in result:
            raise ValueError(f"invalid_or_duplicate_{label}_id")
        result[key] = item
    return result


def _evidence_info(item: dict, materials: dict) -> dict:
    mapped = map_evidence(item)
    refs = item.get("source_refs", [])
    source_valid = bool(refs) and all(ref.get("material_id") in materials and bool(ref.get("quote")) for ref in refs)
    weak = (item.get("support_type") in _WEAK_SUPPORT or item.get("ownership") in _UNSCOPED_OWNERSHIP
            or item.get("authoritative") is False or not item.get("ownership") or not source_valid
            or any(materials.get(ref.get("material_id"), {}).get("kind") in _WEAK_MATERIAL_KINDS for ref in refs))
    return {"item": item, "mapped": mapped, "weak": weak, "source_valid": source_valid}


def _coverage(tree: dict, records: list[dict], allowed_skills: set[str]) -> tuple[bool | None, list[dict]]:
    op = tree.get("op")
    if op in {"unresolved", "conditional"}:
        # Choosing a target does not establish that a local applicability clause
        # holds. The conditional and children are kept, never flattened into all.
        return None, []
    if op == "atom":
        skills = set(tree.get("skills", [])) & allowed_skills
        technical = set(tree.get("technical_tags", []))
        matched = [record for record in records if skills & set(record["mapped"]["skills"])
                   or technical & set(record["mapped"]["technical_tags"])]
        strong = [record for record in matched if not record["weak"]]
        supported_skills = set().union(*(set(record["mapped"]["skills"]) for record in strong)) if strong else set()
        supported_tech = set().union(*(set(record["mapped"]["technical_tags"]) for record in strong)) if strong else set()
        supported = bool(skills or technical) and skills <= supported_skills and technical <= supported_tech
        observed = (None if tree.get("coverage_scope") == "partial_task" and supported
                    else supported if skills or technical else None)
        return observed, [{
            "text": tree["text"], "skills": sorted(skills), "technical_tags": sorted(technical),
            "related_conditions_supported": observed, "coverage_scope": tree.get("coverage_scope"),
            "mapped_topics_supported": supported if skills or technical else None,
            "missing_technical_tags": sorted(technical - supported_tech),
            "unobserved_skills": sorted(skills - supported_skills),
            "evidence_ids": sorted(record["item"]["id"] for record in matched),
            "strength": tree.get("strength"),
        }]
    children = [child for child in tree.get("children", []) if child.get("strength") != "preferred"]
    results = [_coverage(child, records, allowed_skills) for child in children]
    values = [value for value, _ in results]
    details = [detail for _, child_details in results for detail in child_details]
    if not values:
        return None, details
    if op == "all":
        return (False if False in values else None if None in values else True), details
    if op in {"any", "at_least"}:
        k = tree.get("k", 1)
        count = values.count(True)
        return (True if count >= k else None if count + values.count(None) >= k else False), details
    return None, details


def _observed_failures(row: dict, assessments: list[dict], anchors: dict, evidence_ids: set[str]) -> list[dict]:
    failures = []
    for assessment in assessments:
        # Versioned task reviews must pass the M3 compatibility gate, never the
        # older evidence-ID snippet interface.
        if not isinstance(assessment, dict) or "schema_version" in assessment or "task_id" in assessment:
            continue
        if assessment.get("requirement_id") != row["id"]:
            continue
        anchor_id = assessment.get("anchor_id")
        anchor = anchors.get(anchor_id)
        if not anchor or anchors[anchor_id]["skill_id"] not in row["skills"]:
            continue
        if assessment.get("method") not in {"task_review", "self_task_review", "independent_task_review", "runtime_check"}:
            continue
        # A saved state or old curated 'gap' is not an observed task failure.
        checks = assessment.get("quality_by_acceptance", assessment.get("acceptance_results"))
        if isinstance(checks, dict):
            checks = [{"criterion": key, **(value if isinstance(value, dict) else {"status": value})}
                      for key, value in checks.items()]
        if not isinstance(checks, list) or not assessment.get("context") or assessment.get("assistance") is None:
            continue
        for check in checks:
            criterion = check.get("criterion", check.get("acceptance"))
            refs = check.get("evidence_refs", [])
            ids = [ref if isinstance(ref, str) else ref.get("evidence_id") for ref in refs]
            if (check.get("status") == "fail" and criterion in anchor["anchor"].get("acceptance", [])
                    and ids and set(ids) <= evidence_ids):
                failures.append({"assessment_id": assessment.get("id"), "anchor_id": anchor_id,
                                 "criterion": criterion, "evidence_refs": sorted(ids),
                                 "method": assessment["method"], "context": deepcopy(assessment["context"]),
                                 "assistance": deepcopy(assessment["assistance"]),
                                 "limits": deepcopy(assessment.get("limits", []))})
    return failures


def _task_reviews(assessments: list[dict], target: dict, artifacts: dict) -> list[dict]:
    """Accept service-verified M3 observations; preserve independent scopes.

    The service authenticates pinned artifacts/quotes and resolves supersession.
    Duplicate tasks without an explicit unique sequence are ambiguous and do
    not become active observations based on arbitrary input order.
    """
    if not isinstance(artifacts, dict):
        return []
    groups = {}
    for review in assessments:
        if not isinstance(review, dict) or review.get("schema_version") != "career-assessment-v1":
            continue
        if review.get("compatible") is not True:
            continue
        required_ids = ("id", "route_id", "task_id", "target_id", "catalog_id",
                        "evidence_snapshot_id", "artifact_evidence_snapshot_id", "artifact_fingerprint")
        if any(not isinstance(review.get(key), str) or not review[key].strip() for key in required_ids):
            continue
        artifact = artifacts.get(review["artifact_evidence_snapshot_id"])
        if not isinstance(artifact, dict) or artifact.get("fingerprint") != review["artifact_fingerprint"]:
            continue
        materials = {item["id"]: item for item in artifact.get("materials", [])
                     if isinstance(item, dict) and isinstance(item.get("id"), str)}
        revision = review.get("target_revision")
        if type(revision) is not int or revision < 1:
            continue
        if (target.get("target_id", target.get("id")) not in {None, review["target_id"]}
                or target.get("revision") not in {None, revision}):
            continue
        bindings = target.get("bindings", {})
        if any(bindings.get(key) not in {None, review[key]} for key in ("catalog_id", "evidence_snapshot_id")):
            continue
        ids = review.get("requirement_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(rid, str) or not rid for rid in ids):
            continue
        if (any(not isinstance(review.get(key), str) for key in ("task_kind", "method", "subject", "outcome"))
                or any(review.get(key) is not None and not isinstance(review[key], str)
                       for key in ("anchor_id", "skill_id"))):
            continue
        if (review.get("task_kind") not in {"qualification_review", "evidence_collection", "verification", "practice"}
                or review.get("method") not in {"artifact_review", "self_task_review", "independent_task_review", "runtime_check"}
                or review.get("subject") not in {"artifact", "candidate"}
                or review.get("outcome") not in {"passed", "failed", "pending"}
                or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()
                or not isinstance(review.get("context"), (str, dict)) or not review["context"]
                or not isinstance(review.get("assistance"), (str, dict, list))
                or not isinstance(review.get("limits", []), list)
                or any(not isinstance(limit, str) for limit in review.get("limits", []))):
            continue
        review = deepcopy(review)
        checks = review.get("acceptance_results")
        if not isinstance(checks, list) or not checks:
            continue
        valid = True
        criteria = set()
        for check in checks:
            if (not isinstance(check, dict) or not isinstance(check.get("criterion"), str)
                    or not check["criterion"].strip() or check["criterion"] in criteria
                    or not isinstance(check.get("status"), str)
                    or check.get("status") not in {"pass", "fail", "not_observed"}):
                valid = False
                break
            criteria.add(check["criterion"])
            refs = check.get("evidence_refs")
            if not isinstance(refs, list) or (check["status"] != "not_observed" and not refs):
                valid = False
                break
            for ref in refs:
                if (not isinstance(ref, dict) or not isinstance(ref.get("material_id"), str)
                        or ref["material_id"] not in materials or not isinstance(ref.get("quote"), str)
                        or not ref["quote"].strip()):
                    valid = False
                    break
                start, end = ref.get("line_start"), ref.get("line_end")
                if ((start is not None and (type(start) is not int or start < 1))
                        or (end is not None and (type(end) is not int or start is None or end < start))):
                    valid = False
                    break
                material = materials[ref["material_id"]]
                source_text = material.get("text")
                quote = ref["quote"].replace("\r\n", "\n")
                if not isinstance(source_text, str):
                    valid = False
                    break
                lines = source_text.replace("\r\n", "\n").splitlines()
                if ((ref.get("snapshot_digest") is not None
                     and ref["snapshot_digest"] != material.get("snapshot_digest"))
                        or (end is not None and end > len(lines))
                        or quote not in ("\n".join(lines[start - 1:(end or start)])
                                         if start is not None else source_text.replace("\r\n", "\n"))):
                    valid = False
                    break
                ref["snapshot_digest"] = material.get("snapshot_digest")
        statuses = [check.get("status") for check in checks if isinstance(check, dict)]
        if (not valid or (review["outcome"] == "passed" and any(s != "pass" for s in statuses))
                or (review["outcome"] == "failed" and "fail" not in statuses)):
            continue
        groups.setdefault((review["route_id"], review["task_id"]), []).append(review)
    selected = []
    for reviews in groups.values():
        if len(reviews) == 1:
            selected.extend(reviews)
            continue
        sequences = [review.get("sequence") for review in reviews]
        if all(type(seq) is int and seq >= 1 for seq in sequences) and len(set(sequences)) == len(sequences):
            selected.append(max(reviews, key=lambda review: review["sequence"]))
    return sorted(selected, key=lambda review: (review["route_id"], review["task_id"], review["id"]))


def _task_observations(row: dict, reviews: list[dict], anchors: dict) -> tuple[list[dict], list[dict], list[dict]]:
    observations, passes, failures = [], [], []
    for review in reviews:
        if row["id"] not in review["requirement_ids"]:
            continue
        anchor = anchors.get(review.get("anchor_id"))
        checks = review["acceptance_results"]
        accepted = set(anchor["anchor"].get("acceptance", [])) if anchor else set()
        is_task_observation = (
            row["parse_status"] != "unresolved" and row["kind"] not in {"qualification", "administrative"}
            and review["task_kind"] in {"verification", "practice"} and review["subject"] == "candidate"
            and review["method"] in {"self_task_review", "independent_task_review", "runtime_check"}
            and anchor is not None and anchor["skill_id"] in row["skills"]
            and review.get("skill_id") == anchor["skill_id"] and bool(accepted)
            and all(check["criterion"] in accepted for check in checks))
        observation = {key: deepcopy(value) for key, value in review.items()
                       if key not in {"compatible", "schema_version"}}
        observation["assessment_id"] = review["id"]
        observation["candidate_task_observed"] = is_task_observation
        full_pass = review["outcome"] == "passed" and {check["criterion"] for check in checks} == accepted
        observation["candidate_task_result"] = (
            ("passed" if full_pass else "failed" if review["outcome"] == "failed" else "pending")
            if is_task_observation else None)
        observation["limits"] = ["结论只限本任务、已检查验收项、环境及协助；不证明整条岗位要求或技能等级。",
                                 *deepcopy(review.get("limits", []))]
        observations.append(observation)
        if not is_task_observation:
            continue
        if full_pass:
            passes.append(deepcopy(observation))
        if review["outcome"] == "failed":
            for check in checks:
                if check["status"] == "fail":
                    failures.append({**deepcopy(observation), "criterion": check["criterion"],
                                     "evidence_refs": deepcopy(check["evidence_refs"])})
    return observations, passes, failures


def _model_suggestions(suggestions: list[dict], requirements: dict, evidence: dict) -> dict:
    by_requirement = {}
    for suggestion in suggestions:
        if set(suggestion) != {"requirement_id", "evidence_id", "relation", "reason"}:
            raise ValueError("invalid_model_suggestion_fields")
        rid, eid = suggestion["requirement_id"], suggestion["evidence_id"]
        if (rid not in requirements or eid not in evidence or suggestion["relation"] not in {"related", "transferable"}
                or not isinstance(suggestion["reason"], str) or not suggestion["reason"].strip()):
            raise ValueError("invalid_model_suggestion_reference")
        by_requirement.setdefault(rid, []).append(deepcopy(suggestion))
    return {rid: sorted(items, key=lambda item: (item["evidence_id"], item["relation"], item["reason"]))
            for rid, items in by_requirement.items()}


def build_diagnosis(*, target: dict, jobs: list[dict], rubrics: dict, evidence: dict,
                    assessments: list[dict] | None = None, model_suggestions: list[dict] | None = None) -> dict:
    """Build reproducible associations from supplied snapshots, without I/O.

    Callers own snapshot/quote authenticity and target selection. This function
    owns domain mapping, scoped conditions, evidence authority and diagnosis
    states. All missing measurements remain null; model suggestions are visible
    annotations and never change a deterministic state.
    """
    assessments = assessments or []
    skills = _unique(rubrics.get("skills", []), "skill")
    materials = _unique(evidence.get("materials", []), "material")
    evidence_items = _unique(evidence.get("evidence", []), "evidence")
    _unique(jobs, "job")
    rows = parse_requirements(jobs)
    requirements = _unique(rows, "requirement")
    suggestions = _model_suggestions(model_suggestions or [], requirements, evidence_items)
    anchors = {}
    for sid, skill in skills.items():
        for anchor in skill.get("anchors", []):
            aid = anchor.get("id")
            if not aid or aid in anchors:
                raise ValueError("invalid_or_duplicate_anchor_id")
            anchors[aid] = {"skill_id": sid, "anchor": anchor}
    reviews = _task_reviews(assessments, target, evidence.get("assessment_artifacts", {}))
    records = [_evidence_info(item, materials) for _, item in sorted(evidence_items.items())]
    counts = dict.fromkeys(STATUSES, 0)
    for row in rows:
        row["skills"] = [sid for sid in row["skills"] if sid in skills]
        relevant = [record for record in records if set(row["skills"]) & set(record["mapped"]["skills"])]
        coverage, atom_results = _coverage(row["condition_tree"], relevant, set(skills))
        failed = _observed_failures(row, assessments, anchors,
                                   {record["item"]["id"] for record in records if not record["weak"]})
        observations, passes, task_failures = _task_observations(row, reviews, anchors)
        failed.extend(task_failures)
        if row["parse_status"] == "unresolved":
            status = "unresolved"
        elif row["kind"] in {"qualification", "administrative"}:
            status = "qualification_unknown"
        elif not row["skills"]:
            status = "uncovered"
        elif failed:
            status = "observed_task_gap"
        elif coverage is True:
            status = "related_evidence"
        elif any(not record["weak"] for record in relevant):
            status = "transferable_evidence"
        elif relevant:
            status = "evidence_missing"
        else:
            status = "needs_verification"
        action_kind, action_reason = _ACTIONS[status]
        recommended = []
        if status in {"needs_verification", "transferable_evidence", "observed_task_gap"}:
            for sid in row["skills"]:
                available = skills[sid].get("anchors", [])
                anchor = next((item for item in available if item.get("stage") == "guided"), None)
                if anchor is None and available:
                    anchor = available[0]
                if anchor:
                    recommended.append(anchor["id"])
        passed_anchors = {item["anchor_id"] for item in passes} - {item["anchor_id"] for item in failed}
        recommended = [aid for aid in recommended if aid not in passed_anchors]
        row.update(
            status=status, reason_zh=_REASONS[status], candidate_level=None, skill_level_verified=False,
            verified_capability_gap=bool(failed), related_conditions_supported=coverage,
            condition_observations=atom_results, observed_task_failures=failed,
            observed_task_passes=passes, task_observations=observations,
            evidence_refs=[{"evidence_id": record["item"]["id"],
                            "relation": "related" if coverage is True else "transferable",
                            "source_refs": deepcopy(record["item"].get("source_refs", [])),
                            "ownership": record["item"].get("ownership"),
                            "support_type": record["item"].get("support_type"),
                            "does_not_prove": deepcopy(record["item"].get("does_not_prove", [])),
                            "source_valid": record["source_valid"]} for record in relevant],
            next_action={"kind": action_kind, "reason_zh": action_reason, "anchor_ids": recommended},
            model_suggestions=suggestions.get(row["id"], []),
        )
        counts[status] += 1
    used_skills = {sid for row in rows for sid in row["skills"]}
    referenced_assessments = [*reviews, *(item for item in assessments if isinstance(item, dict)
                                          and "schema_version" not in item and "task_id" not in item)]
    return {
        "schema_version": "career-diagnosis-v1", "algorithm_version": ALGORITHM_VERSION,
        "target": deepcopy(target), "requirements": rows,
        "summary": {"status_counts": counts, "requirement_count": len(rows),
                    "required_count": sum(row["strength"] == "required" for row in rows),
                    "preferred_count": sum(row["strength"] == "preferred" for row in rows)},
        "skills": [{"id": sid, "name_zh": skills[sid].get("name_zh"), "level": None,
                    "assessment_refs": sorted({str(item["id"]) for item in referenced_assessments if item.get("id")
                                              and item.get("skill_id") == sid})} for sid in sorted(used_skills)],
        "boundaries": ["材料引用存在只证明该记录可回源，不证明全部岗位要求已满足。",
                       "相关或可迁移经历不等于任务通过；缺少观察不等于能力为零。",
                       "技能等级未测，资格单独核对；模型建议不改变规则状态。",
                       "任务通过只关闭所验收的任务，不将整条岗位要求、资格或技能等级标为通过。",
                       *deepcopy(evidence.get("boundaries", []))],
        "input_refs": {"evidence_fingerprint": evidence.get("fingerprint"),
                       "target_revision": target.get("revision"), "job_ids": sorted(str(job["id"]) for job in jobs),
                       "rubric_version": rubrics.get("schema_version", rubrics.get("version"))},
    }
