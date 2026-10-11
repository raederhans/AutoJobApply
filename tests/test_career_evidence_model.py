"""Fictional candidate fixtures and paid-request recovery contracts."""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from applypilot.career.evidence import load_evidence, require_current
from applypilot.career.model import suggest, validate_suggestions
from applypilot.storage.career import ensure_schema


def evidence_registry(tmp_path):
    material = tmp_path / "project.md"
    material.write_text("Synthetic candidate.\nI built a Python report.\n", encoding="utf-8")
    registry = tmp_path / "evidence.json"
    data = {"materials": [{"id": "m1", "path": "project.md", "kind": "project_fact_context"}],
            "evidence": [{"id": "e1", "claim": "Built a Python report", "ownership": "personal",
                          "demonstrates": ["python"], "does_not_prove": ["C++ capability"],
                          "source_refs": [{"material_id": "m1", "quote": "I built a Python report.",
                                           "line_start": 2, "line_end": 2}]}]}
    registry.write_text(json.dumps(data), encoding="utf-8")
    return registry, material, data


def test_current_sources_and_quotes_not_cached_readback(tmp_path):
    registry, material, _data = evidence_registry(tmp_path)
    first = load_evidence(registry)
    assert first["measured_skill_levels"] == 0
    assert first["evidence"][0]["assessment_status"] == "documented_not_skill_tested"
    assert load_evidence(registry)["fingerprint"] == first["fingerprint"]
    material.write_text("Synthetic candidate.\nI built a Java report.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="quote_changed"):
        require_current(first)
    material.write_text("Extra line\nSynthetic candidate.\nI built a Python report.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="quote_changed"):
        load_evidence(registry)


def test_selected_roots_and_generated_authority(tmp_path):
    _registry, material, data = evidence_registry(tmp_path)
    nested = tmp_path / "registry"
    nested.mkdir()
    data["materials"][0].update(path=str(material), kind="generated")
    selected = nested / "selection.json"
    selected.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="outside_selected_roots"):
        load_evidence(selected)
    result = load_evidence(selected, allowed_roots=[tmp_path])
    assert result["evidence"][0]["authoritative"] is False
    data["materials"].append(dict(data["materials"][0]))
    selected.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_evidence(selected, allowed_roots=[tmp_path])


def model_args(tmp_path, callback):
    registry, _, _ = evidence_registry(tmp_path)
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    return conn, {"target": {"target_id": "t", "revision": 1, "input_fingerprint": "f"},
                  "input_digest": "input", "jobs": [{"requirements": [{"id": "r", "text": "Use Python"}]}],
                  "evidence": load_evidence(registry),
                  "client": SimpleNamespace(base_url="https://model.example/v1", model="fake", chat_once=callback)}


def test_model_response_persisted_before_validation_and_reused(tmp_path):
    calls = []
    def chat(*args, **kwargs):
        calls.append(1)
        return {"content": json.dumps({"suggestions": [{"requirement_id": "r", "evidence_id": "e1",
                                                        "relation": "related", "reason": "有Python项目材料"}]}),
                "request_id": "req", "usage": {"total_tokens": 50}}
    conn, args = model_args(tmp_path, chat)
    a, meta = suggest(conn, **args)
    b, second = suggest(conn, **args)
    assert a == b and calls == [1]
    assert meta["response_reused"] is False and second["response_reused"] is True
    assert conn.execute("SELECT state,usage_known FROM career_model_attempts").fetchone() == ("response_saved", 1)


def test_valid_model_ids_do_not_make_invented_explanation_publishable(tmp_path):
    registry, _, _ = evidence_registry(tmp_path)
    evidence = load_evidence(registry)
    content = json.dumps({"suggestions": [{"requirement_id": "r", "evidence_id": "e1", "relation": "related",
                                           "reason": "Expert in C++ microservices with production performance gains."}]})
    result = validate_suggestions(content, [{"requirements": [{"id": "r"}]}], evidence)
    assert result[0]["evidence_id"] == "e1"
    assert "Built a Python report" in result[0]["reason"]
    assert "microservices" not in result[0]["reason"] and "Expert" not in result[0]["reason"]
    assert "不代表技能熟练度或要求已满足" in result[0]["reason"]


def test_timeout_requires_explicit_retry_and_preserves_prior_attempt(tmp_path):
    calls = []
    def timeout(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("synthetic timeout")
    conn, args = model_args(tmp_path, timeout)
    with pytest.raises(TimeoutError):
        suggest(conn, **args)
    with pytest.raises(ValueError, match="outcome_unknown"):
        suggest(conn, **args)
    assert len(calls) == 1
    with pytest.raises(TimeoutError):
        suggest(conn, **args, retry_unknown=True)
    assert len(calls) == 2
    rows = conn.execute("SELECT state,supersedes_attempt_id FROM career_model_attempts ORDER BY attempt_number").fetchall()
    assert len(rows) == 2 and rows[1][1] and all(r[0] == "outcome_unknown" for r in rows)


def test_invalid_model_reference_does_not_retry_or_grant_authority(tmp_path):
    calls = []
    def invalid(*args, **kwargs):
        calls.append(1)
        return {"content": '{"suggestions":[{"requirement_id":"missing","evidence_id":"e1","relation":"related","reason":"x"}]}'}
    conn, args = model_args(tmp_path, invalid)
    for _ in range(2):
        with pytest.raises(ValueError, match="invalid_reference"):
            suggest(conn, **args)
    assert calls == [1]


@pytest.mark.parametrize(("content", "error"), [("", "model_empty_response"), ("not JSON", "model_invalid_json")])
def test_unusable_model_response_is_saved_and_never_automatically_retried(tmp_path, content, error):
    calls = []
    def chat(*args, **kwargs):
        calls.append(1)
        return {"content": content, "usage": {"completion_tokens": 4096}}
    conn, args = model_args(tmp_path, chat)
    for _ in range(2):
        with pytest.raises(ValueError, match=error):
            suggest(conn, **args)
    state, response, usage = conn.execute(
        "SELECT state,response_json,usage_json FROM career_model_attempts").fetchone()
    assert calls == [1] and state == "response_saved"
    assert json.loads(response)["content"] == content
    assert json.loads(usage)["completion_tokens"] == 4096


def test_local_provider_ports_have_distinct_request_caches(tmp_path):
    calls = []
    def chat(*args, **kwargs):
        calls.append(1)
        return {"content": '{"suggestions":[]}'}
    conn, args = model_args(tmp_path, chat)
    args["client"].base_url = "http://127.0.0.1:8100/v1"
    suggest(conn, **args)
    args["client"].base_url = "http://127.0.0.1:8200/v1"
    suggest(conn, **args)
    assert len(calls) == 2


def test_caller_transaction_cannot_hide_an_outbound_attempt(tmp_path):
    calls = []
    def chat(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("synthetic")
    conn, args = model_args(tmp_path, chat)
    conn.execute("CREATE TABLE sentinel(value TEXT)")
    conn.execute("INSERT INTO sentinel VALUES('pending caller work')")
    with pytest.raises(ValueError, match="requires_committed_connection"):
        suggest(conn, **args)
    assert calls == [] and conn.in_transaction
    assert conn.execute("SELECT value FROM sentinel").fetchone()[0] == "pending caller work"
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM career_model_attempts").fetchone()[0] == 0
