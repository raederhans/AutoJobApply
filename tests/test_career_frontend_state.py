"""Run the browser-free career selection regressions in the core CI tier."""

import shutil
import subprocess
from pathlib import Path


def test_career_frontend_state() -> None:
    root = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    assert node, "Node.js 18+ is required for career frontend state tests"
    result = subprocess.run(
        [node, "--test", "scripts/career-workbench-state.test.mjs"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
