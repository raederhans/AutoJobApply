"""Preparation-subgoal transport/schema boundaries; no model or employer access."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from applypilot.apply.visual_bridge import (
    VisualBridgeError,
    _validate_operation,
    request_visual_operation,
    write_bridge_response,
    write_host_metadata,
)
from applypilot.apply.visual_bridge_mcp import _tool

SCHOOL = {"kind": "school_selection", "record_key": '[id="education"]',
          "field_key": '[id="school"]', "value": "Example University"}
RECORD = {"kind": "record_fields", "record_key": '[id="work"]',
          "steps": [{"operation": "fill_control", "field_key": '[id="company"]', "value": "Example"}]}


@pytest.mark.parametrize("args", [SCHOOL, {**SCHOOL, "query": "Example"}, {**SCHOOL, "mode": "dom"}, RECORD])
def test_subgoal_accepts_bounded_shapes(args):
    _validate_operation("prepare_subgoal", "current-observation", args)


@pytest.mark.parametrize("mutation", [
    {"mode": "screenshot"}, {"kind": "submit"}, {"kind": []}, {"record_key": ""},
    {"record_key": "x" * 12001}, {"record_key": True}, {"query": ""}, {"query": []},
    {"value": ""}, {"value": 42}, {"field_key": ""}, {"field_key": "x" * 12001},
    {"url": "https://other.example.test"}, {"submission_authorized": True}, {"steps": []},
])
def test_subgoal_rejects_unsupported_school_requests(mutation):
    with pytest.raises(VisualBridgeError) as error:
        _validate_operation("prepare_subgoal", "observed", {**SCHOOL, **mutation})
    assert error.value.code == "invalid_request"


@pytest.mark.parametrize("steps", [[], RECORD["steps"] * 2,
    [{"operation": "click", "field_key": "observed", "value": "x"}],
    [{"operation": "fill_control", "field_key": "observed", "value": "x", "selector": "button"}],
    [{"operation": "select_control", "field_key": "observed", "values": ["x"]}],
])
def test_subgoal_record_keeps_existing_scalar_batch_shape(steps):
    with pytest.raises(VisualBridgeError):
        _validate_operation("prepare_subgoal", "observed", {**RECORD, "steps": steps})


def test_subgoal_requires_observation_and_is_discoverable():
    with pytest.raises(VisualBridgeError):
        _validate_operation("prepare_subgoal", None, SCHOOL)
    tool = _tool()
    assert "prepare_subgoal" in tool["inputSchema"]["properties"]["operation"]["enum"]
    properties = tool["inputSchema"]["properties"]["arguments"]["properties"]
    assert properties["kind"]["enum"] == ["school_selection", "record_fields"]
    assert properties["steps"]["maxItems"] == 4
    assert "authoritative candidate facts" in tool["description"]


@pytest.mark.parametrize("phase", ["discovery", "submit"])
def test_non_prepare_host_rejects_subgoal_without_queue_write(tmp_path, phase):
    write_host_metadata(tmp_path, session_id="session", token_epoch="epoch", surface="browser",
        phase=phase, submission_authorized=phase == "submit",
        target={"runtime": "iab", "tab_id": "tab", "application_url": "https://ats.example.test/form"})
    with pytest.raises(VisualBridgeError):
        request_visual_operation(tmp_path, operation="prepare_subgoal", observation_id="observed", arguments=SCHOOL)
    assert list((tmp_path / "pending").iterdir()) == []


def test_preparation_subgoal_uses_existing_queue_and_bound_response(tmp_path):
    write_host_metadata(tmp_path, session_id="session", token_epoch="epoch", surface="browser",
        target={"runtime": "iab", "tab_id": "tab", "application_url": "https://ats.example.test/form"})
    observed = []
    errors = []

    def service_one():
        try:
            end = time.monotonic() + 5
            while time.monotonic() < end:
                pending = next((tmp_path / "pending").glob("*.json"), None)
                if pending:
                    data = json.loads(pending.read_text())
                    observed.append(data)
                    pending.rename(tmp_path / "claimed" / pending.name)
                    write_bridge_response(tmp_path, {"schema_version": 1,
                        **{k: data[k] for k in ("request_id", "session_id", "token_epoch")},
                        "ok": True, "outcome": "completed", "observation_id": "new-observation",
                        "content": [{"type": "text", "text": '{"subgoal_result":{"status":"waiting"}}'}]})
                    return
                time.sleep(0.01)
            raise AssertionError("No request was published")
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=service_one)
    worker.start()
    try:
        response = request_visual_operation(tmp_path, operation="prepare_subgoal", observation_id="observed",
            arguments=copy.deepcopy(SCHOOL), timeout_seconds=5)
    finally:
        worker.join(timeout=6)
    assert not worker.is_alive() and not errors
    assert response["ok"] is True  # Transport success does not upgrade the waiting subgoal.
    assert json.loads(response["content"][0]["text"])["subgoal_result"]["status"] == "waiting"
    assert observed[0]["target"]["tab_id"] == "tab"
    assert observed[0]["phase"] == "prepare"
    assert observed[0]["operation"] == "prepare_subgoal"
    assert not list((tmp_path / "claimed").iterdir())


@pytest.mark.browser
def test_semantic_preparation_actual_dom():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required by the browser CI tier")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, "--test", str(root / "scripts/browser-semantic-preparation.test.mjs")],
        cwd=root, env={**os.environ, "APPLYPILOT_TEST_PYTHON": sys.executable},
        text=True, encoding="utf-8", capture_output=True, timeout=180, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
