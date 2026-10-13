"""Installed Runtime Cells bind the same Python bytes without a source checkout."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from applypilot.apply import launcher
from applypilot.apply.runtime_cell_coordinator import source_manifest_identity


def _package(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "__init__.py").write_text('"""Synthetic package."""\n', encoding="utf-8")
    (root / "apply").mkdir()
    (root / "apply" / "runtime_cell_coordinator.py").write_text("GATE = False\n", encoding="utf-8")
    return root


def test_source_and_installed_package_preserve_v1_manifest_identity(tmp_path):
    checkout = tmp_path / "checkout"
    source = _package(checkout / "src" / "applypilot")
    (checkout / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    installed = _package(tmp_path / "安装" / "Lib" / "site-packages" / "applypilot")
    # The pre-fix v1 algorithm used checkout-relative paths. Keep this oracle
    # independent so changing the namespace cannot silently reuse old receipts.
    legacy_manifest = [
        {"path": p.relative_to(checkout).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(source.rglob("*.py"))
    ]
    legacy = hashlib.sha256(json.dumps(
        legacy_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")).hexdigest()
    assert source_manifest_identity(checkout) == source_manifest_identity(source) == legacy
    assert source_manifest_identity(installed) == legacy


def test_identity_changes_with_executable_bytes_and_module_membership(tmp_path):
    package = _package(tmp_path / "applypilot")
    initial = source_manifest_identity(package)
    (package / "cache.pyc").write_bytes(b"not executable source")
    (package / "README.md").write_text("resource", encoding="utf-8")
    assert source_manifest_identity(package) == initial
    module = package / "new.py"
    module.write_text("value = 1\n", encoding="utf-8")
    added = source_manifest_identity(package)
    assert added != initial
    module.write_text("value = 2\n", encoding="utf-8")
    assert source_manifest_identity(package) not in {initial, added}
    module.unlink()
    assert source_manifest_identity(package) == initial


@pytest.mark.parametrize("repo_marker", [False, True])
def test_missing_package_cannot_generate_an_empty_source_identity(tmp_path, repo_marker):
    if repo_marker:
        (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ApplyPilot repository or Python package"):
        source_manifest_identity(tmp_path)


@pytest.mark.parametrize("mode", ["shadow", "canary"])
def test_installed_worker_capacity_stays_not_admitted_without_repository(tmp_path, mode):
    package = _package(tmp_path / "venv" / "Lib" / "site-packages" / "applypilot")
    workers, decision = launcher._resolve_runtime_cell_worker_capacity(
        2, mode=mode, manifest_path=None, source_root=package,
    )
    assert workers == 1
    assert decision.status == "NOT_ADMITTED"
    assert decision.production_authority is False
    assert "admission_manifest_absent" in decision.reasons
    if mode == "canary":
        assert "app_server_production_cell_not_admitted" in decision.reasons


def test_launcher_worker_ports_select_the_loaded_package():
    runtime = launcher._worker_runtime_ports()
    assert runtime.runtime_cells.source_root == Path(launcher.__file__).resolve().parents[1]
