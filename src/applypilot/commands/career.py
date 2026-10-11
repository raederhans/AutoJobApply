"""Source-grounded career research, diagnostics and learning tasks (M1/M2/M3)."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Annotated

import httpx
import typer

app = typer.Typer(help="Import career research, diagnose gaps and track scoped learning tasks.", no_args_is_help=True)


@app.command("serve")
def serve_workbench(
    workspace: Annotated[Path | None, typer.Option("--workspace", file_okay=False)] = None,
    port: Annotated[int, typer.Option("--port", min=0, max=65535)] = 0,
) -> None:
    """Run the local career workbench for this data workspace."""
    from applypilot.career.server import serve

    serve(workspace or _workspace(), port=port)


@app.command("open")
def open_workbench(
    workspace: Annotated[Path | None, typer.Option("--workspace", file_okay=False)] = None,
    port: Annotated[int, typer.Option("--port", min=0, max=65535)] = 0,
) -> None:
    """Run the local career workbench and open it in the browser."""
    from applypilot.career.server import serve

    serve(workspace or _workspace(), port=port, open_browser=True)


def _workspace() -> Path:
    return Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot"))


@contextmanager
def _connection(*, write: bool = False):
    path = _workspace() / "applypilot.db"
    conn = None
    try:
        if write:
            from applypilot.database import init_db
            conn = init_db(path)
        else:
            if not path.is_file():
                raise ValueError("career_workspace_missing: run career import first")
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        yield conn
    except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        if conn is not None:
            if write:
                from applypilot.database import close_connection
                close_connection(path)
            else:
                conn.close()


def _emit(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=True, indent=2, default=str))


@app.command("import")
def import_bundle(bundle: Annotated[Path, typer.Option("--bundle", exists=True, file_okay=False)]) -> None:
    """Validate and idempotently import public jobs, source snapshots and rubrics."""
    from applypilot.career.importer import import_bundle as load
    with _connection(write=True) as conn:
        _emit(load(conn, bundle))


@app.command("catalog")
def catalog(company: Annotated[str | None, typer.Option("--company")] = None,
            role: Annotated[str | None, typer.Option("--role")] = None,
            job_id: Annotated[str | None, typer.Option("--job-id")] = None) -> None:
    """Read company/job coverage; --job-id includes the full sourced requirements."""
    from applypilot.storage import career as store
    with _connection() as conn:
        if job_id:
            job = store.get_job(conn, job_id)
            if job is None:
                raise ValueError("research_job_not_found")
            _emit({k: v for k, v in job.items() if k != "body_text"})
            return
        rubrics = store.load_catalog(conn)
        jobs = store.list_jobs(conn, company_id=company, role_family=role)
        _emit({"catalog_id": store.get_catalog_id(conn), "companies": store.list_companies(conn),
               "skills": len(rubrics.get("skills", [])),
               "anchors": sum(len(s["anchors"]) for s in rubrics.get("skills", [])),
               "jobs": [{k: j.get(k) for k in ("id", "company_id", "title", "role_family", "locations",
                                               "native_level", "employment_type", "observed_at", "availability",
                                               "deadline_elapsed", "source_snapshot_id")} for j in jobs]})


@app.command("evidence-import")
def evidence_import(registry: Annotated[Path, typer.Option("--registry", exists=True, dir_okay=False)],
                    allow_root: Annotated[list[Path] | None, typer.Option("--allow-root", exists=True, file_okay=False)] = None) -> None:
    """Import private source-checked materials separately from public research."""
    from applypilot.career.service import import_evidence
    with _connection(write=True) as conn:
        _emit(import_evidence(conn, registry, allowed_roots=allow_root))


@app.command("target")
def target(evidence_id: Annotated[str, typer.Option("--evidence-id")],
           job_id: Annotated[list[str] | None, typer.Option("--job-id")] = None,
           company: Annotated[str | None, typer.Option("--company")] = None,
           role: Annotated[str | None, typer.Option("--role")] = None,
           region: Annotated[str | None, typer.Option("--region")] = None,
           track: Annotated[str | None, typer.Option("--track")] = None,
           level: Annotated[str | None, typer.Option("--level")] = None,
           target_id: Annotated[str | None, typer.Option("--target-id")] = None,
           label: Annotated[str, typer.Option("--label")] = "",
           expected_revision: Annotated[int | None, typer.Option("--expected-revision", min=0)] = None) -> None:
    """Pin a specific job or an exact company/role/location/track/level cohort."""
    from applypilot.career.service import create_target
    with _connection(write=True) as conn:
        _emit(create_target(conn, evidence_snapshot_id=evidence_id, job_ids=job_id, company_id=company,
                            role_family=role, region=region, employment_track=track, native_level=level,
                            target_id=target_id, label=label, expected_revision=expected_revision))


@app.command("targets")
def targets() -> None:
    from applypilot.storage.career import list_targets
    with _connection() as conn:
        _emit(list_targets(conn))


@app.command("diagnose")
def diagnose(target_id: Annotated[str, typer.Option("--target-id")],
             output: Annotated[Path | None, typer.Option("--output", file_okay=False)] = None,
             use_model: Annotated[bool, typer.Option("--use-model", help="Send selected quotes to the configured model for advisory associations.")] = False,
             retry_unknown: Annotated[bool, typer.Option("--retry-unknown", help="Explicitly permit a new request after an unknown outcome; it may incur duplicate usage.")] = False,
             recompute: Annotated[bool, typer.Option("--recompute", help="Re-run deterministic computation and compare with the saved run.")] = False) -> None:
    """Create a reproducible diagnosis, without changing fit scores or applications."""
    from applypilot.career.service import diagnose as run
    if retry_unknown and not use_model:
        raise typer.BadParameter("--retry-unknown requires --use-model")
    if use_model:
        from dotenv import load_dotenv
        load_dotenv(_workspace() / ".env", override=False)
    with _connection(write=True) as conn:
        try:
            result = run(conn, target_id, use_model=use_model, retry_unknown=retry_unknown, recompute=recompute)
        except (ValueError, sqlite3.Error):
            raise
        except (httpx.HTTPError, RuntimeError, KeyError, TypeError) as exc:
            # Provider exceptions can contain credential-bearing URLs. The ledger
            # exposes state and request IDs; never echo raw provider exceptions.
            typer.echo(f"Career analysis failed ({type(exc).__name__}); inspect career attempts. No complete diagnosis was published.", err=True)
            raise typer.Exit(1) from None
        files = {}
        if output is not None:
            from applypilot.career.report import write_report
            files = write_report(result, output)
        _emit({"diagnosis_id": result["diagnosis_id"], "target_id": target_id,
               "input_digest": result["input_digest"], "mode": result["mode"],
               "summary": result.get("summary"), "files": files})


@app.command("show")
def show(diagnosis_id: Annotated[str, typer.Option("--diagnosis-id")],
         output: Annotated[Path | None, typer.Option("--output", file_okay=False)] = None) -> None:
    """Read a saved diagnosis and calculate staleness without database writes."""
    from applypilot.career.service import read_diagnosis
    with _connection() as conn:
        result = read_diagnosis(conn, diagnosis_id)
        if output:
            from applypilot.career.report import write_report
            _emit(write_report(result, output))
        else:
            _emit({k: v for k, v in result.items() if k != "evidence"})


@app.command("attempts")
def attempts() -> None:
    """Read model outcomes and known/unknown usage without exposing request bodies."""
    from applypilot.career.model import attempts as read
    with _connection() as conn:
        _emit(read(conn))


class _LearningEvent(str, Enum):
    started = "started"
    marked_done = "marked_done"
    skipped = "skipped"
    reopened = "reopened"


def _learning_files(progress: dict, output: Path | None, *, diagnosis: dict | None = None) -> dict:
    if output is None:
        return {}
    from applypilot.career.learning_report import write_report
    files = {"route": write_report(progress, output)}
    if diagnosis is not None:
        from applypilot.career.report import write_report as write_diagnosis
        files["diagnosis"] = write_diagnosis(diagnosis, output / "diagnosis")
    return files


def _learning_summary(progress: dict, files: dict) -> dict:
    route = progress["route"]
    return {**{k: route.get(k) for k in ("route_id", "target_id", "diagnosis_id", "parent_route_id")},
            "revision": progress["revision"], "summary": progress["summary"], "budget": route.get("budget"),
            "stale": progress["stale"], "files": files}


@app.command("route")
def route(target_id: Annotated[str, typer.Option("--target-id")],
          diagnosis_id: Annotated[str | None, typer.Option("--diagnosis-id")] = None,
          parent_route_id: Annotated[str | None, typer.Option("--parent-route-id")] = None,
          weekly_hours: Annotated[float | None, typer.Option("--weekly-hours", min=0, help="Optional weekly budget; omitted means order only.")] = None,
          weeks: Annotated[int, typer.Option("--weeks", min=1)] = 4,
          prefer_skill: Annotated[list[str] | None, typer.Option("--prefer-skill")] = None,
          anchor_id: Annotated[list[str] | None, typer.Option("--anchor-id", help="Select target tasks; prerequisites remain included.")] = None,
          output: Annotated[Path | None, typer.Option("--output", file_okay=False)] = None) -> None:
    """Create an immutable learning route from current target inputs, without a default time budget."""
    from applypilot.career import learning
    with _connection(write=True) as conn:
        saved = learning.create_route(conn, target_id, diagnosis_id=diagnosis_id, parent_route_id=parent_route_id,
                                      weekly_hours=weekly_hours, weeks=weeks, preferred_skills=prefer_skill,
                                      selected_anchor_ids=anchor_id)
        progress = learning.read_progress(conn, saved["route_id"])
        _emit(_learning_summary(progress, _learning_files(progress, output)))


@app.command("routes")
def routes(target_id: Annotated[str | None, typer.Option("--target-id")] = None) -> None:
    """List saved learning routes without creating or migrating the workspace."""
    from applypilot.storage.career_learning import list_routes
    with _connection() as conn:
        _emit([{**{k: item.get(k) for k in ("route_id", "target_id", "target_revision", "diagnosis_id",
                                          "parent_route_id", "created_at")}, "tasks": len(item["tasks"])}
               for item in list_routes(conn, target_id)])


@app.command("progress")
def progress(route_id: Annotated[str, typer.Option("--route-id")],
             output: Annotated[Path | None, typer.Option("--output", file_okay=False)] = None) -> None:
    """Read current task states; optionally export local Markdown, HTML and JSON reports."""
    from applypilot.career.learning import read_progress
    with _connection() as conn:
        result = read_progress(conn, route_id)
        _emit(_learning_summary(result, _learning_files(result, output)) if output is not None else result)


@app.command("task-event")
def task_event(route_id: Annotated[str, typer.Option("--route-id")],
               task_id: Annotated[str, typer.Option("--task-id")],
               event: Annotated[_LearningEvent, typer.Option("--event")],
               reason: Annotated[str, typer.Option("--reason")],
               event_key: Annotated[str, typer.Option("--event-key")],
               expected_revision: Annotated[int, typer.Option("--expected-revision", min=0)]) -> None:
    """Append an idempotent state event; marked_done reports completion and does not prove a task pass."""
    from applypilot.career.learning import record_event
    with _connection(write=True) as conn:
        result = record_event(conn, route_id, task_id, event_type=event.value, reason=reason,
                              event_key=event_key, expected_revision=expected_revision)
        _emit({k: result[k] for k in ("event_id", "route_id", "task_id", "event_type", "revision")})


@app.command("task-submit")
def task_submit(route_id: Annotated[str, typer.Option("--route-id")],
                task_id: Annotated[str, typer.Option("--task-id")],
                registry: Annotated[Path, typer.Option("--registry", exists=True, dir_okay=False)],
                event_key: Annotated[str, typer.Option("--event-key")],
                expected_revision: Annotated[int, typer.Option("--expected-revision", min=0)],
                allow_root: Annotated[list[Path] | None, typer.Option("--allow-root", exists=True, file_okay=False)] = None) -> None:
    """Submit explicitly registered local materials; submission alone is not acceptance or skill proof."""
    from applypilot.career.learning import submit_task
    with _connection(write=True) as conn:
        result = submit_task(conn, route_id, task_id, registry, allowed_roots=allow_root,
                             event_key=event_key, expected_revision=expected_revision)
        _emit({**{k: result[k] for k in ("event_id", "route_id", "task_id", "event_type", "revision")},
               "artifact_evidence_snapshot_id": result["payload"]["artifact_evidence_snapshot_id"]})


@app.command("task-review")
def task_review(route_id: Annotated[str, typer.Option("--route-id")],
                task_id: Annotated[str, typer.Option("--task-id")],
                review: Annotated[Path, typer.Option("--review", exists=True, dir_okay=False)],
                review_key: Annotated[str, typer.Option("--review-key")],
                expected_revision: Annotated[int, typer.Option("--expected-revision", min=0)]) -> None:
    """Record a JSON object with criterion observations, quoted evidence, method, subject and assistance."""
    from applypilot.career.learning import read_progress, review_task
    with _connection(write=True) as conn:
        payload = json.loads(review.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise TypeError("learning_review_must_be_object")
        result = review_task(conn, route_id, task_id, payload, review_key=review_key,
                             expected_revision=expected_revision)
        _emit({**{k: result[k] for k in ("assessment_id", "route_id", "task_id", "outcome", "method", "subject")},
               "revision": read_progress(conn, route_id)["revision"]})


@app.command("supplement")
def supplement(target_id: Annotated[str, typer.Option("--target-id")],
               registry: Annotated[Path, typer.Option("--registry", exists=True, dir_okay=False)],
               expected_revision: Annotated[int, typer.Option("--expected-revision", min=1, help="Expected target revision, not task-event revision.")],
               allow_root: Annotated[list[Path] | None, typer.Option("--allow-root", exists=True, file_okay=False)] = None,
               parent_route_id: Annotated[str | None, typer.Option("--parent-route-id")] = None,
               output: Annotated[Path | None, typer.Option("--output", file_okay=False)] = None) -> None:
    """Add selected material evidence, revise the target and derive a fresh diagnosis and route."""
    from applypilot.career.learning import supplement_materials
    with _connection(write=True) as conn:
        result = supplement_materials(conn, target_id, registry, allowed_roots=allow_root,
                                      expected_revision=expected_revision, parent_route_id=parent_route_id)
        files = _learning_files(result["progress"], output, diagnosis=result["diagnosis"])
        _emit({**_learning_summary(result["progress"], files),
               "added_materials": result["added_materials"], "added_evidence": result["added_evidence"]})


@app.command("rediagnose")
def rediagnose(route_id: Annotated[str, typer.Option("--route-id")],
               output: Annotated[Path | None, typer.Option("--output", file_okay=False)] = None) -> None:
    """Recompute diagnosis from current reviewed evidence and derive an idempotent successor route."""
    from applypilot.career.learning import rediagnose as refresh
    with _connection(write=True) as conn:
        result = refresh(conn, route_id)
        files = _learning_files(result["progress"], output, diagnosis=result["diagnosis"])
        _emit(_learning_summary(result["progress"], files))
