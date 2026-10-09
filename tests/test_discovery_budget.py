from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest

from applypilot.database import close_connection, init_db
from applypilot.discovery.budget import plan_exploration, plan_official_collection
from applypilot.discovery.explore import _QUERY_PAIRS, default_exploration_queries
from applypilot.storage.radar import ingest_radar_leads, record_radar_observation

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    connection = init_db(tmp_path / "budget.db")
    yield connection
    close_connection()


def add_run(conn, source, *, query=None, age=60, status="partial", new=None,
            lead_count=0, error=None, running=False, **metadata):
    if query is not None:
        metadata["query"] = query
    if new is not None:
        metadata.update(budget_evidence_version=1, new_observations=new)
    finished = NOW - timedelta(minutes=age)
    conn.execute(
        "INSERT INTO radar_fetch_runs(run_id, source_id, started_at, finished_at, status, "
        "metadata_json, lead_count, new_count, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"run-{conn.execute('SELECT count(*) FROM radar_fetch_runs').fetchone()[0]}",
         source, finished.isoformat(), None if running else finished.isoformat(),
         status, json.dumps(metadata), lead_count, new or 0, error),
    )
    conn.commit()


def test_empty_database_plan_is_read_only_and_cold_start_rotates():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA query_only=ON")
    plan = plan_exploration(conn, now=NOW)
    expected = [(query, site) for query in default_exploration_queries(NOW.date())
                for site in ("linkedin", "indeed")]
    assert [(item["query"], item["site"]) for item in plan["selected"]] == expected
    assert len(plan["deferred"]) == 12
    assert plan["coverage"] == "non_exhaustive"
    assert plan["promotion_attribution"] == "unavailable"
    assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    conn.close()


def test_explicit_queries_preserve_order_and_default_all_combinations(conn):
    plan = plan_exploration(conn, queries=["custom c", "custom a", "custom b"], now=NOW)
    assert len(plan["selected"]) == 6
    assert [item["query"] for item in plan["selected"]] == ["custom c", "custom c", "custom a", "custom a", "custom b", "custom b"]
    restricted = plan_exploration(conn, queries=["custom c", "custom a"], budget=1, now=NOW)
    assert len(restricted["selected"]) == 1
    assert all(item["reason"] == "budget_exhausted" for item in restricted["deferred"])
    assert plan_exploration(conn, budget=0, now=NOW)["why"] == "zero_budget"
    with pytest.raises(ValueError):
        plan_exploration(conn, budget=7)
    with pytest.raises(ValueError):
        plan_exploration(conn, budget=-1)


def test_yield_ranking_reserves_unseen_slot_and_does_not_use_legacy_counts(conn):
    pool = [query for pair in _QUERY_PAIRS for query in pair]
    for query in pool[:-1]:
        add_run(conn, "indeed-jobs", query=query, new=1)
    add_run(conn, "indeed-jobs", query=pool[0], new=11)
    add_run(conn, "indeed-jobs", query=pool[1], lead_count=100000)
    plan = plan_exploration(conn, sites=["indeed"], budget=2, now=NOW)
    first, second = plan["selected"]
    assert first["query"] == pool[-1]
    assert first["reason"] == "exploration_slot"
    assert second["query"] == pool[0]
    assert second["evidence"]["new_observations_per_attempt"] == 6
    legacy = next(item for item in plan["deferred"] if item["query"] == pool[1])
    assert legacy["evidence"]["attempts"] == 2
    assert legacy["evidence"]["measured_attempts"] == 1
    assert legacy["evidence"]["new_observations"] == 1


def test_oldest_slot_and_filter_scope_use_real_history(conn):
    pool = [query for pair in _QUERY_PAIRS for query in pair]
    for index, query in enumerate(pool):
        add_run(conn, "indeed-jobs", query=query, age=60 + index, new=index)
    plan = plan_exploration(conn, sites=["indeed"], budget=1, now=NOW)
    assert plan["selected"][0]["query"] == pool[-1]
    fresh_filter = plan_exploration(conn, sites=["indeed"], job_type="internship", now=NOW)
    assert all(item["evidence"]["attempts"] == 0 for item in fresh_filter["selected"])


@pytest.mark.parametrize("status,error", [("failed", None), ("blocked", None), ("partial", "timeout")])
def test_provider_failure_cooldown_ignores_running_and_is_provider_wide(conn, status, error):
    add_run(conn, "linkedin-jobs", query="other", age=10, status=status, error=error)
    add_run(conn, "linkedin-jobs", query="intern", age=1, status="running", running=True)
    plan = plan_exploration(conn, queries=["intern"], now=NOW)
    assert [item["site"] for item in plan["selected"]] == ["indeed"]
    assert plan["deferred"][0]["reason"] == "provider_cooldown"
    later = plan_exploration(conn, queries=["intern"], now=NOW + timedelta(minutes=20))
    assert len(later["selected"]) == 2


def test_empty_and_newer_finished_success_clear_failure_cooldown(conn):
    add_run(conn, "indeed-jobs", query="intern", age=10, status="failed")
    add_run(conn, "indeed-jobs", query="other", age=1, search_status="empty", new=0)
    assert len(plan_exploration(conn, queries=["intern"], sites=["indeed"], now=NOW)["selected"]) == 1


def test_skipped_plan_does_not_hide_provider_failure_and_plan_can_use_read_only_db(conn):
    add_run(conn, "indeed-jobs", query="intern", age=10, status="failed")
    add_run(conn, "indeed-jobs", query="other", age=1, status="skipped")
    conn.execute("PRAGMA query_only=ON")
    plan = plan_exploration(conn, queries=["intern"], sites=["indeed"], now=NOW)
    assert plan["selected"] == []
    assert plan["deferred"][0]["reason"] == "provider_cooldown"
    assert plan_official_collection(conn, companies(), now=NOW)["selected"]


def test_query_yield_outside_30_day_window_is_not_used(conn):
    query = _QUERY_PAIRS[0][0]
    add_run(conn, "indeed-jobs", query=query, age=31 * 24 * 60, new=10000)
    plan = plan_exploration(conn, queries=[query], sites=["indeed"], now=NOW)
    assert plan["selected"][0]["evidence"]["yield_status"] == "unavailable"
    assert plan["selected"][0]["evidence"]["new_observations"] is None


def companies():
    return [{"id": "a", "provider": "lever", "active": True, "cadence": "daily"},
            {"id": "b", "provider": "greenhouse", "active": True, "cadence": "daily"},
            {"id": "off", "provider": "lever", "active": False}]


def test_official_legacy_behavior_and_opt_in_due_cooldown(conn):
    add_run(conn, "official:a:lever", age=10, status="failed", new=2)
    add_run(conn, "official:b:greenhouse", age=60, status="complete", new=3)
    legacy = plan_official_collection(conn, companies(), now=NOW)
    assert [item["company_id"] for item in legacy["selected"]] == ["a", "b"]
    assert legacy["selected"][1]["evidence"]["new_stored_jobs"] == 3
    due = plan_official_collection(conn, companies(), due_only=True, now=NOW)
    assert due["selected"] == []
    assert [item["reason"] for item in due["deferred"]] == ["provider_cooldown", "not_due", "inactive"]
    explicit = plan_official_collection(conn, companies(), due_only=True, explicit=True, now=NOW)
    assert [item["company_id"] for item in explicit["selected"]] == ["b"]
    assert explicit["selected"][0]["company"] == companies()[1]
    later = plan_official_collection(conn, companies(), due_only=True, now=NOW + timedelta(days=1))
    assert len(later["selected"]) == 2
    assert plan_official_collection(conn, companies(), budget=0, now=NOW)["selected"] == []


def test_official_failed_attempt_can_retry_after_cooldown_not_after_daily_interval(conn):
    add_run(conn, "official:a:lever", age=10, status="failed")
    assert plan_official_collection(conn, companies()[:1], due_only=True, now=NOW)["selected"] == []
    plan = plan_official_collection(conn, companies()[:1], due_only=True, now=NOW + timedelta(minutes=20))
    assert plan["selected"][0]["evidence"]["due_at"] is None
    assert plan["selected"][0]["reason"] == "least_recent_attempt"
    add_run(conn, "official:a:lever", age=25 * 60, status="complete")
    plan = plan_official_collection(conn, companies()[:1], due_only=True, now=NOW + timedelta(minutes=20))
    assert plan["selected"][0]["evidence"]["due"] is True


def test_official_budget_moves_to_uncollected_then_oldest_source(conn):
    active = companies()[:2]
    first = plan_official_collection(conn, active, budget=1, now=NOW)
    assert first["selected"][0]["company_id"] == "a"
    add_run(conn, "official:a:lever", age=1, status="complete", new=100)
    second = plan_official_collection(conn, active, budget=1, now=NOW)
    assert second["selected"][0]["company_id"] == "b"
    add_run(conn, "official:b:greenhouse", age=0, status="complete", new=1)
    third = plan_official_collection(conn, active, budget=1, now=NOW + timedelta(days=1))
    assert third["selected"][0]["company_id"] == "a"
    explicit = plan_official_collection(conn, list(reversed(active)), budget=1, explicit=True, now=NOW)
    assert explicit["selected"][0]["company_id"] == "b"


def test_repeated_failed_source_does_not_starve_older_successful_source(conn):
    active = companies()[:2]
    add_run(conn, "official:a:lever", age=31, status="failed", error="unavailable")
    add_run(conn, "official:b:greenhouse", age=25 * 60, status="complete", new=3)
    plan = plan_official_collection(conn, active, budget=1, due_only=True, now=NOW)
    assert plan["selected"][0]["company_id"] == "b"
    assert plan["selected"][0]["reason"] == "least_recent_attempt"
    latest_only = [{"id": "rss", "provider": "rss", "active": True}]
    add_run(conn, "official:rss:rss", age=31, status="partial", error="source exposes latest items only; full pagination unavailable")
    plan = plan_official_collection(conn, latest_only, budget=1, due_only=True, now=NOW)
    item = plan["selected"][0]
    assert item["reason"] == "least_recent_attempt"
    assert item["evidence"]["last_finished_at"] is not None
    assert item["evidence"]["last_successful_at"] is None


def test_new_observation_counts_deduplicate_batch_and_preserve_existing_updates(conn):
    source = {"source_id": "indeed-jobs", "source_type": "job_lead"}
    lead = {"source_url": "https://sg.indeed.com/viewjob?jk=one", "title": "Old", "company_name": "A"}
    counts = ingest_radar_leads(conn, "one", source, [lead, lead], return_evidence=True)
    assert counts == {"leads": 2, "new_observations": 1, "duplicate_observations": 1}
    first = dict(conn.execute("SELECT * FROM radar_source_observations").fetchone())
    changed = {**lead, "title": "New", "verification_status": "updated"}
    assert ingest_radar_leads(conn, "two", source, [changed]) == {"leads": 1}
    row = dict(conn.execute("SELECT * FROM radar_source_observations").fetchone())
    assert row["first_seen_at"] == first["first_seen_at"]
    assert row["title"] == "New" and row["verification_status"] == "updated"
    assert row["last_run_id"] == "two"
    key = record_radar_observation(conn, "three", {**changed, "source_id": "indeed-jobs"})
    assert isinstance(key, str)
    assert conn.execute("SELECT source_type FROM radar_source_observations").fetchone()[0] == "job_lead"


def test_concurrent_connections_cannot_both_count_same_observation_as_new(conn):
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    barrier = Barrier(2)

    def ingest():
        separate = sqlite3.connect(path, timeout=10)
        try:
            barrier.wait(timeout=10)
            return ingest_radar_leads(separate, "concurrent", {"source_id": "indeed-jobs"},
                [{"source_url": "https://sg.indeed.com/viewjob?jk=race", "title": "Intern"}], return_evidence=True)
        finally:
            separate.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: ingest(), range(2)))
    assert sum(result["new_observations"] for result in results) == 1
    assert sum(result["duplicate_observations"] for result in results) == 1
    assert conn.execute("SELECT count(*) FROM radar_source_observations").fetchone()[0] == 1
