"""Explicit, source-checked material selection for career diagnosis.

This registry is evidence of documented experience, never a skills assessment.
No file is found by crawling the workspace and no source is fetched from a URL.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

KINDS = {
    "candidate_fact_master", "project_fact_context", "resume_copy",
    "candidate_facts", "project_evidence", "resume", "generated",
}
ORIGINAL_KINDS = {"candidate_fact_master", "project_fact_context", "candidate_facts", "project_evidence"}


def digest(value: Any) -> str:
    """Version identity for canonical JSON inputs, not a factual trust signal."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _local_path(value: str, base: Path, roots: list[Path]) -> Path:
    if not value or "://" in value or value.startswith(("\\\\", "//")):
        raise ValueError("evidence_path_not_local")
    p = Path(value)
    p = (p if p.is_absolute() else base / p).resolve(strict=True)
    if not p.is_file() or not any(p.is_relative_to(root) for root in roots):
        raise ValueError("evidence_path_outside_selected_roots")
    if p.suffix.lower() not in {".txt", ".md"}:
        raise ValueError("evidence_requires_text_export: select an existing .txt or .md material")
    if p.stat().st_size > 2_000_000:
        raise ValueError("evidence_material_too_large")
    return p


def _convert_writing_registry(raw: dict) -> dict:
    """Adapt the existing writing registry without promoting its source kinds."""
    return {
        "candidate_label": raw.get("candidate_label", "Selected candidate"),
        "materials": [{**s, "kind": s["kind"]} for s in raw.get("sources", [])
                      if s.get("kind") in KINDS],
        "evidence": [{"id": e["id"], "claim": e["quote"],
                      "source_refs": [{"material_id": e["source_id"], "quote": e["quote"]}],
                      "ownership": e.get("scope", "not_recorded"),
                      "support_type": "registry_" + e.get("status", "unknown"),
                      "does_not_prove": e.get("boundaries", []),
                      "demonstrates": e.get("tags", [])}
                     for e in raw.get("candidate_evidence", [])],
        "boundaries": raw.get("boundaries", []),
    }


def load_evidence(registry_path: Path | str, *, allowed_roots: list[Path | str] | None = None) -> dict:
    """Read only explicitly registered text materials, checking each quoted span.

    Additional roots are selected by the local operator, not trusted from the
    imported JSON. Absolute source paths outside those roots are rejected.
    """
    registry = Path(registry_path).resolve(strict=True)
    raw = json.loads(registry.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise TypeError("evidence_registry_must_be_object")
    if "sources" in raw and "candidate_evidence" in raw:
        raw = _convert_writing_registry(raw)
    roots = [registry.parent, *(Path(r).resolve(strict=True) for r in (allowed_roots or []))]
    materials: dict[str, dict] = {}
    for source in raw.get("materials", []):
        sid = source.get("id")
        if not isinstance(sid, str) or not sid or sid in materials:
            raise ValueError("evidence_material_id_invalid_or_duplicate")
        kind = source.get("kind")
        if kind not in KINDS:
            raise ValueError(f"evidence_material_kind_invalid: {sid}")
        if source.get("path"):
            path = _local_path(source["path"], registry.parent, roots)
            text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
            path_value = str(path)
            # Embedded copies must not obscure a changed original file.
            if "text" in source and source["text"].replace("\r\n", "\n") != text:
                raise ValueError(f"evidence_embedded_text_changed: {sid}")
        else:
            text = source.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"evidence_material_missing_text: {sid}")
            text = text.replace("\r\n", "\n")
            path_value = None
        materials[sid] = {
            "id": sid, "kind": kind, "path": path_value, "text": text,
            "snapshot_digest": digest(text), "title": source.get("title", sid),
            "authority": "original_record" if kind in ORIGINAL_KINDS else "non_authoritative",
        }
    if not materials:
        raise ValueError("evidence_materials_empty")
    records, ids = [], set()
    for item in raw.get("evidence", []):
        eid = item.get("id")
        if not isinstance(eid, str) or not eid or eid in ids:
            raise ValueError("evidence_id_invalid_or_duplicate")
        ids.add(eid)
        refs = []
        for ref in item.get("source_refs", []):
            source = materials.get(ref.get("material_id"))
            quote = ref.get("quote")
            if source is None or not isinstance(quote, str) or not quote.strip():
                raise ValueError(f"evidence_reference_invalid: {eid}")
            quote = quote.replace("\r\n", "\n")
            start, end = ref.get("line_start"), ref.get("line_end")
            if start is not None or end is not None:
                if (type(start) is not int or type(end) is not int or start < 1 or end < start):
                    raise ValueError(f"evidence_line_range_invalid: {eid}")
                lines = source["text"].splitlines()
                if end > len(lines) or quote not in "\n".join(lines[start - 1:end]):
                    raise ValueError(f"evidence_quote_changed: {eid}")
            elif quote not in source["text"]:
                raise ValueError(f"evidence_quote_not_found: {eid}")
            refs.append({"material_id": source["id"], "quote": quote,
                         "line_start": start, "line_end": end, "kind": source["kind"],
                         "snapshot_digest": source["snapshot_digest"]})
        if not refs:
            raise ValueError(f"evidence_requires_source: {eid}")
        claim = item.get("claim", item.get("claim_zh", ""))
        if not isinstance(claim, str) or not claim.strip():
            raise ValueError(f"evidence_claim_missing: {eid}")
        records.append({
            "id": eid, "claim": claim, "claim_zh": claim, "project": item.get("project", ""),
            "source_refs": refs, "ownership": item.get("ownership", "not_recorded"),
            "support_type": item.get("support_type", "documented_claim"),
            "demonstrates": item.get("demonstrates", []), "skills": item.get("skills", []),
            "does_not_prove": item.get("does_not_prove", []),
            "source_checked": True, "assessment_status": "documented_not_skill_tested",
            "authoritative": all(r["kind"] in ORIGINAL_KINDS for r in refs),
        })
    if not records:
        raise ValueError("evidence_records_empty")
    result = {"schema_version": "career-evidence-1", "candidate_label": raw.get("candidate_label", "Selected candidate"),
              "materials": list(materials.values()), "evidence": records,
              "boundaries": raw.get("boundaries", []), "measured_skill_levels": 0}
    result["fingerprint"] = digest(result)
    result["selection"] = {"registry_path": str(registry), "allowed_roots": [str(p) for p in roots]}
    return result


def require_current(evidence: dict) -> None:
    selection = evidence["selection"]
    if "components" in selection:
        components = selection["components"]
        current = [_reload_selection(item["selection"]) for item in components]
        if any(item["fingerprint"] != now["fingerprint"] for item, now in zip(components, current, strict=True)):
            raise ValueError("input_changed: supplemented evidence changed; import the new version")
        combined = _merge(current)
        if combined["fingerprint"] != evidence["fingerprint"]:
            raise ValueError("input_changed: combined evidence changed")
        return
    current = load_evidence(selection["registry_path"], allowed_roots=selection["allowed_roots"])
    if current["fingerprint"] != evidence["fingerprint"]:
        raise ValueError("input_changed: candidate evidence changed; import the new version")


def _reload_selection(selection: dict) -> dict:
    if "components" in selection:
        loaded = [_reload_selection(item["selection"]) for item in selection["components"]]
        result = _merge(loaded)
        result["selection"] = selection
        return result
    return load_evidence(selection["registry_path"], allowed_roots=selection["allowed_roots"])


def _merge(parts: list[dict]) -> dict:
    if not parts:
        raise ValueError("evidence_components_empty")
    result = {k: v for k, v in parts[0].items() if k not in {"fingerprint", "selection"}}
    for key in ("materials", "evidence"):
        entries = {}
        for part in parts:
            for entry in part[key]:
                if entry["id"] in entries and entries[entry["id"]] != entry:
                    raise ValueError(f"supplement_id_conflict: {key}/{entry['id']}; use a distinct ID")
                entries[entry["id"]] = entry
        result[key] = list(entries.values())
    result["boundaries"] = list(dict.fromkeys(boundary for part in parts for boundary in part["boundaries"]))
    result["fingerprint"] = digest(result)
    return result


def combine_evidence(base: dict, supplement: dict) -> dict:
    """Add explicitly selected evidence, retaining all source/version boundaries."""
    for part in (base, supplement):
        require_current(part)
    result = _merge([base, supplement])
    result["selection"] = {"components": [
        {"selection": part["selection"], "fingerprint": part["fingerprint"]} for part in (base, supplement)]}
    return result
