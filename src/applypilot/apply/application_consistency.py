"""Read-only, source-bound checks across preparation and final review.

The attending host selects mappings; it cannot provide expected fact values.
This checks explicit bindings, not free-form entailment or whole-form completeness.
Contracts contain references and digests only and never grant submission authority.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path

MAX_BINDINGS = 128
_CONTACT_KEYS = frozenset({"full_name", "preferred_display_name", "email", "phone", "address",
                          "address_line_2", "city", "province_state", "country", "postal_code"})
_RECORD_ROOTS = {"education": "education", "experience": "employment", "project_references": "project"}


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 12000:
        raise ValueError("consistency requires a nonempty bounded scalar text value")
    # Do not remove punctuation, case, phone prefixes, date precision or qualifiers.
    return unicodedata.normalize("NFC", " ".join(value.split()))


def question_digest(field: Mapping) -> str:
    question = field.get("application_question")
    if not isinstance(question, Mapping) or question.get("completeness") != "known":
        raise ValueError("consistency question meaning is incomplete")
    return digest({key: question.get(key) for key in ("text", "help_text", "constraints", "section_path", "language")})


def _record_kind(field: Mapping) -> str:
    # Conservative diagnostics, not a learned classifier or a fill authorization.
    path = field.get("record_path", [])
    if not isinstance(path, list):
        return "unknown"
    names = " ".join(str(item.get("label") or "") for item in path if isinstance(item, Mapping))
    matches = [kind for kind, pattern in {
        "education": r"\b(?:education|academic|school|university)\b|教育|学历",
        "employment": r"\b(?:employment|work experience|work history)\b|工作经历|任职",
        "project": r"\bprojects?\b|项目",
    }.items() if re.search(pattern, names, re.IGNORECASE)]
    return matches[0] if len(matches) == 1 else "unknown"


def _profile_source(profile: Mapping, path: object) -> tuple[str, str]:
    if not isinstance(path, list) or not 2 <= len(path) <= 6:
        raise ValueError("unsupported consistency profile path")
    root = path[0]
    if root == "personal":
        if len(path) != 2 or path[1] not in _CONTACT_KEYS:
            raise ValueError("sensitive or unsupported consistency profile path")
        kind = "contact"
    elif root == "resume_facts":
        kinds = {"preserved_companies": "employment", "preserved_projects": "project", "preserved_school": "education"}
        if path[1] not in kinds:
            raise ValueError("unsupported resume fact path")
        kind = kinds[path[1]]
    elif root in _RECORD_ROOTS:
        kind = _RECORD_ROOTS[root]
    else:
        raise ValueError("unsupported consistency profile authority")
    if any(isinstance(part, str) and re.search(r"password|secret|token|passport|nric|ssn|bank|salary|sponsor|visa", part, re.IGNORECASE)
           for part in path):
        raise ValueError("protected consistency source")
    value = profile
    for part in path:
        if ((type(part) is int and part >= 0 and isinstance(value, (list, tuple)) and part < len(value))
                or (isinstance(part, str) and isinstance(value, Mapping) and part in value)):
            value = value[part]
        else:
            raise ValueError("consistency profile source unavailable")
    return _text(value), kind


def _writing_source(source: Mapping, job: Mapping) -> tuple[str, str, dict]:
    from applypilot.writing_common import content_digest, validate_draft, validate_review

    path = Path(source.get("path", ""))
    if not path.is_absolute() or path.name != "artifact.json" or not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("explicit bounded writing artifact.json required")
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(artifact, dict):
        raise ValueError("invalid writing artifact")  # noqa: TRY004 - attended request validation uses ValueError
    if any(not isinstance(artifact.get(key), dict) for key in ("task", "draft", "validation", "context_snapshot", "review")):
        raise ValueError("invalid writing artifact components")
    task = artifact["task"]
    if (artifact.get("genre") not in {"application_answer", "cover_letter"}
            or not task.get("job_id") or task["job_id"] not in {value for value in (job.get("url"), job.get("application_url")) if value}
            or artifact.get("revision_id") != source.get("revision_id")
            or artifact.get("status") != "reviewed_draft" or artifact.get("authority") != "none"
            or artifact.get("submission_ready") is not False
            or artifact.get("validation", {}).get("passed") is not True
            or artifact.get("draft", {}).get("missing_facts")
            or artifact.get("draft", {}).get("text") != artifact.get("text")
            or content_digest(artifact.get("text", "")) != artifact.get("text_digest")
            or content_digest(artifact.get("context_snapshot", {})) != artifact.get("context_digest")
            or validate_review(artifact.get("review"))["verdict"] != "pass"):
        raise ValueError("writing revision, evidence, review or job binding is invalid")
    recheck = validate_draft(artifact["draft"], artifact["context_snapshot"], genre=artifact["genre"],
                            constraints=task.get("constraints", []), surface=task.get("surface", "body"))
    if not recheck["passed"]:
        raise ValueError("writing artifact failed current deterministic checks")
    return _text(artifact["text"]), "narrative", {"artifact_digest": digest(artifact),
        "question": task.get("question"), "constraints": task.get("constraints", []), "genre": artifact["genre"]}


def resolve_source(source: Mapping, profile: Mapping, job: Mapping, materials: Mapping) -> tuple[str, str, dict]:
    if not isinstance(source, Mapping):
        raise ValueError("consistency source must be a reference")  # noqa: TRY004 - attended request validation uses ValueError
    kind = source.get("kind")
    if kind == "profile" and set(source) == {"kind", "path"}:
        value, record = _profile_source(profile, source["path"])
        return value, record, {}
    if kind == "fact" and set(source) == {"kind", "fact_ref", "scope"}:
        from applypilot.apply.answer_provenance import _scope_token
        from applypilot.apply.application_facts import current_profile_facts, resolve_application_fact_ref

        # Reuse existing scope encoding. Never manufacture a CDP lease for IAB.
        scopes = {"global:*"}
        for name, value in (("jurisdiction", job.get("location")),
                            ("employment_type", job.get("employment_type") or job.get("job_type")),
                            ("job_family", job.get("job_family")), ("employer", job.get("company_name"))):
            if value:
                scopes.add(_scope_token(name, value))
        if source["scope"] not in scopes:
            raise ValueError("fact scope is not bound to this job")
        result = resolve_application_fact_ref(current_profile_facts(profile), fact_ref=source["fact_ref"], scope=source["scope"])
        if not result.production_ready or result.sensitivity == "high":
            raise ValueError("current non-high-sensitivity fact unavailable")
        return _text(result.value), "fact", {"fact_ref": result.fact_ref}
    if kind == "writing" and set(source) == {"kind", "path", "revision_id"}:
        return _writing_source(source, job)
    if kind == "material" and set(source) == {"kind", "material_kind"}:
        purpose = source["material_kind"]
        if purpose not in {"resume", "cover_letter_pdf"}:
            raise ValueError("unsupported material purpose")
        found = [item for item in materials.get("materials", []) if item.get("kind") == purpose and item.get("sha256")]
        if len(found) != 1:
            raise ValueError("frozen attachment unavailable")
        return found[0]["sha256"], purpose, {"size": found[0]["size"]}
    raise ValueError("unsupported consistency source shape; expected values are not accepted")


def _fields(form: Mapping) -> dict:
    if not isinstance(form, Mapping) or not isinstance(form.get("fields"), list) or len(form["fields"]) > 1000:
        raise ValueError("bounded observed form required")
    rows = form["fields"]
    if any(not isinstance(f, Mapping) or not isinstance(f.get("field_key"), str) or not f["field_key"] for f in rows):
        raise ValueError("observed field identity missing")
    indexed = {f["field_key"]: f for f in rows}
    if len(indexed) != len(rows):
        raise ValueError("duplicate observed field identity")
    return indexed


def build_contract(bindings: list, form: Mapping, profile: Mapping, job: Mapping, materials: Mapping) -> dict:
    if not isinstance(bindings, list) or not 1 <= len(bindings) <= MAX_BINDINGS:
        raise ValueError("consistency requires 1..128 explicit bindings")
    fields = _fields(form)
    items = []
    for binding in bindings:
        if not isinstance(binding, Mapping) or set(binding) != {"field_key", "source"} or binding["field_key"] not in fields:
            raise ValueError("consistency binding needs an observed field and source reference")
        field = fields[binding["field_key"]]
        semantic = field.get("semantic", {})
        if not isinstance(semantic, Mapping) or semantic.get("identification") != "identified":
            raise ValueError("ambiguous consistency field")
        value, kind, meta = resolve_source(binding["source"], profile, job, materials)
        if kind in {"resume", "cover_letter_pdf"}:
            attachment = semantic.get("attachment")
            purpose = "resume" if kind == "resume" else "cover_letter"
            if not isinstance(attachment, Mapping) or attachment.get("status") != "identified" or attachment.get("purpose") != purpose:
                raise ValueError("attachment_purpose_unproven_or_mismatched")
        record_kind = _record_kind(field)
        if kind in {"employment", "education", "project"} and record_kind != kind:
            raise ValueError("source_record_kind_mismatch")
        if kind == "narrative" and meta["genre"] == "application_answer":
            question = meta.get("question") or {}
            observed = field.get("application_question") or {}
            if (any(question.get(k, "") != observed.get(k, "") for k in ("text", "help_text"))
                    or meta["constraints"] != observed.get("constraints", [])):
                raise ValueError("writing_question_revision_mismatch")
        claim_id = digest({"field_key": field["field_key"], "source": binding["source"]})
        items.append({"claim_id": claim_id, "field_key": field["field_key"], "source": dict(binding["source"]),
                      "source_digest": digest({"value": value, "kind": kind, "meta": meta}),
                      "question_digest": question_digest(field),
                      "record_digest": digest(field.get("record_path", [])), "record_kind": record_kind,
                      "value_digest": digest(value), "kind": kind})
    if len({i["field_key"] for i in items}) != len(items):
        raise ValueError("duplicate consistency binding")
    return {"schema_version": 1, "items": items, "materials_digest": digest(materials),
            "raw_values_stored": False, "scope": "explicit_bindings_only", "submit_authority": False}


def _observed_value(field: Mapping) -> str:
    if field.get("invalid") is True or field.get("value_source") == "unavailable":
        raise ValueError("field value unavailable or invalid")
    if field.get("control") == "combobox":
        display = field.get("selected_display", [])
        options = [o for o in field.get("options", []) if o.get("selected") is True]
        if isinstance(display, list) and len(display) == 1:
            return _text(display[0])
        if not display and len(options) == 1:
            return _text(options[0].get("label"))
        raise ValueError("uncommitted or ambiguous selection")
    if field.get("control") == "select":
        options = [o for o in field.get("options", []) if o.get("value") == field.get("value")]
        if field.get("multiple") or len(options) != 1:
            raise ValueError("ambiguous native selection")
        return _text(options[0].get("label"))
    return _text(field.get("value"))


def check_contract(contract: Mapping, observed: Mapping, profile: Mapping, job: Mapping, materials: Mapping) -> dict:
    """Re-resolve sources every time; absent checks cannot turn into a passing result."""
    if (not isinstance(contract, Mapping) or contract.get("schema_version") != 1
            or not isinstance(contract.get("items"), list) or not 1 <= len(contract["items"]) <= MAX_BINDINGS
            or contract.get("submit_authority") is not False or contract.get("raw_values_stored") is not False):
        raise ValueError("invalid bound consistency contract")
    if not isinstance(observed, Mapping) or set(observed) - {"form_state", "review_entries", "attachments"}:
        raise ValueError("consistency observation has unsupported fields")
    form = observed.get("form_state")
    fields = _fields(form) if form is not None else {}
    review = observed.get("review_entries", [])
    attachments = observed.get("attachments", [])
    for collection in (review, attachments):
        if not isinstance(collection, list) or len(collection) > MAX_BINDINGS or any(not isinstance(i, Mapping) for i in collection):
            raise ValueError("invalid bounded review evidence")
    for collection, keys in ((review, {"claim_id", "value", "question_digest", "record_kind"}),
                             (attachments, {"claim_id", "purpose", "material_sha256", "size", "acceptance_marker"})):
        if any(set(r) != keys for r in collection):
            raise ValueError("unsupported review evidence shape")
        ids = [r.get("claim_id") for r in collection]
        if any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("duplicate or missing review claim identity")
    expected_ids = {i["claim_id"] for i in contract["items"]}
    if any(r["claim_id"] not in expected_ids for r in review + attachments):
        raise ValueError("unbound review evidence")
    issues = []
    checked = 0
    if digest(materials) != contract["materials_digest"]:
        issues.append({"code": "material_revision_changed"})
    for item in contract["items"]:
        code = None
        try:
            value, kind, meta = resolve_source(item["source"], profile, job, materials)
            if digest({"value": value, "kind": kind, "meta": meta}) != item["source_digest"]:
                code = "source_revision_changed"
            elif kind in {"resume", "cover_letter_pdf"}:
                evidence = [r for r in attachments if r["claim_id"] == item["claim_id"]]
                field = fields.get(item["field_key"])
                attachment = field.get("semantic", {}).get("attachment") if field else None
                purpose = attachment.get("purpose") if isinstance(attachment, Mapping) else None
                expected_purpose = "resume" if kind == "resume" else "cover_letter"
                if field is not None and (not isinstance(attachment, Mapping) or attachment.get("status") != "identified" or purpose != expected_purpose):
                    code = "attachment_purpose_mismatch"
                elif field is not None and (question_digest(field) != item["question_digest"] or digest(field.get("record_path", [])) != item["record_digest"]):
                    code = "question_or_record_changed"
                elif len(evidence) != 1 or evidence[0].get("acceptance_marker") not in {"attachment_card", "uploaded_file_list"}:
                    code = "attachment_acceptance_unproven"
                elif type(evidence[0].get("size")) is not int or (evidence[0].get("material_sha256"), evidence[0].get("size"), evidence[0].get("purpose")) != (value, meta["size"], expected_purpose):
                    code = "attachment_revision_or_purpose_mismatch"
            else:
                field = fields.get(item["field_key"])
                entry = [r for r in review if r["claim_id"] == item["claim_id"]]
                if field is not None:
                    if not isinstance(field.get("semantic"), Mapping) or field["semantic"].get("identification") != "identified":
                        code = "field_semantics_unproven"
                    elif question_digest(field) != item["question_digest"] or digest(field.get("record_path", [])) != item["record_digest"]:
                        code = "question_or_record_changed"
                    elif digest(_observed_value(field)) != item["value_digest"]:
                        code = "bound_value_mismatch"
                    # Review identity and text must agree even with a correct editable field.
                    if not code and entry and (entry[0].get("question_digest") != item["question_digest"] or entry[0].get("record_kind") != item["record_kind"]):
                        code = "review_binding_mismatch"
                    if not code and entry and digest(_text(entry[0].get("value"))) != item["value_digest"]:
                        code = "review_value_mismatch"
                elif len(entry) != 1:
                    code = "bound_field_not_observed"
                elif entry[0].get("question_digest") != item["question_digest"] or entry[0].get("record_kind") != item["record_kind"]:
                    code = "review_binding_mismatch"
                elif digest(_text(entry[0].get("value"))) != item["value_digest"]:
                    code = "review_value_mismatch"
        except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError):
            code = "source_or_observation_unavailable"
        checked += 1
        if code:
            issues.append({"claim_id": item["claim_id"], "code": code})
    return {"status": "consistent" if not issues else "blocked", "checked": checked,
            "issues": issues, "scope": "explicit_bindings_only", "whole_application_verified": False,
            "semantic_entailment_verified": False, "submit_authority": False}
