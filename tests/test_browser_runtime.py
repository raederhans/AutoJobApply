"""Installed and source artifact location must not depend on user data or cwd."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from applypilot import cli
from applypilot.apply import browser_runtime
from scripts.build_release import BROWSER_RUNTIME_MEMBERS

ROOT = Path(__file__).resolve().parents[1]


def _package(tmp_path, monkeypatch, *, source=False):
    package = tmp_path / ("src" if source else "site-packages") / "applypilot"
    module = package / "apply" / "browser_runtime.py"
    module.parent.mkdir(parents=True)
    module.write_text("", encoding="utf-8")
    monkeypatch.setattr(browser_runtime, "__file__", str(module))
    if source:
        (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")
    return package


def _assets(directory):
    directory.mkdir(parents=True)
    for name in browser_runtime.BROWSER_RUNTIME_FILES:
        (directory / name).write_text("// fixture", encoding="utf-8")


def test_wheel_mapping_covers_the_actual_host_import_closure():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mapping = project["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    names = set(browser_runtime.BROWSER_RUNTIME_FILES)
    assert BROWSER_RUNTIME_MEMBERS == {"applypilot/browser_runtime/" + name for name in names}
    assert {key for key in mapping if mapping[key].startswith("applypilot/browser_runtime/")} == {
        "scripts/" + name for name in names}
    for name in names:
        assert mapping["scripts/" + name] == "applypilot/browser_runtime/" + name
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        dependencies = re.findall(r"(?:from\s+|import\s*\()['\"]\.\/([^'\"]+)['\"]", source)
        assert set(dependencies) <= names, (name, dependencies)
    assert not any(".test." in name or "fixture" in name for name in names)


def test_installed_runtime_wins_without_a_source_checkout(tmp_path, monkeypatch):
    package = _package(tmp_path / "安装 目录", monkeypatch)
    _assets(package / "browser_runtime")
    monkeypatch.chdir(tmp_path)
    info = browser_runtime.browser_host_info()
    assert info["origin"] == "installed_package"
    assert Path(info["entrypoint"]).is_relative_to(package)
    assert info["module_uri"] == Path(info["entrypoint"]).as_uri()
    assert info["module_uri"].isascii()


def test_incomplete_installed_runtime_never_falls_back_to_cwd(tmp_path, monkeypatch):
    package = _package(tmp_path / "installed", monkeypatch)
    _assets(package / "browser_runtime")
    (package / "browser_runtime" / "attended-host-journal.mjs").unlink()
    _assets(tmp_path / "scripts")
    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="attended-host-journal"):
        browser_runtime.browser_host_info()


def test_missing_installed_runtime_never_searches_cwd(tmp_path, monkeypatch):
    _package(tmp_path / "installed", monkeypatch)
    _assets(tmp_path / "scripts")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="no packaged"):
        browser_runtime.browser_host_info()


def test_source_runtime_is_bound_to_the_imported_module(tmp_path, monkeypatch):
    _package(tmp_path / "checkout", monkeypatch, source=True)
    _assets(tmp_path / "checkout" / "scripts")
    monkeypatch.chdir(tmp_path)
    info = browser_runtime.browser_host_info()
    assert info["origin"] == "source_checkout"
    assert Path(info["entrypoint"]).parent == tmp_path / "checkout" / "scripts"


def test_cli_location_does_not_initialize_a_workspace(tmp_path):
    workspace = tmp_path / "unused-workspace"
    result = CliRunner().invoke(cli.app, ["--workspace", str(workspace), "browser-host-path", "--json"])
    assert result.exit_code == 0, result.output
    info = json.loads(result.stdout)
    assert info["origin"] == "source_checkout"
    assert Path(info["entrypoint"]) == ROOT / "scripts" / "visual-bridge-host.mjs"
    assert not workspace.exists()


def test_cli_missing_runtime_fails_without_creating_a_workspace(tmp_path, monkeypatch):
    _package(tmp_path / "installed", monkeypatch)
    workspace = tmp_path / "unused-workspace"
    result = CliRunner().invoke(cli.app, ["--workspace", str(workspace), "browser-host-path"])
    assert result.exit_code == 2
    assert "no packaged" in result.output
    assert not workspace.exists()
