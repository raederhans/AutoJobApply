"""Offline quality-contract commands; no workspace-bound imports at registration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from applypilot import quality_eval

app = typer.Typer(help="Offline fixture evidence checks and Promptfoo replay export.", no_args_is_help=True)


@app.command("run")
def run(file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)]) -> None:
    """Evaluate recorded fixture outputs; failures return exit code 1."""
    try:
        result = quality_eval.run_suite(json.loads(file.read_text(encoding="utf-8-sig")))
    except (TypeError, ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, ensure_ascii=True, indent=2))
    if not result["passed"]:
        raise typer.Exit(1)


@app.command("export-promptfoo")
def export_promptfoo(
    file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)],
    output_dir: Annotated[Path, typer.Option("--output-dir", file_okay=False)],
) -> None:
    """Create a new directory with an offline echo config and shared assertion."""
    try:
        result = quality_eval.export_promptfoo(json.loads(file.read_text(encoding="utf-8-sig")), output_dir)
    except (TypeError, ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, ensure_ascii=True, indent=2))
