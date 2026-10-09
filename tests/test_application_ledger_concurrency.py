"""Real SQLite races over synthetic data; no runtime/profile/browser dependencies."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from applypilot.storage import application_ledger as ledger

WORKERS = 8
NOW = datetime(2026, 10, 9, 1, tzinfo=UTC)


def _connect(path: Path, *, autocommit: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10, isolation_level=None if autocommit else "")
    connection.row_factory = sqlite3.Row
    return connection


def _seed(path: Path, urls: list[str], journal: str = "DELETE") -> list[str]:
    with _connect(path) as connection:
        connection.execute(f"PRAGMA journal_mode={journal}")
        connection.execute(
            "CREATE TABLE jobs (url TEXT PRIMARY KEY, apply_status TEXT, applied_at TEXT, "
            "agent_id TEXT, apply_retry_blocked INTEGER DEFAULT 0, apply_retry_reason TEXT, "
            "verification_confidence TEXT, application_evidence TEXT, apply_error TEXT)"
        )
        ledger.ensure_schema(connection)
        attempts = []
        for index, url in enumerate(urls):
            connection.execute(
                "INSERT OR IGNORE INTO jobs(url, apply_status) VALUES (?, 'in_progress')", (url,)
            )
            attempt = ledger.start_attempt(connection, url, f"synthetic-worker-{index}", batch_id="batch")
            connection.execute(
                "UPDATE application_attempts SET phase='reservation', lease_expires_at=? WHERE attempt_id=?",
                ((NOW + timedelta(hours=1)).isoformat(), attempt),
            )
            attempts.append(attempt)
        connection.commit()
    connection.close()
    return attempts


def _race(path: Path, candidates: list[tuple], action, *, autocommit: bool = False) -> list:
    barrier = threading.Barrier(len(candidates), timeout=10)

    def compete(candidate):
        connection = _connect(path, autocommit=autocommit)
        try:
            barrier.wait()
            return action(connection, *candidate)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
        futures = [pool.submit(compete, candidate) for candidate in candidates]
        return [future.result(timeout=20) for future in futures]


def _claim(connection, url, attempt, *, batch="batch", cap=WORKERS, **limits):
    return ledger.claim_submission_gate(
        connection, batch, url, cap, attempt, success_target=WORKERS,
        hourly_maximum=limits.pop("hourly_maximum", 100),
        minimum_gap_seconds=limits.pop("minimum_gap_seconds", 0),
        audit_fingerprint=limits.pop("audit_fingerprint", "synthetic-audit"), now=NOW, **limits,
    )


def _counts(path: Path) -> tuple[int, int]:
    connection = _connect(path)
    try:
        return (
            connection.execute("SELECT COUNT(*) FROM application_batch_consumptions").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM application_submission_gates").fetchone()[0],
        )
    finally:
        connection.close()


def _evidence(label, results, path):
    outcomes = Counter(
        str(result.get("reason")) if isinstance(result, dict) else str(result) for result in results
    )
    print(json.dumps({"race": label, "workers": len(results), "outcomes": dict(outcomes),
                      "slots_and_gates": _counts(path)}, sort_keys=True))


@pytest.mark.parametrize("journal", ["DELETE", "WAL"])
@pytest.mark.parametrize("autocommit", [False, True])
@pytest.mark.parametrize("same_job", [False, True], ids=["last-slot", "same-job"])
def test_batch_reservation_has_exactly_one_winner(tmp_path, journal, autocommit, same_job):
    path = tmp_path / "reservation.db"
    urls = [f"https://synthetic.invalid/jobs/{0 if same_job else i}" for i in range(WORKERS)]
    _seed(path, urls, journal)
    results = _race(
        path, [(url,) for url in urls],
        lambda connection, url: ledger.reserve_batch_submission(connection, "batch", url, 1),
        autocommit=autocommit,
    )
    _evidence(f"reservation-{journal}-{autocommit}-{same_job}", results, path)
    assert results.count(True) == 1
    assert results.count(False) == WORKERS - 1
    assert _counts(path) == (1, 0)


@pytest.mark.parametrize("journal", ["DELETE", "WAL"])
@pytest.mark.parametrize("scenario", ["same-job", "last-slot", "global-writer", "hourly-cap", "gap"])
def test_gate_race_preserves_batch_and_global_capacity(tmp_path, journal, scenario):
    path = tmp_path / "gate.db"
    urls = [f"https://synthetic.invalid/jobs/{0 if scenario == 'same-job' else i}" for i in range(WORKERS)]
    attempts = _seed(path, urls, journal)

    def claim(connection, url, attempt, index):
        return _claim(
            connection, url, attempt,
            batch=f"batch-{index}" if scenario in {"global-writer", "hourly-cap", "gap"} else "batch",
            cap=1 if scenario == "last-slot" else WORKERS,
            hourly_maximum=1 if scenario == "hourly-cap" else 100,
            minimum_gap_seconds=20 if scenario == "gap" else 0,
        )

    results = _race(path, [(url, attempt, i) for i, (url, attempt) in enumerate(zip(urls, attempts))], claim)
    _evidence(f"gate-{journal}-{scenario}", results, path)
    denied_reason = {
        "same-job": "job_already_reserved", "last-slot": "authorization_batch_capacity_exhausted",
        "global-writer": "submit_writer_busy", "hourly-cap": "rolling_hour_submission_cap",
        "gap": "minimum_submission_gap",
    }[scenario]
    assert sum(result["claimed"] is True for result in results) == 1
    assert sum(result["reason"] == denied_reason for result in results) == WORKERS - 1
    assert _counts(path) == (1, 1)


@pytest.mark.parametrize("conflicting", [False, True], ids=["exact-intent", "conflicting-intent"])
def test_concurrent_attempt_intent_is_idempotent_or_conflicting(tmp_path, conflicting):
    path = tmp_path / "intent.db"
    url = "https://synthetic.invalid/jobs/intent"
    attempt = _seed(path, [url])[0]
    results = _race(
        path, [(i,) for i in range(WORKERS)],
        lambda connection, index: _claim(
            connection, url, attempt,
            audit_fingerprint=f"audit-{index}" if conflicting else "synthetic-audit",
        ),
    )
    _evidence(f"intent-{conflicting}", results, path)
    assert sum(result.get("replay") is False for result in results) == 1
    if conflicting:
        assert sum(result["claimed"] is True for result in results) == 1
        assert sum(result["reason"] == "submission_gate_claim_conflict" for result in results) == WORKERS - 1
    else:
        assert all(result["claimed"] is True for result in results)
        assert sum(result.get("replay") is True for result in results) == WORKERS - 1
        assert len({result["gate_id"] for result in results}) == 1
        assert len({result["idempotency_key"] for result in results}) == 1
    assert _counts(path) == (1, 1)


@pytest.mark.parametrize("state", ["cancelled_before_action", "failed", "applied", "submission_uncertain"])
def test_terminal_claim_cannot_be_replayed_as_permission_to_submit(tmp_path, state):
    path = tmp_path / "terminal.db"
    url = "https://synthetic.invalid/jobs/terminal"
    attempt = _seed(path, [url])[0]
    connection = _connect(path)
    try:
        assert _claim(connection, url, attempt)["claimed"] is True
        assert ledger.update_submission_gate_state(connection, attempt, state)
    finally:
        connection.close()
    results = _race(path, [(url, attempt)] * WORKERS, _claim)
    _evidence(f"terminal-replay-{state}", results, path)
    assert all(result["claimed"] is False for result in results)
    assert all(result["reason"] == "submission_gate_not_active" for result in results)
    assert _counts(path) == (1, 1)


@pytest.mark.parametrize("change", ["expired", "finalized", "submit-started"])
def test_replay_checks_current_attempt_authority(tmp_path, change):
    path = tmp_path / "stale-replay.db"
    url = "https://synthetic.invalid/jobs/stale"
    attempt = _seed(path, [url])[0]
    connection = _connect(path)
    try:
        assert _claim(connection, url, attempt)["claimed"] is True
        if change == "expired":
            connection.execute("UPDATE application_attempts SET lease_expires_at=? WHERE attempt_id=?",
                               ((NOW - timedelta(seconds=1)).isoformat(), attempt))
        elif change == "finalized":
            connection.execute("UPDATE application_attempts SET status='released' WHERE attempt_id=?", (attempt,))
        else:
            connection.execute("UPDATE application_attempts SET submit_started=1, phase='submit' WHERE attempt_id=?",
                               (attempt,))
        connection.commit()
    finally:
        connection.close()
    results = _race(path, [(url, attempt)] * WORKERS, _claim)
    _evidence(f"stale-replay-{change}", results, path)
    assert all(result["claimed"] is False for result in results)
    expected = "submission_gate_attempt_lease_expired" if change == "expired" else "submission_gate_attempt_not_ready"
    assert all(result["reason"] == expected for result in results)
    assert _counts(path) == (1, 1)


@pytest.mark.parametrize("state", ["cancelled_before_action", "failed", "submission_uncertain"])
def test_terminal_slot_remains_consumed_for_new_attempt_and_job(tmp_path, state):
    path = tmp_path / "consumed.db"
    url = "https://synthetic.invalid/jobs/consumed"
    urls = [url, url] + [f"https://synthetic.invalid/jobs/{i}" for i in range(WORKERS - 1)]
    attempts = _seed(path, urls)
    connection = _connect(path)
    try:
        assert _claim(connection, url, attempts[0], cap=1)["claimed"] is True
        assert ledger.update_submission_gate_state(connection, attempts[0], state)
    finally:
        connection.close()
    results = _race(
        path, list(zip(urls[1:], attempts[1:])),
        lambda connection, candidate_url, attempt: _claim(connection, candidate_url, attempt, cap=1),
    )
    _evidence(f"consumed-{state}", results, path)
    assert all(result["claimed"] is False for result in results)
    assert results[0]["reason"] == "job_already_reserved"
    assert all(result["reason"] == "authorization_batch_capacity_exhausted" for result in results[1:])
    assert _counts(path) == (1, 1)


def test_concurrent_expiry_recovery_counts_once_and_preserves_post_submit_uncertainty(tmp_path):
    path = tmp_path / "recovery.db"
    urls = ["https://synthetic.invalid/jobs/pre", "https://synthetic.invalid/jobs/post"]
    attempts = _seed(path, urls)
    connection = _connect(path)
    try:
        connection.execute("UPDATE application_attempts SET lease_expires_at=?",
                           ((NOW - timedelta(seconds=1)).isoformat(),))
        connection.execute("UPDATE application_attempts SET submit_started=1 WHERE attempt_id=?", (attempts[1],))
        connection.commit()
    finally:
        connection.close()
    results = _race(path, [()] * WORKERS, lambda connection: ledger.recover_stale_attempts(connection, now=NOW))
    print(json.dumps({"race": "expiry-recovery", "results": results}, sort_keys=True))
    assert sum(result["pre_submit"] for result in results) == 1
    assert sum(result["submission_uncertain"] for result in results) == 1
    connection = _connect(path)
    try:
        statuses = [tuple(row) for row in connection.execute(
            "SELECT apply_status, apply_retry_blocked FROM jobs ORDER BY url"
        )]
        assert statuses == [("submission_uncertain", 1), ("failed", 0)]
        assert ledger.finalize_attempt(connection, attempts[1], "applied") is False
    finally:
        connection.close()
