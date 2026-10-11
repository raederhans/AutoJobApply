from __future__ import annotations

import sys
from types import ModuleType

import pytest
from typer.testing import CliRunner

from applypilot.commands.career import app
from applypilot.view import _career_workbench_entry


@pytest.mark.parametrize(
    ("command", "open_browser"),
    [("serve", False), ("open", True)],
)
def test_career_workbench_commands_use_selected_workspace(monkeypatch, tmp_path, command, open_browser):
    calls = []
    server = ModuleType("applypilot.career.server")
    server.serve = lambda workspace, *, port=0, open_browser=False: calls.append(
        (workspace, port, open_browser)
    )
    monkeypatch.setitem(sys.modules, "applypilot.career.server", server)

    result = CliRunner().invoke(
        app,
        [command, "--workspace", str(tmp_path), "--port", "43123"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [(tmp_path, 43123, open_browser)]


def test_dashboard_career_entry_requires_active_local_instance(monkeypatch, tmp_path):
    server = ModuleType("applypilot.career.server")
    server.active_instance = lambda workspace: {
        "url": "http://127.0.0.1:43123/",
        "workspace": str(workspace),
        "instance_id": "fixture-instance",
    }
    monkeypatch.setitem(sys.modules, "applypilot.career.server", server)

    assert _career_workbench_entry(tmp_path) == {
        "active": True,
        "url": "http://127.0.0.1:43123/",
    }

    server.active_instance = lambda _workspace: None
    assert _career_workbench_entry(tmp_path) == {"active": False}

    server.active_instance = lambda _workspace: {"url": "https://example.com/"}
    assert _career_workbench_entry(tmp_path) == {"active": False}

    server.active_instance = lambda _workspace: {
        "url": "http://127.0.0.1:43123/",
        "workspace": str(tmp_path / "other-workspace"),
        "instance_id": "wrong-workspace",
    }
    assert _career_workbench_entry(tmp_path) == {"active": False}
