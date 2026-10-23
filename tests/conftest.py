"""Session-wide test isolation.

``applypilot.config`` resolves ``APPLYPILOT_DIR`` once at import time and defaults to
``~/.applypilot``, so this must run before any test module imports applypilot. The value
is overridden unconditionally: a developer shell pointing at real data must never receive
test writes. Tests that need a specific workspace still set their own via monkeypatch.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="applypilot-pytest-"))
os.environ["APPLYPILOT_DIR"] = str(_SESSION_DIR)


def _cleanup_session_dir() -> None:
    shutil.rmtree(_SESSION_DIR, ignore_errors=True)


def pytest_sessionfinish(session, exitstatus):
    _cleanup_session_dir()


# Windows can still hold a SQLite handle during sessionfinish; retry once the interpreter exits.
atexit.register(_cleanup_session_dir)
