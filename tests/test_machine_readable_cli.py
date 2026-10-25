"""`status --json` and `doctor --json` give agent harnesses parseable state."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PROVIDER_ENV = (
    "GEMINI_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "LLM_URL", "LLM_API_KEY",
    "LLM_MODEL", "LLM_MODEL_FAST", "LLM_MODEL_STANDARD", "LLM_MODEL_QUALITY", "LLM_MODEL_SCORE",
)


def _run(workspace: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if key not in _PROVIDER_ENV}
    environment.pop("APPLYPILOT_DIR", None)
    environment.update(PYTHONIOENCODING="utf-8", PYTHONPATH=str(_ROOT / "src"), COLUMNS="300", **env)
    return subprocess.run(
        [sys.executable, "-m", "applypilot.cli", "--workspace", str(workspace), *args],
        cwd=_ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=120,
        check=False,
    )


def test_doctor_json_reports_checks_and_llm_routes(tmp_path: Path) -> None:
    result = _run(tmp_path, "doctor", "--json", DEEPSEEK_API_KEY="test-only", LLM_MODEL_SCORE="deepseek-flash")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    checks = {item["check"]: item for item in payload["checks"]}
    assert checks["Active workspace"]["status"] == "ok"
    assert checks["profile.json"]["status"] == "missing"
    assert all("[" not in item["status"] for item in payload["checks"])
    routes = {route["task"]: route for route in payload["llm_routes"]}
    assert routes["score"]["models"] == ["deepseek-flash"]
    assert routes["score"]["source"] == "LLM_MODEL_SCORE"
    assert routes["extract"]["tier"] == "fast"
    assert isinstance(payload["tier"], int)


def test_doctor_json_without_a_provider_has_no_routes(tmp_path: Path) -> None:
    result = _run(tmp_path, "doctor", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["llm_routes"] == []
    assert {item["check"]: item["status"] for item in payload["checks"]}["LLM API key"] == "missing"


def test_status_json_reports_pipeline_counts(tmp_path: Path) -> None:
    (tmp_path / "profile.json").write_text(json.dumps({"personal": {"full_name": "Fixture"}}), encoding="utf-8")

    result = _run(tmp_path, "status", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["total"] == 0
    assert payload["admission_ready"] == 0
    assert "score_distribution" in payload
