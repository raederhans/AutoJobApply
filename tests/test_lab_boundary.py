"""Production code never depends on the development-only applypilot_lab package."""

from __future__ import annotations

import ast
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "applypilot"


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_production_package_does_not_import_lab_modules() -> None:
    offenders = sorted(
        str(path.relative_to(_PACKAGE))
        for path in _PACKAGE.rglob("*.py")
        if any(name.split(".")[0] == "applypilot_lab" for name in _imported_modules(path))
    )
    assert offenders == []
