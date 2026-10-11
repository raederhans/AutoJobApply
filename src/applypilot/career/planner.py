"""Pure, deterministic task DAG planning from a diagnosis and authored rubrics."""

from __future__ import annotations

import math
from copy import deepcopy

from .contracts import content_id
from .resources import RESOURCE_VERSION, resources_for

PLANNER_VERSION = "career-route-rules-v2"
_EVIDENCE_STATUSES = {"evidence_missing", "related_evidence", "transferable_evidence"}
_STATUSES = _EVIDENCE_STATUSES | {
    "needs_verification", "observed_task_gap", "qualification_unknown", "unresolved", "uncovered",
}
_TASK_METHODS = {"task_review", "self_task_review", "independent_task_review", "runtime_check"}
_STRENGTH = {"required": 20, "responsibility": 40, "mixed": 20, "preferred": 60}


def _index(items: list[dict], label: str) -> dict[str, dict]:
    result = {}
    for item in items:
        key = item.get("id")
        if not isinstance(key, str) or not key or key in result:
            raise ValueError(f"invalid_or_duplicate_{label}_id")
        result[key] = item
    return result


def _catalog(rubrics: dict) -> tuple[dict, dict, dict]:
    skills = _index(rubrics.get("skills", []), "skill")
    anchors, guided = {}, {}
    for sid, skill in skills.items():
        for anchor in skill.get("anchors", []):
            aid = anchor.get("id")
            if not isinstance(aid, str) or not aid or aid in anchors:
                raise ValueError("invalid_or_duplicate_anchor_id")
            for field in ("task", "context", "deliverable", "support"):
                if not isinstance(anchor.get(field), str) or not anchor[field].strip():
                    raise ValueError(f"missing_anchor_{field}:{aid}")
            if (not isinstance(anchor.get("acceptance"), list) or not anchor["acceptance"]
                    or any(not isinstance(c, str) or not c for c in anchor["acceptance"])):
                raise ValueError(f"invalid_anchor_acceptance:{aid}")
            anchors[aid] = (sid, anchor)
            if anchor.get("stage") == "guided":
                if sid in guided:
                    raise ValueError(f"duplicate_guided_anchor:{sid}")
                guided[sid] = aid
        learning_id = skill.get("learning_task", {}).get("anchor_id")
        if learning_id is not None and (learning_id not in anchors or anchors[learning_id][0] != sid):
            raise ValueError(f"missing_learning_anchor:{sid}")
    # Validate even unused catalog dependencies: an invalid catalog cannot be a
    # reproducible route source. Stage labels never imply a within-skill ladder.
    visited, visiting = set(), set()

    def visit(sid):
        if sid in visiting:
            raise ValueError(f"prerequisite_cycle:{sid}")
        if sid in visited:
            return
        visiting.add(sid)
        deps = skills[sid].get("prerequisites", [])
        if not isinstance(deps, list):
            raise TypeError(f"invalid_prerequisites:{sid}")
        for dep in deps:
            if dep not in skills:
                raise ValueError(f"missing_prerequisite_skill:{dep}")
            if dep not in guided:
                raise ValueError(f"missing_guided_anchor:{dep}")
            visit(dep)
        visiting.remove(sid)
        visited.add(sid)

    for sid in sorted(skills):
        visit(sid)
    return skills, anchors, guided


def _tree_skills(node: dict) -> set[str]:
    return set(node.get("skills", [])) | set().union(
        *(_tree_skills(child) for child in node.get("children", [])))


def _select_conditions(row: dict, explicit: set[str], preferred: set[str], choices: list[dict]) -> dict:
    """Choose a learning branch, preserving alternatives and technology scope."""
    observations = row.get("condition_observations", [])

    def related(node):
        if node.get("op") == "atom":
            return any(item.get("text") == node.get("text")
                       and item.get("related_conditions_supported") is True for item in observations)
        values = [related(child) for child in node.get("children", [])]
        if node.get("op") == "all":
            return bool(values) and all(values)
        return sum(values) >= node.get("k", 1)

    def walk(node, inherited, path):
        strength = node.get("strength") or inherited
        op = node.get("op")
        if op == "atom":
            return {sid: strength for sid in node.get("skills", [])}
        if op not in {"all", "any", "at_least"}:
            raise ValueError(f"unsupported_condition_tree:{row['id']}:{op}")
        children = node.get("children", [])
        indices = list(range(len(children)))
        if op in {"any", "at_least"}:
            count = node.get("k", 1)
            if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= len(children):
                raise ValueError(f"invalid_alternative_count:{row['id']}")
            ranking = sorted(indices, key=lambda i: (
                -bool(_tree_skills(children[i]) & explicit), -related(children[i]),
                -bool(_tree_skills(children[i]) & preferred), i))
            indices = sorted(ranking[:count])
            choices.append({
                "requirement_id": row["id"], "path": path, "op": op, "k": count,
                "selected_children": indices,
                "alternatives": [{"index": i, "condition": deepcopy(child)} for i, child in enumerate(children)],
                "reason": "显式任务选择、已有条件证据、技能偏好后按原句顺序选取有限学习分支。",
                "limits": "这只是学习路线选择；替代条件无需全部学习，不证明该岗位条件已满足。",
            })
        selected = {}
        for i in indices:
            for sid, value in walk(children[i], strength, f"{path}.{i}").items():
                if sid not in selected or _STRENGTH.get(value, 40) < _STRENGTH.get(selected[sid], 40):
                    selected[sid] = value
        return selected

    tree = row.get("condition_tree")
    if not tree:
        return dict.fromkeys(row.get("skills", []), row.get("strength", "required"))
    return walk(tree, row.get("strength", "required"), "root")


def _priority(strength: str, status: str, sid: str | None, preferences: set[str]) -> tuple[int, list[str]]:
    priority = _STRENGTH.get(strength, 40)
    reasons = [f"requirement_strength:{strength}"]
    if status == "observed_task_gap":
        priority -= 10
        reasons.append("observed_failure:仅针对已记录失败的任务和标准补练")
    if status in _EVIDENCE_STATUSES:
        priority -= 5
        reasons.append("existing_evidence_first:先整理材料和本人贡献，避免重复学习")
    if sid in preferences:
        priority -= 2
        reasons.append("user_preferred_skill")
    return priority, reasons


def _estimate(kind: str, stage: str | None = None) -> dict:
    # Broad author estimates include setup, delivery and recording/review time.
    # They are not measured from this candidate or guaranteed course durations.
    ranges = {"qualification_review": (0.5, 2), "evidence_collection": (1, 3)}
    lower, upper = ranges.get(kind, {
        "understand": (1, 3), "guided": (3, 8), "routine": (6, 16), "adaptive": (10, 24),
    }.get(stage, (3, 8)))
    if kind == "practice":
        upper += 4
    return {"min": lower, "max": upper,
            "basis": "pilot_author_estimate_not_candidate_measured; 含环境准备、交付与验收记录；按上界保守预留。"}


def _task(task_id, kind, sid, aid, *, title, deliverable, acceptance, context, assistance, limits, stage=None):
    resources = resources_for(sid, aid) if aid else []
    return {
        "task_id": task_id, "kind": kind, "title": title, "skill_id": sid, "anchor_id": aid,
        "requirement_ids": [], "priority": 100, "priority_reasons": [], "depends_on": [],
        "deliverable": deliverable, "acceptance": deepcopy(acceptance), "context": context,
        "assistance_policy": assistance,
        "limits": [limits] if isinstance(limits, str) else deepcopy(limits),
        "estimate_hours": _estimate(kind, stage), "scheduled": False, "deferred_reason": None,
        "resources": resources,
        "resources_note": "资料仅作参考，完成阅读不等于任务通过。" if resources
        else "此任务暂无已核实且匹配的官方资料；按所给任务、支持条件与验收执行。",
    }


def _merge(task: dict, rid: str, priority: int, reasons: list[str]):
    task["requirement_ids"] = sorted(set(task["requirement_ids"]) | {rid})
    task["priority"] = min(task["priority"], priority)
    task["priority_reasons"] = sorted(set(task["priority_reasons"]) | set(reasons))


def _topological(tasks: dict) -> list[str]:
    pending, order = set(tasks), []
    for task in tasks.values():
        if not set(task["depends_on"]) <= tasks.keys():
            raise ValueError("missing_task_dependency")
    while pending:
        ready = [tid for tid in pending if not set(tasks[tid]["depends_on"]) & pending]
        if not ready:
            raise ValueError("task_dependency_cycle")
        tid = min(ready, key=lambda item: (tasks[item]["priority"], item))
        pending.remove(tid)
        order.append(tid)
    return order


def _budget(tasks: dict, order: list[str], weekly_hours: float | None, weeks: int) -> dict:
    total = weekly_hours * weeks if weekly_hours is not None else None
    if total is not None and not math.isfinite(total):
        raise ValueError("non_finite_total_budget")
    reserved = 0.0
    for tid in order:
        task = tasks[tid]
        if total is None:
            reason = "budget_unknown_order_only"
        elif any(not tasks[dep]["scheduled"] for dep in task["depends_on"]):
            reason = "dependency_deferred"
        elif task["estimate_hours"]["max"] > total - reserved:
            reason = "exceeds_remaining_budget"
        else:
            reason = None
            reserved += task["estimate_hours"]["max"]
        task["scheduled"] = reason is None
        task["deferred_reason"] = reason
    return {"mode": "budgeted" if total is not None else "order_only", "weekly_hours": weekly_hours,
            "weeks": weeks, "total_hours": total, "reserved_max_hours": reserved if total is not None else None,
            "remaining_hours": total - reserved if total is not None else None,
            "estimate_policy": "作者估计非本人测量；按任务上界预算，超额任务延后，不缩小估计或虚构日历。"}


def build_route(diagnosis: dict, rubrics: dict, *, weekly_hours: float | None = None, weeks: int = 4,
                preferred_skills: list[str] | None = None, selected_anchor_ids: list[str] | None = None,
                prior_task_observations: list[dict] | None = None) -> dict:
    """Plan within supplied snapshots; no I/O, candidate grade or hiring score.

    Callers must authenticate prior observations. Only a compatible candidate
    task pass of the exact prerequisite anchor can bypass that anchor. Passing a
    course, a different stage or a related task cannot do so. Explicit selections
    restrict target anchors; all transitive guided prerequisites remain accounted.
    """
    if not isinstance(weeks, int) or isinstance(weeks, bool) or weeks < 1:
        raise ValueError("weeks_must_be_positive_integer")
    if weekly_hours is not None and (
            isinstance(weekly_hours, bool) or not isinstance(weekly_hours, (int, float))
            or not math.isfinite(weekly_hours) or weekly_hours < 0):
        raise ValueError("weekly_hours_must_be_finite_nonnegative")
    skills, anchors, guided = _catalog(rubrics)
    preferences = set(preferred_skills or [])
    if not preferences <= skills.keys():
        raise ValueError("unknown_preferred_skill")
    explicit = set(selected_anchor_ids or [])
    if not explicit <= anchors.keys():
        raise ValueError("unknown_selected_anchor")
    explicit_skills = {anchors[aid][0] for aid in explicit}
    rows = _index(diagnosis.get("requirements", []), "requirement")
    observations = prior_task_observations or []
    passed = {}
    reviewed_evidence_skills = set()
    for obs in observations:
        aid = obs.get("anchor_id")
        if aid and aid not in anchors:
            raise ValueError("unknown_observation_anchor")
        if (aid and obs.get("outcome") == "passed" and obs.get("method") in _TASK_METHODS
                and obs.get("subject") == "candidate" and obs.get("compatible") is True):
            passed.setdefault(aid, []).append(deepcopy(obs))
        sid = obs.get("skill_id")
        if (sid in skills and obs.get("task_id") == f"evidence:{sid}"
                and obs.get("task_kind") == "evidence_collection" and aid is None
                and obs.get("outcome") == "passed" and obs.get("compatible") is True
                and obs.get("subject") in {"artifact", "candidate"}
                and obs.get("method") in _TASK_METHODS | {"artifact_review"}):
            reviewed_evidence_skills.add(sid)
    tasks, excluded, choices, bypasses = {}, [], [], []
    linked_explicit = set()

    def exclude(row, reason, **details):
        excluded.append({"requirement_id": row["id"], "reason": reason,
                         "quote": row.get("quote", row.get("text", "")), **details})

    def add_prerequisites(tid, sid, row, strength):
        for dep_sid in sorted(set(skills[sid].get("prerequisites", []))):
            dep_aid = guided[dep_sid]
            if dep_aid in passed:
                bypass = {"task_id": tid, "prerequisite_anchor_id": dep_aid,
                          "reason": "exact_compatible_candidate_task_pass",
                          "observations": sorted(passed[dep_aid], key=lambda item: content_id("observation", item)),
                          "limits": "仅豁免此 anchor 的重复前置练习，不外推整项技能或岗位胜任。"}
                if bypass not in bypasses:
                    bypasses.append(bypass)
                tasks[tid]["priority_reasons"] = sorted(set(tasks[tid]["priority_reasons"]) | {
                    f"evidence_bypass:{dep_aid}"})
                # Exact task evidence does not establish every other skill in
                # its dependency subtree. Keep those independently accounted.
                add_prerequisites(tid, dep_sid, row, strength)
            else:
                dep = add_anchor(dep_aid, row, "verification", strength, prerequisite=True)
                tasks[tid]["depends_on"] = sorted(set(tasks[tid]["depends_on"]) | {dep})

    def add_anchor(aid, row, kind, strength, *, prerequisite=False):
        sid, anchor = anchors[aid]
        tid = f"anchor:{aid}"
        priority, reasons = _priority(strength, row["status"], sid, preferences)
        if prerequisite:
            reasons.append("transitive_learning_prerequisite:学习顺序，不是资格门槛")
        if tid not in tasks:
            tasks[tid] = _task(tid, kind, sid, aid, title=anchor["task"], deliverable=anchor["deliverable"],
                               acceptance=anchor["acceptance"], context=anchor["context"],
                               assistance=anchor["support"], limits=anchor.get("limits", []), stage=anchor.get("stage"))
        if kind == "practice":
            tasks[tid]["kind"] = kind
            tasks[tid]["estimate_hours"] = _estimate(kind, anchor.get("stage"))
        _merge(tasks[tid], row["id"], priority, reasons)
        add_prerequisites(tid, sid, row, strength)
        return tid

    for rid, row in sorted(rows.items()):
        status = row.get("status")
        if status not in _STATUSES:
            raise ValueError(f"unknown_diagnosis_status:{rid}")
        recommended = row.get("next_action", {}).get("anchor_ids", [])
        if not set(recommended) <= anchors.keys():
            raise ValueError(f"missing_recommended_anchor:{rid}")
        if status in {"unresolved", "uncovered"}:
            exclude(row, "system_coverage_issue", diagnosis_status=status)
            continue
        if status == "qualification_unknown":
            if "qualification" not in tasks:
                tasks["qualification"] = _task(
                    "qualification", "qualification_review", None, None, title="核对目标的资格与申请时间条件",
                    deliverable="逐项保留原句、事实来源、替代路径及未知项的核对记录",
                    acceptance=["每条资格对应来源或明确标为未知", "替代路径和时间条件按原句核对"],
                    context="仅针对所选岗位；资格核对与技能学习分别进行。",
                    assistance="可整理资料；事实或必要文件缺失时保持未知。",
                    limits=["不认定资格已满足，不生成学习等级或录用概率。"])
            _merge(tasks["qualification"], rid, 0, ["qualification_feasibility_first",
                                                    f"requirement_strength:{row.get('strength', 'required')}"])
            continue
        selected = _select_conditions(row, explicit_skills, preferences, choices)
        unknown = sorted(set(selected) - skills.keys())
        if unknown:
            exclude(row, "system_coverage_issue", diagnosis_status="unknown_skill", skill_ids=unknown)
        known = set(selected) & skills.keys()
        if not known:
            if not unknown:
                exclude(row, "system_coverage_issue", diagnosis_status="no_mapped_selected_condition")
            continue
        if selected_anchor_ids is not None:
            chosen_aids = sorted(aid for aid in explicit if anchors[aid][0] in known)
            linked_explicit.update(chosen_aids)
            known &= {anchors[aid][0] for aid in chosen_aids}
            if not known:
                exclude(row, "outside_explicit_task_selection")
                continue
        for sid in sorted(known):
            if status in _EVIDENCE_STATUSES:
                tid = f"evidence:{sid}"
                if tid not in tasks:
                    tasks[tid] = _task(
                        tid, "evidence_collection", sid, None, title=f"整理 {skills[sid].get('name_zh', sid)} 的既有证据",
                        deliverable="材料索引、本人贡献、目标情境及尚未核对条件",
                        acceptance=["引用可回源产物并标明本人贡献", "区分相关材料与任务通过，逐项列出未知和未覆盖条件"],
                        context="先核对已有产物；缺少材料不代表不会，相关经历不代表验收通过。",
                        assistance="可由工具帮助整理，保留本人、团队与 AI 的具体贡献。",
                        limits=["证据关联不自动满足岗位全部条件，不自动跳过前置任务。"])
                priority, reasons = _priority(selected[sid], status, sid, preferences)
                _merge(tasks[tid], rid, priority, reasons)
            if (selected_anchor_ids is None and status in {"related_evidence", "evidence_missing"}
                    and sid not in reviewed_evidence_skills):
                continue
            failures = row.get("observed_task_failures", [])
            for failure in failures:
                if failure.get("anchor_id") not in anchors:
                    raise ValueError(f"missing_failed_anchor:{rid}")
                fsid, fa = anchors[failure["anchor_id"]]
                if fsid not in row.get("skills", []) or failure.get("criterion") not in fa["acceptance"]:
                    raise ValueError(f"invalid_failed_anchor_scope:{rid}")
            if selected_anchor_ids is not None:
                aids = [aid for aid in chosen_aids if anchors[aid][0] == sid]
            else:
                failed_aids = sorted({f["anchor_id"] for f in failures if anchors[f["anchor_id"]][0] == sid})
                suggested = [aid for aid in recommended if anchors[aid][0] == sid]
                aids = failed_aids or suggested or ([guided[sid]] if sid in guided else [])
            if not aids:
                exclude(row, "system_coverage_issue", diagnosis_status="missing_guided_anchor", skill_ids=[sid])
            completed = sorted(aid for aid in aids if aid in passed)
            if completed:
                exclude(row, "exact_completed_target_task", anchor_ids=completed,
                        observations=[obs for aid in completed for obs in sorted(
                            passed[aid], key=lambda item: content_id("observation", item))])
            for aid in aids:
                if aid in passed:
                    continue
                failed_here = any(f.get("anchor_id") == aid for f in failures)
                tid = add_anchor(aid, row, "practice" if failed_here else "verification", selected[sid])
                if sid in reviewed_evidence_skills:
                    tasks[tid]["priority_reasons"] = sorted(set(tasks[tid]["priority_reasons"]) | {
                        "evidence_collection_reviewed:材料验收已通过，下一步核对本人目标任务"})
                if status in _EVIDENCE_STATUSES:
                    tasks[tid]["depends_on"] = sorted(set(tasks[tid]["depends_on"]) | {f"evidence:{sid}"})
    if explicit - linked_explicit:
        raise ValueError("selected_anchor_not_in_target_conditions")
    # A skill may also enter through another target's prerequisite closure.
    # Its existing evidence collection still precedes that verification task.
    for task in tasks.values():
        eid = f"evidence:{task['skill_id']}"
        if task["anchor_id"] and eid in tasks:
            task["depends_on"] = sorted(set(task["depends_on"]) | {eid})
    order = _topological(tasks)
    budget = _budget(tasks, order, weekly_hours, weeks)
    return {
        "planner_version": PLANNER_VERSION, "diagnosis_id": diagnosis.get("diagnosis_id"),
        "target_id": diagnosis.get("target_id", diagnosis.get("target", {}).get("id")),
        "catalog_id": diagnosis.get("input_manifest", {}).get("catalog_id") or content_id("catalog", rubrics),
        "input_digest": content_id("route-input", {
            "diagnosis": diagnosis, "rubrics": rubrics, "weekly_hours": weekly_hours, "weeks": weeks,
            "preferred_skills": sorted(preferences), "selected_anchor_ids": sorted(explicit)
            if selected_anchor_ids is not None else None, "prior_task_observations": observations,
            "resource_version": RESOURCE_VERSION, "planner_version": PLANNER_VERSION,
        }),
        "tasks": [tasks[tid] for tid in order], "excluded_requirements": excluded, "budget": budget,
        "topological_order": order, "branch_choices": choices,
        "evidence_bypasses": sorted(bypasses, key=lambda item: (item["task_id"], item["prerequisite_anchor_id"])),
        "boundaries": ["任务来源为作者试点量规；没有测出通用技能等级或录用概率。",
                       "路线排序尊重依赖；资格不作为学习任务前置，OR 替代条件无需全部学习。",
                       "未知预算仅提供顺序；估时不是本人能力测量，阅读资料或完成课程不构成任务通过。"],
    }
