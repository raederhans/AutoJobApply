"""Read-only access to an existing workspace SQLite file."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_read_only(
    path: Path | str,
    *,
    timeout: float = 5.0,
    row_factory: type | None = None,
    snapshot: bool = False,
) -> sqlite3.Connection:
    """Open ``path`` read-only without creating, migrating or writing it.

    The URI ``mode=ro`` refuses to create a missing file, ``query_only`` rejects
    writes on the connection itself, and ``snapshot`` starts a read transaction so
    every query sees one consistent state. The caller closes the connection.
    """
    connection = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=timeout)
    try:
        if row_factory is not None:
            connection.row_factory = row_factory
        connection.execute("PRAGMA query_only=ON")
        if snapshot:
            connection.execute("BEGIN")
    except BaseException:
        connection.close()
        raise
    return connection
