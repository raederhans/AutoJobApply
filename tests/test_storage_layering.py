"""Static layering guards for the storage and shared contract packages."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

import applypilot.apply.contracts as apply_contracts_facade
import applypilot.career.contracts as career_contracts_facade
import applypilot.contracts.career as career_contracts
import applypilot.contracts.runtime as runtime_contracts

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "applypilot"


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(PACKAGE_ROOT.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(path: Path) -> set[str]:
    """Return absolute module names imported anywhere in one source file."""
    module = _module_name(path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                target = ".".join([*base, node.module] if node.module else base)
            else:
                target = node.module or ""
            imported.add(target)
            # ``from applypilot import apply`` imports the submodule itself.
            imported.update(f"{target}.{alias.name}" for alias in node.names)
    return imported


def _within(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


@pytest.mark.parametrize(
    "path",
    sorted((PACKAGE_ROOT / "storage").glob("*.py")),
    ids=lambda path: path.name,
)
def test_storage_does_not_import_apply_or_career(path: Path) -> None:
    forbidden = sorted(
        name
        for name in _imported_modules(path)
        if _within(name, "applypilot.apply") or _within(name, "applypilot.career")
    )
    assert forbidden == []


@pytest.mark.parametrize(
    "path",
    sorted((PACKAGE_ROOT / "contracts").glob("*.py")),
    ids=lambda path: path.name,
)
def test_contracts_import_only_stdlib_and_themselves(path: Path) -> None:
    disallowed = sorted(
        name
        for name in _imported_modules(path)
        if not _within(name, "applypilot.contracts")
        and name.partition(".")[0] not in sys.stdlib_module_names
        and name != "__future__"
    )
    assert disallowed == []


@pytest.mark.parametrize(
    ("facade", "target"),
    [
        (apply_contracts_facade, runtime_contracts),
        (career_contracts_facade, career_contracts),
    ],
    ids=["apply.contracts", "career.contracts"],
)
def test_contract_facades_reexport_the_same_objects(facade, target) -> None:
    public = sorted(name for name in vars(target) if not name.startswith("_") and name not in _imports(target))
    assert sorted(facade.__all__) == public
    for name in facade.__all__:
        assert getattr(facade, name) is getattr(target, name), name


def _imports(module) -> set[str]:
    """Names bound by import statements at the top level of ``module``."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).partition(".")[0] for alias in node.names)
    return names
