"""Bounded schema contracts at the dynamic tool input/output boundary."""

from __future__ import annotations

import json

import pytest

from applypilot.apply.ats_tools_mcp import dynamic_tool_specs
from applypilot.apply.codex_app_server import DynamicToolSpec, DynamicToolSurface


def _object(properties: dict, *, required: tuple[str, ...] = ()) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _surface(input_schema: dict, output_schema: dict, output: dict, **limits) -> tuple[DynamicToolSurface, list]:
    calls = []

    def handler(arguments):
        calls.append(dict(arguments))
        return output

    spec = DynamicToolSpec(
        name="inspect_fixture",
        description="Inspect synthetic public context.",
        input_schema=input_schema,
        output_schema=output_schema,
        handler=handler,
        **limits,
    )
    return DynamicToolSurface((spec,)), calls


def _dispatch(surface: DynamicToolSurface, arguments: dict, *, name: str = "inspect_fixture") -> dict:
    return dict(surface.dispatch(
        thread_id="fixture-thread", turn_id="fixture-turn", call_id="fixture-call",
        name=name, arguments=arguments,
    ))


@pytest.mark.parametrize(
    ("stage", "value", "valid"),
    [("input", None, True), ("input", "ready", True),
     ("output", None, True), ("output", "ready", True),
     ("input", 7, False), ("output", 7, False)],
)
def test_nullable_schema_validates_input_and_output(stage: str, value: object, valid: bool) -> None:
    schema = _object({"value": {"type": ["string", "null"]}}, required=("value",))
    surface, calls = _surface(schema if stage == "input" else _object({}),
                              schema if stage == "output" else _object({}),
                              {"value": value} if stage == "output" else {})

    response = _dispatch(surface, {"value": value} if stage == "input" else {})

    assert response["success"] is valid
    assert len(calls) == (0 if stage == "input" and not valid else 1)
    if valid and stage == "output":
        assert json.loads(response["contentItems"][0]["text"]) == {"value": value}


@pytest.mark.parametrize(("guidance", "valid"), [(["ready"], True), ([{"unexpected": True}], False)])
def test_canonical_detect_ats_output_enforces_guidance_items(guidance: list, valid: bool) -> None:
    declaration = next(spec for spec in dynamic_tool_specs() if spec.name == "detect_ats")
    # A mock isolates the boundary from the built-in handler's own normalization.
    spec = DynamicToolSpec(
        name=declaration.name, description=declaration.description,
        input_schema=declaration.input_schema, output_schema=declaration.output_schema,
        handler=lambda _arguments: {"schema_version": "1", "adapter": "generic", "guidance": guidance},
    )

    response = _dispatch(DynamicToolSurface((spec,)), {"url": "https://fixture.example.test/job"}, name=spec.name)

    assert response["success"] is valid


@pytest.mark.parametrize(
    ("property_schema", "value", "valid"),
    [
        pytest.param({"type": "object", "properties": {"kind": {"type": "string"}},
                      "required": ["kind"]}, {"kind": "form", "extra": 3}, True, id="nested-default-allows-extra"),
        pytest.param({"type": "object", "required": ["kind"], "additionalProperties": True},
                     {}, False, id="nested-required"),
        pytest.param({"type": "object", "properties": {"kind": {"type": "string"}},
                      "additionalProperties": False}, {"kind": "form", "extra": 3}, False,
                     id="nested-forbids-extra"),
        pytest.param({"type": "object", "additionalProperties": {"type": "string"}},
                     {"extra": "ready"}, True, id="nested-schema-allows-extra"),
        pytest.param({"type": "object", "additionalProperties": {"type": "string"}},
                     {"extra": 3}, False, id="nested-schema-checks-extra"),
        pytest.param({"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2},
                     ["a", "b"], True, id="array-upper-bound"),
        pytest.param({"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2},
                     [], False, id="array-below-minimum"),
        pytest.param({"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2},
                     ["a", "b", "c"], False, id="array-above-maximum"),
        pytest.param({"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2},
                     [True], False, id="array-invalid-item"),
        pytest.param({"type": "string", "minLength": 2, "maxLength": 3}, "ab", True, id="string-minimum"),
        pytest.param({"type": "string", "minLength": 2, "maxLength": 3}, "abc", True, id="string-maximum"),
        pytest.param({"type": "string", "minLength": 2, "maxLength": 3}, "a", False, id="string-too-short"),
        pytest.param({"type": "string", "minLength": 2, "maxLength": 3}, "abcd", False, id="string-too-long"),
        pytest.param({"type": "number", "minimum": 0, "maximum": 2}, 0, True, id="number-minimum"),
        pytest.param({"type": "number", "minimum": 0, "maximum": 2}, 2, True, id="number-maximum"),
        pytest.param({"type": "number", "minimum": 0, "maximum": 2}, -1, False, id="number-below-minimum"),
        pytest.param({"type": "number", "minimum": 0, "maximum": 2}, 3, False, id="number-above-maximum"),
        pytest.param({"type": "number"}, True, False, id="bool-is-not-number"),
        pytest.param({"type": "integer"}, False, False, id="bool-is-not-integer"),
        pytest.param({"type": "integer"}, 1.5, False, id="fraction-is-not-integer"),
        pytest.param({"type": "object", "additionalProperties": {}}, {"data": [None, 1, "ready"]}, True,
                     id="extra-fields-with-empty-schema"),
        pytest.param({"enum": [1]}, 1, True, id="enum-without-type"),
        pytest.param({"enum": [1]}, True, False, id="enum-bool-is-not-number"),
        pytest.param({"const": False}, False, True, id="const-without-type"),
        pytest.param({"const": False}, 0, False, id="const-number-is-not-bool"),
    ],
)
def test_recursive_input_contract(property_schema: dict, value: object, valid: bool) -> None:
    surface, calls = _surface(_object({"value": property_schema}, required=("value",)), _object({}), {})

    response = _dispatch(surface, {"value": value})

    assert response["success"] is valid
    assert len(calls) == (1 if valid else 0)


@pytest.mark.parametrize(
    "property_schema",
    [
        pytest.param({"type": "string", "pattern": "ready"}, id="unsupported-pattern"),
        pytest.param({"oneOf": [{"type": "string"}, {"type": "null"}]}, id="unsupported-oneOf"),
        pytest.param({"type": "array", "uniqueItems": True}, id="unsupported-uniqueItems"),
        pytest.param({"type": []}, id="empty-type-union"),
        pytest.param({"type": ["string", 7]}, id="nonstring-type-union"),
        pytest.param({"type": ["string", "unrecognized"]}, id="unknown-type-union"),
        pytest.param({"type": "number", "minimum": True}, id="boolean-numeric-bound"),
        pytest.param({"type": "array", "minItems": -1}, id="negative-array-bound"),
        pytest.param({"type": "object", "required": "kind"}, id="required-is-not-array"),
        pytest.param({"type": "object", "required": [7]}, id="required-contains-nonstring"),
    ],
)
def test_registration_rejects_unsupported_or_malformed_schemas(property_schema: dict) -> None:
    with pytest.raises((TypeError, ValueError)):
        _surface(_object({"value": property_schema}), _object({}), {})


def test_registration_allows_annotation_keywords() -> None:
    property_schema = {"type": "string", "title": "Status", "description": "Synthetic status",
                       "default": "ready", "examples": ["ready"], "format": "uri"}
    surface, calls = _surface(_object({"value": property_schema}), _object({}), {})

    # Annotation-only format does not promise URI validation in this subset.
    response = _dispatch(surface, {"value": "ready"})

    assert response["success"] is True
    assert calls == [{"value": "ready"}]


@pytest.mark.parametrize("limit", ["byte", "depth", "reference"])
def test_registration_preserves_existing_schema_limits(limit: str) -> None:
    property_schema = {"type": "string"}
    if limit == "byte":
        property_schema["description"] = "x" * 16_384
    elif limit == "depth":
        for _ in range(5):
            property_schema = {"type": "object", "properties": {"nested": property_schema}}
    else:
        property_schema = {"$ref": "https://fixture.example.test/schema"}

    with pytest.raises(ValueError, match="byte limit|depth limit|unbounded|unsupported"):
        _surface(_object({"value": property_schema}), _object({}), {})


@pytest.mark.parametrize("stage", ["input", "output"])
def test_payload_byte_limits_remain_enforced(stage: str) -> None:
    schema = _object({"value": {"type": "string"}}, required=("value",))
    surface, calls = _surface(schema if stage == "input" else _object({}),
                              schema if stage == "output" else _object({}),
                              {"value": "x" * 100} if stage == "output" else {},
                              **{f"max_{stage}_bytes": 64})

    response = _dispatch(surface, {"value": "x" * 100} if stage == "input" else {})

    assert response["success"] is False
    assert len(calls) == (0 if stage == "input" else 1)
    assert "byte limit" in response["contentItems"][0]["text"]


def test_integer_accepts_a_json_number_without_fractional_part() -> None:
    surface, calls = _surface(_object({"value": {"type": "integer"}}), _object({}), {})

    response = _dispatch(surface, {"value": 1.0})

    assert response["success"] is True
    assert calls == [{"value": 1.0}]


@pytest.mark.parametrize(
    ("property_schema", "value", "valid"),
    [
        pytest.param({"enum": [{"flags": [True]}]}, {"flags": [1]}, False, id="nested-enum-distinguishes-bool"),
        pytest.param({"const": {"flags": [False]}}, {"flags": [0]}, False, id="nested-const-distinguishes-bool"),
        pytest.param({"enum": [{"values": [1]}]}, {"values": [1.0]}, True, id="nested-enum-equal-numbers"),
        pytest.param({"const": {"values": [1]}}, {"values": [1.0]}, True, id="nested-const-equal-numbers"),
    ],
)
def test_enum_and_const_compare_nested_json_values(property_schema: dict, value: dict, valid: bool) -> None:
    surface, calls = _surface(_object({"value": property_schema}), _object({}), {})

    response = _dispatch(surface, {"value": value})

    assert response["success"] is valid
    assert len(calls) == (1 if valid else 0)


@pytest.mark.parametrize(
    ("property_schema", "value"),
    [
        pytest.param({"type": "object", "properties": {"pattern": {"type": "string"}},
                      "additionalProperties": False}, {"pattern": "literal field name"}, id="keyword-is-field-name"),
        pytest.param({"enum": [{"$ref": "literal data"}]}, {"$ref": "literal data"}, id="reference-is-enum-data"),
        pytest.param({"const": {"$ref": "literal data"}}, {"$ref": "literal data"}, id="reference-is-const-data"),
        pytest.param({"type": "object", "properties": {"$ref": {"type": "string"}, "oneOf": {"type": "boolean"}},
                      "additionalProperties": False}, {"$ref": "literal name", "oneOf": True},
                     id="reference-and-combination-are-field-names"),
        pytest.param({"default": {"$ref": "literal default", "oneOf": [True]}}, {"data": None},
                     id="reference-and-combination-are-annotation-data"),
    ],
)
def test_schema_keywords_are_distinguished_from_property_names_and_literals(property_schema: dict, value: dict) -> None:
    surface, calls = _surface(_object({"value": property_schema}), _object({}), {})

    response = _dispatch(surface, {"value": value})

    assert response["success"] is True
    assert calls == [{"value": value}]


@pytest.mark.parametrize(
    ("value", "valid"),
    [([None, {"status": None}, {"status": "ready"}], True), ([None, {"status": 7}], False)],
)
def test_nullable_nested_object_items(value: list, valid: bool) -> None:
    item_schema = {"type": ["object", "null"], "properties": {"status": {"type": ["string", "null"]}},
                   "required": ["status"], "additionalProperties": False}
    surface, calls = _surface(_object({"value": {"type": "array", "items": item_schema}}), _object({}), {})

    response = _dispatch(surface, {"value": value})

    assert response["success"] is valid
    assert len(calls) == (1 if valid else 0)


@pytest.mark.parametrize("value", [float("nan"), float("inf")], ids=["nan", "infinity"])
def test_number_values_must_be_finite(value: float) -> None:
    surface, calls = _surface(_object({"value": {"type": "number"}}), _object({}), {})

    response = _dispatch(surface, {"value": value})

    assert response["success"] is False
    assert calls == []


@pytest.mark.parametrize("bound", [float("nan"), float("inf")], ids=["nan", "infinity"])
def test_numeric_schema_bounds_must_be_finite(bound: float) -> None:
    with pytest.raises((TypeError, ValueError)):
        _surface(_object({"value": {"type": "number", "maximum": bound}}), _object({}), {})


@pytest.mark.parametrize(
    ("property_schema", "value", "valid"),
    [
        pytest.param({}, {"content": ["ready", 1, None, True]}, True, id="empty-schema-json-object"),
        pytest.param({"title": "Public context", "examples": [None]}, ["ready", {"count": 1}], True,
                     id="annotation-only-json-array"),
        pytest.param({}, {"content": [float("nan")]}, False, id="empty-schema-rejects-nonfinite-nested-value"),
    ],
)
def test_empty_and_annotation_only_schemas_still_require_json_values(
    property_schema: dict, value: object, valid: bool,
) -> None:
    surface, calls = _surface(_object({"value": property_schema}), _object({}), {})

    response = _dispatch(surface, {"value": value})

    assert response["success"] is valid
    assert len(calls) == (1 if valid else 0)


@pytest.mark.parametrize(
    "property_schema",
    [
        pytest.param({"type": "array", "items": [{"type": "string"}]}, id="items-is-not-a-schema"),
        pytest.param({"type": ["string", "string"]}, id="duplicate-type-union"),
        pytest.param({"type": "number", "minimum": 2, "maximum": 1}, id="contradictory-bounds"),
    ],
)
def test_registration_rejects_ambiguous_or_contradictory_contracts(property_schema: dict) -> None:
    with pytest.raises((TypeError, ValueError)):
        _surface(_object({"value": property_schema}), _object({}), {})
