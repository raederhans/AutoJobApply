"""Local application inbox over the existing durable operator boundary."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from applypilot.apply.human_handoff import HumanResponseRef, append_human_response
from applypilot.apply.operator_commands import OperatorCommand, OperatorCommandError
from applypilot.apply.operator_runtime import OperatorRuntime
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
            "SELECT stage,outcome,terminal_status FROM operator_command_results WHERE command_id=? ORDER BY stage",
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
            elif not active:
                label, description = "需要原流程重新核对", "申请或浏览器会话已过期或变化，请在原流程核对当前页面后接续。"
            elif snapshot["command"]:
                stages = {row[0] for row in snapshot["stages"]}
                if stages == {"requested"}:
                    status, label, description = "resume_requested", "已请求继续", _WAITING
                else:
                    label, description = "正在接续或需要核对", "请在原申请流程查看接续结果；当前记录不会重复执行。"
            elif response:
                status, label, resume = "ready_to_resume", "答复已保存", True
                description = "点击申请继续后，由原申请进程重新核对状态并接续。"
            else:
                status, label, respond = "awaiting_response", "等待人工答复", True
        job = snapshot["job"] or {}
        return {"id": item["exception_id"], "job": {"title": job.get("title") or "未记录岗位名称",
                "company": job.get("company_name") or "未记录公司", "url": job.get("url")},
                "kind": item["queue_kind"], "kind_label": kind_label, "status": status, "status_label": label,
                "prompt": request["prompt"] if request else "请回到原申请流程查看具体问题。",
                "created_at": item["created_at"], "request_id": request["request_id"] if request else None,
                "response_recorded": response is not None, "revision": _digest(snapshot),
                "next_action": {"label": "保存答复" if respond else "申请继续" if resume else label, "description": description},
                "can_respond": respond, "can_resume": resume}

    def list_items(self, conn):
        tables = _tables(conn)
        if "agent_exception_queue" not in tables:
            return {"available": False, "items": [], "counts": {"open": None, "needs_response": None,
                    "resume_requested": None, "blocked": None}, "limit": LIMIT, "truncated": False}
        rows = conn.execute("SELECT exception_id FROM agent_exception_queue WHERE status='open' "
                            "ORDER BY created_at DESC,exception_id LIMIT ?", (LIMIT + 1,)).fetchall()
        items = [self._view(self._snapshot(conn, row[0], tables)) for row in rows[:LIMIT]]
        return {"available": True, "items": items, "counts": {"open": len(items),
                "needs_response": sum(item["can_respond"] for item in items),
                "resume_requested": sum(item["status"] == "resume_requested" for item in items),
                "blocked": sum(item["status"] == "blocked" for item in items)},
                "limit": LIMIT, "truncated": len(rows) > LIMIT}

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
