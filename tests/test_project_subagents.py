"""Project subagent definitions stay valid and the Codex/Claude mirrors agree."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_CODEX = sorted((_ROOT / ".codex" / "agents").glob("*.toml"))


def _claude(name: str) -> tuple[dict, str]:
    text = (_ROOT / ".claude" / "agents" / f"{name}.md").read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---\n", 2)
    return yaml.safe_load(frontmatter), body


def test_project_subagents_exist() -> None:
    assert {path.stem for path in _CODEX} == {
        "applypilot-job-scout", "applypilot-fit-reviewer", "applypilot-materials-reviewer",
    }


@pytest.mark.parametrize("path", _CODEX, ids=lambda path: path.stem)
def test_codex_and_claude_definitions_mirror_each_other(path: Path) -> None:
    codex = tomllib.loads(path.read_text(encoding="utf-8"))
    assert codex["name"] == path.stem
    assert codex["description"] and codex["model"]
    assert codex["model_reasoning_effort"] in {"low", "medium", "high"}

    claude, body = _claude(path.stem)
    assert claude["name"] == codex["name"]
    assert claude["description"] == codex["description"]
    assert claude["model"] in {"haiku", "sonnet", "opus"}
    assert codex["developer_instructions"].strip() in body


@pytest.mark.parametrize("path", _CODEX, ids=lambda path: path.stem)
def test_subagents_never_hold_submission_or_import_authority(path: Path) -> None:
    instructions = tomllib.loads(path.read_text(encoding="utf-8"))["developer_instructions"].casefold()
    assert "submit" in instructions
    assert "parent" in instructions and "simplified chinese" in instructions


def test_read_only_reviewers_have_no_write_tools_in_claude() -> None:
    for name in ("applypilot-fit-reviewer", "applypilot-materials-reviewer"):
        claude, _ = _claude(name)
        assert {tool.strip() for tool in claude["tools"].split(",")} == {"Read", "Grep", "Glob"}
    scout, _ = _claude("applypilot-job-scout")
    assert "Bash" in scout["disallowedTools"]
