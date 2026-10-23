import subprocess
import sys
from pathlib import Path

import pytest

from applypilot.apply.runtime_namespace import RuntimeNamespace


def _namespace(
    root: Path,
    *,
    run_id: str = "run-1",
    session_id: str = "session-1",
    profile_id: str = "edge:worker:0",
) -> RuntimeNamespace:
    return RuntimeNamespace(
        root=root,
        run_id=run_id,
        session_id=session_id,
        profile_id=profile_id,
    )


def test_run_session_profile_and_output_namespaces_cannot_alias(tmp_path: Path) -> None:
    base = _namespace(tmp_path)
    variants = [
        _namespace(tmp_path, run_id="run-2"),
        _namespace(tmp_path, session_id="session-2"),
        _namespace(tmp_path, profile_id="edge:worker:1"),
    ]

    outputs = {base.output_root, *(item.output_root for item in variants)}

    assert len(outputs) == 4
    assert all(path.is_relative_to(tmp_path) for path in outputs)
    assert base.path("agent-turn-report.json").parent == base.output_root
    assert base.as_dict()["output_root"] == str(base.output_root)


@pytest.mark.parametrize("name", ["", ".", "..", "../report.json", "a/report.json"])
def test_namespace_rejects_output_traversal(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="one direct child"):
        _namespace(tmp_path).path(name)


# Realistic identities copied from a launcher turn (run/session/profile ids).
_REAL_IDS = {
    "run_id": "apply-run-d5-748ef52e06c1",
    "session_id": "agent-6d08e8f1-4a2b-4c1e-9d3f-0b8a7c6e5d4f",
    "profile_id": "edge-worker-0:persistent:9f2c",
}
# Agent child processes run in output_root and in one nested directory.
_NESTED_CWD = ".app-server-shadow"


def _long_root(tmp_path: Path, length: int) -> Path:
    """Return an existing directory whose absolute path has ``length`` chars."""
    root = tmp_path.resolve()
    while len(str(root)) < length:
        remaining = length - len(str(root)) - 1
        root = root / ("w" * max(1, min(40, remaining)))
    assert len(str(root)) == length
    return root


def test_long_workspace_keeps_agent_cwd_within_windows_budget(tmp_path: Path) -> None:
    # A ~180 character worker root (deep OneDrive/enterprise install) used to
    # push output_root past MAX_PATH; the agent cwd must stay well below it.
    namespace = RuntimeNamespace(root=_long_root(tmp_path, 180), **_REAL_IDS)

    assert len(str(namespace.output_root / _NESTED_CWD)) < 240
    assert namespace.output_root.is_relative_to(namespace.root)
    # Same identities still map to the same directory.
    again = RuntimeNamespace(root=namespace.root, **_REAL_IDS)
    assert again.output_root == namespace.output_root


@pytest.mark.skipif(sys.platform != "win32", reason="CreateProcess cwd limit")
def test_long_workspace_agent_subprocess_starts_in_output_root(tmp_path: Path) -> None:
    namespace = RuntimeNamespace(root=_long_root(tmp_path, 180), **_REAL_IDS)
    nested = namespace.output_root / _NESTED_CWD
    nested.mkdir(parents=True)

    for cwd in (namespace.output_root, nested):
        subprocess.run([sys.executable, "-c", "pass"], cwd=str(cwd), check=True)


def test_overlong_workspace_fails_closed_with_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from applypilot.apply import runtime_namespace as module

    monkeypatch.setattr(module, "_ENFORCE_PROCESS_CWD_LIMIT", True)
    namespace = RuntimeNamespace(root=_long_root(tmp_path, 230), **_REAL_IDS)

    with pytest.raises(ValueError, match="shorter workspace path"):
        _ = namespace.output_root


def test_process_cwd_limit_is_not_enforced_off_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from applypilot.apply import runtime_namespace as module

    monkeypatch.setattr(module, "_ENFORCE_PROCESS_CWD_LIMIT", False)
    namespace = RuntimeNamespace(root=_long_root(tmp_path, 230), **_REAL_IDS)

    assert namespace.output_root.is_relative_to(namespace.root)
