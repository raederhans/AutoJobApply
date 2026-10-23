"""Launcher compatibility facades must re-export, not duplicate, moved helpers."""

from __future__ import annotations

import ast
import inspect
from types import ModuleType

import pytest

from applypilot.apply import launcher, runtime_failure_policy

MOVED_NAMES: dict[ModuleType, tuple[str, ...]] = {
    runtime_failure_policy: (
        "_runtime_timeout_status",
        "_resolve_cli_process_failure",
        "_TOOL_FAILURE_STATUSES",
        "_TOOL_SUCCESS_STATUSES",
        "_tool_result_reports_error",
        "_tool_result_succeeded",
        "_tool_failure_text",
        "_classify_browser_tool_failure",
        "_normalize_browser_runtime_failure",
        "PERMANENT_FAILURES",
        "PERMANENT_PREFIXES",
        "_is_permanent_failure",
    ),
}

CASES = [(owner, name) for owner, names in MOVED_NAMES.items() for name in names]


def _top_level_bindings(module: ModuleType) -> list[str]:
    bound: list[str] = []
    for node in ast.parse(inspect.getsource(module)).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.append(node.name)
        elif isinstance(node, ast.Assign):
            bound.extend(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.append(node.target.id)
    return bound


@pytest.mark.parametrize(("owner", "name"), CASES, ids=[f"{o.__name__}.{n}" for o, n in CASES])
def test_launcher_reexports_owner_object(owner: ModuleType, name: str) -> None:
    assert getattr(launcher, name) is getattr(owner, name)


@pytest.mark.parametrize("owner", list(MOVED_NAMES), ids=lambda owner: owner.__name__)
def test_owner_defines_each_moved_name_once(owner: ModuleType) -> None:
    bound = _top_level_bindings(owner)
    for name in MOVED_NAMES[owner]:
        assert bound.count(name) == 1, name


def test_launcher_does_not_redefine_moved_names() -> None:
    redefined: list[str] = []
    for node in ast.parse(inspect.getsource(launcher)).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            redefined.append(node.name)
    moved = {name for names in MOVED_NAMES.values() for name in names}
    assert moved.isdisjoint(redefined)
