"""Learning orchestration with versioned inputs and explicit task observations."""

from __future__ import annotations

from collections import Counter
from contextlib import nullcontext
from pathlib import Path

from applypilot.career.assessment import validate_review
from applypilot.career.contracts import InputChangedError, json_text, required_text
from applypilot.career.evidence import combine_evidence, digest, load_evidence, require_current
from applypilot.storage import career as store
from applypilot.storage import career_learning as learning_store
from applypilot.storage.transactions import write_transaction


def _route(conn, route_id):
    route = learning_store.get_route(conn, route_id)
    if route is None:
        raise ValueError("learning_route_not_found")
    return route


def _task(route, task_id):
    task = next((t for t in route["tasks"] if t["task_id"] == task_id), None)
    if task is None:
        raise ValueError("learning_task_not_found")
    return task


def _fold(route, events, assessments):
    indexed = {a["assessment_id"]: a for a in assessments}
    states = {t["task_id"]: {**t, "state": "pending", "submission": None, "assessment": None}
              for t in route["tasks"]}
    for event in events:
        row = states[event["task_id"]]
        kind = event["event_type"]
        if kind == "submitted":
            row.update(state="submitted", submission=event, assessment=None)
        elif kind in {"reopened", "skipped"}:
            row.update(state="pending" if kind == "reopened" else "skipped", submission=None, assessment=None)
        elif kind == "started":
            row["state"] = "in_progress"
        elif kind == "marked_done":
            row["state"] = "completion_reported"
        elif kind == "assessment_recorded":
            assessment = indexed.get(event["payload"].get("assessment_id"))
            if assessment and row["submission"] and assessment["submission_event_id"] == row["submission"]["event_id"]:
                row["assessment"] = assessment
                result = assessment["outcome"]
                can_close = row["kind"] == "evidence_collection" or assessment["subject"] == "candidate"
                row["state"] = ("passed" if can_close else "completion_reported") if result == "passed" else (
                    "needs_revision" if result == "failed" else "submitted")
    return states


def _same_task_contract(left, right):
    def scope(task):
        return "task_observation" if task.get("kind") in {"verification", "practice"} else task.get("kind")

    return scope(left) == scope(right) and all(left.get(k) == right.get(k) for k in (
        "skill_id", "anchor_id", "acceptance", "context", "assistance_policy", "requirement_ids"))


def active_assessments(conn, target):
    """Authenticate current observations; historical or edited artifacts stay historical."""
    selected, artifacts, excluded, resets = [], {}, [], []
    assessment_sequences = {}
    routes = {r["route_id"]: r for r in learning_store.list_routes(conn, target["target_id"])}
    for route in routes.values():
        records = learning_store.list_assessments(conn, route_id=route["route_id"])
        events = learning_store.list_events(conn, route["route_id"])
        assessment_sequences.update({e["payload"]["assessment_id"]: e["sequence"] for e in events
                                     if e["event_type"] == "assessment_recorded"})
        bindings_match = route["target_revision"] == target["revision"] and all(
            route[k] == target["bindings"][k] for k in ("catalog_id", "evidence_snapshot_id"))
        if bindings_match:
            ancestor, ancestors = route.get("parent_route_id"), set()
            while ancestor and ancestor not in ancestors:
                ancestors.add(ancestor)
                ancestor = routes[ancestor].get("parent_route_id")
            for event in events:
                if event["event_type"] in {"reopened", "skipped", "submitted"}:
                    resets.append((route, event, ancestors))
        states = _fold(route, events, records)
        latest = {row["assessment"]["assessment_id"] for row in states.values() if row["assessment"]}
        for record in records:
            reason = None
            if not bindings_match:
                reason = "target_binding_changed"
            elif record["assessment_id"] not in latest:
                reason = "superseded_or_reopened"
            else:
                try:
                    snapshot = store.get_snapshot(conn, record["artifact_evidence_snapshot_id"])
                    if snapshot is None or snapshot["kind"] != "candidate_evidence":
                        raise ValueError("artifact_unavailable")
                    artifact = snapshot["payload"]
                    require_current(artifact)
                    normalized = validate_review(task=_task(route, record["task_id"]), review=record, artifact=artifact)
                    if normalized["outcome"] != record["outcome"]:
                        raise ValueError("assessment_outcome_changed")
                    artifacts[record["artifact_evidence_snapshot_id"]] = artifact
                    selected.append({**record, "compatible": True})
                except (ValueError, OSError, KeyError) as exc:
                    reason = str(exc)
            if reason:
                excluded.append({"assessment_id": record["assessment_id"], "reason": reason})
    selected.sort(key=lambda a: a["sequence"])
    resets.sort(key=lambda item: item[1]["sequence"])
    retained = []
    for record in selected:
        current_task = _task(routes[record["route_id"]], record["task_id"])
        # An explicit reset in a descendant takes precedence even before that
        # route has its own review. Otherwise replanning revives an inherited pass.
        reset = next((event for child, event, ancestors in reversed(resets)
                      if record["route_id"] in ancestors and event["task_id"] == record["task_id"]
                      and event["sequence"] > assessment_sequences[record["assessment_id"]]
                      and _same_task_contract(current_task, _task(child, record["task_id"]))), None)
        if reset:
            excluded.append({"assessment_id": record["assessment_id"], "reason": "reset_in_child_route",
                             "event_id": reset["event_id"]})
            continue
        descendant = routes[record["route_id"]].get("parent_route_id")
        ancestors = set()
        while descendant and descendant not in ancestors:
            ancestors.add(descendant)
            descendant = routes[descendant].get("parent_route_id")
        for prior in list(retained):
            if (prior["route_id"] not in ancestors or prior["task_id"] != record["task_id"]
                    or prior["subject"] != record["subject"] or prior["method"] != record["method"]):
                continue
            prior_task = _task(routes[prior["route_id"]], prior["task_id"])
            if _same_task_contract(current_task, prior_task):
                retained.remove(prior)
                excluded.append({"assessment_id": prior["assessment_id"], "reason": "superseded_in_child_route"})
        retained.append(record)
    selected = retained
    active_artifact_ids = {a["artifact_evidence_snapshot_id"] for a in selected}
    artifacts = {k: v for k, v in artifacts.items() if k in active_artifact_ids}
    # Every persisted assessment projection participates in cache identity.
    # Reopening a task can return active observations to empty while exclusions
    # differ from the original diagnosis; reusing that run ID would conflict.
    identity = {"assessments": selected, "artifacts": {k: v["fingerprint"] for k, v in sorted(artifacts.items())}}
    if excluded:
        identity["excluded"] = excluded
    return {"assessments": selected, "artifacts": artifacts, "fingerprint": digest(identity), "excluded": excluded}


def create_route(conn, target_id, *, diagnosis_id=None, parent_route_id=None, weekly_hours=None,
                 weeks=4, preferred_skills=None, selected_anchor_ids=None, publication_guard=None):
    from applypilot.career import service
    from applypilot.career.planner import build_route

    if diagnosis_id is None:
        diagnosis_id = service.diagnose(conn, target_id)["diagnosis_id"]
    diagnosis = service.read_diagnosis(conn, diagnosis_id)
    if diagnosis["target_id"] != target_id or diagnosis.get("stale"):
        raise InputChangedError("learning_requires_current_target_diagnosis")
    target = diagnosis["target"]
    _, rubrics, _ = service._inputs(conn, target)
    active = active_assessments(conn, target)
    if active["fingerprint"] != diagnosis["input_manifest"].get("assessment_fingerprint"):
        raise InputChangedError("learning_assessments_changed")
    route = build_route(diagnosis, rubrics, weekly_hours=weekly_hours, weeks=weeks,
                        preferred_skills=preferred_skills, selected_anchor_ids=selected_anchor_ids,
                        prior_task_observations=active["assessments"])
    route.update(target_id=target_id, target_revision=target["revision"], target_label=target["label"],
                 catalog_id=target["bindings"]["catalog_id"], evidence_snapshot_id=target["bindings"]["evidence_snapshot_id"],
                 planning_options={"weekly_hours": weekly_hours, "weeks": weeks,
                                   "preferred_skills": preferred_skills, "selected_anchor_ids": selected_anchor_ids})
    with publication_guard.publication() if publication_guard else nullcontext(), write_transaction(conn):
        service._check_db_versions(conn, target)
        service._inputs(conn, target)
        if active_assessments(conn, target)["fingerprint"] != active["fingerprint"]:
            raise InputChangedError("learning_assessments_changed")
        saved = learning_store.save_route(conn, route, parent_route_id=parent_route_id)
        if publication_guard:
            publication_guard.record_result({"target_id": target_id, "route_id": saved["route_id"],
                                             "diagnosis_id": saved["diagnosis_id"]})
        return saved


def read_progress(conn, route_id):
    from applypilot.career import service

    route = _route(conn, route_id)
    target = store.get_target(conn, route["target_id"])
    reasons = []
    if target is None or target["revision"] != route["target_revision"]:
        reasons.append("target_revision_changed")
    elif any(target["bindings"][k] != route[k] for k in ("catalog_id", "evidence_snapshot_id")):
        reasons.append("target_binding_changed")
    if not reasons:
        try:
            service._inputs(conn, target)
        except (ValueError, OSError) as exc:
            reasons.append(str(exc))
    events = learning_store.list_events(conn, route_id)
    assessments = learning_store.list_assessments(conn, route_id=route_id)
    states = _fold(route, events, assessments)
    active = active_assessments(conn, target) if target else {"assessments": []}
    valid_ids = {a["assessment_id"] for a in active["assessments"]}
    for row in states.values():
        assessment = row["assessment"]
        if assessment and assessment["assessment_id"] not in valid_ids:
            row["assessment_current"] = False
            row["historical_state"] = row["state"]
            row["state"] = "submitted"
        else:
            row["assessment_current"] = assessment is not None
    # Replanned tasks inherit only an exact reviewed task under identical input
    # bindings. History remains linked and any new event takes precedence.
    event_tasks = {e["task_id"] for e in events}
    for record in reversed(active["assessments"]):
        tid = record["task_id"]
        if tid not in states or tid in event_tasks or states[tid]["assessment"]:
            continue
        prior = _route(conn, record["route_id"])
        task = _task(prior, tid)
        if not _same_task_contract(task, states[tid]):
            continue
        if record["outcome"] == "passed" and (record["subject"] == "candidate" or task["kind"] == "evidence_collection"):
            states[tid].update(state="passed", assessment=record, assessment_current=True,
                               inherited_from=record["route_id"])
    for row in states.values():
        row["blocked_by"] = [tid for tid in row["depends_on"] if states[tid]["state"] != "passed"]
        row["ready"] = not reasons and not row["blocked_by"] and row["state"] not in {"passed", "skipped"}
    diagnosis = service.read_diagnosis(conn, route["diagnosis_id"])
    return {"route": route, "tasks": list(states.values()), "revision": learning_store.route_revision(conn, route_id),
            "stale": bool(reasons), "stale_reasons": reasons, "diagnosis_refresh_needed": diagnosis["stale"],
            "summary": dict(Counter(r["state"] for r in states.values())), "history": events,
            "historical_routes": [{k: r.get(k) for k in ("route_id", "diagnosis_id", "parent_route_id", "created_at")}
                                  for r in learning_store.list_routes(conn, route["target_id"])],
            "boundaries": ["已完成仅指具体任务验收；没有测出通用技能等级。", "历史评审保留，输入改变后需要重新核对。"]}


def _fresh_task(conn, route_id, task_id, expected_revision, *, require_ready=True):
    progress = read_progress(conn, route_id)
    if progress["stale"]:
        raise InputChangedError("learning_route_stale: " + "; ".join(progress["stale_reasons"]))
    if progress["revision"] != expected_revision:
        raise InputChangedError("route revision changed")
    task = _task({"tasks": progress["tasks"]}, task_id)
    if require_ready and not task["ready"]:
        raise ValueError("learning_task_not_ready: " + ", ".join(task["blocked_by"] or [task["state"]]))
    return progress["route"], task


def record_event(conn, route_id, task_id, *, event_type, reason, event_key, expected_revision):
    if event_type not in {"started", "marked_done", "skipped", "reopened"}:
        raise ValueError("learning_event_type_invalid")
    payload = {"reason": required_text(reason, "reason")}
    with write_transaction(conn):
        previous = next((e for e in learning_store.list_events(conn, route_id) if e["event_key"] == event_key), None)
        if previous is None:
            _fresh_task(conn, route_id, task_id, expected_revision, require_ready=event_type not in {"skipped", "reopened"})
        return learning_store.append_event(conn, route_id=route_id, task_id=task_id, event_type=event_type,
                                           payload=payload, event_key=event_key, expected_revision=expected_revision)


def submit_task(conn, route_id, task_id, registry, *, allowed_roots=None, event_key, expected_revision):
    artifact = load_evidence(registry, allowed_roots=allowed_roots)
    with write_transaction(conn):
        previous = next((e for e in learning_store.list_events(conn, route_id) if e["event_key"] == event_key), None)
        if previous is None:
            _fresh_task(conn, route_id, task_id, expected_revision)
        require_current(artifact)
        snapshot_id = store.put_snapshot(conn, kind="candidate_evidence", source_id=str(Path(registry).resolve()),
                                         payload=artifact, raw_bytes=json_text(artifact).encode("utf-8"))
        return learning_store.append_event(conn, route_id=route_id, task_id=task_id, event_type="submitted",
                                           payload={"artifact_evidence_snapshot_id": snapshot_id}, event_key=event_key,
                                           expected_revision=expected_revision)


def review_task(conn, route_id, task_id, review, *, review_key, expected_revision):
    with write_transaction(conn):
        old = next((r for r in learning_store.list_assessments(conn, route_id=route_id) if r["review_key"] == review_key), None)
        if old:
            _fresh_task(conn, route_id, task_id, learning_store.route_revision(conn, route_id), require_ready=False)
            # Revalidate the submitted content even for a retry; a reused key
            # with changed criteria must never return a false success.
            task = _task(_route(conn, route_id), task_id)
            identity = {k: old[k] for k in ("route_id", "target_id", "target_revision", "catalog_id",
                                          "evidence_snapshot_id", "submission_event_id", "artifact_evidence_snapshot_id",
                                          "artifact_fingerprint")}
        else:
            route, task = _fresh_task(conn, route_id, task_id, expected_revision, require_ready=False)
            if not task["submission"]:
                raise ValueError("learning_task_requires_submission")
            if task["blocked_by"]:
                raise ValueError("learning_task_prerequisites_not_passed")
            identity = {k: route[k] for k in ("route_id", "target_id", "target_revision", "catalog_id", "evidence_snapshot_id")}
            identity.update(submission_event_id=task["submission"]["event_id"],
                            artifact_evidence_snapshot_id=task["submission"]["payload"]["artifact_evidence_snapshot_id"])
        snapshot = store.get_snapshot(conn, identity["artifact_evidence_snapshot_id"])
        require_current(snapshot["payload"])
        for key, value in identity.items():
            if key in review and review[key] != value:
                raise InputChangedError("review_binding_conflict: " + key)
        normalized = validate_review(task=task, review={**review, **identity}, artifact=snapshot["payload"])
        return learning_store.save_assessment(
            conn, normalized, review_key=review_key, validate_review=validate_review
        )


def rediagnose(conn, route_id):
    from applypilot.career import service

    route = _route(conn, route_id)
    diagnosis = service.diagnose(conn, route["target_id"], recompute=True)
    # Repeated refresh with unchanged inputs is idempotent, not an endless chain.
    if diagnosis["diagnosis_id"] == route["diagnosis_id"]:
        return {"diagnosis": diagnosis, "route": route, "progress": read_progress(conn, route_id)}
    child = create_route(conn, route["target_id"], diagnosis_id=diagnosis["diagnosis_id"], parent_route_id=route_id,
                         **route["planning_options"])
    return {"diagnosis": diagnosis, "route": child, "progress": read_progress(conn, child["route_id"])}


def supplement_materials(conn, target_id, registry, *, allowed_roots=None, expected_revision, parent_route_id=None):
    from applypilot.career import service

    target = store.get_target(conn, target_id)
    if target is None or target["revision"] != expected_revision:
        raise InputChangedError("target revision changed")
    _, _, base = service._inputs(conn, target)
    addition = load_evidence(registry, allowed_roots=allowed_roots)
    combined = combine_evidence(base, addition)
    parent = _route(conn, parent_route_id) if parent_route_id else None
    if parent and parent["target_id"] != target_id:
        raise ValueError("supplement_parent_target_mismatch")
    with write_transaction(conn):
        service._check_db_versions(conn, target)
        require_current(combined)
        if combined["fingerprint"] != base["fingerprint"]:
            snapshot_id = store.put_snapshot(conn, kind="candidate_evidence", source_id="supplement:" + target_id,
                                             payload=combined, raw_bytes=json_text(combined).encode("utf-8"))
            service.create_target(conn, target_id=target_id, evidence_snapshot_id=snapshot_id,
                                  expected_revision=expected_revision, label=target["label"],
                                  job_ids=target["bindings"]["job_ids"] if target["mode"] == "specific_job" else None,
                                  **{k: target[k] for k in ("company_id", "role_family", "region", "employment_track", "native_level")})
        diagnosis = service.diagnose(conn, target_id)
        child = create_route(conn, target_id, diagnosis_id=diagnosis["diagnosis_id"], parent_route_id=parent_route_id,
                             **(parent["planning_options"] if parent else {}))
    return {"diagnosis": diagnosis, "route": child, "progress": read_progress(conn, child["route_id"]),
            "added_materials": len(combined["materials"]) - len(base["materials"]),
            "added_evidence": len(combined["evidence"]) - len(base["evidence"])}
