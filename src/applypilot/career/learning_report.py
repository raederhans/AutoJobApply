"""Portable, read-only learning progress reports with scoped review evidence."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

_STATES = {
    "pending": "待开始", "in_progress": "进行中", "submitted": "已提交，待验收",
    "completion_reported": "已自报完成，尚未通过验收", "passed": "任务验收通过",
    "needs_revision": "需修改并复测", "skipped": "已跳过，未证明通过",
}
_BOUNDARY = (
    "本报告包含私人任务与评审材料，仅供本地查看。任务观察不授予通用技能等级，也不证明整条岗位要求或资格已满足。"
    "阅读资料、完成课程、自报完成和提交产物均不等于任务通过；自评仍是自评。"
    "产物验收不能证明候选人亲自完成任务或个人能力。"
)


def _text(value, fallback="未提供") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _html(value) -> str:
    return html.escape(_text(value), quote=True).replace("\n", "<br>")


def _md(value) -> str:
    # Keep supplied Markdown syntax as literal text, including headings/links.
    escaped = html.escape(_text(value), quote=True).replace("\n", " / ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", escaped)


def _safe_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username or parsed.password:
        return None
    return value.strip()


def _ordered_tasks(progress: dict) -> list[dict]:
    tasks = {task["task_id"]: task for task in progress.get("tasks", []) if isinstance(task, dict)}
    order = list(dict.fromkeys(progress.get("route", {}).get("topological_order", [])))
    order.extend(tid for tid in tasks if tid not in order)
    return [tasks[tid] for tid in order if tid in tasks]


def _metadata(progress: dict) -> list[tuple[str, object]]:
    route = progress.get("route", {})
    budget = route.get("budget", {})
    fields = [("路线", route.get("route_id")), ("目标", route.get("target_id")),
              ("诊断", route.get("diagnosis_id")), ("父路线", route.get("parent_route_id")),
              ("进度修订", progress.get("revision")), ("创建时间", route.get("created_at"))]
    if progress.get("stale") is True:
        fields.append(("输入时效", "已过期；请重新诊断后查看新路线，不能作为当前输入的结论。"))
        fields.append(("过期原因", progress.get("stale_reasons", [])))
    elif progress.get("stale") is False:
        fields.append(("输入时效", "已核验为当前。"))
    else:
        fields.append(("输入时效", "未核验。"))
    if progress.get("diagnosis_refresh_needed"):
        fields.append(("诊断更新", "已有新任务评审，需要重新诊断以更新关联与后续路线。"))
    if budget.get("total_hours") is None:
        fields.append(("时间预算", "未设定；仅提供依赖与优先顺序，不生成日历或默认学习期限。"))
    else:
        fields.extend([("每周小时", budget.get("weekly_hours")), ("周数", budget.get("weeks")),
                       ("总预算小时", budget.get("total_hours")),
                       ("按估时上界预留小时", budget.get("reserved_max_hours")),
                       ("剩余预算小时", budget.get("remaining_hours"))])
    fields.append(("估时边界", budget.get("estimate_policy", "作者估计，非本人能力或用时测量。")))
    return fields


def _task_fields(task: dict) -> list[tuple[str, object]]:
    fields = [("任务 ID", task.get("task_id")), ("任务类型", task.get("kind")),
              ("关联技能 / 锚点", f"{_text(task.get('skill_id'))} / {_text(task.get('anchor_id'))}"),
              ("绑定要求", task.get("requirement_ids", [])), ("优先理由", task.get("priority_reasons", [])),
              ("前置任务", task.get("depends_on", [])), ("当前阻塞", task.get("blocked_by", [])),
              ("执行情境", task.get("context")), ("交付物", task.get("deliverable")),
              ("允许协助", task.get("assistance_policy")), ("范围限制", task.get("limits", [])),
              ("估计小时", task.get("estimate_hours")), ("安排状态", "已纳入预算" if task.get("scheduled") else "未纳入预算"),
              ("延后原因", task.get("deferred_reason")), ("资料边界", task.get("resources_note"))]
    if task.get("inherited_from"):
        fields.append(("继承评审来源路线", task["inherited_from"]))
    submission = task.get("submission")
    if isinstance(submission, dict):
        payload = submission.get("payload", submission)
        fields.append(("提交产物快照", payload.get("artifact_evidence_snapshot_id")))
        fields.append(("提交产物指纹", payload.get("artifact_fingerprint")))
        fields.append(("提交边界", "提交仅记录产物；尚不证明任务通过。"))
    return fields


def _review_fields(task: dict) -> list[tuple[str, object]]:
    review = task.get("assessment")
    if not isinstance(review, dict):
        return [("任务评审", "尚无评审记录；任务状态不会自行授予能力等级。")]
    method = review.get("method")
    label = {"self_task_review": "自评", "independent_task_review": "独立任务评审",
             "runtime_check": "运行检查", "artifact_review": "产物评审"}.get(method, _text(method))
    subject = review.get("subject")
    conclusion = "产物验收记录；不能证明候选人个人能力。" if subject == "artifact" else (
        "候选人任务观察仅限本任务、实际环境、协助和验收项；不等于整条岗位要求满足。")
    fields = [("评审方式", f"{label}（{_text(method)}）"), ("评审对象", subject),
              ("评审者", review.get("reviewer")), ("评审记录", review.get("id", review.get("assessment_id"))),
              ("评审结论", review.get("outcome")), ("结论边界", conclusion),
              ("实际环境", review.get("context")), ("实际协助", review.get("assistance")),
              ("评审限制", review.get("limits", [])),
              ("评审产物快照", review.get("artifact_evidence_snapshot_id")),
              ("评审产物指纹", review.get("artifact_fingerprint"))]
    if task.get("assessment_current") is False:
        fields.insert(0, ("评审时效", "历史评审；当前输入不适用，不作为当前任务通过或能力结论。"))
    if review.get("route_id"):
        fields.append(("评审原路线", review["route_id"]))
    for check in review.get("acceptance_results", []):
        fields.append(("实际检查验收项", f"{_text(check.get('criterion'))}：{_text(check.get('status'))}"))
        for ref in check.get("evidence_refs", []):
            fields.append(("验收来源摘录", (f"材料 {_text(ref.get('material_id'))}；"
                           f"行 {_text(ref.get('line_start'))}–{_text(ref.get('line_end'))}；"
                           f"摘要 {_text(ref.get('snapshot_digest'))}：{_text(ref.get('quote'))}")))
    return fields


def _state(task: dict) -> str:
    state = task.get("state", "pending")
    readiness = "已结束" if state in {"passed", "skipped"} else "可开始" if task.get("ready") is True else "当前不可开始"
    return f"{_STATES.get(state, state)}（{state}）；{readiness}"


def _history_fields(progress: dict) -> list[tuple[str, object]]:
    fields = []
    for event in progress.get("history", []):
        selected = {key: event[key] for key in ("event_type", "task_id", "created_at", "reason") if key in event}
        if "reason" not in selected and isinstance(event.get("payload"), dict) and event["payload"].get("reason"):
            selected["reason"] = event["payload"]["reason"]
        fields.append(("进度事件", selected))
    for route in progress.get("historical_routes", []):
        fields.append(("历史路线", {key: route[key] for key in ("route_id", "diagnosis_id", "parent_route_id", "created_at") if key in route}))
    return fields


def _route_notes(progress: dict) -> list[tuple[str, object]]:
    route = progress.get("route", {})
    return [(label, {key: item[key] for key in keys if key in item})
            for field, label, keys in (
                ("excluded_requirements", "未编排要求", ("requirement_id", "reason", "quote", "diagnosis_status")),
                ("branch_choices", "学习替代分支", ("requirement_id", "op", "k", "selected_children", "reason", "limits")))
            for item in route.get(field, []) if isinstance(item, dict)]


def render_markdown(progress: dict) -> str:
    """Render selected progress fields literally; raw snapshots stay in JSON."""
    tasks = _ordered_tasks(progress)
    lines = ["# 学习路线与任务进度", "", _BOUNDARY, ""]
    lines.extend(f"- {label}：{_md(value)}" for label, value in _metadata(progress))
    lines.extend(f"- {label}：{_md(value)}" for label, value in _route_notes(progress))
    lines += ["", "## 依赖与优先顺序", "", "顺序来自路线拓扑排序；可开始状态由当前依赖验收决定。", ""]
    for index, task in enumerate(tasks, 1):
        lines.append(f"{index}. {_md(task.get('title'))} — {_md(_state(task))}")
    if not tasks:
        lines.append("当前路线没有已编排任务；这不表示岗位要求已满足。")
    lines += ["", "## 状态计数", ""]
    lines.extend(f"- {_md(key)}：{_md(value)}" for key, value in progress.get("summary", {}).items())
    for task in tasks:
        lines += ["", f"## {_md(task.get('title'))}", "", f"- 状态：{_md(_state(task))}"]
        lines.extend(f"- {label}：{_md(value)}" for label, value in _task_fields(task))
        lines.extend(f"- 原始验收项：{_md(criterion)}" for criterion in task.get("acceptance", []))
        lines.extend(f"- {label}：{_md(value)}" for label, value in _review_fields(task))
        for resource in task.get("resources", []):
            url = _safe_url(resource.get("url"))
            lines.append(f"- 参考资料：{_md(resource.get('title'))}；{_md(url) if url else '链接已省略'}")
            lines.append(f"- 资料说明：{_md({key: resource[key] for key in ('language', 'foundation', 'type', 'verified_at', 'limits') if key in resource})}")
    lines += ["", "## 历史记录", ""]
    lines.extend(f"- {label}：{_md(value)}" for label, value in _history_fields(progress))
    lines += ["", "## 路线边界", ""]
    lines.extend(f"- {_md(boundary)}" for boundary in [*progress.get("route", {}).get("boundaries", []),
                                                    *progress.get("boundaries", [])])
    return "\n".join(lines) + "\n"


def _dl(fields: list[tuple[str, object]]) -> str:
    return "<dl>" + "".join(f"<dt>{_html(label)}</dt><dd>{_html(value)}</dd>" for label, value in fields) + "</dl>"


def render_html(progress: dict) -> str:
    """Render standalone HTML with no remote assets, scripts or edit controls."""
    tasks = _ordered_tasks(progress)
    ordered = "".join(f"<li>{_html(task.get('title'))} — {_html(_state(task))}</li>" for task in tasks)
    if not ordered:
        ordered = "<li>当前路线没有已编排任务；这不表示岗位要求已满足。</li>"
    cards = []
    for task in tasks:
        acceptance = "".join(f"<li>{_html(item)}</li>" for item in task.get("acceptance", []))
        resources = []
        for resource in task.get("resources", []):
            url = _safe_url(resource.get("url"))
            title = _html(resource.get("title"))
            if url:
                title = f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{title}</a>'
            resource_fields = [(key, resource[key]) for key in ("language", "foundation", "type", "verified_at", "limits") if key in resource]
            resources.append(f"<li>{title}{_dl(resource_fields)}</li>")
        resources_html = "<ul>" + "".join(resources) + "</ul>" if resources else "<p>暂无选定参考资料。</p>"
        cards.append(
            '<details class="task">'
            f"<summary>{_html(task.get('title'))}<span>{_html(_state(task))}</span></summary>"
            f"{_dl(_task_fields(task))}<h3>原始验收项</h3><ul>{acceptance}</ul>"
            f"<h3>任务评审</h3>{_dl(_review_fields(task))}<h3>参考资料</h3>{resources_html}</details>")
    summary = _dl([(_STATES.get(key, key), value) for key, value in progress.get("summary", {}).items()])
    metadata = _metadata(progress)
    visible_metadata = [(label, value) for label, value in metadata if label in {
        "输入时效", "过期原因", "诊断更新", "时间预算", "每周小时", "周数", "总预算小时"}]
    boundaries = "".join(f"<li>{_html(item)}</li>" for item in [*progress.get("route", {}).get("boundaries", []),
                                                            *progress.get("boundaries", [])])
    history = _dl(_history_fields(progress))
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>学习路线与任务进度</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f6f9;color:#10253b;font:16px/1.65 "Segoe UI","Microsoft YaHei",sans-serif}}
main{{max-width:980px;margin:30px auto;padding:32px;background:white;border:1px solid #d6e0e8;border-radius:12px}}
h1,h2,h3{{line-height:1.3}}h1{{font-size:28px}}h2{{font-size:22px;margin-top:30px}}h3{{font-size:18px}}
.notice{{background:#edf4f7;padding:14px;border-left:4px solid #176b87}}.task{{margin:14px 0;padding:18px;border:1px solid #d6e0e8;border-radius:8px}}
summary{{cursor:pointer;font-weight:600;overflow-wrap:anywhere}}summary span{{display:block;font-size:14px;font-weight:400;color:#526477}}
dl{{display:grid;grid-template-columns:145px 1fr;gap:5px 14px}}dt{{color:#526477}}dd{{margin:0;overflow-wrap:anywhere}}
a{{color:#176b87;overflow-wrap:anywhere}}li{{margin:6px 0}}@media(max-width:650px){{main{{margin:0;padding:20px 16px}}dl{{grid-template-columns:1fr}}dd{{margin-bottom:8px}}}}
</style></head><body><main><h1>学习路线与任务进度</h1><p class="notice">按优先顺序选择下一项任务；展开卡片查看交付物、验收标准及评审依据。</p>
{_dl(visible_metadata)}{summary}<h2>依赖与优先顺序</h2><p>可开始状态由当前依赖验收决定；资格核对单独进行。</p><ol>{ordered}</ol>
<h2>任务详情</h2>{''.join(cards)}
<details><summary>来源版本、未编排要求与分支选择</summary>{_dl([*metadata, *_route_notes(progress)])}</details>
<details><summary>历史记录与旧路线</summary>{history}</details><details><summary>如何理解任务通过</summary><p>{_html(_BOUNDARY)}</p><ul>{boundaries}</ul></details>
</main></body></html>'''


def write_report(progress: dict, directory: Path | str) -> dict[str, str]:
    """Write literal Markdown, standalone HTML and the complete private JSON."""
    if not isinstance(progress, dict):
        raise TypeError("progress must be a dict")
    directory = Path(directory).expanduser().absolute()
    route_id = progress.get("route", {}).get("route_id") or "route"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(route_id)).strip("-._")[:80] or "route"
    paths = {key: directory / f"career-learning-{slug}.{suffix}" for key, suffix in (
        ("markdown", "md"), ("html", "html"), ("json", "json"))}
    directory.mkdir(parents=True, exist_ok=True)
    paths["markdown"].write_text(render_markdown(progress), encoding="utf-8")
    paths["html"].write_text(render_html(progress), encoding="utf-8")
    paths["json"].write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}
