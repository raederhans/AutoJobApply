"""Trusted attending-operator cover requirement evidence; no browser actions."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from applypilot.apply.visual_bridge import read_active_host

_COVER = re.compile(r"\bcover\s*letter\b", re.IGNORECASE)


def validate_cover_observation(job: dict, bridge_dir: Path, observation_file: Path) -> dict:
    """Admit a fresh, exact-page host inspection plus explicit whole-form review."""
    host = read_active_host(bridge_dir)
    application_url = str(job.get("application_url") or job["url"])
    data = json.loads(observation_file.read_text(encoding="utf-8-sig"))
    if (not isinstance(data, dict) or data.get("source") != "attending_host"
            or data.get("all_form_checked") is not True):
        raise ValueError("independent attending_host whole-form review required")
    observed = datetime.fromisoformat(str(data.get("observed_at") or ""))
    if observed.tzinfo is None or not 0 <= (datetime.now(UTC) - observed).total_seconds() <= 300:
        raise ValueError("cover observation must be timezone-aware and within five minutes")
    refs = data.get("evidence_refs")
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise ValueError("nonempty cover observation evidence_refs required")
    if (host.surface != "browser" or host.phase != "prepare" or host.target.get("runtime") != "iab"
            or host.target.get("application_url") != application_url or data.get("target") != host.target
            or data.get("session_id") != host.session_id or data.get("phase") != "prepare"
            or data.get("submission_authorized") is not False):
        raise ValueError("cover observation must match the active exact IAB prepare host")
    content = data.get("content")
    if not isinstance(content, list):
        raise TypeError("host inspection content required")
    reports, contexts, page_text = [], [], []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
            continue
        try:
            value = json.loads(item["text"])
        except ValueError:
            page_text.append(item["text"])
            continue
        if isinstance(value, dict) and "form_state" in value:
            reports.append(value["form_state"])
        if isinstance(value, dict) and "tab_id" in value and "page_url" in value:
            contexts.append(value)
    if (len(reports) != 1 or not isinstance(reports[0], dict) or len(contexts) != 1
            or contexts[0].get("tab_id") != host.target["tab_id"]
            or contexts[0].get("page_url") != application_url or reports[0].get("page_url") != application_url):
        raise ValueError("one exact current tab context and form_state required")
    form = reports[0]
    coverage = form.get("coverage")
    if (not isinstance(coverage, dict) or coverage.get("scope") != "visible_top_document_open_shadow"
            or type(coverage.get("iframe_count")) is not int or coverage["iframe_count"] != 0):
        raise ValueError("complete supported form observation without unreviewed frames required")
    fields = form.get("fields")
    if not isinstance(fields, list) or not fields or any(not isinstance(field, dict) for field in fields):
        raise ValueError("observed form fields required")
    cover_fields = [field for field in fields if _COVER.search(str(field.get("label") or ""))]
    proof = data.get("cover_letter")
    if not isinstance(proof, dict) or proof.get("operator_attested") is not True:
        raise ValueError("explicit operator cover requirement attestation required")
    status = proof.get("status")
    if status == "absent":
        if cover_fields or proof.get("field_keys") != [] or any(_COVER.search(text) for text in page_text):
            raise ValueError("cover absence conflicts with current observed form")
    elif status == "optional":
        keys = [field.get("field_key") for field in cover_fields]
        if (not keys or any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys)
                or proof.get("field_keys") != keys or any(field.get("required") is not False
                or field.get("required_source") != "not_asserted" for field in cover_fields)):
            raise ValueError("optional cover proof must bind all observed unmarked cover fields")
        evidence = proof.get("evidence_text")
        if (not isinstance(evidence, str) or not evidence.strip() or not _COVER.search(evidence)
                or not any(evidence in text for text in page_text)):
            raise ValueError("cover evidence_text must occur in the actual page snapshot")
        basis = proof.get("basis")
        if basis == "explicit_optional_text":
            if not re.search(r"\boptional\b", evidence, re.IGNORECASE):
                raise ValueError("explicit optional wording required")
        elif basis == "visible_required_marker_convention":
            marked = {field.get("field_key") for field in fields if field.get("required") is True
                      and field.get("required_source") == "visible_label" and isinstance(field.get("field_key"), str)}
            if len(marked) < 2:
                raise ValueError("visible required-marker convention needs observed required peers")
        else:
            raise ValueError("supported reviewed optional cover basis required")
    else:
        raise ValueError("cover observation status must be optional or absent")
    if read_active_host(bridge_dir) != host:
        raise ValueError("cover observation host changed during review")
    return {
        "source": "attending_host", "observed_at": data["observed_at"], "evidence_refs": refs,
        "host_session_id": host.session_id, "tab_id": host.target["tab_id"], "page_url": application_url,
        "status": status, "basis": proof.get("basis", "whole_form_absence_review"),
        "observation_sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
    }
