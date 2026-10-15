from __future__ import annotations

import subprocess
import sys

import pytest

from applypilot.apply import specialists


@pytest.fixture
def children(monkeypatch):
    spawned = []
    popen = subprocess.Popen

    def tracked(*args, **kwargs):
        process = popen(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(specialists.subprocess, "Popen", tracked)
    yield spawned
    # Also reclaim children when running these regressions against the old helper.
    for process in spawned:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def run_local(script, *, input_text="", timeout_seconds=2):
    return specialists._run_bounded_subprocess(
        [sys.executable, "-I", "-S", "-c", script],
        input_text=input_text, timeout_seconds=timeout_seconds,
        stdout_limit=1024, stderr_limit=256, env={},
    )


@pytest.mark.parametrize("input_text", ["", "x" * (1024 * 1024)], ids=["empty", "blocked-write"])
def test_timeout_covers_stdin_and_reaps_child(children, input_text):
    with pytest.raises(subprocess.TimeoutExpired):
        run_local(
            "import sys,time; time.sleep(0.6); sys.stdin.buffer.read()",
            input_text=input_text, timeout_seconds=0.1,
        )
    assert len(children) == 1 and children[0].poll() is not None


@pytest.mark.parametrize("failed_start", [1, 2, 3])
def test_thread_start_failure_reaps_child_and_started_threads(monkeypatch, children, failed_start):
    thread_class = specialists.threading.Thread
    threads = []

    def thread(*args, **kwargs):
        value = thread_class(*args, **kwargs)
        threads.append(value)
        if len(threads) == failed_start:
            def fail():
                raise RuntimeError("injected thread start failure")
            value.start = fail
        return value

    monkeypatch.setattr(specialists.threading, "Thread", thread)
    try:
        with pytest.raises(RuntimeError, match="injected thread start failure"):
            run_local("import time; time.sleep(0.6)", timeout_seconds=2)
        assert len(children) == 1 and children[0].poll() is not None
        assert all(not value.is_alive() for value in threads)
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        for value in threads:
            if value.ident is not None:
                value.join(timeout=5)


def test_stdin_preserves_utf8_and_eof(children):
    value = "中文规划🌏\n" * 10000
    result = run_local(
        "import sys; print(len(sys.stdin.buffer.read().decode('utf-8')))", input_text=value,
    )
    assert result.returncode == 0 and result.stdout.strip() == str(len(value))
    assert children[0].poll() == 0


def test_output_limit_still_reaps_child(children):
    with pytest.raises(specialists.AtsFillPlanOutputLimitError) as caught:
        run_local("import os\nwhile True: os.write(1, b'x' * 4096)")
    assert len(caught.value.stdout_prefix) == 1024
    assert len(caught.value.stderr_prefix) <= 256
    assert children[0].poll() is not None
