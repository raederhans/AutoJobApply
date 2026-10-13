"""Browser preview and downloads over the shared, read-only progress report."""

from __future__ import annotations

import html
import re
from collections import Counter

from applypilot import progress_report

LIMIT = 100
_LABELS = {
    "not_started": "尚未开始", "in_progress": "进行中", "applying": "申请处理中",
    "applied": "已投递（记录）", "already_applied": "已有投递记录", "failed": "执行未完成",
    "previewed": "已预览", "submission_uncertain": "提交结果待核对", "expired": "已过期",
    "skipped": "已跳过", "blocked": "流程受阻", "needs_human": "需要人工处理",
    "submission_unverified": "提交尚未核验", "unknown": "未知", "manual": "人工处理",
    "captcha": "需要页面验证", "duplicate": "重复记录", "contacted": "已有联系记录",
    "assessment": "测评阶段", "offer": "录用意向", "accepted": "已接受", "declined": "已谢绝",
    "rejected": "招聘方拒绝", "withdrawn": "已退出", "conflict": "记录有冲突",
    "interview_unknown": "面试轮次未知",
}


def _report(conn, *, q="", since=None, until=None, date_field="updated_at", statuses=(), record_id=None):
    if not isinstance(q, str) or len(q) > 200 or len(statuses) > 30:
        raise ValueError("progress_filter_invalid")
    if any(not isinstance(value, str) or len(value) > 80 for value in statuses):
        raise ValueError("progress_filter_invalid")
    if record_id is not None and (not isinstance(record_id, str) or not re.fullmatch(r"application-[0-9a-f]{16}", record_id)):
        raise ValueError("progress_filter_invalid")
    owned = not conn.in_transaction
    if owned:
        conn.execute("BEGIN")
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        try:
            report = progress_report.collect_report(conn, since=since, until=until,
                                                    statuses=statuses, date_field=date_field)
        except (ValueError, TypeError) as exc:
            raise ValueError("progress_filter_invalid") from exc
    finally:
        if owned:
            conn.rollback()
    query = q.strip()
    if record_id is not None:
        report["rows"] = [row for row in report["rows"] if row["record_id"] == record_id]
    if query:
        needle = query.casefold()
        report["rows"] = [row for row in report["rows"] if any(
            needle in row[field].casefold() for field in ("title", "company", "location"))]
    report["filters"]["q"] = query
    report["filters"]["record_id"] = record_id
    report["application_status_counts"] = dict(sorted(Counter(row["application_status"] for row in report["rows"]).items()))
    report["recruiting_stage_counts"] = dict(sorted(Counter(row["recruiting_stage"] for row in report["rows"] if row["recruiting_stage"]).items()))
    report["todo_count"] = sum(bool(row["todo_reason"]) for row in report["rows"])
    return report, bool({"jobs", "followup_applications"} & tables)


def preview(conn, *, offset=0, **filters):
    if type(offset) is not int or not 0 <= offset <= 1_000_000:
        raise ValueError("progress_filter_invalid")
    report, available = _report(conn, **filters)
    rows, total = report["rows"], len(report["rows"])
    offset = min(offset, ((total - 1) // LIMIT) * LIMIT) if total else 0
    has_more = offset + LIMIT < total
    codes = progress_report.APPLICATION_STATUSES | progress_report.RECRUITING_STAGES | set(report["recruiting_stage_counts"])
    options = [{"value": code, "label": _LABELS.get(code, "第 " + code.removeprefix("interview_") + " 轮面试")}
               for code in sorted(codes)]
    return {"available": available, "generated_at": report["generated_at"], "timezone": "UTC",
            "filters": report["filters"], "rows": rows[offset:offset + LIMIT], "limit": LIMIT,
            "summary": {"record_count": total if available else None,
                        "todo_count": report["todo_count"] if available else None,
                        # Association totals are not necessarily unique actions; the UI counts records.
                        "open_action_count": sum(row["open_action_count"] for row in rows) if available else None,
                        "open_action_record_count": sum(row["open_action_count"] > 0 for row in rows) if available else None,
                        "application_status_counts": report["application_status_counts"],
                        "recruiting_stage_counts": report["recruiting_stage_counts"]},
            "pagination": {"offset": offset, "total": total if available else None, "has_more": has_more,
                           "next_offset": offset + LIMIT if has_more else None,
                           "previous_offset": max(0, offset - LIMIT) if offset else None},
            "status_options": options}


def download(conn, *, format, **filters):
    if format not in {"csv", "markdown"}:
        raise ValueError("progress_filter_invalid")
    report, available = _report(conn, **filters)
    if not available:
        raise ValueError("progress_report_unavailable")
    if format == "csv":
        return progress_report.render_csv(report).encode("utf-8-sig"), "text/csv; charset=utf-8", "job-progress.csv"
    markdown = progress_report.render_markdown(report)
    if report["filters"]["q"]:
        escaped = re.sub(r"([\\`*_{}\[\]()#+.!|])", r"\\\1", html.escape(report["filters"]["q"], quote=False))
        markdown += "\nLiteral keyword filter: " + escaped + "\n"
    if report["filters"]["record_id"]:
        markdown += "\nSelected local record: " + report["filters"]["record_id"] + "\n"
    return markdown.encode("utf-8"), "text/markdown; charset=utf-8", "job-progress.md"
