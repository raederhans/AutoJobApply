"""Connect selected progress records to the shared public job comparison."""

from __future__ import annotations

import hashlib
import re

from applypilot.career.job_progress import _LABELS
from applypilot.career.progress_links import _source

FIELDS = (
    ("source", "来源"), ("location", "地点"), ("application_status", "申请执行状态"),
    ("availability", "岗位可用性记录"), ("salary", "薪资原文"), ("deadline", "明确截止日期原文"),
    ("employment_type", "雇佣类型"), ("native_level", "岗位级别"), ("observed_at", "来源观察日期原文"),
    ("requirements", "来源要求原句（最多 5 条）"), ("description_excerpt", "岗位描述原文预览（最多 240 字符）"),
)


def _display(key, value, unknown):
    if value in {"", "unknown"}:
        return unknown
    if key == "source" and value == "jobs":
        return "本地岗位记录"
    if key == "application_status":
        return _LABELS.get(value, value)
    return value


def _collect(conn, record_ids):
    if (not isinstance(record_ids, list) or not 2 <= len(record_ids) <= 4
            or any(not isinstance(rid, str) or re.fullmatch(r"application-[0-9a-f]{16}", rid) is None for rid in record_ids)
            or len(set(record_ids)) != len(record_ids)):
        raise ValueError("comparison_selection_invalid")
    from applypilot.job_comparison import collect_comparison

    owned = not conn.in_transaction
    if owned:
        conn.execute("BEGIN")
    try:
        records, job_ids = [], []
        for record_id in record_ids:
            try:
                record, url = _source(conn, record_id)
            except ValueError as exc:
                code = "comparison_record_changed" if str(exc) == "progress_record_changed" else "comparison_source_unavailable"
                raise ValueError(code) from exc
            records.append({key: record[key] for key in ("record_id", "title", "company")})
            # Public job comparison IDs hash the exact saved URL, never labels.
            job_ids.append("job-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16])
        if len(set(job_ids)) != len(job_ids):
            raise ValueError("comparison_duplicate_job")
        try:
            report = collect_comparison(conn, job_ids)
        except ValueError as exc:
            raise ValueError("comparison_source_unavailable") from exc
        return records, report
    finally:
        if owned:
            conn.rollback()


def preview(conn, record_ids):
    from applypilot.job_comparison import UNKNOWN

    records, report = _collect(conn, record_ids)
    return {
        "records": records,
        "fields": [{"key": key, "label": label,
                    "values": [_display(key, job[key], UNKNOWN) for job in report["jobs"]]}
                   for key, label in FIELDS],
        "notes": ["按所选顺序展示本地保存的岗位字段；未知可能表示未记录或无法安全展示。",
                  "薪资与截止日期沿用已有原文；观察日期只表示曾保存该记录，不能作为截止日期。",
                  "要求和描述是公开岗位原文的有限预览，请结合当前来源核对。"],
    }


def download(conn, record_ids, *, format):
    from applypilot.job_comparison import render_comparison_html, render_comparison_markdown

    if format not in {"html", "markdown"}:
        raise ValueError("comparison_selection_invalid")
    _, report = _collect(conn, record_ids)
    if format == "html":
        return render_comparison_html(report).encode("utf-8"), "text/html; charset=utf-8", "job-comparison.html"
    return render_comparison_markdown(report).encode("utf-8"), "text/markdown; charset=utf-8", "job-comparison.md"
