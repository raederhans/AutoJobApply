"""Local application inbox over the existing durable operator boundary."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from applypilot.apply.human_handoff import HumanResponseRef, append_human_response
from applypilot.apply.operator_commands import OperatorCommand, OperatorCommandError
from applypilot.apply.operator_runtime import OperatorRuntime
from applypilot.career.application_timeline import application_timeline
from applypilot.career.workbench import previous_request, save_request
from applypilot.storage.transactions import write_transaction

LIMIT = 100
_KINDS = {
    "human_only": ("需要人工答复", "核对原流程中的问题，再保存答复并申请继续。"),
    "receipt_reconciliation": ("需要核对回执", "请在原申请流程核对提交回执，再决定下一步。"),
    "capability": ("需要恢复连接", "请在原申请流程恢复浏览器连接并重新观察页面。"),
    "recovery_execution": ("需要核对恢复结果", "请在原申请流程核对恢复结果与当前页面。"),
    "parked": ("申请已暂停", "请回到原申请流程查看暂停原因与下一步。"),
}
_WAITING = "已请求继续，等待申请进程接续。"


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _row(conn, sql, args=()):
    cursor = conn.execute(sql, args)
    row = cursor.fetchone()
    return None if row is None else dict(zip((column[0] for column in cursor.description), row))


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _object(value):
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def _current(value):
    try:
        when = datetime.fromisoformat(value)
        return when.tzinfo is not None and when > datetime.now(UTC)
    except (ValueError, TypeError):
        return False


def _blocked_next_step(snapshot):
    attempt, lease, checkpoint = (snapshot[name] for name in ("attempt", "lease", "checkpoint"))
    context = _object(snapshot["exception"]["context_json"])
    if attempt and attempt["submit_started"]:
        return "需要核对提交回执", "原申请已有提交开始记录，请先在原流程核对回执与当前页面，避免重复提交。"
    if attempt and attempt["status"] != "in_progress":
        return "申请状态已变化", "请在原申请流程查看最新结果，再决定是否需要新的处理步骤。"
    if attempt and not _current(attempt["lease_expires_at"]):
        return "申请会话已过期", "请回到原申请流程确认当前状态后再继续；此处不能延长旧会话。"
    if lease and (lease["status"] != "active" or not _current(lease["expires_at"])):
        return "浏览器会话需重新核对", "请在原申请流程重新确认浏览器中的岗位、页面和已填写内容。"
    if checkpoint and checkpoint["checkpoint_id"] != context.get("checkpoint_id"):
        return "申请进度已更新", "请查看原流程中的最新进度与人工请求；这条旧记录不能直接继续。"
    return "需要原流程重新核对", "申请与页面的关联已变化或缺少记录，请在原流程核对当前岗位和页面后接续。"


@contextmanager
def _read_snapshot(conn):
    owned = not conn.in_transaction
    if owned:
        conn.execute("BEGIN")
    try:
        yield
    finally:
        if owned:
            conn.rollback()


class ApplicationInbox:
    def __init__(self, workspace):
        self.root = Path(workspace).resolve() / "career" / "workbench" / "human-responses"

    def _snapshot(self, conn, exception_id, tables):
        item = _row(conn, "SELECT * FROM agent_exception_queue WHERE exception_id=?", (exception_id,))
        if item is None:
            raise KeyError("application_inbox_not_found")
        context = _object(item["context_json"])
        request_id = context.get("request_id")
        request = (_row(conn, "SELECT * FROM agent_human_requests WHERE request_id=?", (request_id,))
                   if isinstance(request_id, str) and "agent_human_requests" in tables else None)
        response = (_row(conn, "SELECT * FROM agent_human_responses WHERE request_id=?", (request_id,))
                    if request and "agent_human_responses" in tables else None)
        attempt = (_row(conn, "SELECT * FROM application_attempts WHERE attempt_id=?", (item["attempt_id"],))
                   if "application_attempts" in tables else None)
        job = (_row(conn, "SELECT url,title,company_name FROM jobs WHERE url=?", (attempt["job_url"],))
               if attempt and "jobs" in tables else None)
        lease = (_row(conn, "SELECT * FROM browser_resource_leases WHERE lease_id=?", (context.get("browser_lease_id"),))
                 if isinstance(context.get("browser_lease_id"), str) and "browser_resource_leases" in tables else None)
        parent = (_row(conn, "SELECT * FROM agent_runtime_turns WHERE turn_id=?", (item["run_id"],))
                  if "agent_runtime_turns" in tables else None)
        checkpoint = (_row(conn, "SELECT * FROM agent_checkpoints WHERE actor_id=? AND schema_version='2' "
                           "ORDER BY sequence DESC LIMIT 1", (item["actor_id"],))
                      if "agent_checkpoints" in tables else None)
        command = (_row(conn, "SELECT * FROM operator_command_envelopes WHERE exception_id=? AND action='resume' "
                       "ORDER BY created_at DESC,command_id DESC LIMIT 1", (exception_id,))
                   if "operator_command_envelopes" in tables else None)
        stages = ([tuple(row) for row in conn.execute(
            "SELECT stage,outcome,terminal_status,occurred_at FROM operator_command_results WHERE command_id=? ORDER BY stage",
            (command["command_id"],))] if command and "operator_command_results" in tables else [])
        return {"exception": item, "request": request, "response": response, "attempt": attempt,
                "job": job, "lease": lease, "parent": parent, "checkpoint": checkpoint,
                "command": command, "stages": stages}

    @staticmethod
    def _view(snapshot):
        item, request, response = (snapshot[name] for name in ("exception", "request", "response"))
        attempt, lease, parent, checkpoint = (snapshot[name] for name in ("attempt", "lease", "parent", "checkpoint"))
        context = _object(item["context_json"])
        request_context = _object(request["context_json"]) if request else {}
        kind_label, description = _KINDS.get(item["queue_kind"], ("需要处理", "请回到原申请流程查看详情。"))
        status, label, respond, resume = "blocked", kind_label, False, False
        stages = {row[0] for row in snapshot["stages"]}
        if item["queue_kind"] == "human_only":
            bound = bool(request and request["status"] == "open"
                         and (request["run_id"], request["attempt_id"], request_context.get("actor_id"), request_context.get("turn_id"))
                         == (item["run_id"], item["attempt_id"], item["actor_id"], item["turn_id"]))
            active = bool(attempt and attempt["status"] == "in_progress" and attempt["submit_started"] == 0
                          and attempt["job_url"] == context.get("job_url") and _current(attempt["lease_expires_at"])
                          and lease and lease["status"] == "active" and _current(lease["expires_at"])
                          and (lease["actor_id"], lease["attempt_id"], lease["profile_id"], lease["lease_epoch"], lease["page_target_id"], lease["page_epoch"])
                          == (item["actor_id"], item["attempt_id"], context.get("profile_id"), context.get("browser_lease_epoch"), context.get("page_target_id"), context.get("page_epoch"))
                          and parent and parent["status"] in {"blocked", "cancelled", "closed", "completed", "failed", "timed_out"}
                          and parent["submit_started"] == 0
                          and (parent["actor_id"], parent["attempt_id"], parent["profile_id"], parent["runtime_id"])
                          == (item["actor_id"], item["attempt_id"], lease["profile_id"], lease["runtime_id"])
                          and checkpoint and (checkpoint["checkpoint_id"], checkpoint["attempt_id"], checkpoint["turn_id"])
                          == (context.get("checkpoint_id"), item["attempt_id"], item["run_id"]))
            if item["status"] != "open" or not bound:
                label, description = "记录已变化", "请回到原申请流程核对这条人工请求。"
            elif "failed" in stages:
                status, label, description = "failed", "接续未完成", "原流程记录了失败或阻断结果，请核对当前页面和已有结果；此处不会自动重试。"
                if any(row[1] == "resume_request_expired" for row in snapshot["stages"]):
                    status, label, description = "expired", "接续请求已过期", "原流程已记录过期处理，请回到原申请重新核对。"
            elif not active:
                label, description = _blocked_next_step(snapshot)
            elif snapshot["command"]:
                if stages == {"requested"}:
                    status, label, description = "resume_requested", "已请求继续", _WAITING
                elif "started" in stages and "verified" not in stages:
                    label, description = "接续结果待核对", "已有执行开始记录，尚无持久完成结果；请在原流程核对，不能据此重复执行。"
                else:
                    label, description = "接续结果已记录", "请在原申请流程查看持久结果与当前状态；此处不会重复执行。"
            elif response and response["response_type"] != request["request_type"]:
                label, description = "答复关联待核对", "已保存的答复与当前问题类型不一致，请在原流程核对。"
            elif response:
                status, label, resume = "ready_to_resume", "答复已保存", True
                description = "点击申请继续后，由原申请进程重新核对状态并接续。"
            else:
                status, label, respond = "awaiting_response", "等待人工答复", True
        if item["status"] != "open":
            respond = resume = False
            status = "expired" if item["status"] == "expired" else "resolved"
            label = "暂停项已过期" if status == "expired" else "暂停项已关闭"
            description = ("请在原申请流程重新核对当前页面后决定下一步。" if status == "expired"
                           else "这条暂停记录已关闭；岗位是否已投递请以申请回执为准。")
        elif request and request["status"] == "expired":
            respond = resume = False
            status, label, description = "expired", "人工请求已过期", "请在原申请流程重新核对问题与当前页面。"
        job = snapshot["job"] or {}
        return {"id": item["exception_id"], "job": {"title": job.get("title") or "未记录岗位名称",
                "company": job.get("company_name") or "未记录公司", "url": job.get("url")},
                "kind": item["queue_kind"], "kind_label": kind_label, "status": status, "status_label": label,
                "queue_status": item["status"],
                "prompt": request["prompt"] if request else "请回到原申请流程查看具体问题。",
                "created_at": item["created_at"], "request_id": request["request_id"] if request else None,
                "response_recorded": response is not None, "revision": _digest(snapshot),
                "next_action": {"label": "保存答复" if respond else "申请继续" if resume else label, "description": description},
                "can_respond": respond, "can_resume": resume}

    def list_items(self, conn, *, q="", scope="open", sort="newest", offset=0, job_url=None):
        if not isinstance(q, str) or len(q) > 200 or scope not in {"open", "history", "all"} or sort not in {"newest", "oldest"}:
            raise ValueError("invalid_inbox_filter")
        if type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise ValueError("invalid_inbox_offset")
        if job_url is not None and (not isinstance(job_url, str) or not job_url):
            raise ValueError("invalid_inbox_filter")
        with _read_snapshot(conn):
            return self._list_items(conn, q=q.strip(), scope=scope, sort=sort, offset=offset, job_url=job_url)

    def _list_items(self, conn, *, q, scope, sort, offset, job_url):
        tables = _tables(conn)
        parameters = {"query": q, "scope": scope, "sort": sort}
        if "agent_exception_queue" not in tables or (job_url is not None and "application_attempts" not in tables):
            return {"available": False, "items": [], "counts": {"open": None, "needs_response": None,
                    "resume_requested": None, "blocked": None, "shown": None}, "limit": LIMIT, "truncated": False,
                    **parameters, "pagination": {"offset": 0, "total": None, "has_more": False, "next_offset": None, "previous_offset": None}}
        clauses, args = [], []
        if scope != "all":
            clauses.append("e.status='open'" if scope == "open" else "e.status<>'open'")
        joins = ""
        search_fields = ["e.exception_id", "e.queue_kind"]
        if "application_attempts" in tables:
            joins += " LEFT JOIN application_attempts a ON a.attempt_id=e.attempt_id"
            if "jobs" in tables:
                joins += " LEFT JOIN jobs j ON j.url=a.job_url"
                search_fields += ["j.title", "j.company_name", "j.url"]
        if job_url is not None:
            clauses.append("a.job_url=?")
            args.append(job_url)
        if "agent_human_requests" in tables:
            # JSON extraction applies only to valid object contexts; it never trusts a caller path.
            joins += (" LEFT JOIN agent_human_requests h ON h.request_id="
                      "CASE WHEN json_valid(e.context_json) THEN json_extract(e.context_json,'$.request_id') END")
            search_fields += ["h.prompt"]
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            clauses.append("(" + " OR ".join(f"{field} LIKE ? ESCAPE '\\'" for field in search_fields) + ")")
            args.extend([pattern] * len(search_fields))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        source = " FROM agent_exception_queue e" + joins + where
        total = conn.execute("SELECT COUNT(*)" + source, args).fetchone()[0]
        # A previous page may vanish when the owner closes an item. Return the last valid page.
        offset = min(offset, ((total - 1) // LIMIT) * LIMIT) if total else 0
        direction = "DESC" if sort == "newest" else "ASC"
        rows = conn.execute("SELECT e.exception_id" + source +
                            f" ORDER BY julianday(e.created_at) {direction},e.exception_id {direction} LIMIT ? OFFSET ?",
                            [*args, LIMIT, offset]).fetchall()
        items = [self._view(self._snapshot(conn, row[0], tables)) for row in rows]
        has_more = offset + len(items) < total
        return {"available": True, "items": items, "counts": {"shown": len(items),
                "open": sum(item["queue_status"] == "open" for item in items),
                "needs_response": sum(item["can_respond"] for item in items),
                "resume_requested": sum(item["status"] == "resume_requested" for item in items),
                "blocked": sum(item["status"] in {"blocked", "expired", "failed"} for item in items)},
                **parameters, "limit": LIMIT, "truncated": total > len(items),
                "pagination": {"offset": offset, "total": total, "has_more": has_more,
                               "next_offset": offset + LIMIT if has_more else None,
                               "previous_offset": max(0, offset - LIMIT) if offset else None}}

    def detail(self, conn, exception_id):
        with _read_snapshot(conn):
            tables = _tables(conn)
            if "agent_exception_queue" not in tables:
                raise KeyError("application_inbox_not_found")
            snapshot = self._snapshot(conn, exception_id, tables)
            return {"item": self._view(snapshot), **application_timeline(conn, snapshot, tables)}

    def mutate(self, conn, exception_id, action, data):
        required = {"request_key", "revision"} | ({"answer"} if action == "respond" else set())
        if action not in {"respond", "resume"} or set(data) != required:
            raise ValueError("invalid_inbox_input")
        if not isinstance(data["revision"], str) or len(data["revision"]) != 64:
            raise ValueError("invalid_revision")
        if action == "respond" and (not isinstance(data["answer"], str) or not data["answer"].strip()
                                    or len(data["answer"]) > 4000):
            raise ValueError("invalid_answer")
        operation = f"application-inbox/{exception_id}/{action}"
        # Retain an idempotency digest, not a second copy of the private answer.
        inputs = {**data, "answer": _digest(data["answer"])} if action == "respond" else data
        old = previous_request(conn, data["request_key"], operation, inputs)
        if old is not None:
            return old
        if action == "respond":
            with write_transaction(conn):
                old = previous_request(conn, data["request_key"], operation, inputs)
                if old is not None:
                    return old
                snapshot, _view = self._check(conn, exception_id, data, "can_respond")
                request = snapshot["request"]
                raw = _encoded({"schema_version": "1", "request_id": request["request_id"],
                                "answer": data["answer"].strip()})
                digest = hashlib.sha256(raw).hexdigest()
                self.root.mkdir(parents=True, exist_ok=True)
                path = self.root / f"{digest}.json"
                try:
                    with path.open("xb") as stream:
                        stream.write(raw)
                except FileExistsError:
                    if path.read_bytes() != raw:
                        raise ValueError("response_artifact_conflict") from None
                append_human_response(conn, HumanResponseRef(
                    request_id=request["request_id"], response_ref=str(path), response_digest=digest,
                    response_type=request["request_type"], resolved_by="career-workbench", resolved_at=datetime.now(UTC)))
                result = {"item": self._view(self._snapshot(conn, exception_id, _tables(conn))),
                          "message": "答复已保存。请核对后点击申请继续。"}
                save_request(conn, data["request_key"], operation, inputs, result)
                return result
        snapshot = self._snapshot(conn, exception_id, _tables(conn))
        view = self._view(snapshot)
        command_id = "workbench-resume:" + _digest([exception_id, view["request_id"]])
        # Recover the queue/cache crash window without requesting another command.
        if not (view["status"] == "resume_requested" and snapshot["command"]["command_id"] == command_id):
            snapshot, view = self._check(conn, exception_id, data, "can_resume")
            item = snapshot["exception"]
            command = OperatorCommand(command_id=command_id, exception_id=exception_id, action="resume",
                run_id=item["run_id"], attempt_id=item["attempt_id"], actor_id=item["actor_id"], turn_id=item["turn_id"],
                input_ref="human-response:" + hashlib.sha256(view["request_id"].encode("utf-8")).hexdigest(),
                input_sha256=snapshot["response"]["response_digest"])
            try:
                # No executor is injected: only the original runtime can resume.
                OperatorRuntime(conn).resume(command, request_id=view["request_id"])
            except OperatorCommandError as exc:
                raise ValueError("inbox_state_changed") from exc
        result = {"item": self._view(self._snapshot(conn, exception_id, _tables(conn))), "message": _WAITING}
        with write_transaction(conn):
            old = previous_request(conn, data["request_key"], operation, inputs)
            if old is not None:
                return old
            save_request(conn, data["request_key"], operation, inputs, result)
        return result

    def _check(self, conn, exception_id, data, flag):
        snapshot = self._snapshot(conn, exception_id, _tables(conn))
        view = self._view(snapshot)
        if view["revision"] != data["revision"] or not view[flag]:
            raise ValueError("inbox_revision_changed")
        return snapshot, view
