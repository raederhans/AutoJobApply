"""Synchronous M1/M2 application service shared by the CLI and future UI."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from pathlib import Path

from applypilot.career.evidence import digest, load_evidence, require_current
from applypilot.storage import career as store
from applypilot.storage.transactions import write_transaction

ENGINE_VERSION = "career-rules-5"


def import_evidence(conn: sqlite3.Connection, registry: Path | str, *, allowed_roots: list[Path | str] | None = None) -> dict:
    evidence = load_evidence(registry, allowed_roots=allowed_roots)
    snapshot_id = store.put_snapshot(conn, kind="candidate_evidence", source_id=str(Path(registry).resolve()),
                                     payload=evidence, raw_bytes=json.dumps(evidence, ensure_ascii=False).encode("utf-8"))
    return {"evidence_snapshot_id": snapshot_id, "fingerprint": evidence["fingerprint"],
            "materials": len(evidence["materials"]), "evidence": len(evidence["evidence"]),
            "references_checked": sum(len(e["source_refs"]) for e in evidence["evidence"]),
            "measured_skill_levels": 0}


def create_target(conn: sqlite3.Connection, *, evidence_snapshot_id: str, job_ids: list[str] | None = None,
                  company_id: str | None = None, role_family: str | None = None, region: str | None = None,
                  employment_track: str | None = None, native_level: str | None = None,
                  target_id: str | None = None, label: str = "", expected_revision: int | None = None) -> dict:
    source = store.get_snapshot(conn, evidence_snapshot_id)
    if source is None or source["kind"] != "candidate_evidence":
        raise ValueError("candidate_evidence_snapshot_required")
    require_current(source["payload"])
    catalog_id = store.get_catalog_id(conn)
    if not catalog_id:
        raise ValueError("career_catalog_missing: import the research bundle first")
    jobs = []
    if job_ids:
        for jid in sorted(set(job_ids)):
            job = store.get_job(conn, jid)
            if job is None:
                raise ValueError(f"unknown_research_job: {jid}")
            jobs.append(job)
        if len(jobs) != 1:
            raise ValueError("specific_job_target_requires_one_job: create separate comparable targets")
        job = jobs[0]
        if ((company_id and company_id != job["company_id"])
                or (role_family and role_family != job["role_family"])
                or (region and region not in job.get("locations", []))
                or (employment_track and employment_track != job.get("employment_type"))
                or (native_level and native_level != job.get("native_level"))):
            raise ValueError("specific_job_scope_conflict")
        mode = "specific_job"
    else:
        if not all([company_id, role_family, region, employment_track, native_level]):
            raise ValueError("company_target_requires_company_role_region_track_and_native_level")
        jobs = [j for j in store.list_jobs(conn, company_id=company_id, role_family=role_family)
                if region in j.get("locations", [])
                and j.get("employment_type") == employment_track and j.get("native_level") == native_level]
        mode = "company_direction"
    if not jobs:
        raise ValueError("target_has_no_matching_samples: use exact catalog scope or a specific job")
    bindings = {"catalog_id": catalog_id, "evidence_snapshot_id": evidence_snapshot_id,
                "job_ids": [j["id"] for j in jobs],
                "job_snapshots": {j["id"]: j["source_snapshot_id"] for j in jobs}}
    target = {"mode": mode, "label": label or jobs[0]["title"], "company_id": company_id or jobs[0]["company_id"],
              "role_family": role_family or jobs[0]["role_family"], "region": region,
              "employment_track": employment_track, "native_level": native_level,
              "bindings": bindings, "status": "active"}
    fingerprint = digest(target)
    target["target_id"] = target_id or "target-" + fingerprint[:24]
    target["input_fingerprint"] = fingerprint
    existing = store.get_target(conn, target["target_id"])
    if existing and existing["input_fingerprint"] == fingerprint and expected_revision is None:
        return existing
    return store.save_target(conn, target, expected_revision=expected_revision)


def _inputs(conn: sqlite3.Connection, target: dict) -> tuple[list[dict], dict, dict]:
    bindings = target["bindings"]
    if store.get_catalog_id(conn) != bindings["catalog_id"]:
        raise ValueError("input_changed: a new catalog exists; refresh the target")
    source = store.get_snapshot(conn, bindings["evidence_snapshot_id"])
    if source is None or source["kind"] != "candidate_evidence":
        raise ValueError("evidence_source_unavailable")
    evidence = source["payload"]
    require_current(evidence)
    jobs = []
    for jid, snapshot in bindings["job_snapshots"].items():
        latest = store.get_job(conn, jid)
        if latest is None or latest["source_snapshot_id"] != snapshot:
            raise ValueError("input_changed: a job description changed; refresh the target")
        jobs.append(latest)
    return jobs, store.load_catalog(conn, bindings["catalog_id"]), evidence


def _check_db_versions(conn: sqlite3.Connection, target: dict) -> None:
    current = store.get_target(conn, target["target_id"])
    if (current is None or current["revision"] != target["revision"]
            or current["input_fingerprint"] != target["input_fingerprint"]):
        raise ValueError("diagnosis inputs changed: target revision")
    bindings = target["bindings"]
    if store.get_catalog_id(conn) != bindings["catalog_id"]:
        raise ValueError("input_changed: catalog changed during computation")
    for jid, version in bindings["job_snapshots"].items():
        job = store.get_job(conn, jid)
        if job is None or job["source_snapshot_id"] != version:
            raise ValueError("input_changed: job changed during computation")


def diagnose(conn: sqlite3.Connection, target_id: str, *, use_model: bool = False,
             retry_unknown: bool = False, recompute: bool = False, client=None, publication_guard=None) -> dict:
    from applypilot.career.catalog import MAPPING_VERSION
    from applypilot.career.diagnosis import ALGORITHM_VERSION, build_diagnosis
    from applypilot.career.learning import active_assessments
    from applypilot.career.requirements import PARSER_VERSION

    target = store.get_target(conn, target_id)
    if target is None:
        raise ValueError("target_not_found")
    jobs, rubrics, evidence = _inputs(conn, target)
    active = active_assessments(conn, target)
    mode = "rules_with_model_suggestions" if use_model else "rules_only"
    manifest = {"engine_version": ENGINE_VERSION, "algorithm_version": ALGORITHM_VERSION,
                "parser_version": PARSER_VERSION, "mapping_version": MAPPING_VERSION,
                "mode": mode, "target_revision": target["revision"],
                "target_fingerprint": target["input_fingerprint"], "bindings": target["bindings"],
                "evidence_fingerprint": evidence["fingerprint"], "assessment_fingerprint": active["fingerprint"]}
    input_digest = digest(manifest)
    suggestions, model_meta = [], None
    if use_model:
        from applypilot.career.model import suggest
        owned_client = client is None
        if client is None:
            from applypilot.llm import create_client
            # Resolve this invocation's environment; a singleton could retain
            # another workspace's model configuration in a long-lived process.
            client = create_client(task="career")
        try:
            suggestions, model_meta = suggest(conn, target=target, input_digest=input_digest, jobs=jobs,
                                              evidence=evidence, client=client, retry_unknown=retry_unknown)
        finally:
            if owned_client:
                client.close()
        # Cache identity includes actual model output; reused is execution metadata only.
        manifest["model"] = {k: v for k, v in model_meta.items() if k != "response_reused"}
        manifest["suggestions_digest"] = digest(suggestions)
        input_digest = digest(manifest)
    run_id = "career-run-" + input_digest
    old = next((d for d in store.list_diagnoses(conn, target_id=target_id) if d.get("run_id") == run_id), None)
    if old and not recompute:
        _inputs(conn, target)
        with publication_guard.publication() if publication_guard else nullcontext(), write_transaction(conn):
            _check_db_versions(conn, target)
            if active_assessments(conn, target)["fingerprint"] != active["fingerprint"]:
                raise ValueError("input_changed: task assessments changed during computation")
            if publication_guard:
                publication_guard.record_result({"target_id": target_id, "diagnosis_id": old["diagnosis_id"]})
            return old
    evidence = {**evidence, "assessment_artifacts": active["artifacts"]}
    result = build_diagnosis(target=target, jobs=jobs, rubrics=rubrics, evidence=evidence,
                             model_suggestions=suggestions, assessments=active["assessments"])
    anchors = {a["id"]: {**a, "skill_name": s["name_zh"]} for s in rubrics["skills"] for a in s["anchors"]}
    for row in result["requirements"]:
        action = row.get("next_action", {})
        action["tasks"] = [anchors[aid] for aid in action.get("anchor_ids", [])]
    result.update({"target": target, "engine_version": ENGINE_VERSION, "mode": mode,
                   "input_manifest": manifest, "input_digest": input_digest,
                   "evidence": evidence, "model": manifest.get("model"),
                   "assessment_context": {"fingerprint": active["fingerprint"],
                                          "active_ids": [a["assessment_id"] for a in active["assessments"]],
                                          "excluded": active["excluded"]},
                   "job_contexts": [{k: v for k, v in j.items() if k not in {"body_text", "requirements"}}
                                    for j in jobs]})
    # No write transaction spans model calls or parsing. Re-read every external
    # material and DB binding immediately before the target CAS publication.
    _inputs(conn, target)
    with publication_guard.publication() if publication_guard else nullcontext(), write_transaction(conn):
        _check_db_versions(conn, target)
        if active_assessments(conn, target)["fingerprint"] != active["fingerprint"]:
            raise ValueError("input_changed: task assessments changed during computation")
        saved = store.save_diagnosis(conn, result, target_id=target_id, input_revision=target["revision"],
                                    input_fingerprint=target["input_fingerprint"], run_id=run_id)
        if publication_guard:
            publication_guard.record_result({"target_id": target_id, "diagnosis_id": saved["diagnosis_id"]})
        return saved


def read_diagnosis(conn: sqlite3.Connection, diagnosis_id: str) -> dict:
    from applypilot.career.learning import active_assessments

    result = store.get_diagnosis(conn, diagnosis_id)
    if result is None:
        raise ValueError("diagnosis_not_found")
    reasons = []
    if result.get("engine_version") != ENGINE_VERSION:
        reasons.append("diagnosis_engine_version_changed")
    if result.get("mode") == "rules_with_model_suggestions":
        from applypilot.career.model import SUGGESTION_VALIDATION_VERSION
        if (result.get("model") or {}).get("validation_version") != SUGGESTION_VALIDATION_VERSION:
            reasons.append("model_suggestion_validation_changed")
    try:
        _inputs(conn, result["target"])
    except (ValueError, OSError) as exc:
        reasons.append(str(exc))
    current = store.get_target(conn, result["target"]["target_id"])
    if current is None or current["revision"] != result["target"]["revision"]:
        reasons.append("target_revision_changed")
    if current and active_assessments(conn, current)["fingerprint"] != result.get("input_manifest", {}).get("assessment_fingerprint"):
        reasons.append("task_assessments_changed")
    return {**result, "stale": bool(reasons), "stale_reasons": reasons}
