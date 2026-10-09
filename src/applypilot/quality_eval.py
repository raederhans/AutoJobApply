"""Offline deterministic contracts for evidence mappings and submission results.

Fixture authors supply adjudicated support labels and approved full statements.
Quote provenance alone is never treated as proof of semantic support or truth.
No model, profile, database, mailbox or browser is invoked.
"""

from __future__ import annotations

import json
from pathlib import Path

CHECK_NAMES = ("fixture", "schema", "citations", "support_levels", "unsupported_claims", "submission_evidence")
SUPPORT_LEVELS = frozenset({"direct", "transferable", "gap"})
SUBMISSION_STATUSES = frozenset({"submitted", "submission_uncertain", "failed", "ready"})
ASSERTION_SCRIPT = "from applypilot.quality_eval import get_assert\n"
_MISSING = object()


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must be nonempty")
    return value


def _keys(value: object, required: set[str], allowed: set[str], name: str) -> dict:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    if required - value.keys():
        raise ValueError(f"{name} is missing {', '.join(sorted(required - value.keys()))}")
    if value.keys() - allowed:
        raise ValueError(f"{name} has unsupported fields: {', '.join(sorted(value.keys() - allowed))}")
    return value


def _array(value: object, name: str, *, nonempty: bool = True) -> list:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be an array")
    if nonempty and not value:
        raise ValueError(f"{name} must be nonempty")
    return value


def validate_case(case: dict) -> None:
    """Validate trusted fixture inputs before judging any supplied output."""
    required = {"schema_version", "id", "kind", "jd", "sources"}
    _keys(case, required, required | {"requirements", "job_url", "output"}, "case")
    if type(case["schema_version"]) is not int or case["schema_version"] != 1:
        raise ValueError("case.schema_version must be 1")
    _text(case["id"], "case.id")
    _text(case["jd"], "case.jd")
    if case["kind"] not in {"evidence", "receipt"}:
        raise ValueError("case.kind must be evidence or receipt")
    sources = _array(case["sources"], "case.sources", nonempty=False)
    source_ids = set()
    for source in sources:
        _keys(source, {"id", "text"}, {"id", "text", "kind", "job_url", "status", "receipt_id"}, "source")
        identity = _text(source["id"], "source.id")
        _text(source["text"], "source.text")
        if identity in source_ids:
            raise ValueError("Duplicate source.id")
        source_ids.add(identity)
        if case["kind"] == "receipt":
            if source.get("kind") not in {"receipt", "submit_attempt", "note"}:
                raise ValueError("Receipt source.kind must be receipt, submit_attempt or note")
            _text(source.get("job_url"), "source.job_url")
            if source.get("kind") == "receipt":
                if source.get("status") not in {"confirmed", "uncertain", "failed"}:
                    raise ValueError("Receipt source.status must be confirmed, uncertain or failed")
                _text(source.get("receipt_id"), "source.receipt_id")
    if case["kind"] == "receipt":
        _text(case.get("job_url"), "case.job_url")
        if "requirements" in case:
            raise ValueError("Receipt fixtures do not contain evidence requirements")
        return
    if "job_url" in case:
        raise ValueError("Evidence fixtures do not contain a submission job_url")
    requirements = _array(case.get("requirements"), "case.requirements")
    requirement_ids = set()
    for requirement in requirements:
        fields = {"id", "text", "expected_support", "source_ids", "allowed_statements"}
        _keys(requirement, fields, fields, "requirement")
        identity = _text(requirement["id"], "requirement.id")
        if identity in requirement_ids:
            raise ValueError("Duplicate requirement.id")
        requirement_ids.add(identity)
        text = _text(requirement["text"], "requirement.text")
        if text not in case["jd"]:
            raise ValueError("requirement.text must occur verbatim in the fixture JD")
        if requirement["expected_support"] not in SUPPORT_LEVELS:
            raise ValueError("requirement.expected_support must be direct, transferable or gap")
        references = _array(requirement["source_ids"], "requirement.source_ids", nonempty=False)
        if any(not isinstance(reference, str) or reference not in source_ids for reference in references):
            raise ValueError("requirement.source_ids must refer to registered sources")
        if requirement["expected_support"] == "gap" and references:
            raise ValueError("Gap requirements must have empty source_ids")
        if requirement["expected_support"] != "gap" and not references:
            raise ValueError("Supported requirements need at least one source_id")
        for statement in _array(requirement["allowed_statements"], "requirement.allowed_statements"):
            _text(statement, "allowed statement")


def _parse_output(output: object) -> object:
    if not isinstance(output, str):
        return output
    if not output.strip():
        raise ValueError("Output is empty")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"Invalid JSON constant: {value}")

    return json.loads(output, object_pairs_hook=pairs, parse_constant=reject_constant)


def _evidence_schema(output: object) -> list[dict]:
    _keys(output, {"evidence_map"}, {"evidence_map"}, "output")
    items = _array(output["evidence_map"], "output.evidence_map")
    fields = {"requirement_id", "support_level", "source_id", "source_quote", "statement"}
    for item in items:
        _keys(item, fields, fields, "evidence_map item")
        _text(item["requirement_id"], "requirement_id")
        _text(item["statement"], "statement")
        if item["support_level"] not in SUPPORT_LEVELS:
            raise ValueError("support_level must be direct, transferable or gap")
        if not isinstance(item["source_id"], str) or not isinstance(item["source_quote"], str):
            raise TypeError("source_id and source_quote must be strings (empty for gap)")
    return items


def _check_evidence(case: dict, items: list[dict], checks: dict) -> None:
    # Reuse the production validator's conservative numeric tokenizer. Import
    # lazily: its resume parser binds config paths, which must follow callbacks.
    from applypilot.scoring.validator import _numeric_claims

    for name in ("citations", "support_levels", "unsupported_claims"):
        checks[name]["passed"] = True
    sources = {source["id"]: source for source in case["sources"]}
    requirements = {requirement["id"]: requirement for requirement in case["requirements"]}
    seen = set()
    for index, item in enumerate(items):
        prefix = f"evidence_map[{index}]"
        identity = item["requirement_id"]
        if identity not in requirements or identity in seen:
            checks["support_levels"]["errors"].append(f"{prefix}: unknown or duplicate requirement_id")
            continue
        seen.add(identity)
        expected = requirements[identity]
        if item["support_level"] != expected["expected_support"]:
            checks["support_levels"]["errors"].append(f"{prefix}: support label differs from the fixture oracle")
        if item["statement"] not in expected["allowed_statements"]:
            checks["unsupported_claims"]["errors"].append(f"{prefix}: statement is not an approved full assertion")
        if item["support_level"] == "gap":
            if item["source_id"] or item["source_quote"]:
                checks["citations"]["errors"].append(f"{prefix}: gap must not cite positive candidate evidence")
            continue
        source = sources.get(item["source_id"])
        if source is None or item["source_id"] not in expected["source_ids"]:
            checks["citations"]["errors"].append(f"{prefix}: source is unregistered or not approved for this requirement")
            continue
        quote = item["source_quote"]
        if not quote.strip() or quote not in source["text"]:
            checks["citations"]["errors"].append(f"{prefix}: source_quote is not verbatim in the named source")
        if _numeric_claims(item["statement"]) - _numeric_claims(quote):
            checks["unsupported_claims"]["errors"].append(f"{prefix}: statement adds numeric claims absent from its quote")
    if seen != set(requirements):
        checks["support_levels"]["errors"].append("Output must cover every fixture requirement exactly once")


def _check_receipt(case: dict, output: dict, checks: dict) -> None:
    checks["citations"]["passed"] = True
    checks["submission_evidence"]["passed"] = True
    sources = {source["id"]: source for source in case["sources"]}
    references = output["evidence_refs"]
    if len(set(references)) != len(references) or any(ref not in sources for ref in references):
        checks["citations"]["errors"].append("evidence_refs must be unique registered source IDs")
    if output["job_url"] != case["job_url"]:
        checks["submission_evidence"]["errors"].append("Result job_url differs from the exact fixture job")
    if output["status"] == "submitted":
        confirmed = any(
            source.get("kind") == "receipt" and source.get("status") == "confirmed"
            and source.get("job_url") == case["job_url"] and source.get("receipt_id")
            for ref in references if (source := sources.get(ref)) is not None
        )
        if not confirmed:
            checks["submission_evidence"]["errors"].append("Submitted requires a confirmed receipt for the exact job; clicks are insufficient")


def evaluate_case(case: dict, output: object = _MISSING) -> dict:
    """Return auditable checks, with semantic truth limited to the fixture oracle."""
    checks = {name: {"passed": None, "errors": []} for name in CHECK_NAMES}
    try:
        validate_case(case)
        checks["fixture"]["passed"] = True
    except (TypeError, ValueError) as exc:
        checks["fixture"] = {"passed": False, "errors": [str(exc)]}
    if checks["fixture"]["passed"]:
        try:
            parsed = _parse_output(case.get("output") if output is _MISSING else output)
            if case["kind"] == "evidence":
                items = _evidence_schema(parsed)
            else:
                fields = {"job_url", "status", "evidence_refs"}
                _keys(parsed, fields, fields, "output")
                _text(parsed["job_url"], "output.job_url")
                if parsed["status"] not in SUBMISSION_STATUSES:
                    raise ValueError("Invalid output.status")
                for reference in _array(parsed["evidence_refs"], "evidence_refs", nonempty=False):
                    _text(reference, "evidence reference")
            checks["schema"]["passed"] = True
        except (TypeError, ValueError) as exc:
            checks["schema"] = {"passed": False, "errors": [str(exc)]}
        if checks["schema"]["passed"]:
            if case["kind"] == "evidence":
                _check_evidence(case, items, checks)
            else:
                _check_receipt(case, parsed, checks)
    for check in checks.values():
        if check["errors"]:
            check["passed"] = False
    applicable = [check for check in checks.values() if check["passed"] is not None]
    passed = bool(applicable) and all(check["passed"] for check in applicable)
    return {"id": case.get("id") if isinstance(case, dict) else None, "passed": passed,
            "score": sum(check["passed"] is True for check in applicable) / len(applicable),
            "scope": "deterministic_contract_and_fixture_evidence", "checks": checks}


def load_suite(payload: object) -> list[dict]:
    _keys(payload, {"schema_version", "cases"}, {"schema_version", "cases"}, "suite")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("suite.schema_version must be 1")
    cases = _array(payload["cases"], "suite.cases")
    identities = set()
    for case in cases:
        validate_case(case)
        if case["id"] in identities:
            raise ValueError("Duplicate case.id")
        identities.add(case["id"])
    return cases


def run_suite(payload: object) -> dict:
    cases = load_suite(payload)
    results = [evaluate_case(case) for case in cases]
    return {"passed": all(result["passed"] for result in results), "case_count": len(results),
            "failed_count": sum(not result["passed"] for result in results),
            "score": sum(result["score"] for result in results) / len(results), "results": results}


def get_assert(output: object, context: dict) -> dict:
    """Promptfoo external Python assertion; uses the same evaluator as the CLI."""
    try:
        case = context["vars"]["case"]
        if isinstance(case, str):
            case = json.loads(case)
        result = evaluate_case(case, output)
    except (KeyError, TypeError, ValueError) as exc:
        return {"pass": False, "score": 0.0, "reason": f"Invalid assertion context: {exc}"}
    errors = [f"{name}: {error}" for name, check in result["checks"].items() for error in check["errors"]]
    return {"pass": result["passed"], "score": result["score"],
            "reason": "; ".join(errors) if errors else "All applicable deterministic contract checks passed"}


def export_promptfoo(payload: object, output_dir: Path) -> dict:
    """Create a new replay-only config directory, refusing all existing targets."""
    import yaml

    cases = load_suite(payload)
    config = {"description": "ApplyPilot offline contract replay (no model quality benchmark)",
              "providers": ["echo"], "prompts": ["{{ output }}"], "tests": []}
    for case in cases:
        output = case.get("output")
        config["tests"].append({
            "description": case["id"],
            "vars": {"case": {key: value for key, value in case.items() if key != "output"},
                     "output": output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)},
            "assert": [{"type": "python", "value": "file://assertion.py"}],
        })
    # mkdir(exist_ok=False) is the overwrite boundary, including an empty directory.
    output_dir.mkdir(parents=True, exist_ok=False)
    written = []
    try:
        for name, text in (("promptfooconfig.yaml", yaml.safe_dump(config, allow_unicode=True, sort_keys=False)),
                           ("assertion.py", ASSERTION_SCRIPT)):
            path = output_dir / name
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                written.append(path)
                stream.write(text)
    except OSError:
        for path in written:
            path.unlink()
        output_dir.rmdir()
        raise
    return {"directory": str(output_dir.resolve()), "config": str((output_dir / "promptfooconfig.yaml").resolve()),
            "case_count": len(cases), "provider": "echo", "model_calls": 0}
