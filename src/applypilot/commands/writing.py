"""Explicit draft preparation and synthetic evaluation; never bootstrap a DB."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Question-led answers and separate cover drafts. No form filling or submission.", no_args_is_help=True)
InputFile = Annotated[Path, typer.Option(exists=True, dir_okay=False)]


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Input must be a JSON object")  # noqa: TRY004 - malformed user data
    return value


def _print(value: dict) -> None:
    typer.echo(json.dumps(value, ensure_ascii=True, indent=2))


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


@contextmanager
def _client():
    from applypilot.commands.interview import _llm_environment
    from applypilot.llm import LLMClient, _detect_provider

    workspace = Path(os.environ.get("APPLYPILOT_DIR", str(Path.home() / ".applypilot")))
    with _llm_environment(workspace):
        # A fresh client binds this invocation's environment, not an old singleton.
        client = LLMClient(*_detect_provider())
        try:
            yield client
        finally:
            client._client.close()


@app.command("context-check")
def context_check(file: InputFile) -> None:
    """Validate an explicit source registry locally; do not call a model."""
    from applypilot.writing_context import load_context

    try:
        context = load_context(file)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print({"valid": True, "sources": len(context["sources"]), "candidate_evidence": len(context["candidate_evidence"]),
            "job_id": (context["role"] or {}).get("job_id"), "authority": "none"})


@app.command("questions")
def questions(
    file: InputFile,
    job_id: Annotated[str, typer.Option("--job-id")],
    page_id: Annotated[str, typer.Option("--page-id", help="Explicit step identity, including same-URL wizards.")],
    output: Annotated[Path, typer.Option("--output", help="New question-set JSON file.")],
    existing: Annotated[Path | None, typer.Option("--existing", exists=True, dir_okay=False)] = None,
) -> None:
    """Import a saved observation and accumulate this page; no browser access."""
    from applypilot.application_questions import merge_question_set, questions_from_observation

    try:
        prior = _read(existing) if existing else {"schema_version": 1, "job_id": job_id, "pages": {}, "revisions": {}}
        if prior.get("job_id") != job_id:
            raise ValueError("Question set belongs to another job")
        items = questions_from_observation(_read(file), job_id=job_id, page_id=page_id)
        result = merge_question_set(prior, items, page_id=page_id)
        _write(output, result)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print({"path": str(output), "page_id": page_id, "questions": len(items), "whole_form": "unknown", "authority": "none"})


@app.command("plan")
def plan(
    context: InputFile,
    question: InputFile,
    sibling: Annotated[list[Path] | None, typer.Option("--sibling", exists=True, dir_okay=False)] = None,
) -> None:
    """Preview original question, selected evidence and missing context offline."""
    from applypilot.application_answers import build_answer_plan
    from applypilot.writing_common import prompt_context
    from applypilot.writing_context import load_context

    try:
        task, selected = build_answer_plan(_read(question), load_context(context), siblings=[_read(path) for path in sibling or []])
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print({"task": task, "selected_context": prompt_context(selected), "diagnostics": selected["diagnostics"], "authority": "none"})


@app.command("answer")
def answer(
    context: InputFile,
    question: InputFile,
    output_dir: Annotated[Path, typer.Option("--output-dir", file_okay=False)],
    sibling: Annotated[list[Path] | None, typer.Option("--sibling", exists=True, dir_okay=False)] = None,
    previous: Annotated[Path | None, typer.Option("--previous", exists=True, dir_okay=False)] = None,
    revision_request: Annotated[str, typer.Option("--revision-request")] = "",
    max_repairs: Annotated[int, typer.Option("--max-repairs", min=0, max=1)] = 1,
    editorial: Annotated[bool, typer.Option("--editorial/--no-editorial", help="One source-bounded prose edit before review.")] = True,
) -> None:
    """Send selected sources to the configured model and save a new answer revision."""
    from applypilot.application_answers import generate_answer
    from applypilot.writing_common import save_artifact
    from applypilot.writing_context import load_context

    try:
        registry, item = load_context(context), _read(question)
        old = _read(previous) if previous else None
        with _client() as client:
            artifact = generate_answer(item, registry, client=client, siblings=[_read(path) for path in sibling or []], previous_draft=old,
                                       revision_request=revision_request, max_repairs=max_repairs, editorial=editorial)
        paths = save_artifact(artifact, output_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print({**paths, "authority": "none", "submission_ready": False})
    if artifact["status"] != "reviewed_draft":
        raise typer.Exit(2)


@app.command("cover")
def cover(
    context: InputFile,
    output_dir: Annotated[Path, typer.Option("--output-dir", file_okay=False)],
    brief: Annotated[str, typer.Option("--brief")] = "",
    previous: Annotated[Path | None, typer.Option("--previous", exists=True, dir_okay=False)] = None,
    revision_request: Annotated[str, typer.Option("--revision-request")] = "",
    surface: Annotated[str, typer.Option("--surface", help="body or formal")]= "body",
    language: Annotated[str, typer.Option("--language")] = "en",
    max_words: Annotated[int | None, typer.Option("--max-words", min=1)] = None,
    max_repairs: Annotated[int, typer.Option("--max-repairs", min=0, max=1)] = 1,
    editorial: Annotated[bool, typer.Option("--editorial/--no-editorial", help="One source-bounded prose edit before review.")] = True,
) -> None:
    """Generate or revise a separate cover letter; never reuse an open answer."""
    from applypilot.cover_letter_drafts import generate_cover_draft
    from applypilot.writing_common import save_artifact
    from applypilot.writing_context import load_context

    constraints = [{"kind": "max", "unit": "words", "value": max_words, "source": "explicit_cli"}] if max_words else []
    try:
        registry = load_context(context)
        old = _read(previous) if previous else None
        with _client() as client:
            artifact = generate_cover_draft(registry, client=client, brief=brief, previous_draft=old,
                                            revision_request=revision_request, surface=surface, language=language,
                                            constraints=constraints, max_repairs=max_repairs, editorial=editorial)
        paths = save_artifact(artifact, output_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print({**paths, "authority": "none", "submission_ready": False})
    if artifact["status"] != "reviewed_draft":
        raise typer.Exit(2)


@app.command("evaluate")
def evaluate(suite: InputFile, results: InputFile) -> None:
    """Recheck saved synthetic outputs against current suite; no model call."""
    from applypilot.writing_eval import evaluate_recorded

    try:
        report = evaluate_recorded(_read(suite), _read(results))
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print(report)
    if report["summary"].get("contract_failed", 0) or report["summary"].get("errors", 0):
        raise typer.Exit(1)


@app.command("benchmark")
def benchmark(
    suite: InputFile,
    output: Annotated[Path, typer.Option("--output", help="New report file; existing reports are never overwritten.")],
    variant: Annotated[str, typer.Option("--variant")] = "new",
    case_id: Annotated[list[str] | None, typer.Option("--case-id")] = None,
    max_repairs: Annotated[int, typer.Option("--max-repairs", min=0, max=1)] = 0,
    editorial: Annotated[bool, typer.Option("--editorial/--no-editorial", help="Enable editing for the new variant.")] = True,
    use_llm: Annotated[bool, typer.Option("--use-llm", help="Run model calls on the explicit suite; default only previews.")]=False,
) -> None:
    """Preview or run synthetic cases, recording errors and actual model outputs."""
    from applypilot.writing_eval import run_benchmark, validate_suite

    try:
        inputs = validate_suite(_read(suite))
        if variant not in {"new", "baseline"}:
            raise ValueError("variant must be new or baseline")
        ids = {case["id"] for case in inputs["cases"]}
        if case_id and not set(case_id) <= ids:
            raise ValueError("Unknown benchmark case ID")
        if output.exists():
            raise ValueError("Report already exists; choose a new output path")
        if not use_llm:
            _print({"status": "preview_only", "cases": case_id or sorted(ids), "variant": variant, "model_calls": 0})
            return
        with _client() as client:
            report = run_benchmark(inputs, client=client, case_ids=case_id, variant=variant, max_repairs=max_repairs, editorial=editorial)
        _write(output, report)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print({"path": str(output), "summary": report["summary"], "authority": "none"})
    if report["summary"].get("errors", 0):
        raise typer.Exit(1)
