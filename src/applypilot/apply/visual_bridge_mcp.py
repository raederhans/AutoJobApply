"""Minimal stdio MCP surface for the supervised visual-operation bridge."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from applypilot.apply.procedure_experience import enrich_bridge_response
from applypilot.apply.visual_bridge import (
    VisualBridgeError,
    request_visual_operation,
    timeout_from_environment,
)

BRIDGE_DIR_ENV = "APPLYPILOT_VISUAL_BRIDGE_DIR"
TOOL_NAME = "visual_operation"

# Set from the MCP initialize request. Claude Code gives the model structuredContent
# INSTEAD of content whenever both are present (verified against claude-code 2.1.239),
# which hides the host's content blocks (form_state) from it.
_client_name = ""


def _adapt_for_client(result: dict[str, object]) -> dict[str, object]:
    """Make a tool result fully visible to Claude Code without adding any new data.

    The compact structuredContent JSON is appended to content and the duplicate
    structuredContent is dropped, so every field the model needs is in content.
    Other clients (Codex reads content and structuredContent) are left unchanged.
    """
    structured = result.get("structuredContent")
    if not _client_name.casefold().startswith("claude") or structured is None:
        return result
    adapted = {key: value for key, value in result.items() if key != "structuredContent"}
    adapted["content"] = [
        *result["content"],
        {"type": "text", "text": json.dumps(structured, ensure_ascii=False, separators=(",", ":"))},
    ]
    return adapted


def _result(request_id: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_result(code: str, message: str, *, outcome: str | None = None) -> dict[str, object]:
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": {"code": code, "outcome": outcome or code},
        "isError": True,
    }


def _tool() -> dict[str, object]:
    argument_properties: dict[str, object] = {
        "x": {"type": "integer", "minimum": 0},
        "y": {"type": "integer", "minimum": 0},
        "mode": {"type": "string", "enum": ["dom", "screenshot"], "description": "Observation format, also optional after an action (default dom)."},
        "node_id": {"type": "string", "minLength": 1, "description": "Observed node for click, or optional observed text input for type_text (browser only)."},
        "element_index": {"type": "integer", "minimum": 0},
        "scroll_x": {"type": "integer", "minimum": -2000, "maximum": 2000},
        "scroll_y": {"type": "integer", "minimum": -2000, "maximum": 2000},
        "text": {"type": "string", "minLength": 1, "maxLength": 4000},
        "artifact_id": {"type": "string", "minLength": 1, "description": "Material reference supplied by the host; never an arbitrary file path."},
        "url": {"type": "string", "description": "An exact web link from the current observation; opens in the same tab."},
        "key": {
            "type": "string",
            "enum": [
                "Enter",
                "Tab",
                "Escape",
                "ArrowUp",
                "ArrowDown",
                "PageUp",
                "PageDown",
                "Home",
                "End",
                "Space",
                "Backspace",
            ],
        },
    }
    argument_properties["keys"] = {
        "type": "array",
        "items": argument_properties["key"],
        "minItems": 1,
        "maxItems": 8,
    }
    argument_properties.update({
        "field_key": {"type": "string", "minLength": 1},
        "value": {"type": "string", "maxLength": 12000},
        "values": {"type": "array", "minItems": 1, "maxItems": 80, "uniqueItems": True,
                   "items": {"type": "string", "maxLength": 12000}},
        "checked": {"type": "boolean"},
        "kind": {"type": "string", "enum": ["school_selection", "record_fields"]},
        "record_key": {"type": "string", "minLength": 1, "maxLength": 12000},
        "query": {"type": "string", "minLength": 1, "maxLength": 12000},
        "steps": {"type": "array", "minItems": 1, "maxItems": 4, "items": {
            "type": "object", "properties": {
                "operation": {"type": "string", "enum": ["fill_control", "select_control"]},
                "field_key": {"type": "string", "minLength": 1},
                "value": {"type": "string", "maxLength": 12000},
            }, "required": ["operation", "field_key", "value"], "additionalProperties": False,
        }},
    })
    return {
        "name": TOOL_NAME,
        "description": (
            "Ask the supervised visual host to observe or operate its fixed application tab. "
            "Actions after observe must reference the returned observation_id. "
            "navigate opens a currently observed link in the same tab; it is browser-only. "
            "upload_artifact selects one host-provided artifact through an observed file control field_key, "
            "or a host-reviewed legacy upload node_id, never both. Playwright observation uses field_key. "
            "Check upload_result: file_selection_done leaves webpage acceptance unverified; inspect the page before continuing. "
            "fill_control replaces ordinary text/date values and commits blur; select_control selects one exact observed option via value, "
            "or the complete nonempty native SELECT multiple set via values, never both. Clearing that set is unsupported. "
            "open_control opens an observed single combobox; search_control fills only an editable ARIA combobox query. "
            "These return fresh candidates with persisted=null: a query or open menu is not a selection. "
            "set_checked sets an ordinary checkbox state or checked=true for a completely observed native radio group. "
            "These use field_key from form_state, never a guessed selector. "
            "fill_batch prepares up to four ordinary text/native-select fields in one supervised request, using steps. "
            "Only prepare supports batches. Each field is rechecked and read back; inspect batch_result. "
            "A parked batch may have partial writes: reobserve and review before any further action, never replay the batch. "
            "Check control_result and post_upload_changes; changed values are observations requiring fact review, not approved answers. "
            "prepare_subgoal is IAB prepare-only with DOM feedback: school_selection uses observed field_key, "
            "record_key and exact value (optional query); record_fields uses record_key and 1-4 scalar steps. "
            "Read field semantic diagnostics first. The host binds the current record, rechecks dependencies, "
            "and verifies committed selection or requested record fields. Use only authoritative candidate facts. "
            "It never creates records, uploads, navigates or submits. Inspect subgoal_result: waiting, needs_fact "
            "or parked is not success; retain partial progress and reobserve before a reviewed continuation. "
            "Reuse the returned observation; no redundant whole-page observation is needed without a new question. "
            "Verified is immediate and scoped, not proof of application readiness or later persistence. "
            "Optional procedure_advice is value-free, non-authorizing guidance, not facts or a replay command. "
            "Passwords and OTPs must never be placed in this tool; request secure host authentication instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["observe", "click", "scroll", "type_text", "press_key", "navigate", "upload_artifact",
                             "fill_control", "select_control", "open_control", "search_control", "set_checked", "fill_batch", "prepare_subgoal"],
                },
                "observation_id": {"type": "string", "minLength": 1},
                "arguments": {
                    "type": "object",
                    "properties": argument_properties,
                    "additionalProperties": False,
                },
            },
            "required": ["operation", "arguments"],
            "additionalProperties": False,
        },
    }


def _handle(message: dict[str, object]) -> dict[str, object] | None:
    response = _dispatch(message)
    if (
        response is not None
        and message.get("method") == "tools/call"
        and isinstance(response.get("result"), dict)
    ):
        response["result"] = _adapt_for_client(response["result"])
    return response


def _dispatch(message: dict[str, object]) -> dict[str, object] | None:
    global _client_name
    method = str(message.get("method") or "")
    request_id = message.get("id")
    if method == "initialize":
        params = message.get("params")
        client_info = params.get("clientInfo") if isinstance(params, dict) else None
        name = client_info.get("name") if isinstance(client_info, dict) else None
        _client_name = name if isinstance(name, str) else ""
        return _result(
            request_id,
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "applypilot-visual-bridge", "version": "1"},
            },
        )
    if method.startswith("notifications/"):
        return None
    if method == "tools/list":
        return _result(request_id, {"tools": [_tool()]})
    if method != "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"},
        }

    params = message.get("params")
    params = params if isinstance(params, dict) else {}
    if params.get("name") != TOOL_NAME:
        return _result(request_id, _error_result("unknown_tool", "Unknown tool."))
    bridge_dir = os.environ.get(BRIDGE_DIR_ENV, "").strip()
    if not bridge_dir:
        return _result(
            request_id,
            _error_result("not_available", f"{BRIDGE_DIR_ENV} is not configured."),
        )
    arguments = params.get("arguments")
    arguments = arguments if isinstance(arguments, dict) else {}
    unexpected = set(arguments) - {"operation", "observation_id", "arguments"}
    if unexpected:
        return _result(
            request_id,
            _error_result("invalid_request", "Visual operation contains unsupported fields."),
        )
    operation = str(arguments.get("operation") or "")
    observation_id = arguments.get("observation_id")
    observation_id = observation_id if isinstance(observation_id, str) else None
    operation_arguments = arguments.get("arguments")
    operation_arguments = operation_arguments if isinstance(operation_arguments, dict) else {}
    try:
        response = request_visual_operation(
            Path(bridge_dir),
            operation=operation,
            observation_id=observation_id,
            arguments=operation_arguments,
            timeout_seconds=timeout_from_environment(),
        )
    except VisualBridgeError as exc:
        return _result(
            request_id,
            _error_result(exc.code, str(exc), outcome=exc.outcome),
        )

    response = enrich_bridge_response(response, Path(bridge_dir))
    content = response["content"]
    structured = {key: value for key, value in response.items() if key != "content"}
    return _result(
        request_id,
        {
            "content": content,
            "structuredContent": structured,
            "isError": not bool(response["ok"]),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--bridge-dir")
    options, _unknown = parser.parse_known_args()
    if options.bridge_dir:
        os.environ[BRIDGE_DIR_ENV] = options.bridge_dir
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        try:
            message = json.loads(line)
            response = _handle(message) if isinstance(message, dict) else None
        except (TypeError, ValueError) as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(exc)},
            }
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
