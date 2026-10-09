"""Local followup CLI. Workspace-dependent imports run only inside commands."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from applypilot import followup

app = typer.Typer(help="Local recruiting timeline, next actions and calendar export.", no_args_is_help=True)


def _database_path() -> Path:
    # Use the root callback's current environment, including repeated invocations
    # in one process. Never retain config.DB_PATH from an earlier workspace.
    return Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot")) / "applypilot.db"


@contextmanager
def _connection(*, write: bool = False):
    conn = None
    try:
        path = _database_path()
        if write:
            from applypilot.database import init_db

            conn = init_db(path)
        elif path.is_file():
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        yield conn
    except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        if conn is not None and not write:
            conn.close()


def _print(data: object) -> None:
    # Escapes keep structured output safe on legacy Windows console encodings.
    typer.echo(json.dumps(data, ensure_ascii=True, indent=2))


@app.command("timeline")
def timeline(url: Annotated[str, typer.Option("--url")]) -> None:
    """Show feedback, interview rounds and actions for one exact local job."""
    with _connection() as conn:
        _print(followup.timeline(conn, url))


@app.command("import")
def import_file(file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)]) -> None:
    """Atomically import a user-reviewed JSON event array."""
    with _connection(write=True) as conn:
        _print(followup.import_events(conn, json.loads(file.read_text(encoding="utf-8-sig"))))


@app.command("pending")
def pending() -> None:
    """Show observations that still need an exact job choice."""
    with _connection() as conn:
        _print({"events": followup.pending_events(conn)})


@app.command("resolve")
def resolve(
    event_id: Annotated[str, typer.Option("--event-id")],
    url: Annotated[str, typer.Option("--url")],
) -> None:
    """Explicitly associate one pending observation with an exact jobs.url."""
    with _connection(write=True) as conn:
        _print(followup.resolve_event(conn, event_id, url))


@app.command("add-action")
def add_action(
    url: Annotated[str, typer.Option("--url")],
    summary: Annotated[str, typer.Option("--summary")],
    due_at: Annotated[str, typer.Option("--due-at", help="ISO 8601 date with timezone offset.")],
) -> None:
    """Create one next action with a timezone-aware deadline."""
    with _connection(write=True) as conn:
        _print(followup.add_action(conn, url, summary, due_at))


@app.command("complete-action")
def complete_action(action_id: Annotated[str, typer.Option("--action-id")]) -> None:
    """Mark an action complete; repeating preserves its completion time."""
    with _connection(write=True) as conn:
        _print(followup.complete_action(conn, action_id))


@app.command("due")
def due(at: Annotated[str | None, typer.Option("--at", help="Timezone-aware cutoff; defaults to now.")] = None) -> None:
    """List open actions due at or before the cutoff."""
    with _connection() as conn:
        _print({"actions": followup.due_actions(conn, at=at)})


@app.command("export-ics")
def export_ics(
    file: Annotated[Path, typer.Option("--file", dir_okay=False)],
    url: Annotated[str | None, typer.Option("--url")] = None,
) -> None:
    """Write UTC calendar entries for matched interviews and open actions."""
    with _connection() as conn:
        database_path = _database_path().resolve()
        protected = {database_path, Path(f"{database_path}-wal"), Path(f"{database_path}-shm")}
        if file.resolve() in protected:
            raise ValueError("Calendar output cannot overwrite workspace database files")
        file.write_bytes(followup.export_ics(conn, job_url=url).encode("utf-8"))
        _print({"file": str(file.resolve())})
