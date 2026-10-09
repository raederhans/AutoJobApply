"""Typer commands for JSON Resume export, import, and subset checking."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer

from applypilot.json_resume import check_json_resume, export_json_resume, import_json_resume

app = typer.Typer(
    name="json-resume",
    help="Exchange selected resume files using the JSON Resume format.",
    no_args_is_help=True,
)


def _workspace_imports_dir() -> Path:
    """Resolve the active workspace only after the root CLI callback has run."""
    # The root callback sets APPLYPILOT_DIR before invoking a subcommand.
    # Keep config lazy so importing this Typer app cannot bind the default path.
    from applypilot import config

    active = os.environ.get("APPLYPILOT_DIR")
    return Path(active).expanduser().resolve() / "imports" if active else config.APP_DIR / "imports"


def _emit(value: dict[str, Any]) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@app.command("export")
def export_command(
    source: Path = typer.Option(  # noqa: B008
        ...,
        "--input",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
        help="Selected .txt/.docx resume or ApplyPilot internal .json file.",
    ),
    output: Path = typer.Option(  # noqa: B008
        ...,
        "--output",
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
        help="New JSON Resume output file; existing files are never overwritten.",
    ),
) -> None:
    """Export one explicitly selected resume source."""
    try:
        _emit(export_json_resume(source, output))
    except (FileExistsError, FileNotFoundError, OSError, TypeError, ValueError) as exc:
        typer.echo(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), err=True)
        raise typer.Exit(code=2) from None


@app.command("import")
def import_command(
    source: Path = typer.Option(  # noqa: B008
        ...,
        "--input",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
        help="JSON Resume file to preserve and convert into a review draft.",
    ),
    output_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--output-dir",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Parent directory for the new draft (defaults to the active workspace imports folder).",
    ),
) -> None:
    """Preserve a JSON Resume source and render an unvalidated draft."""
    try:
        target = output_dir if output_dir is not None else _workspace_imports_dir()
        _emit(import_json_resume(source, target))
    except (FileExistsError, FileNotFoundError, OSError, TypeError, ValueError) as exc:
        typer.echo(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), err=True)
        raise typer.Exit(code=2) from None


@app.command("check")
def check_command(
    source: Path = typer.Argument(  # noqa: B008
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
        help="JSON Resume file to inspect.",
    ),
) -> None:
    """Check selected JSON Resume field types and URI/date forms."""
    result = check_json_resume(source)
    _emit(result)
    if not result["ok"]:
        raise typer.Exit(code=1)
