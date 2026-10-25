"""Workspace rebinding keeps config and import-time path copies in step."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from applypilot import config

_SRC = Path(__file__).resolve().parents[1] / "src"
_WORKSPACE_NAMES = set(config.workspace_paths(Path("x")))


def _top_level_copies() -> dict[str, set[str]]:
    copies: dict[str, set[str]] = {}
    for path in (_SRC / "applypilot").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "applypilot.config":
                names = {alias.name for alias in node.names} & _WORKSPACE_NAMES
                if names:
                    module = ".".join(path.relative_to(_SRC).with_suffix("").parts)
                    copies.setdefault(module, set()).update(names)
    return copies


def test_every_import_time_copy_is_registered() -> None:
    registered = {module: set(names) for module, names in config.WORKSPACE_PATH_COPIES.items()}
    assert _top_level_copies() == registered


def test_bind_workspace_updates_config_and_unmodified_copies(tmp_path: Path, monkeypatch) -> None:
    database = importlib.import_module("applypilot.database")
    tailor = importlib.import_module("applypilot.scoring.tailor")
    original = config.APP_DIR
    patched_tailored = tmp_path / "patched-tailored"
    monkeypatch.setattr(tailor, "TAILORED_DIR", patched_tailored)
    first, second = tmp_path / "first", tmp_path / "second"
    try:
        config.bind_workspace(first)
        assert config.DB_PATH == first / "applypilot.db"
        assert config.APPLY_WORKER_DIR == first / "apply-workers"
        assert database.DB_PATH == first / "applypilot.db"
        assert tailor.RESUME_PATH == first / "resume.txt"
        assert tailor.TAILORED_DIR == patched_tailored

        config.bind_workspace(second)
        assert database.DB_PATH == second / "applypilot.db"
        assert tailor.RESUME_PATH == second / "resume.txt"
        assert config.ENV_PATH == second / ".env"
    finally:
        config.bind_workspace(original)
    assert database.DB_PATH == original / "applypilot.db"


def test_cli_workspace_option_rebinds_an_already_loaded_config(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from applypilot.cli import app

    original = config.APP_DIR
    try:
        for name in ("one", "two"):
            workspace = tmp_path / name
            workspace.mkdir()
            result = CliRunner().invoke(app, ["--workspace", str(workspace), "doctor"])
            assert result.exit_code == 0, result.output
            assert config.APP_DIR == workspace.resolve()
            assert importlib.import_module("applypilot.database").DB_PATH == workspace.resolve() / "applypilot.db"
    finally:
        config.bind_workspace(original)
