"""M1/M2 integration using fictional research and candidate materials only."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest
from test_career_evidence_model import evidence_registry
from test_career_import import make_bundle

from applypilot.career import service
from applypilot.career.importer import import_bundle
from applypilot.database import close_connection, init_db
from applypilot.storage import career as store


@pytest.fixture
def prepared(tmp_path):
    bundle = make_bundle(tmp_path / "bundle")
    materials = tmp_path / "materials"
    materials.mkdir()
    registry, source, _ = evidence_registry(materials)
    db = tmp_path / "data" / "applypilot.db"
    conn = init_db(db)
    import_bundle(conn, bundle)
    conn.execute("INSERT INTO jobs(url,title,fit_score,apply_status) VALUES('https://example.test/sentinel','Sentinel',8,'applied')")
    conn.commit()
    eid = service.import_evidence(conn, registry)["evidence_snapshot_id"]
    target = service.create_target(conn, evidence_snapshot_id=eid, job_ids=["example-one"])
    yield conn, target, source, registry, bundle, db
    close_connection(db)


def test_diagnose_recomputes_identically_and_does_not_change_jobs(prepared):
    conn, target, source, *_ = prepared
    before = tuple(conn.execute("SELECT * FROM jobs").fetchone())
    a = service.diagnose(conn, target["target_id"])
    b = service.diagnose(conn, target["target_id"], recompute=True)
    assert a["diagnosis_id"] == b["diagnosis_id"] and a["input_digest"] == b["input_digest"]
    assert len(store.list_diagnoses(conn, target["target_id"])) == 1
    assert a["requirements"] and all(s["level"] is None for s in a["skills"])
    assert tuple(conn.execute("SELECT * FROM jobs").fetchone()) == before
    assert not service.read_diagnosis(conn, a["diagnosis_id"])["stale"]
    source.write_text(source.read_text(encoding="utf-8") + "Additional scope.\n", encoding="utf-8")
    assert service.read_diagnosis(conn, a["diagnosis_id"])["stale"]
    with pytest.raises(ValueError, match="input_changed"):
        service.diagnose(conn, target["target_id"])


def test_candidate_changes_during_computation_not_published(prepared, monkeypatch):
    from applypilot.career import diagnosis
    conn, target, source, *_ = prepared
    original = diagnosis.build_diagnosis
    def change(**kwargs):
        result = original(**kwargs)
        source.write_text("Changed while calculating.\n", encoding="utf-8")
        return result
    monkeypatch.setattr(diagnosis, "build_diagnosis", change)
    with pytest.raises(ValueError, match="quote_changed"):
        service.diagnose(conn, target["target_id"])
    assert store.list_diagnoses(conn, target["target_id"]) == []


def test_model_response_survives_result_publication_failure(prepared, monkeypatch):
    from types import SimpleNamespace
    conn, target, *_ = prepared
    calls = []
    def chat(*args, **kwargs):
        calls.append(1)
        return {"content": '{"suggestions":[]}', "request_id": "synthetic", "usage": {"total_tokens": 12}}
    client = SimpleNamespace(base_url="https://model.example/v1", model="fake", chat_once=chat)
    save = store.save_diagnosis
    def crash(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic publication interruption")
    monkeypatch.setattr(store, "save_diagnosis", crash)
    with pytest.raises(sqlite3.OperationalError):
        service.diagnose(conn, target["target_id"], use_model=True, client=client)
    monkeypatch.setattr(store, "save_diagnosis", save)
    saved = service.diagnose(conn, target["target_id"], use_model=True, client=client)
    assert saved["mode"] == "rules_with_model_suggestions" and calls == [1]
    assert not service.read_diagnosis(conn, saved["diagnosis_id"])["stale"]
    monkeypatch.setattr("applypilot.career.model.SUGGESTION_VALIDATION_VERSION", "future-validation")
    assert "model_suggestion_validation_changed" in service.read_diagnosis(conn, saved["diagnosis_id"])["stale_reasons"]


def test_target_revision_changed_during_computation_not_published(prepared, monkeypatch):
    from applypilot.career import diagnosis
    conn, target, *_ = prepared
    original = diagnosis.build_diagnosis
    def change(**kwargs):
        result = original(**kwargs)
        store.save_target(conn, {**target, "label": "changed", "input_fingerprint": "different"}, expected_revision=1)
        return result
    monkeypatch.setattr(diagnosis, "build_diagnosis", change)
    with pytest.raises(ValueError, match="inputs changed"):
        service.diagnose(conn, target["target_id"])
    assert store.list_diagnoses(conn, target["target_id"]) == []


@pytest.mark.parametrize("model", ["fixture-model", "deepseek-fixture"])
def test_cli_model_path_constructs_configured_client(prepared, monkeypatch, model):
    import httpx
    from typer.testing import CliRunner

    from applypilot import llm
    from applypilot.cli import app
    _conn, target, _source, _registry, _bundle, db = prepared
    calls = []
    def send(self, request, **kwargs):
        calls.append(request)
        return httpx.Response(200, request=request, json={"id": "fixture-request", "choices": [
            {"message": {"content": '{"suggestions":[]}'}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 10}})
    monkeypatch.setattr(httpx.Client, "send", send)
    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "LLM_MODEL_QUALITY", "LLM_MODEL_CAREER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_URL", "https://fixture.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "fictional-key")
    monkeypatch.setenv("LLM_MODEL", model)
    assert llm.resolve_model_route("career").models == (model,)
    result = CliRunner().invoke(app, ["--workspace", str(db.parent), "career", "diagnose", "--target-id",
                                     target["target_id"], "--use-model"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["mode"] == "rules_with_model_suggestions"
    assert len(calls) == 1 and calls[0].url.host == "fixture.example"
    payload = json.loads(calls[0].content)
    with sqlite3.connect(db) as reopened:
        recorded = json.loads(reopened.execute("SELECT request_json FROM career_model_attempts").fetchone()[0])
    assert payload["max_tokens"] == recorded["request_policy"]["max_tokens"] == 4096
    assert payload.get("thinking") == ({"type": "disabled"} if model.startswith("deepseek") else None)
    assert recorded["request_policy"].get("thinking") == payload.get("thinking")


@pytest.mark.parametrize("change", ["target", "material"])
def test_cache_hit_rechecks_changed_inputs(prepared, monkeypatch, change):
    conn, target, source, *_ = prepared
    service.diagnose(conn, target["target_id"])
    original = store.list_diagnoses
    def change_before_return(*args, **kwargs):
        cached = original(*args, **kwargs)
        if change == "target":
            store.save_target(conn, {**target, "label": "new", "input_fingerprint": "new"}, expected_revision=1)
        else:
            source.write_text(source.read_text(encoding="utf-8") + "new scope", encoding="utf-8")
        return cached
    monkeypatch.setattr(store, "list_diagnoses", change_before_return)
    with pytest.raises(ValueError, match="inputs?[_ ]changed"):
        service.diagnose(conn, target["target_id"])


def invoke(workspace, *args, code=0):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "APPLYPILOT_DIR": str(workspace.parent / "wrong-workspace")}
    env.pop("APPLYPILOT_DISCOVERY_ONLY", None)
    run = subprocess.run([sys.executable, "-m", "applypilot", "--workspace", str(workspace), "career", *args],
                         env=env, capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
    assert run.returncode == code, run.stdout + run.stderr
    return json.loads(run.stdout) if run.returncode == 0 else run.stderr


def test_real_cli_end_to_end_and_readonly_missing_workspace(tmp_path):
    missing = tmp_path / "missing"
    invoke(missing, "catalog", code=2)
    assert not missing.exists()
    bundle = make_bundle(tmp_path / "bundle")
    selected = tmp_path / "selected"
    assert invoke(selected, "import", "--bundle", str(bundle))["jobs"] == 1
    materials = tmp_path / "materials"
    materials.mkdir()
    registry, _, _ = evidence_registry(materials)
    eid = invoke(selected, "evidence-import", "--registry", str(registry))["evidence_snapshot_id"]
    target = invoke(selected, "target", "--evidence-id", eid, "--job-id", "example-one")
    out = tmp_path / "report"
    a = invoke(selected, "diagnose", "--target-id", target["target_id"], "--output", str(out))
    b = invoke(selected, "diagnose", "--target-id", target["target_id"], "--recompute")
    assert a["diagnosis_id"] == b["diagnosis_id"]
    assert list(out.glob("*.html")) and list(out.glob("*.md"))
    assert not (tmp_path / "wrong-workspace").exists()
    with sqlite3.connect(selected / "applypilot.db") as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
