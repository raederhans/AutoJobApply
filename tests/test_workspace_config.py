"""Explicit isolated workspaces do not require a resolvable user home."""
import runpy
from pathlib import Path

from applypilot import config


def test_explicit_workspace_does_not_resolve_user_home(tmp_path, monkeypatch):
    workspace = tmp_path / "synthetic-workspace"
    monkeypatch.setenv("APPLYPILOT_DIR", str(workspace))

    def unavailable_home(cls):
        raise RuntimeError("user home is deliberately unavailable")

    monkeypatch.setattr(Path, "home", classmethod(unavailable_home))
    values = runpy.run_path(config.__file__)
    assert values["APP_DIR"] == workspace
    assert values["DB_PATH"] == workspace / "applypilot.db"
    assert not workspace.exists()


def test_unconfigured_workspace_retains_the_home_default(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLYPILOT_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    values = runpy.run_path(config.__file__)
    assert values["APP_DIR"] == tmp_path / ".applypilot"
    assert not values["APP_DIR"].exists()
