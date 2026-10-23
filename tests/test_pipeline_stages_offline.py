"""Offline tests for the pipeline stage runners, pending counts and orchestrators.

Nothing here touches the network, a browser or an LLM: sub-scrapers and stage
implementations are replaced by fake modules or fake runners.
"""

from __future__ import annotations

import sys
import threading
import types
from pathlib import Path

import pytest

from applypilot import database, pipeline

_STATS = {
    "total": 0,
    "pending_detail": 0,
    "with_description": 0,
    "scored": 0,
    "tailored": 0,
    "with_cover_letter": 0,
    "ready_to_apply": 0,
    "applied": 0,
}


def _inject(monkeypatch, module_name: str, **attrs) -> None:
    """Register a fake module so the runner's function-local import resolves to it."""
    monkeypatch.setitem(sys.modules, module_name, types.SimpleNamespace(**attrs))


def _boom(message: str):
    def fail(*_args, **_kwargs):
        raise RuntimeError(message)

    return fail


def _inject_discover(monkeypatch, *, jobspy=None, workday=None, smartextract=None, calls=None) -> None:
    """Inject fake discovery scrapers; ``None`` means a scraper that succeeds."""
    calls = calls if calls is not None else []

    def make(name, override):
        def run(**kwargs):
            calls.append((name, kwargs))
            if override is not None:
                override()

        return run

    _inject(monkeypatch, "applypilot.discovery.jobspy", run_discovery=make("jobspy", jobspy))
    _inject(monkeypatch, "applypilot.discovery.workday", run_workday_discovery=make("workday", workday))
    _inject(monkeypatch, "applypilot.discovery.smartextract", run_smart_extract=make("smartextract", smartextract))


# ---------------------------------------------------------------------------
# Stage runners
# ---------------------------------------------------------------------------

def test_run_discover_isolates_each_scraper_failure(monkeypatch) -> None:
    calls: list = []
    _inject_discover(monkeypatch, workday=_boom("workday down"), calls=calls)

    stats = pipeline._run_discover(workers=3)

    assert stats == {"jobspy": "ok", "workday": "error: workday down", "smartextract": "ok"}
    # A failing scraper must not stop the later ones, and workers is forwarded.
    assert [name for name, _ in calls] == ["jobspy", "workday", "smartextract"]
    assert dict(calls)["workday"] == {"workers": 3}
    assert dict(calls)["smartextract"] == {"workers": 3}


@pytest.mark.parametrize("failing", ["jobspy", "workday", "smartextract"])
def test_run_discover_reports_only_the_failing_scraper(monkeypatch, failing: str) -> None:
    _inject_discover(monkeypatch, **{failing: _boom("nope")})

    stats = pipeline._run_discover()

    for name, value in stats.items():
        assert value == ("error: nope" if name == failing else "ok")


_RUNNER_CASES = [
    pytest.param(
        pipeline._run_enrich, "applypilot.enrichment.detail", "run_enrichment", {"workers": 2}, id="enrich"
    ),
    pytest.param(pipeline._run_score, "applypilot.scoring.scorer", "run_scoring", {}, id="score"),
    pytest.param(
        pipeline._run_tailor,
        "applypilot.scoring.tailor",
        "run_tailoring",
        {"min_score": 8, "validation_mode": "strict"},
        id="tailor",
    ),
    pytest.param(
        pipeline._run_cover,
        "applypilot.scoring.cover_letter",
        "run_cover_letters",
        {"min_score": 8, "validation_mode": "lenient"},
        id="cover",
    ),
    pytest.param(pipeline._run_pdf, "applypilot.scoring.pdf", "batch_convert", {}, id="pdf"),
]


@pytest.mark.parametrize(("runner", "module", "func", "kwargs"), _RUNNER_CASES)
def test_stage_runner_returns_error_status_on_exception(monkeypatch, runner, module, func, kwargs) -> None:
    _inject(monkeypatch, module, **{func: _boom("kaput")})

    assert runner(**kwargs) == {"status": "error: kaput"}


@pytest.mark.parametrize(("runner", "module", "func", "kwargs"), _RUNNER_CASES)
def test_stage_runner_returns_ok_and_forwards_arguments(monkeypatch, runner, module, func, kwargs) -> None:
    seen: list[dict] = []
    _inject(monkeypatch, module, **{func: lambda **kw: seen.append(kw)})

    assert runner(**kwargs) == {"status": "ok"}
    assert seen == [kwargs]


def test_cover_runner_executes_when_profile_opts_in(monkeypatch) -> None:
    seen: list[dict] = []
    _inject(monkeypatch, "applypilot.scoring.cover_letter", run_cover_letters=lambda **kw: seen.append(kw))
    monkeypatch.setattr(pipeline, "load_profile", lambda: {"cover_letter": {"auto_generate_in_full_pipeline": True}})

    assert pipeline._run_cover(min_score=9, allow_speculative=False) == {"status": "ok"}
    assert seen == [{"min_score": 9, "validation_mode": "normal"}]


@pytest.mark.parametrize(
    "profile",
    [
        {"cover_letter": {"auto_generate_in_full_pipeline": "yes"}},
        {"cover_letter": "on"},
        ["not", "a", "mapping"],
    ],
)
def test_cover_opt_in_requires_a_literal_true(monkeypatch, profile) -> None:
    monkeypatch.setattr(pipeline, "load_profile", lambda: profile)

    assert pipeline._cover_auto_generate_in_full_pipeline() is False


def test_cover_opt_in_is_false_when_profile_is_missing(monkeypatch) -> None:
    def missing():
        raise FileNotFoundError("profile.json")

    monkeypatch.setattr(pipeline, "load_profile", missing)

    assert pipeline._cover_auto_generate_in_full_pipeline() is False


# ---------------------------------------------------------------------------
# Stage resolution
# ---------------------------------------------------------------------------

def test_resolve_stages_orders_canonically_and_deduplicates() -> None:
    assert pipeline._resolve_stages(["pdf", "score", "discover", "score"]) == ["discover", "score", "pdf"]


def test_resolve_stages_all_expands_to_every_stage() -> None:
    assert pipeline._resolve_stages(["tailor", "all"]) == list(pipeline.STAGE_ORDER)


def test_resolve_stages_unknown_stage_raises_systemexit(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        pipeline._resolve_stages(["discover", "bogus"])

    assert excinfo.value.code == 1
    assert "bogus" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Pending-work counts
# ---------------------------------------------------------------------------

@pytest.fixture
def pending_db(monkeypatch, tmp_path: Path):
    """Temp DB wired into ``pipeline.get_connection`` (thread-local cached connections)."""
    db_path = tmp_path / "jobs.db"
    conn = database.init_db(db_path)
    monkeypatch.setattr(pipeline, "get_connection", lambda: database.get_connection(db_path))
    yield conn
    database.close_connection(db_path)


def _job(conn, key: str, **columns) -> None:
    row = {"url": f"https://example.com/{key}", "title": key, "detail_scraped_at": "2026-01-01"}
    row.update(columns)
    names = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO jobs ({names}) VALUES ({marks})", tuple(row.values()))


def test_count_pending_sql_per_stage(pending_db) -> None:
    conn = pending_db
    # enrich: only a never-scraped, eligible row counts.
    _job(conn, "A-enrich", detail_scraped_at=None)
    _job(conn, "B-enrich-ineligible", detail_scraped_at=None, eligibility_status="ineligible")
    # score: described but unscored.
    _job(conn, "C-score", full_description="JD")
    # tailor: scored >= min_score, no resume yet, under the attempt cap.
    _job(conn, "D-tailor", full_description="JD", fit_score=8)
    _job(conn, "E-tailor-capped", full_description="JD", fit_score=8, tailor_attempts=5)
    _job(conn, "F-tailor-low", full_description="JD", fit_score=5)
    # cover / pdf.
    _job(conn, "G-cover-pdf", fit_score=8, full_description="JD", tailored_resume_path="g.txt", company_name="Co")
    _job(conn, "H-not-required", tailored_resume_path="h.pdf", company_name="Co", cover_letter_status="not_required")
    _job(conn, "I-no-company", tailored_resume_path="i.txt", company_name="")
    _job(conn, "J-cover-capped", tailored_resume_path="j.txt", company_name="Co", cover_attempts=5)
    _job(conn, "K-has-letter", tailored_resume_path="k.txt", company_name="Co", cover_letter_path="k_cl.txt")
    _job(conn, "L-ineligible", tailored_resume_path="l.txt", company_name="Co", eligibility_status="ineligible")
    conn.commit()

    assert pipeline._count_pending("enrich") == 1
    assert pipeline._count_pending("score") == 1
    assert pipeline._count_pending("tailor", 7) == 1  # D only (F is below 7, E is capped)
    assert pipeline._count_pending("tailor", 5) == 2  # D and F
    assert pipeline._count_pending("tailor", 9) == 0
    assert pipeline._count_pending("cover") == 1  # G only
    assert pipeline._count_pending("pdf") == 4  # G, I, J, K (.txt, eligible)
    assert pipeline._count_pending("discover") == 0  # no SQL: always zero


# ---------------------------------------------------------------------------
# Streaming stages
# ---------------------------------------------------------------------------

def test_streaming_discover_marks_done_once_with_runner_result(monkeypatch) -> None:
    calls: list[dict] = []

    def runner(**kwargs):
        calls.append(kwargs)
        return {"jobspy": "ok", "workday": "ok", "smartextract": "ok"}

    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "discover", runner)
    tracker = pipeline._StageTracker()

    pipeline._run_stage_streaming("discover", tracker, threading.Event(), workers=4)

    assert calls == [{"workers": 4}]
    assert tracker.is_done("discover")
    assert tracker.get_results()["discover"]["status"] == "ok"


def test_streaming_discover_crash_is_recorded_as_error(monkeypatch) -> None:
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "discover", _boom("crashed"))
    tracker = pipeline._StageTracker()

    pipeline._run_stage_streaming("discover", tracker, threading.Event())

    assert tracker.get_results()["discover"] == {"status": "error: crashed"}


def test_streaming_downstream_finishes_when_upstream_done_and_idle(monkeypatch) -> None:
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "score", lambda **_kw: pytest.fail("no pending work"))
    monkeypatch.setattr(pipeline, "_count_pending", lambda *_a, **_k: 0)
    tracker = pipeline._StageTracker()
    tracker.mark_done("enrich")

    pipeline._run_stage_streaming("score", tracker, threading.Event())

    assert tracker.get_results()["score"] == {"status": "ok", "passes": 0, "consecutive_failures": 0}


def test_streaming_downstream_honours_stop_event(monkeypatch) -> None:
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "score", lambda **_kw: pytest.fail("stopped"))
    monkeypatch.setattr(pipeline, "_count_pending", lambda *_a, **_k: pytest.fail("stopped"))
    tracker = pipeline._StageTracker()  # upstream never finishes
    stop = threading.Event()
    stop.set()

    pipeline._run_stage_streaming("score", tracker, stop)

    assert tracker.get_results()["score"]["status"] == "ok"
    assert tracker.get_results()["score"]["passes"] == 0


def test_streaming_downstream_stops_waiting_when_stop_event_fires(monkeypatch) -> None:
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "score", lambda **_kw: pytest.fail("no pending work"))
    monkeypatch.setattr(pipeline, "_count_pending", lambda *_a, **_k: 0)
    monkeypatch.setattr(pipeline, "_STREAM_POLL_INTERVAL", 0.01)
    tracker = pipeline._StageTracker()  # upstream never finishes
    stop = threading.Event()
    worker = threading.Thread(target=pipeline._run_stage_streaming, args=("score", tracker, stop))
    worker.start()

    assert not tracker.wait("score", timeout=0.1)  # still polling for upstream
    stop.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert tracker.is_done("score")


def test_streaming_chain_with_fake_runners_finishes(monkeypatch, pending_db) -> None:
    conn = pending_db
    for key in ("one", "two"):
        _job(conn, key, detail_scraped_at=None, company_name="Co")
    conn.commit()

    def advance(sql: str, params: tuple = ()) -> None:
        local = pipeline.get_connection()
        local.execute(sql, params)
        local.commit()

    runners = {
        "discover": lambda **_kw: {"jobspy": "ok", "workday": "ok", "smartextract": "ok"},
        "enrich": lambda **_kw: advance(
            "UPDATE jobs SET detail_scraped_at='2026-01-02', full_description='JD' WHERE detail_scraped_at IS NULL"
        ),
        "score": lambda **_kw: advance("UPDATE jobs SET fit_score=8 WHERE fit_score IS NULL"),
        "tailor": lambda **_kw: advance("UPDATE jobs SET tailored_resume_path=url || '.txt' WHERE fit_score>=7"),
        "cover": lambda **_kw: advance("UPDATE jobs SET cover_letter_path=url || '.cl.txt'"),
        "pdf": lambda **_kw: advance("UPDATE jobs SET tailored_resume_path=replace(tailored_resume_path,'.txt','.pdf')"),
    }
    for stage, runner in runners.items():
        monkeypatch.setitem(pipeline._STAGE_RUNNERS, stage, lambda _r=runner, **kw: _r(**kw) or {"status": "ok"})
    monkeypatch.setattr(pipeline, "_STREAM_POLL_INTERVAL", 0.01)

    result = pipeline._run_streaming(list(pipeline.STAGE_ORDER), 7, full_pipeline=False)

    assert [s["stage"] for s in result["stages"]] == list(pipeline.STAGE_ORDER)
    assert {s["status"] for s in result["stages"]} == {"ok"}
    assert result["errors"] == {}
    rows = pending_db.execute("SELECT tailored_resume_path, cover_letter_path FROM jobs").fetchall()
    assert all(r[0].endswith(".pdf") and r[1].endswith(".cl.txt") for r in rows)
    assert all(pipeline._count_pending(stage) == 0 for stage in ("enrich", "score", "tailor", "cover", "pdf"))


def test_streaming_skips_stages_outside_the_requested_set(monkeypatch) -> None:
    ran: list[str] = []
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "discover", lambda **_kw: ran.append("discover") or {})
    monkeypatch.setattr(pipeline, "_count_pending", lambda *_a, **_k: 0)

    result = pipeline._run_streaming(["discover"], 7)

    assert ran == ["discover"]
    assert result["stages"][0]["stage"] == "discover"
    assert result["errors"] == {}


# ---------------------------------------------------------------------------
# Discover status: sequential and streaming must agree (sub-scraper errors)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"jobspy": "ok", "workday": "ok", "smartextract": "ok"}, "ok"),
        ({"jobspy": "ok", "workday": "error: x", "smartextract": "ok"}, "partial"),
        ({"jobspy": "error: a", "workday": "error: b", "smartextract": "error: c"}, "partial"),
    ],
)
def test_stage_status_discover_downgrades_sub_errors_to_partial(result: dict, expected: str) -> None:
    assert pipeline._stage_status("discover", result) == expected


def test_stage_status_for_other_stages_uses_the_reported_status() -> None:
    assert pipeline._stage_status("score", {"status": "error: nope"}) == "error: nope"
    assert pipeline._stage_status("score", {"status": "skipped"}) == "skipped"
    assert pipeline._stage_status("score", {}) == "ok"
    assert pipeline._stage_status("score", None) == "ok"
    # Only discover derives its status from per-scraper strings.
    assert pipeline._stage_status("enrich", {"jobspy": "error: ignored"}) == "ok"


@pytest.mark.parametrize("mode", ["sequential", "streaming"])
@pytest.mark.parametrize(
    ("failures", "expected"),
    [
        ((), "ok"),
        (("workday",), "partial"),
        (("jobspy", "workday", "smartextract"), "partial"),
    ],
)
def test_discover_sub_errors_report_the_same_status_in_both_modes(
    monkeypatch, mode: str, failures: tuple, expected: str
) -> None:
    _inject_discover(monkeypatch, **{name: _boom("down") for name in failures})
    run = pipeline._run_sequential if mode == "sequential" else pipeline._run_streaming

    result = run(["discover"], 7)

    assert result["stages"][0]["status"] == expected
    # partial is a warning, not a pipeline error (exit code stays 0 in both modes).
    assert result["errors"] == {}


def test_streaming_discover_keeps_sub_scraper_detail_in_tracker(monkeypatch) -> None:
    _inject_discover(monkeypatch, workday=_boom("down"))
    tracker = pipeline._StageTracker()

    pipeline._run_stage_streaming("discover", tracker, threading.Event())

    stored = tracker.get_results()["discover"]
    assert stored["status"] == "partial"
    assert stored["workday"] == "error: down"
    assert stored["jobspy"] == "ok"


# ---------------------------------------------------------------------------
# Sequential orchestrator
# ---------------------------------------------------------------------------

def test_sequential_collects_errors_but_not_partial_or_skipped(monkeypatch) -> None:
    monkeypatch.setitem(
        pipeline._STAGE_RUNNERS,
        "discover",
        lambda **_kw: {"jobspy": "error: x", "workday": "ok", "smartextract": "ok"},
    )
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "enrich", lambda **_kw: {"status": "error: detail broke"})
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "score", _boom("scorer crashed"))
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "tailor", lambda **_kw: {"status": "skipped"})
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "cover", lambda **_kw: "not-a-dict")

    result = pipeline._run_sequential(["discover", "enrich", "score", "tailor", "cover"], 7)

    statuses = {s["stage"]: s["status"] for s in result["stages"]}
    assert statuses == {
        "discover": "partial",
        "enrich": "error: detail broke",
        "score": "error: scorer crashed",
        "tailor": "skipped",
        "cover": "ok",
    }
    assert result["errors"] == {"enrich": "error: detail broke", "score": "error: scorer crashed"}
    assert result["elapsed"] >= 0


def test_sequential_passes_stage_specific_kwargs(monkeypatch) -> None:
    seen: dict[str, dict] = {}

    def recorder(stage: str):
        def run(**kwargs):
            seen[stage] = kwargs
            return {"status": "ok"}

        return run

    for stage in pipeline.STAGE_ORDER:
        monkeypatch.setitem(pipeline._STAGE_RUNNERS, stage, recorder(stage))

    pipeline._run_sequential(list(pipeline.STAGE_ORDER), 9, workers=5, validation_mode="strict", full_pipeline=True)

    assert seen["discover"] == {"workers": 5}
    assert seen["enrich"] == {"workers": 5}
    assert seen["score"] == {}
    assert seen["pdf"] == {}
    assert seen["tailor"] == {"min_score": 9, "validation_mode": "strict"}
    assert seen["cover"] == {"min_score": 9, "validation_mode": "strict", "allow_speculative": False}


# ---------------------------------------------------------------------------
# run_pipeline
# ---------------------------------------------------------------------------

@pytest.fixture
def bootstrapped(monkeypatch):
    monkeypatch.setattr(pipeline, "load_env", lambda: None)
    monkeypatch.setattr(pipeline, "ensure_dirs", lambda: None)
    monkeypatch.setattr(pipeline, "init_db", lambda: None)
    monkeypatch.setattr(pipeline, "get_stats", lambda: dict(_STATS))


def test_run_pipeline_dry_run_returns_empty_and_does_not_execute(monkeypatch, bootstrapped, capsys) -> None:
    for stage in pipeline.STAGE_ORDER:
        monkeypatch.setitem(pipeline._STAGE_RUNNERS, stage, lambda **_kw: pytest.fail("dry run executed a stage"))
    monkeypatch.setattr(pipeline, "_run_sequential", lambda *a, **k: pytest.fail("dry run executed"))
    monkeypatch.setattr(pipeline, "_run_streaming", lambda *a, **k: pytest.fail("dry run executed"))

    for stream in (False, True):
        result = pipeline.run_pipeline(stages=["score", "discover"], dry_run=True, stream=stream)

        assert result == {"stages": [], "errors": {}, "elapsed": 0.0}

    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "discover -> score" in out


def test_run_pipeline_sequential_summary_and_errors(monkeypatch, bootstrapped, capsys) -> None:
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "enrich", lambda **_kw: {"status": "ok"})
    monkeypatch.setitem(pipeline._STAGE_RUNNERS, "score", lambda **_kw: {"status": "error: scorer offline"})

    result = pipeline.run_pipeline(stages=["score", "enrich"])

    assert [s["stage"] for s in result["stages"]] == ["enrich", "score"]
    assert result["errors"] == {"score": "error: scorer offline"}
    out = capsys.readouterr().out
    assert "Pipeline Summary" in out
    assert "DB Final State" in out


def test_run_pipeline_stream_flag_uses_streaming_orchestrator(monkeypatch, bootstrapped) -> None:
    captured: dict = {}

    def fake_streaming(ordered, min_score, workers, validation_mode, full_pipeline):
        captured.update(ordered=ordered, min_score=min_score, workers=workers, full_pipeline=full_pipeline)
        return {"stages": [{"stage": "score", "status": "partial", "elapsed": 0.5}], "errors": {}, "elapsed": 0.5}

    monkeypatch.setattr(pipeline, "_run_streaming", fake_streaming)
    monkeypatch.setattr(pipeline, "_run_sequential", lambda *a, **k: pytest.fail("sequential used"))

    result = pipeline.run_pipeline(stages=["score"], min_score=9, stream=True, workers=2)

    assert captured == {"ordered": ["score"], "min_score": 9, "workers": 2, "full_pipeline": False}
    assert result["stages"][0]["status"] == "partial"


def test_run_pipeline_rejects_unknown_stage(bootstrapped) -> None:
    with pytest.raises(SystemExit):
        pipeline.run_pipeline(stages=["nope"])
