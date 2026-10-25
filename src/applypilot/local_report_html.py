"""Self-contained, inert HTML snapshots of filtered local progress or reminders."""

from __future__ import annotations

import html
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from applypilot.followup_reminders import STORE_NAME, _day, list_reminders
from applypilot.progress_report import APPLICATION_STATUSES, _stage, collect_report
from applypilot.storage.read_only import open_read_only

_CSS = """
:root{color-scheme:light;--ink:#142b32;--muted:#56696d;--line:#dce6e5;--accent:#16776b}
*{box-sizing:border-box}body{margin:0;background:#f4f7f5;color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
main{max-width:1240px;margin:36px auto;padding:0 28px}header{border-top:5px solid var(--accent);padding:26px 0 20px}
h1{font-size:32px;line-height:1.3;margin:8px 0}h2{font-size:21px;margin:28px 0 12px}h3{font-size:17px;margin:0 0 6px}
p{margin:8px 0}.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);font-weight:750}
.muted,small{color:var(--muted)}small{display:block;font-size:12px}.scope{background:#e9f0ec;border-radius:10px;padding:14px 18px}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:18px 0}.metric{background:white;border:1px solid var(--line);border-radius:12px;padding:17px}
.number{display:block;font-size:29px;font-weight:750;line-height:1.25}.metric-label{color:var(--muted);font-size:13px}
.tags{display:flex;gap:8px;flex-wrap:wrap}.tag{display:inline-block;font-size:12px;background:#e5eeea;color:#1d6359;border-radius:6px;padding:3px 8px;overflow-wrap:anywhere}
.warning{background:#fff0d7;color:#775410}.tasks{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
article{background:white;border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:16px;overflow-wrap:anywhere}
article.overdue{border-left-color:#ae6039}.task-head{display:flex;justify-content:space-between;gap:12px;align-items:start}.date{white-space:nowrap;font-size:13px;font-weight:650}
.next{margin:12px 0 8px;padding:10px 12px;background:#f3f7f5;border-radius:7px}.table-wrap{overflow:auto;background:white;border:1px solid var(--line);border-radius:12px}
table{width:100%;min-width:1080px;border-collapse:collapse;text-align:left;font-size:13px}.table-wrap time{white-space:nowrap}caption{text-align:left;padding:14px;font-weight:650}th{background:#edf3ef;color:#344f52;font-size:12px}
th,td{padding:12px;vertical-align:top;border-bottom:1px solid var(--line);overflow-wrap:anywhere}td:first-child{min-width:220px}tbody tr:last-child td{border-bottom:0}
.empty{border:1px dashed #a9bfba;border-radius:10px;padding:22px;background:white;color:var(--muted)}code{font:12px ui-monospace,Consolas,monospace;overflow-wrap:anywhere}
footer{border-top:1px solid var(--line);margin-top:30px;padding:18px 0 30px;font-size:12px;color:var(--muted)}nav{display:flex;gap:16px;font-size:13px;margin:18px 0}a{color:var(--accent)}
@media(max-width:640px){main{margin:14px auto;padding:0 16px}h1{font-size:26px}.tasks{grid-template-columns:1fr}.task-head{display:block}.date{margin-top:6px}}
@media print{body{background:white}main{max-width:none;margin:0;padding:0}nav{display:none}article,.metric{break-inside:avoid}.table-wrap{overflow:visible}table{font-size:10px;min-width:0;table-layout:fixed}.table-wrap time{white-space:normal}td:first-child{min-width:0}th,td{padding:6px}h2{break-after:avoid}}
"""
_LABELS = {
    "applied": "已申请", "already_applied": "已有申请记录", "failed": "执行失败", "not_started": "未开始",
    "in_progress": "进行中", "applying": "申请处理中", "submission_uncertain": "投递结果待核对",
    "submission_unverified": "提交依据未核实", "previewed": "已预览", "unknown": "未知",
    "contacted": "已有招聘联系", "assessment": "测评阶段", "offer": "Offer 阶段", "accepted": "已接受",
    "declined": "已拒绝 Offer", "rejected": "招聘已拒绝", "withdrawn": "已撤回", "conflict": "记录有冲突",
    "interview_unknown": "面试轮次未知", "verified": "回执已核实", "user_confirmed": "用户确认",
    "reported": "平台或历史记录", "unverified": "依据未核实", "uncertain": "结果不确定",
}
_SOURCE_LABELS = {
    "jobs.discovered_at": "岗位发现", "jobs.last_seen_at": "岗位观察", "jobs.last_attempted_at": "申请尝试",
    "jobs.applied_at": "已有申请时间记录", "jobs.scored_at": "评分记录", "jobs.tailored_at": "材料准备记录",
    "jobs.application_recorded_at": "申请记录", "application_attempts.updated_at": "申请尝试记录",
    "followup_events.occurred_at": "招聘反馈记录", "followup_actions.created_at": "待办创建",
    "followup_actions.completed_at": "待办完成记录", "followup_applications.created_at": "申请身份登记",
    "followup_applications.submitted_at": "投递时间记录",
}


def _h(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _label(value: str) -> str:
    if value.startswith("interview_") and value.removeprefix("interview_").isdigit():
        return "第 " + value.removeprefix("interview_") + " 轮面试"
    return _LABELS.get(value, value) or "未记录"


def _time(value: str) -> str:
    return (f'<time datetime="{_h(value)}">{_h(value.replace("T", " ").replace("+00:00", " UTC"))}</time>'
            if value else '<span class="muted">未记录</span>')


def _metric(label: str, number: int) -> str:
    return f'<div class="metric"><span class="number">{number}</span><span class="metric-label">{_h(label)}</span></div>'


def _tag(value: str, *, warning: bool = False) -> str:
    return f'<span class="tag{" warning" if warning else ""}">{_h(_label(value))}</span>'


def _page(title: str, generated_at: str, scope: str, body: str, footnote: str) -> str:
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{_h(title)} · 本地求职报告</title><style>{_CSS}</style></head>
<body><main><header><div class="eyebrow">Local application snapshot · UTC</div><h1>{_h(title)}</h1>
<p class="muted">生成时间：{_time(generated_at)}。本地静态快照，可离线打开或使用浏览器打印。</p></header>
<div class="scope">{scope}</div><nav><a href="#tasks">待办清单</a><a href="#records">选中记录</a></nav>
{body}<footer>{_h(footnote)}<p>报告只展示选中记录。申请材料、回答、认证信息、完整 URL 和原始错误文本均未包含。</p></footer>
</main></body></html>'''


def _scope(filters: dict) -> str:
    dates = f"{filters.get('since') or '不限'} 至 {filters.get('until') or '不限'}"
    status = "、".join(filters["statuses"]) or "全部"
    return f"<strong>筛选范围</strong> · 日期字段：{_h(filters['date_field'])} · {_h(dates)}（含首尾 UTC 日）<br>状态：{_h(status)}"


def _progress_next(row: dict) -> str:
    if row["recruiting_stage"] in {"rejected", "withdrawn", "accepted", "declined", "conflict"}:
        return "先复核招聘结论与待办是否仍相关，再决定保留或关闭本地提醒。"
    if row["application_status"] == "submission_uncertain" or "submission_confirmation_missing" in row["todo_reason"]:
        return "核对已有投递回执，确认结果后再决定后续步骤。"
    if "followup_action_pending" in row["todo_reason"]:
        return "查看本地提醒清单中的任务与日期，完成准备后手动关闭或延后提醒。"
    if any(reason in row["todo_reason"] for reason in ("human_verification_required", "security_challenge_required")):
        return "复核验证要求与已有页面记录，需要人工处理的步骤由本人完成。"
    if "assessment_required" in row["todo_reason"]:
        return "查看已有测评要求，安排本人完成所需准备或测评。"
    return "复核这条记录的状态与待办原因，再决定所需材料或下一步。"


def render_progress_html(report: dict) -> str:
    rows = report["rows"]
    tasks = [row for row in rows if row["todo_reason"]]
    body = '<section class="metrics" aria-label="所选记录统计">'
    body += _metric("选中岗位 / 申请记录", len(rows)) + _metric("有待办原因或动作", len(tasks))
    body += _metric("有已登记招聘阶段", sum(bool(row["recruiting_stage"]) for row in rows)) + '</section>'
    body += '<section aria-label="所选申请状态统计"><div class="tags">'
    body += ''.join(_tag(f"{_label(status)}：{count}") for status, count in report["application_status_counts"].items())
    body += '</div></section><section id="tasks"><h2>需要复核的待办</h2><div class="tasks">'
    for row in tasks:
        body += f'<article><h3>{_h(row["title"])}</h3><small>{_h(row["company"])}</small>'
        body += f'<p class="next"><strong>下一步：</strong>{_h(_progress_next(row))}</p>'
        body += f'<p>记录原因：<code>{_h(row["todo_reason"])}</code></p><p>已有动作最早到期：{_time(row["todo_due_at"])}</p>'
        body += f'<small>记录 ID：<code>{_h(row["record_id"])}</code></small></article>'
    body += '</div>' if tasks else '<p class="empty">所选记录没有待办原因或开放动作记录。</p></div>'
    body += '</section><section id="records"><h2>选中记录</h2>'
    if rows:
        body += '<div class="table-wrap"><table><caption>所有选中记录；未记录日期保持为空</caption><thead><tr>'
        body += ''.join(f'<th scope="col">{title}</th>' for title in
                        ("岗位 / 公司", "地点", "申请执行状态", "招聘阶段 / 提交依据", "最近记录时间（UTC）", "投递时间（UTC）", "待办原因"))
        body += '</tr></thead><tbody>'
        for row in rows:
            source = _SOURCE_LABELS.get(row["updated_at_source"], "已有记录" if row["updated_at"] else "")
            body += f'<tr><td><strong>{_h(row["title"])}</strong><small>{_h(row["company"])}</small>'
            body += f'<small>发现日期：{_h(row["discovered_at"][:10]) or "未记录"}</small><code>{_h(row["record_id"])}</code></td>'
            body += f'<td>{_h(row["location"])}</td><td>{_tag(row["application_status"], warning=bool(row["todo_reason"]))}</td>'
            body += f'<td>{_h(_label(row["recruiting_stage"]))}<small>{_h(_label(row["submission_basis"]))}</small></td>'
            body += f'<td>{_time(row["updated_at"])}<small>{_h(source)}</small></td><td>{_time(row["submitted_at"])}</td>'
            body += f'<td><code>{_h(row["todo_reason"])}</code></td></tr>'
        body += '</tbody></table></div>'
    else:
        body += '<p class="empty">没有符合这些日期和状态筛选的进度记录。</p>'
    body += '</section>'
    return _page("求职进度", report["generated_at"], _scope(report["filters"]), body,
                 "最近记录时间来自已有观察、申请尝试、招聘反馈或待办时间，不代表申请状态刚发生变化。未知投递日期保留为空，提交依据保留原级别。待办原因可能来自历史失败。")


def collect_reminder_report(workspace: Path, *, on: str | None, bucket: str, since: str | None,
                            until: str | None, statuses: Sequence[str]) -> dict:
    for bound in (since, until):
        if bound is not None:
            _day(bound)
    if since and until and since > until:
        raise ValueError("since cannot be after until")
    selected_statuses = set(statuses)
    if any(s not in APPLICATION_STATUSES and _stage(s) == "unknown" for s in selected_statuses):
        raise ValueError("Unsupported application status or recruiting stage")
    listing = list_reminders(workspace, on=on, bucket=bucket, limit=sys.maxsize)
    groups = {name: [] for name in listing["groups"]}
    for name, items in listing["groups"].items():
        for item in items:
            due = item["due_date"]
            if (since or until) and (not due or (since and due < since) or (until and due > until)):
                continue
            if selected_statuses and item["application_status"] not in selected_statuses and item["recruiting_stage"] not in selected_statuses:
                continue
            groups[name].append(item)
    return {"generated_at": datetime.now(UTC).isoformat(), "date": listing["date"], "groups": groups,
            "counts": {name: len(items) for name, items in groups.items()},
            "filters": {"date_field": "due_date", "since": since, "until": until,
                        "statuses": sorted(selected_statuses), "bucket": bucket}}


def render_reminder_html(report: dict) -> str:
    labels = {"overdue": "逾期", "today": "今天", "future": "未来", "undated": "日期待修正"}
    rows = [item for items in report["groups"].values() for item in items]
    body = '<section class="metrics" aria-label="所选提醒统计">' + _metric("选中提醒", len(rows))
    body += ''.join(_metric(labels[name], count) for name, count in report["counts"].items()) + '</section>'
    body += '<section id="tasks"><h2>到期提醒与下一步</h2>'
    for name, items in report["groups"].items():
        body += f'<h3>{labels[name]} · {len(items)}</h3><div class="tasks">'
        for item in items:
            source = "本地手动提醒" if item["source"] == "local_reminder" else "已有待办的本地提醒日期"
            body += f'<article class="{name}"><div class="task-head"><div><h3>{_h(item["title"])}</h3><small>{_h(item["company"])}</small></div>'
            body += f'<div class="date">{_time(item["due_date"])}</div></div><p>{_tag(item["application_status"])} {_tag(item["recruiting_stage"])}</p>'
            body += f'<p class="next"><strong>下一步：</strong>{_h(item["next_step"])}</p><small>{source}</small>'
            if not item["target_available"]:
                body += '<p class="tag warning">关联记录不可用，提醒保留供人工复核。</p>'
            body += f'<small>提醒 ID：<code>{_h(item["reminder_id"])}</code></small><small>记录 ID：<code>{_h(item["record_id"])}</code></small></article>'
        body += '</div>' if items else '<p class="empty">本组没有符合筛选的提醒。</p></div>'
    body += '</section><section id="records"><h2>筛选说明</h2><p>UTC 分组参考日：' + _h(report["date"]) + '。全部选中提醒均已列出，无条数截断。</p></section>'
    scope = _scope(report["filters"]) + '<br>提醒分组：' + _h(report["filters"]["bucket"]) + ' · UTC 参考日：' + _h(report["date"])
    return _page("本地到期提醒", report["generated_at"], scope, body,
                 "这是本地计划清单；提醒日期与原有待办截止日期可能不同。此快照不更新任何状态，也不发送通知。参考日只用于分组，并非历史状态回放。")


def export_report_html(workspace: Path | str, output_dir: Path | str, *, kind: str = "progress",
                       since: str | None = None, until: str | None = None, statuses: Sequence[str] = (),
                       date_field: str = "updated_at", on: str | None = None, bucket: str = "all") -> dict:
    """Create one new offline HTML file; source DB and reminder sidecar remain read-only."""
    root = Path(workspace).resolve()
    if kind not in {"progress", "reminders"}:
        raise ValueError("kind must be progress or reminders")
    path = Path(output_dir).resolve() / ("job-progress.html" if kind == "progress" else "followup-reminders.html")
    if path.exists():
        raise ValueError("HTML report already exists; choose a new output directory")
    if kind == "progress":
        if on is not None or bucket != "all":
            raise ValueError("on and bucket are reminder-only options")
        database = root / "applypilot.db"
        if not database.is_file():
            raise ValueError("Existing applypilot.db is required; HTML export does not initialize data")
        conn = open_read_only(database, timeout=10, snapshot=True)
        try:
            report = collect_report(conn, since=since, until=until, statuses=statuses, date_field=date_field)
        finally:
            conn.close()
        document = render_progress_html(report)
        count = len(report["rows"])
    else:
        if date_field != "updated_at":
            raise ValueError("date_field is a progress-only option; reminder date bounds use due_date")
        if not (root / "applypilot.db").is_file() and not (root / STORE_NAME).is_file():
            raise ValueError("Existing workspace progress or reminder data is required")
        report = collect_reminder_report(root, on=on, bucket=bucket, since=since, until=until, statuses=statuses)
        document = render_reminder_html(report)
        count = sum(report["counts"].values())
    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with path.open("x", encoding="utf-8", newline="") as stream:
            created = True
            stream.write(document)
    except OSError:
        if created:
            path.unlink(missing_ok=True)
        raise
    return {"kind": kind, "count": count, "timezone": "UTC", "filters": report["filters"], "files": {"html": str(path)}}
