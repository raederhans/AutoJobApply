"""Explicit, source-preserving context for standalone application writing.

Version 1 is a plain JSON registry, never a parser of candidate Markdown or an
authority inferred from a resume. Only registered paths are read by the loader.
Selection keeps claim evidence, company context, positioning and style apart.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
_SOURCE_KINDS = {"candidate_facts", "project_evidence", "resume", "jd", "company", "voice", "generated"}
_STATUSES = {"confirmed", "conditional", "unresolved", "historical", "generated"}
_GENRES = {"application_answer", "cover_letter"}
_ORIGINAL = {"candidate_facts", "project_evidence"}
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "i", "in", "is", "it", "of", "on", "or",
    "that", "the", "this", "to", "was", "with", "my", "me", "we", "our",
}


def _object(value: Any, label: str, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")  # noqa: TRY004 - uniform registry validation error
    missing = required - value.keys()
    unknown = value.keys() - required - (optional or set())
    if missing or unknown:
        raise ValueError(f"{label}: missing fields {sorted(missing)}; unknown fields {sorted(unknown)}")
    return value


def _text(value: Any, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{label} must be {'a' if empty else 'a nonempty'} string")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")  # noqa: TRY004 - uniform registry validation error
    return value


def _texts(value: Any, label: str) -> list[str]:
    return [_text(item, label) for item in _list(value, label)]


def _choice(value: Any, options: set[str], label: str) -> None:
    if not isinstance(value, str) or value not in options:
        raise ValueError(f"{label} must be one of {sorted(options)}")


def _unique(entries: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result = {}
    for entry in entries:
        identifier = _text(entry.get("id"), f"{label}.id")
        if identifier in result:
            raise ValueError(f"Duplicate {label} id: {identifier}")
        result[identifier] = entry
    return result


def validate_context(context: Any) -> dict[str, Any]:
    """Validate references and exact quotations, returning an independent JSON dict.

    `confirmed` is necessary but insufficient for a personal claim: selection also
    requires an original candidate/project source. Resume/generated entries may be
    registered for traceability, but never acquire that source authority.
    """
    value = copy.deepcopy(_object(context, "context", {
        "schema_version", "sources", "candidate_evidence", "role", "company", "voice_examples",
    }))
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported writing context schema_version; expected 1")
    sources = _list(value["sources"], "sources")
    for source in sources:
        _object(source, "source", {"id", "kind", "text"}, {"path", "url", "observed_at", "boundaries"})
        _choice(source["kind"], _SOURCE_KINDS, "source.kind")
        _text(source["text"], "source.text", empty=True)
        for key in ("path", "url", "observed_at"):
            if key in source:
                _text(source[key], f"source.{key}")
        source["boundaries"] = _texts(source.get("boundaries", []), "source.boundaries")
    source_map = _unique(sources, "source")

    def quotation(entry: dict[str, Any], kinds: set[str], label: str) -> None:
        source_id = _text(entry["source_id"], f"{label}.source_id")
        source = source_map.get(source_id)
        if source is None:
            raise ValueError(f"{label}: unknown source reference {source_id}")
        if source["kind"] not in kinds:
            raise ValueError(f"{label}: source {source_id} has incompatible kind {source['kind']}")
        quote = _text(entry["quote"], f"{label}.quote")
        if quote not in source["text"]:
            raise ValueError(f"{label}: quote is absent from source {source_id}")

    evidence = _list(value["candidate_evidence"], "candidate_evidence")
    for entry in evidence:
        _object(entry, "candidate_evidence", {"id", "source_id", "quote", "status"}, {"scope", "boundaries", "tags"})
        quotation(entry, _ORIGINAL | {"resume", "generated"}, "candidate_evidence")
        _choice(entry["status"], _STATUSES, "candidate_evidence.status")
        for key in ("boundaries", "tags"):
            entry[key] = _texts(entry.get(key, []), f"candidate_evidence.{key}")
        if "scope" in entry:
            _object(entry["scope"], "candidate_evidence.scope", set(), {"job_id", "company"})
            if not entry["scope"]:
                raise ValueError("candidate_evidence.scope must specify job_id or company")
            for key, item in entry["scope"].items():
                _text(item, f"candidate_evidence.scope.{key}")
    _unique(evidence, "candidate_evidence")

    role = value["role"]
    if role is not None:
        _object(role, "role", {"job_id", "title", "company_name", "jd_source_id"}, {
            "language", "requirements", "responsibilities",
        })
        for key in ("job_id", "title", "company_name", "jd_source_id"):
            _text(role[key], f"role.{key}")
        if "language" in role:
            _text(role["language"], "role.language")
        source = source_map.get(role["jd_source_id"])
        if source is None or source["kind"] != "jd":
            raise ValueError("role.jd_source_id must reference a registered jd source")
        for key in ("requirements", "responsibilities"):
            role[key] = _list(role.get(key, []), f"role.{key}")
            for entry in role[key]:
                _object(entry, f"role.{key}", {"id", "source_id", "quote"})
                quotation(entry, {"jd"}, f"role.{key}")
                if entry["source_id"] != role["jd_source_id"]:
                    raise ValueError(f"role.{key} must reference role.jd_source_id")
            _unique(role[key], f"role.{key}")

    company = value["company"]
    if company is not None:
        _object(company, "company", {"name", "facts"}, {"scale", "stage", "delivery_model"})
        _text(company["name"], "company.name")
        if role and company["name"].casefold() != role["company_name"].casefold():
            raise ValueError("company.name does not match role.company_name")
        facts = _list(company["facts"], "company.facts")
        for entry in facts:
            _object(entry, "company.fact", {"id", "source_id", "quote", "verification_status"})
            quotation(entry, {"company", "jd"}, "company.fact")
            if source_map[entry["source_id"]]["kind"] == "jd" and (
                role is None or entry["source_id"] != role["jd_source_id"]
            ):
                raise ValueError("Company facts from a JD must reference this role's exact JD")
            _choice(entry["verification_status"], {"verified", "unverified"}, "company.fact.verification_status")
        fact_map = _unique(facts, "company.fact")
        for key in ("scale", "stage", "delivery_model"):
            attribute = company.setdefault(key, {"value": None, "evidence_ids": []})
            _object(attribute, f"company.{key}", {"value", "evidence_ids"})
            references = _texts(attribute["evidence_ids"], f"company.{key}.evidence_ids")
            if attribute["value"] is not None:
                _text(attribute["value"], f"company.{key}.value")
                if not references:
                    raise ValueError(f"company.{key} requires evidence_ids for a known value")
            for reference in references:
                if reference not in fact_map:
                    raise ValueError(f"company.{key}: unknown company fact reference {reference}")

    voices = _list(value["voice_examples"], "voice_examples")
    for entry in voices:
        _object(entry, "voice_example", {
            "id", "source_id", "quote", "language", "genre", "authorship", "user_approved",
        })
        quotation(entry, {"voice"}, "voice_example")
        _text(entry["language"], "voice_example.language")
        _choice(entry["genre"], _GENRES, "voice_example.genre")
        _choice(entry["authorship"], {"user", "assistant", "unknown"}, "voice_example.authorship")
        if type(entry["user_approved"]) is not bool:
            raise ValueError("voice_example.user_approved must be a boolean")
    _unique(voices, "voice_example")
    return value


def load_context(path: str | Path) -> dict[str, Any]:
    """Load an explicit registry and only its registered UTF-8 source files.

    A source may omit `text` when it specifies `path`. Relative paths resolve
    against the registry directory. If both exist, their exact text must match.
    No document content is interpreted as new confirmed evidence.
    """
    def local_path(raw: str | Path) -> Path:
        name = str(raw)
        if name.startswith(("\\\\", "//")) or "://" in name:
            raise ValueError("Writing context paths must be local files, not network paths or URLs")
        return Path(raw).expanduser()

    registry_path = local_path(path).resolve()
    value = json.loads(registry_path.read_bytes().decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("context must be an object")  # noqa: TRY004 - uniform registry validation error
    for source in _list(value.get("sources"), "sources"):
        if not isinstance(source, dict):
            raise ValueError("source must be an object")  # noqa: TRY004 - uniform registry validation error
        if "path" in source:
            registered = local_path(_text(source["path"], "source.path"))
            registered = registered if registered.is_absolute() else registry_path.parent / registered
            snapshot = registered.read_bytes().decode("utf-8-sig")
            if "text" in source and source["text"] != snapshot:
                raise ValueError(f"Source text/file mismatch: {source.get('id', '<missing id>')}")
            source["text"] = snapshot
    return validate_context(value)


def _tokens(text: str) -> set[str]:
    # Han bigrams permit Chinese relevance without equating any shared character
    # with relevance. Other Unicode words retain their complete spelling.
    words = set(re.findall(r"[^\W_]+", re.sub(r"[\u3400-\u9fff]+", " ", text.casefold())))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(run[index:index + 2] for index in range(len(run) - 1))
        if len(run) == 1:
            words.add(run)
    return words - _STOP_WORDS


def select_context(
    context: dict[str, Any], *, query: str, language: str = "en", genre: str = "application_answer",
    job_id: str | None = None, max_evidence: int = 4, max_voice: int = 2,
) -> dict[str, Any]:
    """Select conservatively; diagnostics retain unknown and excluded evidence.

    The digest binds the exact normalized registry snapshots, not truth, user
    approval, or eligibility. Relevance is deterministic token overlap only.
    This result is a trusted selection payload, distinct from the input registry
    schema; callers must not pass it back to `validate_context`.
    """
    value = validate_context(context)
    _text(query, "query", empty=True)
    _text(language, "language")
    _choice(genre, _GENRES, "genre")
    if job_id is not None:
        _text(job_id, "job_id")
    for key, limit in (("max_evidence", max_evidence), ("max_voice", max_voice)):
        if type(limit) is not int or limit < 0:
            raise ValueError(f"{key} must be a nonnegative integer")
    snapshot_digest = hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    source_map = {entry["id"]: entry for entry in value["sources"]}
    role, company = value["role"], value["company"]
    diagnostics: dict[str, Any] = {
        "excluded_candidate_evidence": [], "excluded_voice_examples": [], "unverified_company_facts": [],
        "unverified_company_attributes": [], "missing": [], "voice_status": "uncalibrated",
    }
    if job_id and role and job_id != role["job_id"]:
        diagnostics["role_scope_mismatch"] = {"requested_job_id": job_id, "context_job_id": role["job_id"]}
        role, company = None, None
    effective_job_id = job_id or (role["job_id"] if role else None)
    company_name = company["name"] if company else (role["company_name"] if role else None)
    query_tokens = _tokens(query)
    ranked = []
    for index, entry in enumerate(value["candidate_evidence"]):
        source = source_map[entry["source_id"]]
        entry["boundaries"] = list(dict.fromkeys(source["boundaries"] + entry["boundaries"]))
        scope = entry.get("scope", {})
        score = len(query_tokens & _tokens(entry["quote"] + " " + " ".join(entry["tags"])))
        if source["kind"] not in _ORIGINAL:
            reason = "non_authoritative_source"
        elif entry["status"] != "confirmed":
            reason = f"status_{entry['status']}"
        elif scope.get("job_id") and scope["job_id"] != effective_job_id:
            reason = "job_scope_mismatch"
        elif scope.get("company") and (not company_name or scope["company"].casefold() != company_name.casefold()):
            reason = "company_scope_mismatch"
        elif not score:
            reason = "no_direct_relevance"
        else:
            ranked.append((score, index, entry))
            continue
        diagnostics["excluded_candidate_evidence"].append({"evidence": entry, "reason": reason})
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = [entry for _, _, entry in ranked[:max_evidence]]
    for _, _, entry in ranked[max_evidence:]:
        diagnostics["excluded_candidate_evidence"].append({"evidence": entry, "reason": "selection_limit"})

    voices = []
    for entry in value["voice_examples"]:
        if entry["authorship"] != "user" and not entry["user_approved"]:
            reason = "not_user_authored_or_approved"
        elif entry["language"].casefold().split("-")[0] != language.casefold().split("-")[0]:
            reason = "language_mismatch"
        elif entry["genre"] != genre:
            reason = "genre_mismatch"
        elif len(voices) >= max_voice:
            reason = "selection_limit"
        else:
            voices.append({**entry, "usage": "style_only"})
            continue
        diagnostics["excluded_voice_examples"].append({"example": entry, "reason": reason})
    if voices:
        diagnostics["voice_status"] = "calibrated"
    if company:
        verified_ids = {fact["id"] for fact in company["facts"] if fact["verification_status"] == "verified"}
        diagnostics["unverified_company_facts"] = [
            fact for fact in company["facts"] if fact["id"] not in verified_ids
        ]
        company["facts"] = [fact for fact in company["facts"] if fact["id"] in verified_ids]
        for key in ("scale", "stage", "delivery_model"):
            attribute = company[key]
            if any(reference not in verified_ids for reference in attribute["evidence_ids"]):
                diagnostics["unverified_company_attributes"].append({"attribute": key, **attribute})
                company[key] = {"value": None, "evidence_ids": []}
    resumes = [{"source_id": source["id"], "text": source["text"], "usage": "positioning_only"}
               for source in value["sources"] if source["kind"] == "resume"]
    for key, present in (("candidate_evidence", selected), ("role", role), ("company", company), ("voice_examples", voices)):
        if not present:
            diagnostics["missing"].append(key)
    used_ids = {entry["source_id"] for entry in selected + voices}
    if role:
        used_ids.add(role["jd_source_id"])
    if company:
        used_ids.update(fact["source_id"] for fact in company["facts"])
    return {
        "schema_version": SCHEMA_VERSION, "snapshot_digest": snapshot_digest,
        "sources": [source for source in value["sources"] if source["id"] in used_ids],
        "candidate_evidence": selected, "role": role, "company": company,
        "voice_examples": voices, "resume_positioning": resumes, "diagnostics": diagnostics,
    }
