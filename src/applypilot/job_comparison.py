"""Read-only side-by-side comparison of existing public job facts, without ranking."""

from __future__ import annotations

import hashlib
import html
import json
import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

from applypilot.local_report_html import _CSS
from applypilot.progress_report import APPLICATION_STATUSES, _markdown, _rows, _text
from applypilot.storage.read_only import open_read_only

UNKNOWN = "未知（未记录或不可安全展示）"
_FIELDS = (
    ("company", "公司"), ("title", "岗位"), ("location", "地点"), ("application_status", "申请执行状态"),
    ("availability", "岗位可用性记录"), ("salary", "薪资原文"), ("deadline", "明确截止日期原文"),
    ("employment_type", "雇佣类型"), ("native_level", "岗位级别"), ("observed_at", "观察 / 最近见到日期"),
    ("requirements", "来源要求原句（最多 5 条）"), ("description_excerpt", "岗位描述原文预览（最多 240 字符）"),
)
_JOB_FIELDS = (
    "url", "platform_job_id", "title", "company_name", "location", "apply_status", "salary", "description",
    "full_description", "last_seen_at", "discovered_at", "employment_type", "native_level",
    "deadline", "application_deadline", "closing_date", "valid_through", "validThrough",
)
_AVAILABILITY = {"listed_at_observation", "closed_at_observation", "historical", "open", "closed", "unknown", "unavailable", "not_accepting", "expired"}


class _PublicText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "form", "textarea", "select", "iframe"}:
            self.hidden += 1
        elif tag in {"br", "p", "li", "div"} and not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "form", "textarea", "select", "iframe"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _safe(value: object) -> str:
    if type(value) in {int, float}:
        value = str(value)
    text = _text(value)
    return "" if text in {"[credential omitted]", "[URL omitted]"} else text


def _preview(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    parser = _PublicText()
    parser.feed(value)
    return _safe(" ".join(parser.parts))


def _ref(prefix: str, identity: str) -> str:
    return prefix + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _deadline(record: dict) -> str:
    # Never use observation dates, token/lease expiry or a parsed acceptance lower bound.
    return next((text for name in ("deadline", "application_deadline", "closing_date", "valid_through", "validThrough")
                 if (text := _safe(record.get(name)))), "")


def _catalog(conn: sqlite3.Connection) -> tuple[dict, dict]:
    jobs = {}
    aliases = defaultdict(set)
    for source in _rows(conn, "jobs", _JOB_FIELDS):
        url = source.get("url")
        if not isinstance(url, str) or not url:
            continue
        rid = _ref("job-", url)
        status = source.get("apply_status")
        jobs[rid] = {
            "job_id": rid, "source": "jobs", "company": _safe(source.get("company_name")),
            "title": _safe(source.get("title")), "location": _safe(source.get("location")),
            "application_status": status if status in APPLICATION_STATUSES else "unknown",
            "availability": "", "salary": _safe(source.get("salary")), "deadline": _deadline(source),
            "employment_type": _safe(source.get("employment_type")), "native_level": _safe(source.get("native_level")),
            "observed_at": _safe(source.get("last_seen_at") or source.get("discovered_at")), "requirements": "",
            "description_excerpt": _preview(source.get("full_description") or source.get("description")),
        }
        aliases[rid].add(rid)
        native = source.get("platform_job_id")
        if isinstance(native, str) and native:
            aliases[native].add(rid)
            aliases["platform:" + native].add(rid)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(career_source_snapshots)")}
    if {"kind", "source_id", "sequence", "payload_json"} <= columns:
        names = [name for name in ("source_id", "company_id", "payload_json", "body_text", "observed_at") if name in columns]
        cursor = conn.execute(
            f'SELECT {", ".join("s." + name for name in names)} FROM career_source_snapshots s '
            "WHERE s.kind='research_jd' AND s.sequence=(SELECT MAX(v.sequence) FROM career_source_snapshots v "
            "WHERE v.kind=s.kind AND v.source_id=s.source_id) ORDER BY s.source_id"
        )
        companies = {row.get("company_id"): _safe(row.get("name")) for row in
                     _rows(conn, "career_companies", ("company_id", "name"))}
        for values in cursor:
            source = dict(zip(names, values, strict=True))
            native = source["source_id"]
            if not isinstance(native, str) or not native:
                continue
            try:
                payload = json.loads(source["payload_json"])
            except (ValueError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            rid = _ref("career-job-", native)
            requirements = []
            raw = payload.get("requirements")
            if isinstance(raw, list):
                for requirement in raw[:5]:
                    if isinstance(requirement, dict) and (text := _preview(requirement.get("text"))):
                        section = _safe(requirement.get("source_section"))
                        requirements.append(text + (f"（{section}）" if section else ""))
            locations = payload.get("locations")
            location = "；".join(filter(None, map(_safe, locations))) if isinstance(locations, list) else _safe(locations)
            availability = payload.get("availability")
            jobs[rid] = {
                "job_id": rid, "source": "career public research_jd",
                "company": companies.get(source.get("company_id")) or _safe(payload.get("company")),
                "title": _safe(payload.get("title")), "location": location,
                "application_status": "unknown", "availability": availability if isinstance(availability, str) and availability in _AVAILABILITY else "unknown",
                "salary": _safe(payload.get("salary")), "deadline": _deadline(payload),
                "employment_type": _safe(payload.get("employment_type")), "native_level": _safe(payload.get("native_level")),
                "observed_at": _safe(source.get("observed_at") or payload.get("observed_at")),
                "requirements": "\n".join(requirements),
                "description_excerpt": _preview(source.get("body_text") or payload.get("description")),
            }
            aliases[rid].add(rid)
            aliases[native].add(rid)
            aliases["career:" + native].add(rid)
    return jobs, dict(aliases)


def _connection(workspace: Path) -> sqlite3.Connection:
    path = workspace / "applypilot.db"
    if not path.is_file():
        raise ValueError("Existing applypilot.db is required; comparison does not initialize a workspace")
    return open_read_only(path, timeout=10, snapshot=True)


def list_comparison_jobs(workspace: Path | str, *, query: str = "", limit: int = 40) -> dict:
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be positive")
    conn = _connection(Path(workspace).resolve())
    try:
        jobs, _ = _catalog(conn)
    finally:
        conn.close()
    selected = [row for _, row in sorted(jobs.items()) if query.casefold() in (row["title"] + " " + row["company"]).casefold()]
    return {"count": len(selected), "jobs": [{key: row[key] for key in ("job_id", "title", "company", "source")}
                                               for row in selected[:limit]]}


def collect_comparison(conn: sqlite3.Connection, job_ids: Sequence[str]) -> dict:
    if not 2 <= len(job_ids) <= 6:
        raise ValueError("Choose 2 to 6 distinct job IDs")
    jobs, aliases = _catalog(conn)
    selected = []
    used = set()
    for job_id in job_ids:
        matches = {job_id} if job_id in jobs else aliases.get(job_id, set())
        if len(matches) != 1:
            raise ValueError("A job ID is missing or ambiguous; use compare-jobs --list and its safe IDs")
        rid = next(iter(matches))
        if rid in used:
            raise ValueError("Select distinct jobs; two IDs resolve to the same job")
        used.add(rid)
        selected.append(jobs[rid])
    return {"generated_at": datetime.now(UTC).isoformat(), "jobs": selected}


def render_comparison_markdown(report: dict) -> str:
    jobs = report["jobs"]
    lines = ["# 本地岗位对比", "", "生成时间（UTC）：" + report["generated_at"], "",
             "仅比较既有记录，按选择顺序排列；未知项保留未知，薪资/截止日期不推测。", "",
             "| 字段 | " + " | ".join(_markdown(job["title"] or job["job_id"]) for job in jobs) + " |",
             "| --- | " + " | ".join("---" for _ in jobs) + " |"]
    for key, label in (("job_id", "安全选择 ID"), ("source", "来源"), *_FIELDS):
        lines.append("| " + label + " | " + " | ".join(_markdown(job[key] or UNKNOWN).replace("\n", "<br>") for job in jobs) + " |")
    lines += ["", "描述是公开岗位字段的截断原文预览；来源要求只使用已保存的原句，未进行模型摘要或补全。",
              "观察时间不是截止日期；研究岗位的可用性记录不是申请状态。此文件不提供评分、排名或申请操作。", ""]
    return "\n".join(lines)


def render_comparison_html(report: dict) -> str:
    escape = html.escape
    jobs = report["jobs"]
    table = '<div class="table-wrap"><table><caption>按选择顺序并排展示；窄屏可横向滚动</caption><thead><tr><th scope="col">字段</th>'
    table += ''.join(f'<th scope="col">{escape(job["title"] or job["job_id"])}</th>' for job in jobs) + '</tr></thead><tbody>'
    for key, label in (("job_id", "安全选择 ID"), ("source", "来源"), *_FIELDS):
        table += f'<tr><th scope="row">{label}</th>'
        table += ''.join('<td>' + escape(job[key] or UNKNOWN).replace("\n", "<br>") + '</td>' for job in jobs) + '</tr>'
    table += '</tbody></table></div>'
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>本地岗位对比</title><style>{_CSS}</style></head><body><main><header><div class="eyebrow">Local job comparison · UTC</div>
<h1>本地岗位对比</h1><p class="muted">生成时间（UTC）：{escape(report['generated_at'])} · {len(jobs)} 个既有岗位</p></header>
<div class="scope">按用户选择顺序展示原有字段；缺失项标为未知，薪资与截止日期不推测，不提供评分或排名。</div>
<section id="comparison"><h2>并排清单</h2>{table}</section><footer>描述是公开岗位字段的截断原文预览；来源要求只使用已保存的原句，未进行模型摘要或补全。
<p>观察时间不是截止日期；研究岗位的可用性记录不是申请状态。只读本地数据，未包含材料、回答、原始错误或 URL 字段。</p>
<p>本文件可离线打开或通过浏览器打印；不会发送申请、联系或修改任何状态。</p></footer></main></body></html>'''


def export_comparison(workspace: Path | str, output_dir: Path | str, *, job_ids: Sequence[str]) -> dict:
    paths = {"html": Path(output_dir).resolve() / "job-comparison.html",
             "markdown": Path(output_dir).resolve() / "job-comparison.md"}
    if any(path.exists() for path in paths.values()):
        raise ValueError("Comparison files already exist; choose a new output directory")
    conn = _connection(Path(workspace).resolve())
    try:
        report = collect_comparison(conn, job_ids)
    finally:
        conn.close()
    documents = {"html": render_comparison_html(report), "markdown": render_comparison_markdown(report)}
    next(iter(paths.values())).parent.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for kind, path in paths.items():
            with path.open("x", encoding="utf-8", newline="") as stream:
                written.append(path)
                stream.write(documents[kind])
    except OSError:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return {"count": len(report["jobs"]), "job_ids": [job["job_id"] for job in report["jobs"]],
            "files": {kind: str(path) for kind, path in paths.items()}}
