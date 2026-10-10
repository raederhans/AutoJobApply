"""Lossless, value-free question ingestion for standalone answer preparation.

These records describe observed pages only. They grant no browser write authority.
Native HTML length limits count UTF-16 code units; prose character limits remain
unresolved unless their counting rule is supplied explicitly.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from hashlib import sha256
from typing import Any


class QuestionValidationError(ValueError):
    """Malformed question data; callers can handle all validation uniformly."""


def _text(value: Any, name: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise QuestionValidationError(f"{name} must be {'a nonempty string' if nonempty else 'a string'}")
    return value


def _list(value: Any, name: str) -> list:
    if not isinstance(value, list):
        raise QuestionValidationError(f"{name} must be a list")
    return value


def _digest(value: Any) -> str:
    # Escaping supports every Python Unicode string, including lone surrogates.
    return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _constraints(items: Any) -> list[dict]:
    result = []
    for item in _list(items, "constraints"):
        if not isinstance(item, dict) or set(item) != {"kind", "unit", "value", "source"}:
            raise QuestionValidationError("constraint must contain kind, unit, value and source")
        if item["kind"] not in ("max", "min") or item["unit"] not in ("words", "utf16", "characters"):
            raise QuestionValidationError("unsupported constraint kind or unit")
        if type(item["value"]) is not int or item["value"] < 0:
            raise QuestionValidationError("constraint value must be a nonnegative integer")
        _text(item["source"], "constraint source", nonempty=True)
        if item not in result:
            result.append(dict(item))
    return result


def normalize_question(data: dict) -> dict:
    """Validate and canonicalize a plain JSON question; never shorten its text."""
    if not isinstance(data, dict):
        raise QuestionValidationError("question must be a dict")
    if type(data.get("schema_version", 1)) is not int or data.get("schema_version", 1) != 1:
        raise QuestionValidationError("unsupported question schema_version")
    result = {"schema_version": 1}
    for name in ("job_id", "page_id", "field_key", "text"):
        result[name] = _text(data.get(name), name, nonempty=True)
    result["help_text"] = _text(data.get("help_text", ""), "help_text")
    result["language"] = _text(data.get("language", "unknown"), "language", nonempty=True)
    if type(data.get("required", False)) is not bool:
        raise QuestionValidationError("required must be a boolean")
    result["required"] = data.get("required", False)
    result["options"] = []
    for option in _list(data.get("options", []), "options"):
        if isinstance(option, str):
            option = {"value": option, "label": option}
        if not isinstance(option, dict):
            raise QuestionValidationError("option must be a string or dict")
        normalized = {name: _text(option.get(name), f"option {name}") for name in ("value", "label")}
        if "disabled" in option:
            if type(option["disabled"]) is not bool:
                raise QuestionValidationError("option disabled must be a boolean")
            normalized["disabled"] = option["disabled"]
        result["options"].append(normalized)
    result["constraints"] = _constraints(data.get("constraints", []))
    for name in ("section_path", "unresolved_instructions"):
        result[name] = [_text(item, name) for item in _list(data.get(name, []), name)]
    result["text_sources"] = []
    for source in _list(data.get("text_sources", []), "text_sources"):
        if not isinstance(source, dict) or set(source) != {"source", "text"}:
            raise QuestionValidationError("text source must contain source and text")
        result["text_sources"].append({name: _text(source[name], name) for name in ("source", "text")})
    completeness = data.get("completeness", "partial")
    if completeness not in ("known", "partial"):
        raise QuestionValidationError("completeness must be known or partial")
    result["completeness"] = completeness
    coverage = data.get("coverage", {})
    if not isinstance(coverage, dict):
        raise QuestionValidationError("coverage must be a dict")
    result["coverage"] = {
        "scope": _text(coverage.get("scope", "unknown"), "coverage scope"),
        "page_only": True,
        "whole_form": "unknown",
    }
    for name in ("iframe_count", "fields_truncated"):
        if name in coverage:
            value = coverage[name]
            if name == "iframe_count" and (type(value) is not int or value < 0):
                raise QuestionValidationError("iframe_count must be a nonnegative integer")
            if name == "fields_truncated" and type(value) is not bool:
                raise QuestionValidationError("fields_truncated must be a boolean")
            result["coverage"][name] = value
    result["question_id"] = "q_" + _digest([result[name] for name in ("job_id", "page_id", "field_key")])
    # Current field values, DOM epochs, and capture time are intentionally absent.
    semantic = {key: value for key, value in result.items() if key != "coverage"}
    result["revision"] = _digest(semantic)
    for name in ("question_id", "revision"):
        if name in data and data[name] != result[name]:
            raise QuestionValidationError(f"{name} does not match question content")
    return result


def _instruction_constraints(text: str, source: str) -> list[dict]:
    """Only unambiguous explicitly stated word bounds, not inferred numbers."""
    patterns = (
        ("max", r"(?:maximum|max\.?|at most|no more than|up to|limit(?:ed)? to)\s*[:：]?\s*(\d+)\s*words?\b"),
        ("max", r"\b(\d+)\s*[- ]word\s+(?:limit|maximum|max)\b"),
        ("min", r"(?:minimum|min\.?|at least|no fewer than)\s*[:：]?\s*(\d+)\s*words?\b"),
        ("max", r"(?:最多|不超过|至多|上限为?)\s*(\d+)\s*(?:个)?词"),
        ("min", r"(?:至少|最少|不少于)\s*(\d+)\s*(?:个)?词"),
    )
    return [
        {"kind": kind, "unit": "words", "value": int(match.group(1)), "source": source}
        for kind, pattern in patterns
        for match in re.finditer(pattern, text, re.IGNORECASE)
    ]


def questions_from_observation(observation: dict, *, job_id: str, page_id: str | None = None) -> list[dict]:
    """Read either IAB fields or the external observer's structural form fields."""
    if not isinstance(observation, dict):
        raise QuestionValidationError("observation must be a dict")
    _text(job_id, "job_id", nonempty=True)
    payload = observation.get("form", observation.get("snapshot", observation))
    if not isinstance(payload, dict):
        raise QuestionValidationError("observation payload must be a dict")
    page_id = page_id if page_id is not None else payload.get("page_id", payload.get("page_url", payload.get("url")))
    _text(page_id, "page_id", nonempty=True)
    coverage = payload.get("question_coverage", payload.get("coverage", {"scope": "unknown"}))
    if not isinstance(coverage, dict):
        raise QuestionValidationError("observation coverage must be a dict")
    if "fields" not in payload and "form_fields" not in payload:
        raise QuestionValidationError("observation must explicitly contain a fields or form_fields list")
    fields = payload["fields"] if "fields" in payload else payload["form_fields"]
    result = []
    for field in _list(fields, "observation fields"):
        if not isinstance(field, dict):
            raise QuestionValidationError("observed field must be a dict")
        metadata = field.get("application_question", {})
        if not isinstance(metadata, dict):
            raise QuestionValidationError("application_question must be a dict")
        text = metadata.get("text", field.get("text", field.get("label", "")))
        if not isinstance(text, str):
            raise QuestionValidationError("question text must be a string")
        if not text.strip():
            continue
        help_text = metadata.get("help_text", field.get("help_text", ""))
        constraints = list(_constraints(metadata.get("constraints", field.get("constraints", []))))
        unresolved = list(_list(metadata.get("unresolved_instructions", []), "unresolved_instructions"))
        for source, raw in (("question_text", text), ("help_text", _text(help_text, "help_text"))):
            extracted = _instruction_constraints(raw, source)
            constraints.extend(extracted)
            if (
                re.search(r"words?|characters?|字|词|limit|上限", raw, re.IGNORECASE)
                and (not extracted or re.search(r"characters?|字", raw, re.IGNORECASE))
                and raw not in unresolved
            ):
                unresolved.append(raw)
        question = normalize_question(
            {
                **metadata,
                "job_id": job_id,
                "page_id": page_id,
                "field_key": metadata.get("field_key", field.get("field_key")),
                "text": text,
                "help_text": help_text,
                "required": field.get("required", False),
                "options": metadata.get("options", field.get("options", [])),
                "constraints": constraints,
                "unresolved_instructions": unresolved,
                "coverage": coverage,
                "completeness": "partial"
                if not metadata or coverage.get("iframe_count", 0) or coverage.get("fields_truncated", False)
                else metadata.get("completeness", "known"),
            }
        )
        if any(item["question_id"] == question["question_id"] for item in result):
            raise QuestionValidationError("duplicate question identity on observed page")
        result.append(question)
    return result


def merge_question_set(existing: dict | None, questions: list[dict], *, page_id: str) -> dict:
    """Replace one observed page, retaining every semantic revision and other pages.

    An empty first page needs an existing set to establish its job identity.
    Removed conditional questions leave the active page but remain in revisions.
    """
    _text(page_id, "page_id", nonempty=True)
    incoming = [normalize_question(item) for item in _list(questions, "questions")]
    if any(item["page_id"] != page_id for item in incoming):
        raise QuestionValidationError("questions must belong to the observed page")
    if len({item["question_id"] for item in incoming}) != len(incoming):
        raise QuestionValidationError("duplicate question identity")
    if existing is not None and (
        not isinstance(existing, dict)
        or type(existing.get("schema_version")) is not int
        or existing.get("schema_version") != 1
    ):
        raise QuestionValidationError("unsupported question set")
    job_id = existing.get("job_id") if existing is not None else (incoming[0]["job_id"] if incoming else None)
    _text(job_id, "job_id", nonempty=True)
    if any(item["job_id"] != job_id for item in incoming):
        raise QuestionValidationError("question set cannot mix jobs")
    result = {
        "schema_version": 1,
        "job_id": job_id,
        "pages": {},
        "revisions": {},
        "coverage": {"scope": "observed_pages", "whole_form": "unknown"},
    }
    if existing is not None:
        pages, revisions = existing.get("pages", {}), existing.get("revisions", {})
        if not isinstance(pages, dict) or not isinstance(revisions, dict):
            raise QuestionValidationError("question set pages and revisions must be dicts")
        for prior_page, prior_questions in pages.items():
            _text(prior_page, "prior page_id", nonempty=True)
            result["pages"][prior_page] = [
                normalize_question(item) for item in _list(prior_questions, "page questions")
            ]
            if any(item["job_id"] != job_id or item["page_id"] != prior_page for item in result["pages"][prior_page]):
                raise QuestionValidationError("existing page has inconsistent identity")
        for question_id, versions in revisions.items():
            result["revisions"][question_id] = [normalize_question(item) for item in _list(versions, "revisions")]
            if any(
                item["job_id"] != job_id or item["question_id"] != question_id
                for item in result["revisions"][question_id]
            ):
                raise QuestionValidationError("existing revision has inconsistent identity")
    for question in [item for page in result["pages"].values() for item in page] + incoming:
        versions = result["revisions"].setdefault(question["question_id"], [])
        if not any(item["revision"] == question["revision"] for item in versions):
            versions.append(deepcopy(question))
    result["pages"][page_id] = incoming
    return result
