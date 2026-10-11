"""Run focused production-adapter regressions in the Node-enabled CI tier."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.browser
@pytest.mark.parametrize(
    "script",
    [
        "browser-batch-dependencies.test.mjs",
        "browser-host-group-timing.test.mjs",
        "browser-host-refresh-dependencies.test.mjs",
    ],
)
def test_agent_harness_batch_p0(script: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for browser host contract tests")
    root = Path(__file__).resolve().parents[1]
    flags = ["--experimental-test-module-mocks"] if script == "browser-host-refresh-dependencies.test.mjs" else []
    result = subprocess.run(
        [node, *flags, "--test", str(root / "scripts" / script)],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
