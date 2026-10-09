from __future__ import annotations

import json
import os
import threading
import time

import pytest

from applypilot.apply import observation_adapter as adapter
from applypilot.apply import visual_bridge as bridge

URL = "https://jobs.example.test/application/123"


def fixture():
    actions = {"data": [{"selector": "xpath=//input[1]", "method": "fill", "arguments": ["MODEL-INVENTED"]}]}
    bindings = [{"selector": "xpath=//input[1]", "field_key": "#city", "label": "City", "semantic": "city"}]
    form = {"page_url": URL, "fields": [{"field_key": "#city", "label": "City", "control": "text",
                                        "disabled": False, "readonly": False}]}
    return actions, bindings, form, {"city": "Singapore"}


def response(form, batch=None, **changes):
    result = {"session_id": "s", "token_epoch": "e", "observation_id": "o", "ok": True, "outcome": "completed",
              "content": [{"type": "text", "text": json.dumps({"form_state": form, "batch_result": batch})}]}
    result.update(changes)
    return result


def test_v3_v4_only_use_reviewed_facts_and_never_execute_external_selectors():
    actions, bindings, form, facts = fixture()
    for value in (actions, actions["data"]):
        plan = adapter.build_prepare_plan(value, bindings, form, facts, page_url=URL)
        assert plan["steps"] == [{"field_key": "#city", "operation": "fill_control", "value": "Singapore"}]
        assert "Singapore" not in json.dumps(plan["review"])
        assert "xpath" not in json.dumps(plan["steps"])
        assert plan["submit_authority"] is False


@pytest.mark.parametrize("method", ["click", "press", "type", "evaluate", "submit", "setChecked", None])
def test_nonroutine_actions_reject_entire_batch(method):
    actions, bindings, form, facts = fixture()
    actions["data"].append({"selector": "submit", "method": method})
    with pytest.raises(ValueError):
        adapter.build_prepare_plan(actions, bindings, form, facts, page_url=URL)


@pytest.mark.parametrize("change", [
    {"disabled": True}, {"readonly": True}, {"control": "password"}, {"control": "combobox"},
    {"label": "Password"}, {"field_key": "new"}, {"sensitive": True}, {"value_source": "unavailable"},
])
def test_stale_or_protected_fields_block(change):
    actions, bindings, form, facts = fixture()
    form["fields"][0].update(change)
    with pytest.raises(ValueError):
        adapter.build_prepare_plan(actions, bindings, form, facts, page_url=URL)


def test_missing_facts_ambiguous_bindings_duplicate_fields_and_wrong_page_block():
    actions, bindings, form, facts = fixture()
    cases = [(actions, bindings, form, {}), (actions, bindings * 2, form, facts),
             (actions["data"] * 2, bindings, form, facts),
             (actions, bindings, {**form, "fields": form["fields"] * 2}, facts),
             (actions, bindings, {**form, "page_url": "https://other.test"}, facts)]
    for case in cases:
        with pytest.raises(ValueError):
            adapter.build_prepare_plan(*case, page_url=URL)


def test_native_select_maps_unique_live_label_to_value():
    actions, bindings, form, facts = fixture()
    actions["data"][0]["method"] = "selectOption"
    form["fields"][0].update(control="select", options=[{"value": "SG", "label": "Singapore", "disabled": False}])
    assert adapter.build_prepare_plan(actions, bindings, form, facts, page_url=URL)["steps"][0]["value"] == "SG"
    form["fields"][0]["options"] *= 2
    with pytest.raises(ValueError, match="unique"):
        adapter.build_prepare_plan(actions, bindings, form, facts, page_url=URL)


def host(root, *, phase="prepare", url=URL):
    bridge.write_host_metadata(root, session_id="s", token_epoch="e", surface="browser", phase=phase,
                               submission_authorized=phase == "submit",
                               target={"runtime": "iab", "tab_id": "tab", "application_url": url})


def batch_result():
    return {"status": "verified", "completed": 1,
            "results": [{"field_key": "#city", "persisted": True, "invalid": False}]}


def test_real_bridge_queue_observe_then_one_batch_and_readback(tmp_path):
    host(tmp_path)
    actions, bindings, form, facts = fixture()
    requests, errors = [], []

    def serve():
        try:
            for index in range(2):
                deadline = time.monotonic() + 3
                pending = None
                while time.monotonic() < deadline:
                    pending = next((tmp_path / "pending").glob("*.json"), None)
                    if pending is not None:
                        break
                    time.sleep(0.005)
                assert pending is not None
                request = json.loads(pending.read_text())
                requests.append(request)
                os.replace(pending, tmp_path / "claimed" / pending.name)
                output = response(form, batch_result() if index else None,
                                  schema_version=1, request_id=request["request_id"])
                bridge.write_bridge_response(tmp_path, output)
        except Exception as exc:  # noqa: BLE001 - transfer worker failures to the test thread
            errors.append(exc)

    worker = threading.Thread(target=serve)
    worker.start()
    try:
        result = adapter.execute_prepare(tmp_path, actions, bindings, facts, page_url=URL, timeout_seconds=2)
    finally:
        worker.join(4)
    assert not errors
    assert result["status"] == "prepared"
    assert result["submit_authority"] is False
    assert [r["operation"] for r in requests] == ["observe", "fill_batch"]
    assert requests[1]["arguments"]["steps"][0]["value"] == "Singapore"
    assert not list((tmp_path / "pending").glob("*.json"))


@pytest.mark.parametrize("phase,url", [("submit", URL), ("prepare", "https://other.test")])
def test_wrong_host_rejects_before_observing(tmp_path, monkeypatch, phase, url):
    host(tmp_path, phase=phase, url=url)
    monkeypatch.setattr(adapter, "request_visual_operation", lambda *a, **k: pytest.fail("must not queue"))
    actions, bindings, _, facts = fixture()
    with pytest.raises(ValueError):
        adapter.execute_prepare(tmp_path, actions, bindings, facts, page_url=URL)


@pytest.mark.parametrize("change", [
    {"status": "parked"}, {"completed": 0}, {"results": []},
    {"results": [{"field_key": "#other", "persisted": True, "invalid": False}]},
    {"results": [{"field_key": "#city", "persisted": None, "invalid": False}]},
])
def test_partial_or_unknown_batch_stops_without_retry(tmp_path, monkeypatch, change):
    host(tmp_path)
    actions, bindings, form, facts = fixture()
    batch = batch_result()
    batch.update(change)
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs["operation"])
        return response(form, batch if len(calls) == 2 else None)

    monkeypatch.setattr(adapter, "request_visual_operation", request)
    result = adapter.execute_prepare(tmp_path, actions, bindings, facts, page_url=URL)
    assert result["status"] == "manual_review"
    assert calls == ["observe", "fill_batch"]


def test_changed_session_or_invalid_plan_cannot_enqueue_write(tmp_path, monkeypatch):
    host(tmp_path)
    actions, bindings, form, facts = fixture()
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs["operation"])
        return response(form, session_id="changed")

    monkeypatch.setattr(adapter, "request_visual_operation", request)
    with pytest.raises(ValueError, match="session changed"):
        adapter.execute_prepare(tmp_path, actions, bindings, facts, page_url=URL)
    assert calls == ["observe"]


@pytest.mark.parametrize("value", [[], {}, {"ok": True, "outcome": "completed", "content": None}])
def test_malformed_host_response(value):
    with pytest.raises((ValueError, TypeError)):
        adapter.observation_payload(value)
