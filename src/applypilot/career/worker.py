"""One bounded career worker, with durable journal identity and fenced publication.

Callbacks compute outside a transaction and publish with ``guard.publication()``.
That context serializes cancellation, checks the input lease at entry, and owns
the write transaction. Publication is the cancellation linearization point.
The callback must not commit/rollback, or write results outside that context.
This worker has no browser, ATS, submission, or remote-model authority.
"""

from __future__ import annotations

import json
import os
import queue
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from applypilot.apply.contracts import TaskResult, TaskSpec, ensure_persistable
from applypilot.storage import career_workbench as store
from applypilot.storage import task_journal as journal
from applypilot.storage.transactions import write_transaction

OPERATIONS = frozenset({"diagnose", "route", "rediagnose", "supplement"})


class PublicationStopped(RuntimeError):
    pass


class PublicationGuard:
    def __init__(self, worker: CareerWorker, conn: sqlite3.Connection,
                 token: journal.LeaseToken, input_lease: dict):
        self.worker, self.conn, self.token = worker, conn, token
        self.input_lease = input_lease
        self.published = False
        self._publication_depth = 0

    def _check(self, *, inputs: bool = True, after_publication: bool = False) -> None:
        if self.worker._closing.is_set() and not after_publication:
            raise PublicationStopped("interrupted")
        row = self.conn.execute("SELECT status,owner_id,lease_epoch,cancel_requested "
                                "FROM agent_tasks WHERE task_id=?", (self.token.task_id,)).fetchone()
        if row is None or tuple(row[:3]) != ("running", self.token.owner_id, self.token.lease_epoch):
            raise PublicationStopped("owner_lost")
        if row[3]:
            raise PublicationStopped("cancelled")
        if inputs:
            store.check_input_lease(self.conn, self.input_lease)

    def __call__(self) -> None:
        self._check()

    def record_result(self, result_refs: dict) -> None:
        """Commit durable references alongside the business writes, never later."""
        if not self._publication_depth or not self.conn.in_transaction:
            raise RuntimeError("record_result requires an active guard.publication transaction")
        self._check(inputs=False)
        store.record_publication(self.conn, self.token.task_id, result_refs)

    @contextmanager
    def publication(self) -> Iterator[None]:
        # Enter before a callback's own write_transaction. Holding this lock
        # through commit prevents a successful cancel followed by a late write.
        with self.worker._lock:
            with write_transaction(self.conn, prefix="career_publication"):
                self._check()
                self._publication_depth += 1
                try:
                    yield
                    self._check(inputs=False)  # the operation may change its own inputs
                finally:
                    self._publication_depth -= 1
            self.published = True


class CareerWorker:
    def __init__(self, workspace: Path,
                 execute: Callable[[sqlite3.Connection, str, dict, PublicationGuard], dict],
                 *, max_pending: int = 8, operations=OPERATIONS):
        if isinstance(max_pending, bool) or not isinstance(max_pending, int) or max_pending < 1:
            raise ValueError("max_pending must be a positive integer")
        self.workspace = Path(workspace).resolve()
        self.db_path = self.workspace / "applypilot.db"
        self.execute = execute
        self.operations = frozenset(operations)
        if not self.operations <= OPERATIONS:
            raise ValueError("unsupported career operation")
        self._queue: queue.Queue[str] = queue.Queue(maxsize=max_pending)
        self._lock = threading.RLock()
        self._closing = threading.Event()
        self._thread: threading.Thread | None = None
        self._owner = "career-worker-" + uuid.uuid4().hex
        self._active: PublicationGuard | None = None
        self._lock_fd: int | None = None

    def _connection(self, *, readonly: bool = False) -> sqlite3.Connection:
        mode = "ro" if readonly else "rw"
        conn = sqlite3.connect(f"{self.db_path.as_uri()}?mode={mode}", uri=True, timeout=10)
        try:
            store.supported(conn, writing=not readonly)
        except BaseException:
            conn.close()
            raise
        return conn

    @contextmanager
    def _db(self, *, readonly: bool = False):
        conn = self._connection(readonly=readonly)
        try:
            yield conn
        finally:
            conn.close()

    def _acquire_owner(self) -> None:
        fd = os.open(self.workspace / ".career-worker.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise RuntimeError("career worker already owns this workspace") from exc
        self._lock_fd = fd

    def _release_owner(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def start(self) -> CareerWorker:
        with self._lock:
            if self._closing.is_set():
                raise RuntimeError("create a new worker after close")
            if self._thread is not None:
                if self._thread.is_alive() and not self._closing.is_set():
                    return self
                raise RuntimeError("create a new worker after close")
            # Validate future schema before creating even an ownership file.
            with self._db() as conn:
                self._acquire_owner()
                try:
                    with write_transaction(conn):
                        journal.ensure_schema(conn)
                        store.recover_interrupted(conn)
                except BaseException:
                    self._release_owner()
                    raise
            self._thread = threading.Thread(target=self._run, name=self._owner, daemon=True)
            self._thread.start()
        return self

    @contextmanager
    def mutation(self) -> Iterator[None]:
        """Serialize another local career writer with cancellation/publication.

        The caller still owns its SQLite transaction and revision checks.
        """
        with self._lock:
            yield

    def close(self) -> None:
        with self._lock:
            self._closing.set()
        if self._thread is not None:
            # A callback cannot be forcibly killed. Its guard is fenced now,
            # and its ownership lock stays held until its thread actually exits.
            self._thread.join(timeout=5)

    def get(self, job_id: str) -> dict | None:
        if not self.db_path.exists():
            return None
        with self._db(readonly=True) as conn:
            return store.get(conn, job_id)

    def list_jobs(self, *, limit: int = 50) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if not self.db_path.exists():
            return []
        with self._db(readonly=True) as conn:
            return store.list_jobs(conn, limit=limit)

    def submit(self, operation: str, payload: dict, request_key: str) -> dict:
        return self._submit(operation, payload, request_key)

    def _submit(self, operation: str, payload: dict, request_key: str,
                *, parent_job_id: str | None = None) -> dict:
        if operation not in self.operations:
            raise ValueError("unsupported career operation")
        if not isinstance(payload, dict):
            raise TypeError("career payload must be an object")
        if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 200:
            raise ValueError("request_key must be a bounded non-empty string")
        payload = ensure_persistable(payload)
        if payload.get("use_model") or payload.get("retry_unknown"):
            raise ValueError("career worker supports local rules only")
        lease = payload.get("_input_lease", {})
        if not isinstance(lease, dict):
            raise TypeError("_input_lease must be an object")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 16 * 1024:
            raise ValueError("career payload exceeds the bounded size limit")
        with self._lock:
            if self._thread is None or not self._thread.is_alive() or self._closing.is_set():
                raise RuntimeError("career worker is not running")
            with self._db() as conn:
                with write_transaction(conn):
                    old = store.by_request(conn, request_key)
                    if old is not None:
                        if (old["operation"], old["payload"], old["parent_job_id"]) != (
                                operation, payload, parent_job_id):
                            raise ValueError("request_key was reused with different inputs")
                        return old
                    if self._queue.full():
                        raise RuntimeError("career queue is full")
                    job_id = "career-job-" + uuid.uuid4().hex
                    spec = TaskSpec(task_id=job_id, kind="career." + operation,
                                    objective="Local career " + operation,
                                    inputs={"operation": operation, "payload": payload},
                                    effect_class="career_local_write", authority_scope=("career",),
                                    idempotency_key=job_id, resume_cursor={"parent_job_id": parent_job_id})
                    journal.register(conn, spec, workflow_id=parent_job_id)
                    conn.execute("INSERT INTO career_workbench_jobs(job_id,request_key,parent_job_id,"
                                 "operation,payload_json,input_lease_json,created_at) VALUES(?,?,?,?,?,?,?)",
                                 (job_id, request_key, parent_job_id, operation, encoded,
                                  json.dumps(lease), datetime.now(UTC).isoformat()))
                    value = store.get(conn, job_id)
                self._queue.put_nowait(job_id)
                return value

    def cancel(self, job_id: str) -> dict:
        with self._lock, self._db() as conn:
            current = store.get(conn, job_id)
            if current is None:
                raise KeyError("career job not found")
            if self._active is not None and self._active.token.task_id == job_id and self._active.published:
                return {**current, "cancellation_outcome": "publication_committed"}
            journal.request_cancel(conn, job_id)
            return store.get(conn, job_id)

    def resume(self, job_id: str, request_key: str) -> dict:
        old = self.get(job_id)
        if old is None:
            raise KeyError("career job not found")
        if old["status"] not in {"interrupted", "failed", "cancelled", "blocked"}:
            raise ValueError("only stopped career jobs can be resumed")
        return self._submit(old["operation"], old["payload"], request_key, parent_job_id=job_id)

    def _run_one(self, conn: sqlite3.Connection, job_id: str) -> None:
        with self._lock:
            entry = journal.claim(conn, job_id, self._owner, lease_seconds=86400)
            if entry is None:
                return
            job = store.get(conn, job_id)
            guard = PublicationGuard(self, conn, entry.lease_token, job["input_lease"])
            self._active = guard
        try:
            guard()
            output = self.execute(conn, job["operation"], job["payload"], guard)
            if conn.in_transaction:
                conn.rollback()
                raise RuntimeError("career callback left an uncommitted transaction")
            if not isinstance(output, dict):
                raise TypeError("career callback must return an object")
            # Persist references only; source material/result bodies stay in
            # their career tables and are read through the normal projections.
            refs = {k: v for k, v in output.items()
                    if k.endswith("_id") and isinstance(v, str)}
            with self._lock, write_transaction(conn):
                guard._check(inputs=not guard.published, after_publication=guard.published)
                recorded_refs = store.published_result(conn, job_id)
                if recorded_refs is not None:
                    refs = recorded_refs
                journal.complete(conn, job_id, guard.token,
                                 TaskResult(task_id=job_id, status="completed", output={"result_refs": refs}),
                                 result_ref=next(iter(refs.values()), None))
        except Exception as exc:  # noqa: BLE001 - isolate callback failure in its durable job
            if conn.in_transaction:
                conn.rollback()
            with self._lock, write_transaction(conn):
                row = conn.execute("SELECT status,owner_id,lease_epoch,cancel_requested FROM agent_tasks "
                                   "WHERE task_id=?", (job_id,)).fetchone()
                if tuple(row[:3]) == ("running", self._owner, guard.token.lease_epoch):
                    recorded_refs = store.published_result(conn, job_id)
                    if recorded_refs is not None:
                        journal.complete(conn, job_id, guard.token, TaskResult(
                            task_id=job_id, status="completed", output={"result_refs": recorded_refs}),
                            result_ref=next(iter(recorded_refs.values()), None))
                    elif row[3]:
                        journal.acknowledge_cancel(conn, guard.token)
                    else:
                        category = str(exc) if isinstance(exc, PublicationStopped) else type(exc).__name__
                        if isinstance(exc, ValueError) and str(exc).startswith("input_changed"):
                            category = "input_changed"
                        journal.fail(conn, job_id, guard.token, TaskResult(
                            task_id=job_id, status="blocked" if category == "interrupted" else "failed",
                            failure_category=category))
        finally:
            with self._lock:
                self._active = None

    def _run(self) -> None:
        try:
            with self._db() as conn:
                while not self._closing.is_set():
                    try:
                        job_id = self._queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    try:
                        if not self._closing.is_set():
                            self._run_one(conn, job_id)
                    finally:
                        self._queue.task_done()
                with self._lock, write_transaction(conn):
                    store.recover_interrupted(conn)
        finally:
            self._release_owner()
