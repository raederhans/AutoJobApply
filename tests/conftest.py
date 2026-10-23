"""Session-wide test isolation.

``applypilot.config`` resolves ``APPLYPILOT_DIR`` once at import time and defaults to
``~/.applypilot``, so this must run before any test module imports applypilot. The value
is overridden unconditionally: a developer shell pointing at real data must never receive
test writes. Tests that need a specific workspace still set their own via monkeypatch.
"""

import atexit
import contextlib
import gc
import os
import shutil
import sys
import tempfile
from pathlib import Path

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="applypilot-pytest-"))
os.environ["APPLYPILOT_DIR"] = str(_SESSION_DIR)


def _close_cached_connections() -> None:
    # Windows refuses to delete an SQLite file while a handle is open, and tests using the
    # default path leave connections cached per thread in applypilot.database.
    database = sys.modules.get("applypilot.database")
    connections = getattr(getattr(database, "_local", None), "connections", None)
    for connection in list((connections or {}).values()):
        with contextlib.suppress(Exception):  # best-effort cleanup of test handles
            connection.close()
    if connections:
        connections.clear()
    # Connections cached by finished worker threads close once their objects are collected.
    gc.collect()


def _cleanup_session_dir() -> None:
    _close_cached_connections()
    shutil.rmtree(_SESSION_DIR, ignore_errors=True)


def pytest_sessionfinish(session, exitstatus):
    _cleanup_session_dir()


# Last resort if a handle outlived sessionfinish (e.g. one still owned by a live thread).
atexit.register(_cleanup_session_dir)
