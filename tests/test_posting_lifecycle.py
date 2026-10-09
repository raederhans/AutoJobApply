from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from applypilot import database
from applypilot.apply.job_candidates import select_candidates
from applypilot.discovery import official
from applypilot.storage.database_migrations import DATABASE_SCHEMA_COMPONENT, DATABASE_SCHEMA_TABLE
from applypilot.storage.posting_lifecycle import (
    get_possible_repost_hints,
    get_posting_lifecycle,
    reconcile_posting_lifecycle,
)
from applypilot.storage.transactions import write_transaction

SOURCE = {
    "source_id": "official:example:greenhouse",
    "source_type": "official_careers",
    "provider": "greenhouse",
    "company_id": "example",
    "company_name": "Example",
}
START = datetime.now(UTC) - timedelta(hours=60)
URL = "https://jobs.example.test/one"


def _job(url=URL, identity="one"):
    return {
        "url": url,
        "title": "Data Intern",
        "company_name": "Example",
        "location": "Singapore",
        "external_id": identity,
        "verification_status": "verified_official",
    }


def _metadata(**overrides):
    return {
        "provider": "greenhouse",
        "inventory_contract": "official_api_v1",
        "inventory_scope": {"provider": "greenhouse", "tenant": "example", "country": ""},
        "inventory_complete": True,
        "coverage_mode": "full",
        **overrides,
    }


@pytest.fixture
def conn(tmp_path):
    connection = database.init_db(tmp_path / "lifecycle.db")
    yield connection
    database.close_connection(tmp_path / "lifecycle.db")


def _run(
    conn, jobs, hour=0, *, status="complete", pagination=True, metadata=None, source=None, ingest=True, reconcile=True
):
    source = source or SOURCE
    metadata = _metadata() if metadata is None else metadata
    run_id = database.start_radar_fetch_run(conn, source)
    conn.execute(
        "UPDATE radar_fetch_runs SET started_at=? WHERE run_id=?", ((START + timedelta(hours=hour)).isoformat(), run_id)
    )
    conn.commit()
    if ingest and jobs:
        database.ingest_radar_official_jobs(conn, run_id, source, jobs)
    database.finish_radar_fetch_run(
        conn,
        run_id,
        status=status,
        pagination_complete=pagination,
        normalized_count=len(jobs),
        raw_count=len(jobs),
        metadata=metadata,
    )
    if reconcile:
        reconcile_posting_lifecycle(conn, run_id, jobs)
    return run_id


def _state(conn, url=URL, hour=50):
    return get_posting_lifecycle(conn, url, now=START + timedelta(hours=hour))


def test_additive_migration_preserves_legacy_jobs_and_application_state(conn):
    database.store_jobs(conn, [_job()], site="legacy", strategy="test")
    conn.execute("UPDATE jobs SET apply_status='applied',applied_at='2026-10-01' WHERE url=?", (URL,))
    conn.commit()
    before = dict(conn.execute("SELECT * FROM jobs WHERE url=?", (URL,)).fetchone())
    assert _state(conn)["posting_status"] == "legacy_untracked"
    _run(conn, [_job()], ingest=False)
    _run(conn, [], hour=1)
    _run(conn, [], hour=25)
    assert _state(conn)["posting_status"] == "closed"
    assert dict(conn.execute("SELECT * FROM jobs WHERE url=?", (URL,)).fetchone()) == before
    assert database.DATABASE_SCHEMA_VERSION >= 2


def test_two_complete_absences_must_span_24_hours(conn):
    _run(conn, [_job()])
    _run(conn, [], hour=1)
    assert _state(conn)["posting_status"] == "needs_reverification"
    _run(conn, [], hour=2)
    assert _state(conn)["posting_status"] == "needs_reverification"
    _run(conn, [], hour=25)
    assert _state(conn)["posting_status"] == "closed"
    assert _state(conn)["posting_sources"][0]["missing_count"] == 3


@pytest.mark.parametrize(
    "status,pagination,metadata",
    [
        ("partial", True, _metadata()),
        ("failed", True, _metadata()),
        ("blocked", True, _metadata()),
        ("skipped", True, _metadata()),
        ("complete", False, _metadata()),
        ("complete", True, {}),
        ("complete", True, _metadata(inventory_complete=False)),
        ("complete", True, _metadata(coverage_mode="latest_only")),
        ("complete", True, _metadata(inventory_contract="legacy")),
        ("complete", True, _metadata(provider="rss", inventory_scope={"provider": "rss", "tenant": "example"})),
        (
            "complete",
            True,
            _metadata(
                provider="jobposting_jsonld", inventory_scope={"provider": "jobposting_jsonld", "tenant": "example"}
            ),
        ),
    ],
)
def test_incomplete_or_untrusted_sources_never_count_absence(conn, status, pagination, metadata):
    _run(conn, [_job()])
    _run(conn, [], hour=1, status=status, pagination=pagination, metadata=metadata)
    _run(conn, [], hour=26, status=status, pagination=pagination, metadata=metadata)
    assert _state(conn)["posting_status"] == "open"
    assert _state(conn)["posting_sources"][0]["missing_count"] == 0


def test_filtered_raw_inventory_is_presence_for_existing_jobs(conn):
    _run(conn, [_job()])
    _run(conn, [], hour=1)
    # CLI accepts zero jobs after local title/location filtering but passes raw inventory.
    _run(conn, [_job()], hour=26, ingest=False)
    assert _state(conn)["posting_status"] == "open"
    assert _state(conn)["posting_sources"][0]["missing_count"] == 0


def test_partial_verified_presence_refreshes_but_unverified_presence_does_not(conn):
    _run(conn, [_job()])
    _run(conn, [], hour=1)
    _run(conn, [_job()], hour=26, status="partial", ingest=False)
    assert _state(conn)["posting_status"] == "open"
    invalid = {**_job(), "verification_status": "unverified"}
    _run(conn, [invalid], hour=27, status="partial", ingest=False)
    assert _state(conn)["posting_sources"][0]["last_verified_at"] == (START + timedelta(hours=26)).isoformat()


@pytest.mark.parametrize("change", ["tenant", "country", "source", "type"])
def test_absence_is_isolated_by_scope_source_and_type(conn, change):
    _run(conn, [_job()])
    metadata, source = _metadata(), dict(SOURCE)
    if change in {"tenant", "country"}:
        metadata["inventory_scope"][change] = "other"
    elif change == "source":
        source["source_id"] = "official:other:greenhouse"
    else:
        source["source_type"] = "social_lead"
    _run(conn, [], hour=1, source=source, metadata=metadata)
    _run(conn, [], hour=26, source=source, metadata=metadata)
    assert _state(conn)["posting_status"] == "open"


def test_any_recent_open_source_wins_and_only_all_closed_closes(conn):
    _run(conn, [_job()])
    other = {**SOURCE, "source_id": "official:example:mirror"}
    _run(conn, [_job()], hour=1, source=other, ingest=False)
    _run(conn, [], hour=2)
    _run(conn, [], hour=26)
    assert _state(conn)["posting_status"] == "open"
    _run(conn, [], hour=27, source=other)
    assert _state(conn)["posting_status"] == "needs_reverification"
    _run(conn, [], hour=51, source=other)
    assert _state(conn, hour=51)["posting_status"] == "closed"


def test_repeat_run_and_older_late_completion_cannot_double_count_or_revert(conn):
    _run(conn, [_job()])
    late = _run(conn, [], hour=1, reconcile=False)
    newest = _run(conn, [_job()], hour=26, ingest=False)
    summary = reconcile_posting_lifecycle(conn, late, [])
    assert summary["reason"] == "older_source_run"
    assert reconcile_posting_lifecycle(conn, newest, [_job()])["reason"] == "run_already_reconciled"
    assert _state(conn)["posting_sources"][0]["missing_count"] == 0
    missing = _run(conn, [], hour=27)
    reconcile_posting_lifecycle(conn, missing, [])
    assert _state(conn)["posting_sources"][0]["missing_count"] == 1


def test_concurrent_reconciliation_of_same_run_counts_once(conn):
    _run(conn, [_job()])
    run_id = _run(conn, [], hour=1, reconcile=False)
    path = conn.execute("PRAGMA database_list").fetchone()[2]

    def reconcile():
        local = sqlite3.connect(path, timeout=10)
        local.row_factory = sqlite3.Row
        try:
            return reconcile_posting_lifecycle(local, run_id, [])
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reconcile(), range(2)))
    assert sorted(result["skipped"] for result in results) == [False, True]
    assert _state(conn)["posting_sources"][0]["missing_count"] == 1


def test_ttl_is_read_time_and_same_identity_reopens_only_once(conn):
    _run(conn, [_job()])
    assert _state(conn, hour=72)["posting_status"] == "open"
    assert _state(conn, hour=73)["posting_reason"] == "verified_presence_expired"
    _run(conn, [], hour=1)
    _run(conn, [], hour=25)
    _run(conn, [_job()], hour=26, ingest=False)
    assert _state(conn)["posting_status"] == "reopened"
    _run(conn, [_job()], hour=27, ingest=False)
    assert _state(conn)["posting_status"] == "open"


def test_different_identity_does_not_reopen_original_and_only_advises_repost(conn):
    _run(conn, [_job()])
    _run(conn, [], hour=1)
    _run(conn, [], hour=25)
    second_url = "https://jobs.example.test/two"
    _run(conn, [_job(second_url, "two")], hour=26)
    assert _state(conn)["posting_status"] == "closed"
    assert _state(conn, second_url)["posting_status"] == "open"
    hints = get_possible_repost_hints(conn, second_url)
    assert hints == [{"url": URL, "reason": "same_company_title_different_posting", "advisory": True}]
    assert conn.execute("SELECT apply_status FROM jobs WHERE url=?", (URL,)).fetchone()[0] is None
    assert conn.execute("SELECT possible_repost_of FROM jobs WHERE url=?", (second_url,)).fetchone()[0] is None


def test_changed_identity_at_same_url_requires_reverification(conn):
    _run(conn, [_job()])
    _run(conn, [], hour=1)
    _run(conn, [], hour=25)
    _run(conn, [_job(identity="new")], hour=26, ingest=False)
    assert _state(conn)["posting_status"] == "needs_reverification"
    assert _state(conn)["posting_sources"][0]["reason"] == "posting_identity_changed"


def test_changed_identity_stays_pending_and_blocks_reuse_until_original_returns(conn):
    _run(conn, [_job()])
    conn.execute(
        "UPDATE jobs SET tailored_resume_path='resume.pdf', tailor_status='machine_validated',"
        "cover_letter_status='not_required',eligibility_status='eligible',fit_score=8 WHERE url=?",
        (URL,),
    )
    conn.commit()

    _run(conn, [_job(identity="new")], hour=26, ingest=False)
    _run(conn, [_job(identity="new")], hour=27, ingest=False)

    state = _state(conn)
    assert state["posting_status"] == "needs_reverification"
    assert state["posting_sources"][0]["reason"] == "posting_identity_changed"
    assert state["posting_sources"][0]["posting_identity"] == "one"
    assert _candidates(conn) == []
    assert _candidates(conn, target=URL) == []
    assert _candidates(conn, preview=True) == []
    exact_preview = _candidates(conn, preview=True, target=URL)
    assert len(exact_preview) == 1
    assert exact_preview[0].job["posting_status"] == "needs_reverification"

    _run(conn, [_job(identity="one")], hour=28, ingest=False)
    assert _state(conn)["posting_status"] == "open"
    assert _state(conn)["posting_sources"][0]["posting_identity"] == "one"
    assert len(_candidates(conn)) == 1
    assert len(_candidates(conn, target=URL)) == 1


def _candidates(conn, *, preview=False, target=None):
    return select_candidates(
        conn,
        target_url=target,
        min_score=6,
        max_apply_attempts=3,
        preview_only=preview,
        allow_runtime_cover=False,
        excluded=set(),
        blocked_sites=[],
        blocked_patterns=[],
    )


def test_candidate_filter_and_claim_lock_recheck_lifecycle_without_jobs_change(conn):
    _run(conn, [_job()], hour=50)
    conn.execute(
        "UPDATE jobs SET tailored_resume_path='resume.pdf', tailor_status='machine_validated',"
        "cover_letter_status='not_required',eligibility_status='eligible',fit_score=8 WHERE url=?",
        (URL,),
    )
    conn.commit()
    candidate = _candidates(conn)[0]
    with write_transaction(conn):
        assert candidate.still_current(conn)
    _run(conn, [], hour=51)
    assert _candidates(conn) == []
    assert _candidates(conn, target=URL) == []
    assert _candidates(conn, preview=True) == []
    with write_transaction(conn):
        assert not candidate.still_current(conn)
    exact_preview = _candidates(conn, preview=True, target=URL)
    assert len(exact_preview) == 1
    assert exact_preview[0].job["posting_status"] == "needs_reverification"
    with write_transaction(conn):
        assert exact_preview[0].still_current(conn)
    with pytest.raises(RuntimeError):
        candidate.still_current(conn)


def test_inventory_count_mismatch_or_invalid_json_cannot_close(conn):
    _run(conn, [_job()])
    for hour in (1, 26):
        run_id = _run(conn, [], hour=hour, reconcile=False)
        conn.execute("UPDATE radar_fetch_runs SET normalized_count=2 WHERE run_id=?", (run_id,))
        conn.commit()
        assert reconcile_posting_lifecycle(conn, run_id, [])["missing"] == 0
    run_id = _run(conn, [], hour=27, reconcile=False)
    conn.execute("UPDATE radar_fetch_runs SET metadata_json='{' WHERE run_id=?", (run_id,))
    conn.commit()
    assert reconcile_posting_lifecycle(conn, run_id, [])["skipped"]
    assert _state(conn)["posting_status"] == "open"


@pytest.mark.parametrize("bad", [None, {}, {"url": URL, "title": "Data", "verification_status": "unverified"}])
def test_invalid_inventory_record_disables_absence(conn, bad):
    _run(conn, [_job()])
    for hour in (1, 26):
        _run(conn, [bad], hour=hour, ingest=False)
    assert _state(conn)["posting_sources"][0]["missing_count"] == 0


def test_lifecycle_snapshot_includes_closed_rows_without_current_observation(conn):
    _run(conn, [_job()])
    _run(conn, [], hour=1)
    _run(conn, [], hour=25)
    snapshot = database.get_radar_daily_snapshot(conn)
    assert snapshot["posting_lifecycle"][0]["posting_status"] == "closed"
    assert snapshot["posting_lifecycle"][0]["posting_reason"] == "all_sources_closed"


@pytest.mark.parametrize("provider,key", [("greenhouse", "board"), ("ashby", "board"), ("lever", "site")])
@pytest.mark.parametrize("payload", [{}, {"error": "bad"}, {"jobs": {}}, [None]])
def test_official_wrong_json_shape_never_attests_complete_inventory(provider, key, payload):
    company = {"id": "example", "name": "Example", "provider": provider, key: "example"}
    result = official.collect_company(company, lambda *_a, **_k: json.dumps(payload))
    assert result["status"] == "partial"
    assert result["metadata"]["inventory_complete"] is False


def test_official_inventory_scope_and_non_api_presence_only_contract():
    company = {
        "id": "example",
        "name": "Example",
        "provider": "smartrecruiters",
        "company_id": "Tenant",
        "country": "SG",
    }
    result = official.collect_company(company, lambda *_a, **_k: json.dumps({"content": [], "totalFound": 0}))
    assert result["metadata"]["inventory_complete"] is True
    assert result["metadata"]["inventory_scope"] == {"provider": "smartrecruiters", "tenant": "Tenant", "country": "sg"}
    for provider, key, body in (
        ("rss", "feed_url", "<rss><channel /></rss>"),
        ("jobposting_jsonld", "career_url", "<html></html>"),
    ):
        result = official.collect_company(
            {"id": "example", "provider": provider, key: "https://example.test"}, lambda *_a, body=body, **_k: body
        )
        assert result["metadata"]["inventory_complete"] is False


def test_real_api_empty_lists_are_valid_but_dropped_records_are_partial():
    for provider, key, payload in (
        ("greenhouse", "board", {"jobs": []}),
        ("ashby", "board", {"jobs": []}),
        ("lever", "site", []),
    ):
        company = {"id": "example", "provider": provider, key: "example"}
        result = official.collect_company(company, lambda *_a, payload=payload, **_k: json.dumps(payload))
        assert result["metadata"]["inventory_complete"] is True
        bad = [None] if provider == "lever" else {"jobs": [None]}
        result = official.collect_company(company, lambda *_a, bad=bad, **_k: json.dumps(bad))
        assert result["status"] == "partial"
        assert result["metadata"]["inventory_complete"] is False


def test_conflicting_inventory_identity_needs_reverification_and_cannot_close_other_job(conn):
    other_url = "https://jobs.example.test/other"
    _run(conn, [_job(), _job(other_url, "other")])
    _run(conn, [_job(), _job(identity="conflict")], hour=1, ingest=False)
    assert _state(conn)["posting_status"] == "needs_reverification"
    assert _state(conn)["posting_sources"][0]["reason"] == "conflicting_inventory_identity"
    assert _state(conn, other_url)["posting_sources"][0]["missing_count"] == 0
    assert _state(conn)["posting_sources"][0]["posting_identity"] == "one"
    _run(conn, [_job(identity="conflict")], hour=26, ingest=False)
    assert _state(conn)["posting_status"] == "needs_reverification"
    assert _state(conn)["posting_sources"][0]["posting_identity"] == "one"
    _run(conn, [_job()], hour=27, ingest=False)
    assert _state(conn)["posting_status"] == "open"
    assert _state(conn)["posting_sources"][0]["posting_identity"] == "one"
    assert _state(conn, other_url)["posting_sources"][0]["missing_count"] == 2


def test_first_conflicting_inventory_does_not_bind_an_arbitrary_identity(conn):
    database.store_jobs(conn, [_job()], site="legacy", strategy="test")
    _run(conn, [_job(identity="first"), _job(identity="second")], ingest=False)
    first_state = _state(conn)
    assert first_state["posting_status"] == "needs_reverification"
    assert first_state["posting_sources"][0]["reason"] == "conflicting_inventory_identity"
    assert first_state["posting_sources"][0]["posting_identity"] not in {"first", "second"}

    _run(conn, [_job(identity="first")], hour=26, ingest=False)
    next_state = _state(conn)
    assert next_state["posting_status"] == "needs_reverification"
    assert (
        next_state["posting_sources"][0]["posting_identity"]
        == first_state["posting_sources"][0]["posting_identity"]
    )


@pytest.mark.parametrize("extra", [{"paging": {"next": "cursor"}}, {"hasMore": True}])
def test_unknown_pagination_metadata_cannot_attest_full_inventory(extra):
    result = official.collect_company(
        {"id": "example", "provider": "greenhouse", "board": "example"},
        lambda *_a, **_k: json.dumps({"jobs": [], **extra}),
    )
    assert result["status"] == "partial"
    assert result["metadata"]["inventory_complete"] is False


def test_legacy_schema_version_one_upgrades_without_backfilling_evidence(tmp_path):
    path = tmp_path / "old.db"
    conn = database.init_db(path)
    database.store_jobs(conn, [_job()], site="legacy", strategy="test")
    before = dict(conn.execute("SELECT * FROM jobs").fetchone())
    conn.execute("DROP TABLE posting_source_states")
    conn.execute("DROP TABLE posting_lifecycle_runs")
    conn.execute(f"UPDATE {DATABASE_SCHEMA_TABLE} SET version=1 WHERE component=?", (DATABASE_SCHEMA_COMPONENT,))
    conn.commit()
    database.close_connection(path)
    upgraded = database.init_db(path)
    try:
        assert dict(upgraded.execute("SELECT * FROM jobs").fetchone()) == before
        assert upgraded.execute("SELECT COUNT(*) FROM posting_source_states").fetchone()[0] == 0
        assert get_posting_lifecycle(upgraded, URL)["posting_status"] == "legacy_untracked"
    finally:
        database.close_connection(path)
