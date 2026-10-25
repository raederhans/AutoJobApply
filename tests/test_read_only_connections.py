"""The shared read-only connection never creates, migrates or writes a database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from applypilot.storage.read_only import open_read_only


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "space name" / "applypilot.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE jobs (url TEXT)")
        conn.execute("INSERT INTO jobs VALUES ('https://example.test/1')")
    return path


def test_missing_database_is_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "applypilot.db"
    with pytest.raises(sqlite3.OperationalError):
        open_read_only(missing)
    assert not missing.exists()


def test_writes_are_rejected_and_rows_use_the_requested_factory(tmp_path: Path) -> None:
    conn = open_read_only(_database(tmp_path), row_factory=sqlite3.Row)
    try:
        assert conn.execute("SELECT url FROM jobs").fetchone()["url"] == "https://example.test/1"
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO jobs VALUES ('x')")
    finally:
        conn.close()


def test_snapshot_starts_a_read_transaction(tmp_path: Path) -> None:
    conn = open_read_only(_database(tmp_path), snapshot=True)
    try:
        assert conn.in_transaction
    finally:
        conn.close()
    plain = open_read_only(_database(tmp_path / "other"))
    try:
        assert not plain.in_transaction
    finally:
        plain.close()


def test_relative_paths_resolve(tmp_path: Path, monkeypatch) -> None:
    path = _database(tmp_path)
    monkeypatch.chdir(path.parent)
    conn = open_read_only(Path("applypilot.db"))
    try:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    finally:
        conn.close()
