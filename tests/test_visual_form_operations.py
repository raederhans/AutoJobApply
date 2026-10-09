import pytest

from applypilot.apply import visual_bridge
from applypilot.apply.visual_bridge import VisualBridgeError, _validate_operation
from applypilot.apply.visual_bridge_mcp import _tool


@pytest.mark.parametrize("operation,args", [
    ("fill_control", {"field_key": "observed", "value": "2026-11-10"}),
    ("select_control", {"field_key": "observed", "value": "Singapore"}),
    ("search_control", {"field_key": "observed", "value": "Nanyang"}),
    ("set_checked", {"field_key": "observed", "checked": False}),
])
def test_form_operations_require_fresh_observation_and_typed_arguments(operation, args):
    _validate_operation(operation, "fresh", args)
    with pytest.raises(VisualBridgeError):
        _validate_operation(operation, None, args)
    with pytest.raises(VisualBridgeError):
        _validate_operation(operation, "fresh", {**args, "selector": "guessed"})
    value_key = "checked" if operation == "set_checked" else "value"
    with pytest.raises(VisualBridgeError):
        _validate_operation(operation, "fresh", {**args, value_key: 1})
    assert operation in _tool()["inputSchema"]["properties"]["operation"]["enum"]


def test_field_batch_validates_entire_bounded_plan():
    step = {"operation": "fill_control", "field_key": "observed", "value": "known fact"}
    _validate_operation("fill_batch", "fresh", {"steps": [step]})
    for steps in ([], [step] * 5, [step, step], [{**step, "operation": "click"}],
                  [{**step, "selector": "guessed"}], [{**step, "value": 1}]):
        with pytest.raises(VisualBridgeError):
            _validate_operation("fill_batch", "fresh", {"steps": steps})
    with pytest.raises(VisualBridgeError):
        _validate_operation("fill_batch", None, {"steps": [step]})


def test_open_requires_only_a_fresh_observed_field_key():
    _validate_operation("open_control", "fresh", {"field_key": "observed"})
    for args in ({}, {"field_key": ""}, {"field_key": 1},
                 {"field_key": "observed", "value": "Query"}, {"selector": "guessed"}):
        with pytest.raises(VisualBridgeError):
            _validate_operation("open_control", "fresh", args)
    with pytest.raises(VisualBridgeError):
        _validate_operation("open_control", None, {"field_key": "observed"})
    assert "open_control" in _tool()["inputSchema"]["properties"]["operation"]["enum"]


def test_select_values_is_a_bounded_unique_set_exclusive_with_scalar_value():
    for values in (["a"], ["b", "a"]):
        _validate_operation("select_control", "fresh", {"field_key": "observed", "values": values})
    for args in ({"values": []}, {"values": "a"}, {"values": [1]}, {"values": ["a", "a"]},
                 {"values": [str(i) for i in range(81)]}, {"values": ["a"], "value": "a"},
                 {"values": ["a"], "selector": "guessed"}, {"values": ["a" * 12001]}):
        with pytest.raises(VisualBridgeError):
            _validate_operation("select_control", "fresh", {"field_key": "observed", **args})
    schema = _tool()["inputSchema"]["properties"]["arguments"]["properties"]["values"]
    assert schema["uniqueItems"] is True
    assert schema["minItems"] == 1
    assert schema["maxItems"] == 80


@pytest.mark.parametrize("step", [
    {"operation": "open_control", "field_key": "observed"},
    {"operation": "search_control", "field_key": "observed", "value": "Query"},
    {"operation": "set_checked", "field_key": "observed", "checked": True},
    {"operation": "select_control", "field_key": "observed", "values": ["a"]},
])
def test_new_complex_controls_cannot_enter_routine_scalar_batches(step):
    with pytest.raises(VisualBridgeError):
        _validate_operation("fill_batch", "fresh", {"steps": [step]})


@pytest.mark.parametrize("operation,args", [
    ("open_control", {"field_key": "observed"}),
    ("search_control", {"field_key": "observed", "value": "Query"}),
    ("select_control", {"field_key": "observed", "values": ["a"]}),
])
def test_complex_operation_is_browser_only_before_any_request_is_queued(tmp_path, monkeypatch, operation, args):
    monkeypatch.setattr(visual_bridge, "read_active_host", lambda _, **kwargs: visual_bridge.HostBinding(
        root=tmp_path, session_id="fixture", token_epoch="epoch", surface="computer_use", phase="prepare", target={},
    ))
    with pytest.raises(VisualBridgeError, match="browser"):
        visual_bridge.request_visual_operation(tmp_path, operation=operation, observation_id="fresh", arguments=args)
    assert not list((tmp_path / "pending").glob("*.json"))
