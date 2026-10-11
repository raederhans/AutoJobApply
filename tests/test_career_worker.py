"""Fictional isolated workspaces only; no private DB, ATS, browser, or model."""

from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest

from applypilot.apply.contracts import TaskSpec
from applypilot.career.worker import CareerWorker, PublicationGuard
from applypilot.database import DATABASE_SCHEMA_VERSION, close_connection, init_db
from applypilot.storage import career_workbench as store
from applypilot.storage import task_journal as journal
from applypilot.storage.transactions import write_transaction


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "fictional"
    path.mkdir()
    conn = init_db(path / "applypilot.db")
    with write_transaction(conn):
        store.ensure_schema(conn)
        conn.execute("CREATE TABLE fictional_results(value TEXT)")
    close_connection(path / "applypilot.db")
    return path


def settled(worker, job_id, status="completed"):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        value = worker.get(job_id)
        if value["status"] == status:
            return value
        time.sleep(0.01)
    pytest.fail(f"job did not reach {status}: {worker.get(job_id)}")


def test_workbench_parent_tables_share_transactional_migration():
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(RuntimeError, match="rollback migration"), write_transaction(conn):
            store.ensure_schema(conn)
            conn.execute("INSERT INTO career_workbench_materials VALUES('fictional','{}','now')")
            conn.execute("INSERT INTO career_workbench_requests VALUES('fictional','import','{}','{}','now')")
            raise RuntimeError("rollback migration")
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
        with write_transaction(conn):
            store.ensure_schema(conn)
            store.ensure_schema(conn)
        assert [row[1] for row in conn.execute("PRAGMA table_info(career_workbench_materials)")] == [
            "material_id", "payload_json", "created_at"]
        assert [row[1] for row in conn.execute("PRAGMA table_info(career_workbench_requests)")] == [
            "request_key", "operation", "input_json", "result_json", "created_at"]
    finally:
        conn.close()


def test_submit_is_immediate_idempotent_bounded_and_single_thread(workspace):
    started, release = threading.Event(), threading.Event()
    threads, calls = [], []

    def execute(conn, operation, payload, guard):
        threads.append(threading.get_ident())
        calls.append(payload["n"])
        started.set()
        assert release.wait(5)
        with guard.publication():
            conn.execute("INSERT INTO fictional_results VALUES(?)", (str(payload["n"]),))
        return {"diagnosis_id": "fictional-" + str(payload["n"]), "body": "omit large result"}

    worker = CareerWorker(workspace, execute, max_pending=1).start()
    try:
        began = time.monotonic()
        first = worker.submit("diagnose", {"n": 1}, "first")
        assert time.monotonic() - began < 1
        assert first["status"] == "pending"
        assert started.wait(3)
        same = worker.submit("diagnose", {"n": 1}, "first")
        assert same["job_id"] == first["job_id"]
        with pytest.raises(ValueError, match="different inputs"):
            worker.submit("diagnose", {"n": 2}, "first")
        second = worker.submit("route", {"n": 2}, "second")
        with pytest.raises(RuntimeError, match="full"):
            worker.submit("diagnose", {"n": 3}, "third")
        release.set()
        done = settled(worker, first["job_id"])
        settled(worker, second["job_id"])
        assert done["result_refs"] == {"diagnosis_id": "fictional-1"}
        assert "body" not in done["result"]["output"]
        assert calls == [1, 2] and len(set(threads)) == 1
        assert threads[0] != threading.get_ident()
    finally:
        release.set()
        worker.close()


def test_cancel_during_compute_blocks_late_publication_and_pending_job(workspace):
    started, release = threading.Event(), threading.Event()
    calls = []

    def execute(conn, operation, payload, guard):
        calls.append(1)
        started.set()
        assert release.wait(5)
        with guard.publication():
            conn.execute("INSERT INTO fictional_results VALUES('late')")
        return {"diagnosis_id": "late"}

    worker = CareerWorker(workspace, execute).start()
    try:
        first = worker.submit("diagnose", {}, "running")
        assert started.wait(3)
        second = worker.submit("diagnose", {}, "pending")
        assert worker.cancel(second["job_id"])["status"] == "cancelled"
        assert worker.cancel(first["job_id"])["cancel_requested"]
        release.set()
        settled(worker, first["job_id"], "cancelled")
        assert calls == [1]
        with sqlite3.connect(workspace / "applypilot.db") as conn:
            assert conn.execute("SELECT * FROM fictional_results").fetchall() == []
    finally:
        release.set()
        worker.close()


def test_publication_committed_wins_cancel_then_finishes(workspace):
    published, release = threading.Event(), threading.Event()

    def execute(conn, operation, payload, guard):
        with guard.publication():
            conn.execute("INSERT INTO fictional_results VALUES('committed')")
        published.set()
        assert release.wait(5)
        return {"route_id": "committed"}

    worker = CareerWorker(workspace, execute).start()
    try:
        job = worker.submit("route", {}, "commit")
        assert published.wait(3)
        result = worker.cancel(job["job_id"])
        assert result["cancellation_outcome"] == "publication_committed"
        assert not result["cancel_requested"]
        release.set()
        assert settled(worker, job["job_id"])["result_refs"] == {"route_id": "committed"}
    finally:
        release.set()
        worker.close()


def test_guard_rollback_and_failure_is_durable(workspace):
    def execute(conn, operation, payload, guard):
        with guard.publication():
            conn.execute("INSERT INTO fictional_results VALUES('rollback')")
            raise ValueError("fictional invalid input")

    worker = CareerWorker(workspace, execute).start()
    try:
        job = worker.submit("route", {}, "fail")
        done = settled(worker, job["job_id"], "failed")
        assert done["error"] == "ValueError"
        with sqlite3.connect(workspace / "applypilot.db") as conn:
            assert conn.execute("SELECT * FROM fictional_results").fetchall() == []
    finally:
        worker.close()
    fresh = CareerWorker(workspace, lambda *args: {}).start()
    try:
        assert fresh.get(job["job_id"])["status"] == "failed"
    finally:
        fresh.close()


def test_cancel_serializes_with_publication_transaction(workspace):
    publishing, commit, cancel_started, cancelled = (threading.Event() for _ in range(4))
    outcome = []

    def execute(conn, operation, payload, guard):
        with guard.publication():
            conn.execute("INSERT INTO fictional_results VALUES('atomic')")
            publishing.set()
            assert commit.wait(5)
        return {"route_id": "atomic"}

    worker = CareerWorker(workspace, execute).start()
    job = worker.submit("route", {}, "atomic")

    def cancel():
        cancel_started.set()
        outcome.append(worker.cancel(job["job_id"]))
        cancelled.set()

    thread = threading.Thread(target=cancel)
    try:
        assert publishing.wait(3)
        thread.start()
        assert cancel_started.wait(3)
        assert not cancelled.is_set()
        commit.set()
        assert cancelled.wait(3)
        assert outcome[0]["status"] == "completed" or outcome[0].get(
            "cancellation_outcome") == "publication_committed"
        settled(worker, job["job_id"])
        with sqlite3.connect(workspace / "applypilot.db") as conn:
            assert conn.execute("SELECT value FROM fictional_results").fetchall() == [("atomic",)]
    finally:
        commit.set()
        if thread.ident is not None:
            thread.join(3)
        worker.close()


def test_close_fences_computing_job_and_preserves_queued_history(workspace):
    started, release = threading.Event(), threading.Event()

    def execute(conn, operation, payload, guard):
        started.set()
        assert release.wait(5)
        with guard.publication():
            conn.execute("INSERT INTO fictional_results VALUES('too late')")
        return {}

    worker = CareerWorker(workspace, execute).start()
    first = worker.submit("diagnose", {}, "close-running")
    assert started.wait(3)
    second = worker.submit("diagnose", {}, "close-pending")
    thread = threading.Thread(target=worker.close)
    thread.start()
    try:
        assert worker._closing.wait(3)
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        assert worker.get(first["job_id"])["status"] == "interrupted"
        assert worker.get(second["job_id"])["status"] == "interrupted"
        with sqlite3.connect(workspace / "applypilot.db") as conn:
            assert conn.execute("SELECT * FROM fictional_results").fetchall() == []
    finally:
        release.set()
        worker.close()


def test_restart_marks_pending_and_running_interrupted_explicit_resume(workspace):
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        for key in ("pending", "running"):
            spec = TaskSpec(task_id="old-" + key, kind="career.diagnose", objective="fictional",
                            effect_class="career_local_write")
            journal.register(conn, spec)
            if key == "running":
                journal.claim(conn, spec.task_id, "stopped-worker")
            conn.execute("INSERT INTO career_workbench_jobs(job_id,request_key,operation,payload_json,"
                         "input_lease_json,created_at) VALUES(?,?,'diagnose','{}','{}','fictional')",
                         (spec.task_id, key))
            conn.commit()
    calls = []
    worker = CareerWorker(workspace, lambda *args: calls.append(1) or {"diagnosis_id": "resumed"}).start()
    try:
        assert worker.get("old-running")["status"] == "interrupted"
        assert worker.get("old-pending")["status"] == "interrupted"
        assert calls == []
        new = worker.resume("old-running", "explicit-resume")
        assert new["job_id"] != "old-running" and new["parent_job_id"] == "old-running"
        assert worker.resume("old-running", "explicit-resume")["job_id"] == new["job_id"]
        settled(worker, new["job_id"])
        assert calls == [1] and worker.get("old-running")["status"] == "interrupted"
    finally:
        worker.close()


def interrupted_publication(conn, workspace):
    lease = {"target_id": "fictional-target", "target_revision": 1, "input_fingerprint": "old"}
    with write_transaction(conn):
        conn.execute("INSERT INTO career_targets VALUES('fictional-target',1,'old','{}','now','now')")
        spec = TaskSpec(task_id="crashed-supplement", kind="career.supplement", objective="fictional",
                        inputs={"_input_lease": lease}, effect_class="career_local_write")
        journal.register(conn, spec)
        conn.execute("INSERT INTO career_workbench_jobs(job_id,request_key,operation,payload_json,"
                     "input_lease_json,created_at) VALUES(?,?,'supplement',?,?,?)",
                     (spec.task_id, "crashed-request", json.dumps({"_input_lease": lease}), json.dumps(lease), "now"))
    claimed = journal.claim(conn, spec.task_id, "crashed-owner")
    holder = CareerWorker(workspace, lambda *args: pytest.fail("old publication must not be re-executed"))
    return spec.task_id, PublicationGuard(holder, conn, claimed.lease_token, lease)


def test_restart_recovers_atomic_publication_without_replaying_stale_target_lease(workspace):
    refs = {"target_id": "fictional-target", "diagnosis_id": "committed-diagnosis", "route_id": "committed-route"}
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        job_id, guard = interrupted_publication(conn, workspace)
        with guard.publication():
            conn.execute("UPDATE career_targets SET revision=2,input_fingerprint='new'")
            conn.execute("INSERT INTO fictional_results VALUES('committed-before-crash')")
            guard.record_result(refs)
        assert conn.execute("SELECT status FROM agent_tasks WHERE task_id=?", (job_id,)).fetchone()[0] == "running"
    calls = []
    worker = CareerWorker(workspace, lambda *args: calls.append(1) or {}).start()
    try:
        recovered = worker.get(job_id)
        assert recovered["status"] == "completed" and recovered["result_refs"] == refs
        assert recovered["error"] is None
        assert recovered["input_lease"]["target_revision"] == 1  # intentionally stale after successful supplement
        assert not recovered["cancel_requested"] and calls == []
        with pytest.raises(ValueError, match="only stopped"):
            worker.resume(job_id, "must-not-rerun")
        with sqlite3.connect(workspace / "applypilot.db") as conn:
            assert conn.execute("SELECT revision FROM career_targets").fetchone()[0] == 2
            assert conn.execute("SELECT value FROM fictional_results").fetchall() == [("committed-before-crash",)]
            assert conn.execute("SELECT COUNT(*) FROM career_workbench_jobs").fetchone()[0] == 1
    finally:
        worker.close()
    restarted = CareerWorker(workspace, lambda *args: calls.append(1) or {}).start()
    try:
        assert restarted.get(job_id)["result_refs"] == refs and calls == []
    finally:
        restarted.close()


def test_rolled_back_publication_marker_stays_interrupted_after_restart(workspace):
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        job_id, guard = interrupted_publication(conn, workspace)
        with pytest.raises(RuntimeError, match="crash before commit"), guard.publication():
            conn.execute("UPDATE career_targets SET revision=2,input_fingerprint='new'")
            guard.record_result({"target_id": "fictional-target", "route_id": "uncommitted-route"})
            raise RuntimeError("crash before commit")
        assert store.published_result(conn, job_id) is None
        assert conn.execute("SELECT revision FROM career_targets").fetchone()[0] == 1
    worker = CareerWorker(workspace, lambda *args: pytest.fail("restart must not replay rollback")).start()
    try:
        result = worker.get(job_id)
        assert result["status"] == "interrupted" and result["result_refs"] == {}
    finally:
        worker.close()


def test_result_marker_requires_guard_transaction_and_immutable_path_free_refs(workspace):
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        job_id, guard = interrupted_publication(conn, workspace)
        with pytest.raises(RuntimeError, match="active guard.publication"):
            guard.record_result({"target_id": "fictional-target"})
        with write_transaction(conn), pytest.raises(RuntimeError, match="active guard.publication"):
            guard.record_result({"target_id": "fictional-target"})
        with guard.publication():
            for invalid in ({"path": "fictional"}, {"target_id": "../private"}, {"target_id": r"C:\private"},
                            {"route_id": ""}, {"route_id": 12}):
                with pytest.raises(ValueError, match="bounded IDs"):
                    guard.record_result(invalid)
            refs = {"target_id": "fictional-target", "route_id": "fictional-route"}
            guard.record_result(refs)
            guard.record_result(dict(reversed(list(refs.items()))))
            with pytest.raises(ValueError, match="conflict"):
                guard.record_result({**refs, "route_id": "other-route"})
        assert store.published_result(conn, job_id) == refs
        assert conn.execute("SELECT COUNT(*) FROM career_workbench_publications").fetchone()[0] == 1


def test_failure_after_committed_marker_still_reports_published_result(workspace):
    def execute(conn, operation, payload, guard):
        with guard.publication():
            conn.execute("INSERT INTO fictional_results VALUES('published')")
            guard.record_result({"diagnosis_id": "published"})
        raise RuntimeError("callback interrupted after committed publication")

    worker = CareerWorker(workspace, execute).start()
    try:
        job = worker.submit("diagnose", {}, "post-publication-interruption")
        assert settled(worker, job["job_id"])["result_refs"] == {"diagnosis_id": "published"}
    finally:
        worker.close()


def test_readers_use_only_select_and_do_not_bootstrap(tmp_path, workspace, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("reader attempted journal bootstrap/load")

    monkeypatch.setattr(journal, "load", forbidden)
    monkeypatch.setattr(journal, "ensure_schema", forbidden)
    absent = CareerWorker(tmp_path / "absent", lambda *args: {})
    assert absent.get("none") is None and absent.list_jobs() == []
    assert not absent.workspace.exists()
    statements = []
    original = sqlite3.connect

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    worker = CareerWorker(workspace, lambda *args: {})
    assert worker.get("none") is None and worker.list_jobs() == []
    assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    original(legacy / "applypilot.db").close()
    assert CareerWorker(legacy, lambda *args: {}).list_jobs() == []
    with original(legacy / "applypilot.db") as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_future_schema_rejected_without_writes(workspace):
    path = workspace / "applypilot.db"
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE applypilot_schema_version SET version=?", (DATABASE_SCHEMA_VERSION + 1,))
        conn.commit()
    worker = CareerWorker(workspace, lambda *args: {})
    for action in (worker.start, worker.list_jobs, lambda: worker.get("none")):
        with pytest.raises(RuntimeError, match="newer than supported"):
            action()
    assert not (workspace / ".career-worker.lock").exists()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM career_workbench_jobs").fetchone()[0] == 0


def test_input_lease_checked_before_publication_and_own_changes_allowed(workspace):
    with sqlite3.connect(workspace / "applypilot.db") as conn:
        conn.execute("INSERT INTO career_targets VALUES('fictional-target',1,'old','{}','now','now')")
        conn.commit()

    def execute(conn, operation, payload, guard):
        with guard.publication():
            conn.execute("UPDATE career_targets SET revision=revision+1,input_fingerprint='new'")
        return {"target_id": "fictional-target"}

    worker = CareerWorker(workspace, execute).start()
    payload = {"_input_lease": {"target_id": "fictional-target", "target_revision": 1,
                                "input_fingerprint": "old"}}
    try:
        job = worker.submit("supplement", payload, "fresh")
        settled(worker, job["job_id"])
        stale = worker.submit("supplement", payload, "stale")
        assert settled(worker, stale["job_id"], "failed")["error"] == "input_changed"
        with sqlite3.connect(workspace / "applypilot.db") as conn:
            assert conn.execute("SELECT revision FROM career_targets").fetchone()[0] == 2
    finally:
        worker.close()


def test_no_remote_model_or_ats_authority_and_one_owner(workspace):
    worker = CareerWorker(workspace, lambda *args: {}).start()
    other = CareerWorker(workspace, lambda *args: {})
    try:
        with pytest.raises(RuntimeError, match="already owns"):
            other.start()
        for operation, payload in (("submit", {}), ("diagnose", {"use_model": True}),
                                   ("diagnose", {"retry_unknown": True})):
            with pytest.raises(ValueError):
                worker.submit(operation, payload, "denied")
        assert worker.list_jobs() == []
    finally:
        worker.close()
    replacement = CareerWorker(workspace, lambda *args: {}).start()
    replacement.close()
