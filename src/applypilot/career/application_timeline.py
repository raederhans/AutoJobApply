"""Read-only, source-timed progress for one existing application exception."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

HISTORY_LIMIT = 200


def _instant(value):
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(UTC) if result.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def application_timeline(conn, snapshot, tables):
    """Project timestamps; do not read response artifacts or initialize tables."""
    events = []
    item, request, response = (snapshot[name] for name in ("exception", "request", "response"))

    def add(key, kind, label, description, occurred_at, *, basis="recorded"):
        when = _instant(occurred_at)
        events.append({"id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:24],
                       "kind": kind, "label": label, "description": description,
                       "occurred_at": when.isoformat() if when is not None else None,
                       "time_basis": basis if when is not None else "unknown"})

    add("paused:" + item["exception_id"], "paused", "申请暂停",
        "原申请流程留下了这条待处理记录。", item["created_at"])
    if request:
        add("human:" + request["request_id"], "human_requested", "提出人工问题",
            "原流程请求人工答复；问题内容见当前条目。", request["created_at"])
    if response:
        add("answer:" + response["request_id"], "answer_saved", "人工答复已保存",
            "答复引用与摘要已持久记录；保存答复本身不会执行申请。", response["resolved_at"])

    rows = []
    if {"operator_command_envelopes", "operator_command_results"} <= tables:
        rows = conn.execute(
            "SELECT r.result_id,c.action,r.stage,r.outcome,r.terminal_status,r.occurred_at "
            "FROM operator_command_results r JOIN operator_command_envelopes c ON c.command_id=r.command_id "
            "WHERE c.exception_id=? ORDER BY julianday(r.occurred_at) DESC,r.result_id DESC LIMIT ?",
            (item["exception_id"], HISTORY_LIMIT + 1),
        ).fetchall()
    for result_id, action, stage, outcome, terminal, when in rows[:HISTORY_LIMIT]:
        name = {"resume": "接续", "reconcile": "回执核对", "resolve": "人工处理"}.get(action, "处理")
        if stage == "requested":
            label, description = f"已请求{name}", "请求已持久登记；这不代表原流程已执行或完成。"
        elif stage == "started":
            label, description = f"{name}执行已开始", "已记录执行开始。是否完成取决于后续结果；不能据此重复执行。"
        elif stage == "verified":
            label = f"{name}结果已核验"
            description = "原流程已持久核验本次处理结果；岗位是否已投递仍以申请回执为准。"
        elif stage == "failed" and outcome == "resume_request_expired":
            label, description = "接续请求已登记过期", "原流程已记录过期处理；需要重新核对当前申请与页面。"
        elif stage == "failed":
            label, description = f"{name}未完成", "原流程记录了失败或阻断结果。请核对当前页面和已有结果，不自动重试。"
        else:
            label, description = f"{name}记录已更新", "存在一条原流程持久记录；请在原流程核对其含义。"
        add("operator:" + result_id, "operator_" + stage, label, description, when)

    if request and request["status"] in {"consumed", "resolved", "expired"}:
        label = {"consumed": "人工答复已由原流程接收", "resolved": "人工请求已关闭", "expired": "人工请求已登记过期"}[request["status"]]
        add("human-terminal:" + request["request_id"], "human_" + request["status"], label,
            "此状态来自原人工请求记录，不代表岗位已投递。", request["resolved_at"])
    if item["status"] != "open":
        label = "暂停项已登记过期" if item["status"] == "expired" else "暂停项已关闭"
        add("queue-terminal:" + item["exception_id"], "queue_" + item["status"], label,
            "此状态来自待处理队列的持久记录。", item["resolved_at"])
    else:
        # A deadline is not evidence that a dispatcher observed or handled expiry.
        # Stop presenting current lease deadlines as progress once the item closed.
        now = datetime.now(UTC)
        for key, column, label in (("attempt", "lease_expires_at", "申请有效期已过"),
                                   ("lease", "expires_at", "浏览器租约有效期已过")):
            record = snapshot[key]
            deadline = _instant(record.get(column)) if record else None
            if deadline is not None and deadline <= now:
                add("deadline:" + key + ":" + item["exception_id"], "lease_deadline", label,
                    "这是记录中的有效期截止时间，不是实际执行过期处理的时间；请由原流程重新核对。",
                    deadline.isoformat(), basis="deadline")
    events.sort(key=lambda event: (event["occurred_at"] is None, event["occurred_at"] or ""))
    return {"timeline": events, "timeline_truncated": len(rows) > HISTORY_LIMIT}
