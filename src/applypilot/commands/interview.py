"""Typer group with lazy workspace resolution for offline interview preparation."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Source-bound interview preparation; no applications or fact writes.", no_args_is_help=True)


@contextmanager
def _llm_environment(workspace: Path):
    """Apply only this workspace's model settings and restore the caller's env."""
    from dotenv import dotenv_values

    added = []
    for key, value in dotenv_values(workspace / ".env").items():
        if (value is not None and key not in os.environ
                and key.startswith(("LLM_", "GEMINI_", "OPENAI_", "DEEPSEEK_", "APPLYPILOT_LLM_"))):
            os.environ[key] = value
            added.append(key)
    try:
        yield
    finally:
        for key in added:
            os.environ.pop(key, None)


@app.command("prepare")
def prepare(
    url: Annotated[str, typer.Option("--url", help="Exact registered job URL.")],
    output: Annotated[Path, typer.Option("--output", help="New output directory; existing paths are refused.")],
    round_name: Annotated[str | None, typer.Option("--round", help="Interview round or focus.")] = None,
    resume: Annotated[str | None, typer.Option("--resume", help="Explicit source path, artifact ID or render ID.")] = None,
    use_llm: Annotated[bool, typer.Option("--use-llm", help="Send selected JD/resume text to the configured model for evidence pairing.")] = False,
) -> None:
    from applypilot.interview import build_pack, write_pack

    workspace = Path(os.environ.get("APPLYPILOT_DIR", str(Path.home() / ".applypilot"))).expanduser().resolve()
    try:
        if use_llm:
            with _llm_environment(workspace):
                pack = build_pack(workspace, url=url, resume=resume, round_name=round_name, use_llm=True)
        else:
            pack = build_pack(workspace, url=url, resume=resume, round_name=round_name)
        paths = write_pack(pack, output)
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps({"files": paths, "binding_status": pack["resume"]["binding_status"],
                          "warnings": pack["warnings"], "llm": pack["llm"]}, ensure_ascii=True))
