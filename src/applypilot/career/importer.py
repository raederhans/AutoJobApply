"""Validate and atomically import the public portion of a research bundle.

No globbing or execution: only the two catalogs and their allowed public source
files are read. Candidate material, diagnostics and scripts are never imported.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import sqlite3
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath

from bs4 import BeautifulSoup

from applypilot.career.contracts import BundleValidationError, content_id, json_text, required_text
from applypilot.storage import career
from applypilot.storage.transactions import write_transaction

# Explicit identities, never inferred from a similar spelling or URL substring.
_COMPANIES = {
    "Amazon": {"company_id": "amazon", "name": "Amazon", "aliases": ["Amazon"], "domains": ["amazon.jobs"]},
    "Google": {"company_id": "google", "name": "Google", "aliases": ["Google"], "domains": ["google.com"]},
    "Microsoft": {"company_id": "microsoft", "name": "Microsoft", "aliases": ["Microsoft"], "domains": ["microsoft.com"]},
    "NVIDIA": {"company_id": "nvidia", "name": "NVIDIA", "aliases": ["NVIDIA"], "domains": ["nvidia.com"]},
}
_STAGES = {"understand", "guided", "routine", "adaptive"}
_SUFFIXES = {".txt", ".html", ".json", ".csv"}


def _objects(document: dict, key: str) -> list[dict]:
    rows = document.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise BundleValidationError(f"{key} must be an array of objects")
    return rows


def _index(rows: list[dict], label: str) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for row in rows:
        identity = required_text(row.get("id"), f"{label}.id")
        if identity in indexed:
            raise BundleValidationError(f"duplicate {label} ID: {identity}")
        indexed[identity] = row
    return indexed


def _allowed_source(path: PurePosixPath) -> bool:
    parts = path.parts
    return (path.suffix.lower() in _SUFFIXES and (
        len(parts) >= 3 and parts[:2] == ("skills", "snapshots")
        or len(parts) >= 4 and parts[0] == "companies" and parts[2] in {"raw", "snapshots", "bodies"}
    ))


def _read(root: Path, relative: str, *, catalog: bool = False) -> bytes:
    required_text(relative, "snapshot path")
    path = PurePosixPath(relative)
    if ("\\" in relative or path.is_absolute() or PureWindowsPath(relative).drive
            or any(part in {"..", "."} for part in relative.split("/"))):
        raise BundleValidationError(f"snapshot path must be relative and contained: {relative}")
    if catalog:
        if relative not in {"companies/jobs.json", "skills/rubrics.json"}:
            raise BundleValidationError(f"unapproved catalog: {relative}")
    elif not _allowed_source(path):
        raise BundleValidationError(f"not an allowed public source: {relative}")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise BundleValidationError(f"snapshot escapes bundle root: {relative}")
    resolved_relative = PurePosixPath(resolved.relative_to(root).as_posix())
    if (catalog and resolved_relative.as_posix() != relative
            or not catalog and not _allowed_source(resolved_relative)):
        raise BundleValidationError(f"snapshot resolves outside public source boundary: {relative}")
    if not resolved.is_file():
        raise BundleValidationError(f"missing snapshot: {relative}")
    return resolved.read_bytes()


def _load_json(raw: bytes, label: str) -> dict:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise BundleValidationError(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BundleValidationError(f"invalid JSON: {label}") from exc
    if not isinstance(data, dict):
        raise BundleValidationError(f"{label} must contain an object")
    return data


def _normal(text: str) -> str:
    collapsed = " ".join(unicodedata.normalize("NFKC", html.unescape(text)).split())
    # HTML emphasis can split a date from its following comma. This preserves
    # words and punctuation while ignoring that rendering-only whitespace.
    return re.sub(r"\s+([,.;:!?\)])", r"\1", collapsed)


def _html_text(text: str) -> str:
    soup = BeautifulSoup(text, "html.parser")
    # Visible body and JSON string values are separate sources of text. Do not
    # allow arbitrary script comments to serve as a quotation from a JD.
    for tag in soup(["script", "style"]):
        tag.decompose()
    for tag in soup(["p", "div", "li", "br", "h1", "h2", "h3", "tr", "td", "section"]):
        tag.insert_before("\n")
        tag.insert_after("\n")
    # Inline emphasis may occur in the middle of a word; never insert a space
    # between those text nodes. Block boundaries still separate sentences.
    return soup.get_text()


def _source_text(raw: bytes, relative: str) -> str:
    text = raw.decode("utf-8-sig")
    if relative.endswith(".html"):
        return _html_text(text)
    if relative.endswith(".json"):
        values: list[str] = []

        def strings(value):
            if isinstance(value, str):
                values.append(_html_text(value) if "<" in value else value)
            elif isinstance(value, dict):
                for child in value.values():
                    strings(child)
            elif isinstance(value, list):
                for child in value:
                    strings(child)

        strings(json.loads(text))
        return "\n".join(values)
    return text


def _references(row: dict, key: str, known: dict, label: str) -> None:
    refs = row.get(key, [])
    if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in known for ref in refs):
        raise BundleValidationError(f"invalid {label}.{key}")


def _validate_catalog(catalog: dict, files: dict[str, bytes]) -> tuple[int, int]:
    sources = _index(_objects(catalog, "sources"), "source")
    skills = _index(_objects(catalog, "skills"), "skill")
    anchors: dict[str, dict] = {}
    for skill_id, skill in skills.items():
        _references(skill, "prerequisites", skills, skill_id)
        for ref in _objects({"refs": skill.get("source_refs", [])}, "refs"):
            if ref.get("source_id") not in sources:
                raise BundleValidationError(f"unknown source reference: {skill_id}")
        stages = set()
        for anchor in _objects(skill, "anchors"):
            anchor_id = required_text(anchor.get("id"), "anchor.id")
            if anchor_id in anchors:
                raise BundleValidationError(f"duplicate anchor ID: {anchor_id}")
            if anchor.get("stage") not in _STAGES or anchor["stage"] in stages:
                raise BundleValidationError(f"invalid or duplicate stage: {anchor_id}")
            stages.add(anchor["stage"])
            if anchor_id != f"{skill_id}.{anchor['stage']}":
                raise BundleValidationError(f"anchor does not belong to skill: {anchor_id}")
            for field in ("task", "context", "deliverable", "support", "limits", "provenance"):
                required_text(anchor.get(field), f"{anchor_id}.{field}")
            acceptance = anchor.get("acceptance")
            if not isinstance(acceptance, list) or not acceptance:
                raise BundleValidationError(f"missing acceptance: {anchor_id}")
            for criterion in acceptance:
                required_text(criterion, f"{anchor_id}.acceptance")
            anchors[anchor_id] = anchor
        if stages != _STAGES:
            raise BundleValidationError(f"incomplete anchor stages: {skill_id}")
        learning = skill.get("learning_task", {})
        if not isinstance(learning, dict) or learning.get("anchor_id") not in {a["id"] for a in skill["anchors"]}:
            raise BundleValidationError(f"unknown learning anchor: {skill_id}")
        for mapping in _objects({"mappings": skill.get("native_mappings", [])}, "mappings"):
            source = sources.get(mapping.get("source_id"))
            if source is None:
                raise BundleValidationError(f"unknown native mapping source: {skill_id}")
            path = mapping.get("snapshot_path")
            if path not in source.get("snapshot_paths", []):
                raise BundleValidationError(f"unregistered native mapping snapshot: {skill_id}")
            text = files[path].decode("utf-8-sig")
            columns = {"native_role": "Role", "native_role_level": "Role Level",
                       "native_skill": "Skill Name", "native_level": "Skill Level"}
            if path.endswith(".csv"):
                rows = list(csv.DictReader(io.StringIO(text)))
                if not any(all(_normal(row.get(column, "")) == _normal(mapping[field])
                               for field, column in columns.items()) for row in rows):
                    raise BundleValidationError(f"native mapping not found in source: {skill_id}")
                record = re.search(r"CSV logical record=(\d+)", mapping.get("locator", ""))
                if record:
                    offset = int(record.group(1)) - 2
                    if not 0 <= offset < len(rows) or not all(
                        _normal(rows[offset].get(column, "")) == _normal(mapping[field])
                        for field, column in columns.items()
                    ):
                        raise BundleValidationError(f"incorrect native mapping locator: {skill_id}")
            elif any(_normal(required_text(mapping.get(field), field)) not in _normal(text) for field in columns):
                raise BundleValidationError(f"native mapping not found in source: {skill_id}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(skill_id: str):
        if skill_id in visiting:
            raise BundleValidationError("cyclic skill prerequisites")
        if skill_id in visited:
            return
        visiting.add(skill_id)
        for prerequisite in skills[skill_id].get("prerequisites", []):
            visit(prerequisite)
        visiting.remove(skill_id)
        visited.add(skill_id)

    for skill_id in skills:
        visit(skill_id)
    return len(skills), len(anchors)


def _company_map(document: dict) -> dict[str, dict]:
    definitions = document.get("companies")
    if definitions is None:
        return _COMPANIES.copy()
    if not isinstance(definitions, list):
        raise BundleValidationError("companies must be an explicit array of identities")
    aliases: dict[str, dict] = {}
    identities: set[str] = set()
    for company in definitions:
        if not isinstance(company, dict):
            raise BundleValidationError("invalid company identity")
        identity = required_text(company.get("company_id"), "company_id")
        name = required_text(company.get("name"), "company.name")
        if identity in identities:
            raise BundleValidationError("duplicate company identity")
        identities.add(identity)
        names = company.get("aliases", [name])
        if not isinstance(names, list):
            raise BundleValidationError("company aliases must be a list")
        for alias in [name, *names]:
            required_text(alias, "company alias")
            if alias in aliases and aliases[alias]["company_id"] != identity:
                raise BundleValidationError("ambiguous company alias")
            aliases[alias] = {**company, "aliases": list(dict.fromkeys([name, *names]))}
    return aliases


def _validate_bundle(root: Path) -> dict:
    jobs_raw = _read(root, "companies/jobs.json", catalog=True)
    catalog_raw = _read(root, "skills/rubrics.json", catalog=True)
    document = _load_json(jobs_raw, "jobs catalog")
    catalog = _load_json(catalog_raw, "rubric catalog")
    companies = _company_map(document)
    jobs = _index(_objects(document, "jobs"), "job")
    sources = _index(_objects(catalog, "sources"), "source")
    files: dict[str, bytes] = {}
    for source in sources.values():
        paths = source.get("snapshot_paths", [])
        if not isinstance(paths, list):
            raise BundleValidationError("source.snapshot_paths must be a list")
        for path in paths:
            files[path] = _read(root, path)
    skill_count, anchor_count = _validate_catalog(catalog, files)
    skill_ids = {skill["id"]: skill for skill in catalog["skills"]}
    all_requirements: set[str] = set()
    prepared_jobs = []
    used_companies: dict[str, dict] = {}
    for job_id, job in jobs.items():
        company = companies.get(job.get("company"))
        if company is None or job.get("company_id", company["company_id"]) != company["company_id"]:
            raise BundleValidationError(f"unknown or conflicting company identity: {job_id}")
        required_text(job.get("title"), "job.title")
        required_text(job.get("url"), "job.url")
        if job.get("availability") not in {"unknown", "listed_at_observation", "closed_at_observation", "historical"}:
            raise BundleValidationError(f"unsupported research availability: {job_id}")
        if job.get("content_status") not in {"full_text", "partial_text", "index_only"}:
            raise BundleValidationError(f"unsupported research content_status: {job_id}")
        # Research imports cannot carry Radar admission/verification authority.
        if any(key in job for key in ("verification_status", "official_open", "apply_status", "readiness")):
            raise BundleValidationError(f"application authority is not a research field: {job_id}")
        snapshot_path = required_text(job.get("snapshot_path"), "job.snapshot_path")
        body_path = job.get("body_path", snapshot_path)
        raw_paths = job.get("raw_source_paths", [])
        if not isinstance(raw_paths, list):
            raise BundleValidationError(f"raw_source_paths must be a list: {job_id}")
        paths = list(dict.fromkeys([snapshot_path, body_path, *raw_paths]))
        for path in paths:
            files[path] = _read(root, path)
        body_text = _source_text(files[body_path], body_path)
        source_paths = raw_paths or [snapshot_path]
        source_text = "\n".join(_source_text(files[path], path) for path in source_paths)
        normalized_body, normalized_source = _normal(body_text), _normal(source_text)
        requirements = _index(_objects(job, "requirements"), "requirement")
        for requirement_id, requirement in requirements.items():
            if requirement_id in all_requirements:
                raise BundleValidationError(f"duplicate requirement ID: {requirement_id}")
            all_requirements.add(requirement_id)
            quote = required_text(requirement.get("text"), "requirement.text")
            quote = re.sub(r"^\s*[-*•]\s+", "", quote)
            if _normal(quote) not in normalized_body or _normal(quote) not in normalized_source:
                raise BundleValidationError(f"requirement quotation not found in source: {requirement_id}")
            _references(requirement, "skill_ids", skill_ids, requirement_id)
            if requirement.get("source_job_id", job_id) != job_id:
                raise BundleValidationError(f"incorrect requirement job reference: {requirement_id}")
            if requirement.get("snapshot_path", snapshot_path) not in paths:
                raise BundleValidationError(f"incorrect requirement snapshot reference: {requirement_id}")
        prepared_jobs.append({"payload": job, "company_id": company["company_id"],
                              "body_text": body_text, "paths": paths})
        used_companies[company["company_id"]] = company
    return {"catalog": catalog, "catalog_raw": catalog_raw, "jobs": prepared_jobs,
            "companies": used_companies, "files": files, "skills": skill_count, "anchors": anchor_count}


def import_bundle(connection: sqlite3.Connection, bundle_dir: Path | str) -> dict:
    """Validate everything before one transaction; append changed immutable versions."""
    try:
        prepared = _validate_bundle(Path(bundle_dir).resolve())
    except (ValueError, KeyError, TypeError, UnicodeError, OSError) as exc:
        if isinstance(exc, BundleValidationError):
            raise
        raise BundleValidationError(str(exc)) from exc
    career._require_write_schema(connection)
    summary = {"companies": len(prepared["companies"]), "jobs": len(prepared["jobs"]),
               "skills": prepared["skills"], "anchors": prepared["anchors"],
               "snapshots_inserted": 0, "jobs_inserted": 0, "catalog_inserted": False}
    with write_transaction(connection):
        existing = career.list_companies(connection)
        aliases = {alias: row["company_id"] for row in existing for alias in row["aliases"]}
        identities = {row["company_id"]: row for row in existing}
        for identity, company in prepared["companies"].items():
            if identity in identities and identities[identity]["identity"] != company:
                raise BundleValidationError(f"company identity changed: {identity}")
            if any(alias in aliases and aliases[alias] != identity for alias in company["aliases"]):
                raise BundleValidationError(f"company alias belongs to another identity: {identity}")
            connection.execute("""INSERT OR IGNORE INTO career_companies
                (company_id,name,aliases_json,identity_json,created_at) VALUES (?,?,?,?,?)""",
                (identity, company["name"], json_text(company["aliases"]), json_text(company), career._now()))
        file_ids: dict[str, str] = {}
        for path, raw in prepared["files"].items():
            snapshot_id, inserted = career._put_snapshot(connection, kind="public_source", source_id=path,
                payload={"relative_path": path}, raw_bytes=raw)
            file_ids[path] = snapshot_id
            summary["snapshots_inserted"] += int(inserted)
        catalog_sources = {path: file_ids[path] for source in prepared["catalog"]["sources"]
                           for path in source.get("snapshot_paths", [])}
        catalog_id = content_id("catalog", {"catalog": prepared["catalog"], "sources": catalog_sources}, prepared["catalog_raw"])
        summary["catalog_id"] = catalog_id
        cursor = connection.execute("""INSERT OR IGNORE INTO career_catalogs
            (catalog_id,payload_json,raw_bytes,source_snapshot_ids_json,created_at) VALUES (?,?,?,?,?)""",
            (catalog_id, json_text(prepared["catalog"]), prepared["catalog_raw"], json_text(catalog_sources), career._now()))
        summary["catalog_inserted"] = cursor.rowcount == 1
        for job in prepared["jobs"]:
            payload = {**job["payload"], "public_source_snapshot_ids": {path: file_ids[path] for path in job["paths"]}}
            _, inserted = career._put_snapshot(connection, kind="research_jd", source_id=payload["id"],
                company_id=job["company_id"], payload=payload, raw_bytes=job["body_text"].encode("utf-8"),
                body_text=job["body_text"], observed_at=payload.get("observed_at"), source_url=payload["url"])
            summary["jobs_inserted"] += int(inserted)
            summary["snapshots_inserted"] += int(inserted)
    return summary
