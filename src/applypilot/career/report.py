"""Render evidence-linked career diagnoses as portable, read-only reports."""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from applypilot.career._report_common import _safe_url, _text

_STATUS_ZH = {
    "related_evidence": "有关联经历材料",
    "transferable_evidence": "存在可迁移证据",
    "evidence_missing": "材料待补，能力未知",
    "needs_verification": "需要任务验证，能力未知",
    "observed_task_gap": "已观察到任务未通过",
    "qualification_unknown": "资格未知",
    "unresolved": "原句范围待澄清",
    "uncovered": "当前目录未覆盖",
    "met": "有材料关联",
    "partially_met": "部分材料关联",
    "gap": "材料缺口",
    "unknown": "未知",
    "not_assessed": "未测评",
}
_ACTION_ZH = {
    "organize_evidence": "整理已有证据",
    "verify_target_context": "核对目标任务情境",
    "collect_existing_evidence": "补充已有材料",
    "verify_task": "安排小型任务验证",
    "practice_failed_criteria": "针对未通过项练习并复测",
    "verify_qualification": "独立核实资格",
    "clarify_source_condition": "澄清原句条件范围",
    "review_catalog_coverage": "复核目录覆盖范围",
}


def _slug(value: Any) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "career-report" )).strip("-._")
    return slug[:80] or "career-report"


def _scope_lines(diagnosis: dict) -> list[str]:
    target = diagnosis.get("target") or {}
    target = target if isinstance(target, dict) else {}
    lines = []
    target_fields = (
        ("目标", target.get("label")),
        ("职位方向", target.get("role_family")),
        ("公司", target.get("company_id")),
        ("地区", target.get("region")),
        ("雇用类型", target.get("employment_track")),
        ("职位级别", target.get("native_level")),
    )
    lines.extend(f"- {label}：{_text(value)}" for label, value in target_fields if value is not None)
    contexts = diagnosis.get("job_contexts") or []
    if isinstance(contexts, dict):
        contexts = list(contexts.values())
    contexts = [context for context in contexts if isinstance(context, dict)]
    if contexts:
        lines.append(f"- 选定 JD 样本：{len(contexts)} 份")
        for context in contexts:
            title = context.get("title") or context.get("id") or "职位标题未提供"
            company = context.get("company") or context.get("company_id")
            locations = context.get("locations") or context.get("region")
            level = context.get("native_level")
            details = [str(value) for value in (company, locations, level) if value]
            suffix = f"（{'；'.join(details)}）" if details else ""
            lines.append(f"- JD：{title}{suffix}")
        observed = [str(c["observed_at"]) for c in contexts if c.get("observed_at")]
        if observed:
            lines.append(f"- 样本记录日期：{min(observed)} 至 {max(observed)}")
    elif isinstance(diagnosis.get("requirements"), list):
        lines.append(f"- 分析条件数：{len(diagnosis['requirements'])}（未提供职位样本详情）")
    return lines


def _freshness_lines(diagnosis: dict) -> list[str]:
    stale = diagnosis.get("stale")
    reasons = diagnosis.get("stale_reasons") or []
    if stale is True:
        lines = ["- 输入时效：已过期；不得将本报告作为当前输入的结论。"]
        lines.extend(f"- 过期原因：{_text(reason)}" for reason in reasons)
        if not reasons:
            lines.append("- 过期原因：服务标记输入过期，但未提供具体原因。")
        return lines
    if stale is False:
        return ["- 输入时效：生成前已核验为当前。"]
    return ["- 输入时效：未核验；本报告不声称输入与当前目标或材料一致。"]


def _generation_label_value(diagnosis: dict) -> tuple[str, str]:
    created_at = diagnosis.get("created_at")
    if isinstance(created_at, str) and created_at.strip():
        return "报告生成时间", created_at.strip()
    return "报告生成日期（UTC）", datetime.now(UTC).date().isoformat()


def _maps(diagnosis: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    raw_evidence = diagnosis.get("evidence") or {}
    if isinstance(raw_evidence, dict):
        evidence_items = raw_evidence.get("evidence", [])
        materials = raw_evidence.get("materials", [])
    else:
        evidence_items, materials = raw_evidence, []
    evidence_by_id = {
        item["id"]: item for item in evidence_items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    } if isinstance(evidence_items, list) else {}
    material_by_id = {
        item["id"]: item for item in materials
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    } if isinstance(materials, list) else {}
    return evidence_by_id, material_by_id


def _refs(requirement: dict, evidence_by_id: dict[str, dict]) -> list[dict]:
    result = []
    for ref in requirement.get("evidence_refs") or []:
        if not isinstance(ref, dict):
            continue
        evidence = evidence_by_id.get(ref.get("evidence_id"), {})
        source_refs = ref.get("source_refs") or evidence.get("source_refs") or []
        for source_ref in source_refs:
            if isinstance(source_ref, dict):
                result.append({**ref, **source_ref, "evidence_id": ref.get("evidence_id")})
        if not source_refs and (ref.get("quote") or ref.get("material_id")):
            result.append(ref)
    return result


def _status(value: Any) -> str:
    raw = _text(value, "unknown")
    return f"{_STATUS_ZH.get(raw, raw)}（{raw}）"


def _action_text(action: Any) -> str:
    if not isinstance(action, dict):
        return _text(action)
    kind = action.get("kind")
    label = _ACTION_ZH.get(kind, kind)
    reason = action.get("reason_zh")
    if label and reason:
        return f"{label}：{reason}"
    return _text(reason or label)


def _has_alternatives(tree: dict) -> bool:
    return tree.get("op") in {"any", "at_least"} or any(
        _has_alternatives(child) for child in tree.get("children", []))


def _gap_details(requirement: dict) -> list[tuple[str, str]]:
    details = []
    missing = sorted({tag for item in requirement.get("condition_observations", [])
                      for tag in item.get("missing_technical_tags", [])})
    if missing and requirement.get("related_conditions_supported") is not True:
        label = "尚缺原始材料支持的技术条件"
        note = "；简历可能已有自述，仍需原始产物核对；缺少记录不等于不会。"
        if _has_alternatives(requirement.get("condition_tree") or {}):
            label = "待核对的技术记录（含替代选项）"
            note += "原句含可选分支，应按条件结构选择，不要求补齐所有列出的技术。"
        details.append((label, "、".join(missing) + note))
    for task in (requirement.get("next_action") or {}).get("tasks", []):
        description = (f"{task.get('skill_name', '')}：{task['task']}；"
                       f"交付物：{task.get('deliverable', '')}；验收：{'；'.join(task.get('acceptance', []))}")
        details.append(("可选验证任务", description))
    for ref in requirement.get("evidence_refs", []):
        boundaries = "；".join(ref.get("does_not_prove", []))
        if boundaries:
            details.append((f"{ref.get('evidence_id', '')} 材料边界", boundaries))
    for suggestion in requirement.get("model_suggestions", []):
        details.append(("模型补充建议（不改变规则结论）", f"{suggestion.get('evidence_id', '')}：{suggestion.get('reason', '')}"))
    return details


def _task_details(requirement: dict) -> list[tuple[str, str]]:
    details = []
    for observation in requirement.get("task_observations", []):
        if not isinstance(observation, dict):
            continue
        method = observation.get("method")
        method_label = {"self_task_review": "自评", "independent_task_review": "独立任务评审",
                        "runtime_check": "运行检查", "artifact_review": "产物评审"}.get(method, _text(method))
        subject = observation.get("subject")
        if subject == "artifact":
            conclusion = "产物验收记录；不表示候选人任务通过或能力已证明"
        elif observation.get("candidate_task_result") == "passed":
            conclusion = "本任务已观察到通过；整条岗位要求仍按材料关联状态判读"
        elif observation.get("candidate_task_result") == "failed":
            conclusion = "本任务已观察到未通过；只限绑定要求与已检查验收项"
        else:
            conclusion = "尚未形成候选人任务通过或失败的观察结论"
        details.extend([
            ("任务观察", f"{_text(observation.get('task_id'))}；评审 {_text(observation.get('assessment_id'))}；{conclusion}"),
            ("评审方式与对象", f"{method_label}（{_text(method)}）；对象 {_text(subject)}；评审者 {_text(observation.get('reviewer'))}"),
            ("观察范围", (f"路线 {_text(observation.get('route_id'))}；锚点 {_text(observation.get('anchor_id'))}；"
             f"要求 {_text(observation.get('requirement_ids'))}；情境 {_text(observation.get('context'))}")),
            ("实际协助", _text(observation.get("assistance"))),
            ("产物来源", (f"快照 {_text(observation.get('artifact_evidence_snapshot_id'))}；"
             f"指纹 {_text(observation.get('artifact_fingerprint'))}")),
            ("观察限制", _text(observation.get("limits"))),
        ])
        for check in observation.get("acceptance_results", []):
            if not isinstance(check, dict):
                continue
            details.append(("实际检查验收项", f"{_text(check.get('criterion'))}：{_text(check.get('status'))}"))
            for ref in check.get("evidence_refs", []):
                if not isinstance(ref, dict):
                    continue
                span = f"；行 {_text(ref.get('line_start'))}–{_text(ref.get('line_end'))}" if ref.get("line_start") is not None else ""
                details.append(("验收来源摘录", (f"材料 {_text(ref.get('material_id'))}{span}；"
                                f"快照摘要 {_text(ref.get('snapshot_digest'))}：{_text(ref.get('quote'))}")))
    return details


def _requirement_title(requirement: dict) -> str:
    return _text(requirement.get("quote") or requirement.get("requirement_id") or requirement.get("id"))


def _requirement_markdown(requirement: dict, evidence_by_id: dict[str, dict], material_by_id: dict[str, dict]) -> list[str]:
    job = requirement.get("job") or {}
    title = _requirement_title(requirement)
    safe_title = html.escape(title, quote=False)
    job_title = _text(job.get("title") or job.get("company") or requirement.get("job_id")) if isinstance(job, dict) else _text(requirement.get("job_id"))
    lines = [f"### {safe_title}", "", f"- 职位：{html.escape(job_title, quote=False)}"]
    if isinstance(job, dict) and job.get("observed_at"):
        lines.append(f"- 职位记录时间：{html.escape(_text(job['observed_at']), quote=False)}")
    source = requirement.get("source") or {}
    if requirement.get("source_snapshot_id") or source:
        snapshot_id = requirement.get("source_snapshot_id") or source.get("snapshot_id") if isinstance(source, dict) else requirement.get("source_snapshot_id")
        source_parts = [f"快照 {snapshot_id}" if snapshot_id else ""]
        if isinstance(source, dict):
            if source.get("section"):
                source_parts.append(f"章节 {source['section']}")
            if source.get("line_start") is not None:
                span = str(source["line_start"])
                if source.get("line_end") is not None and source["line_end"] != source["line_start"]:
                    span += f"–{source['line_end']}"
                source_parts.append(f"行 {span}")
        lines.append(f"- 职位来源：{html.escape('；'.join(source_parts) or '来源信息未提供', quote=False)}")
    if isinstance(job, dict) and job.get("url"):
        url = _safe_url(job["url"])
        lines.append(f"- 职位链接：{url}" if url else "- 职位链接：已省略（仅保留 HTTPS 外链）")
    lines += [
        f"- 条件类别：{html.escape(_text(requirement.get('kind') or requirement.get('raw_kind')), quote=False)}",
        f"- 强度：{html.escape(_text(requirement.get('strength')), quote=False)}",
        f"- 判读状态：{html.escape(_status(requirement.get('status')), quote=False)}",
        f"- 解析状态：{html.escape(_text(requirement.get('parse_status')), quote=False)}",
        f"- 原始引文：\n\n  > {html.escape(_text(requirement.get('quote')), quote=False).replace(chr(10), chr(10) + '  > ')}",
    ]
    if requirement.get("condition_tree"):
        lines.append(f"- 条件结构：`{html.escape(_text(requirement['condition_tree']), quote=False)}`")
    if requirement.get("reason_zh"):
        lines.append(f"- 说明：{html.escape(_text(requirement['reason_zh']), quote=False)}")
    if requirement.get("next_action"):
        action = requirement["next_action"]
        action_text = _action_text(action)
        lines.append(f"- 下一步：{html.escape(_text(action_text), quote=False)}")
        if isinstance(action, dict) and action.get("anchor_ids"):
            lines.append(f"- 目录锚点：{html.escape(', '.join(map(str, action['anchor_ids'])), quote=False)}")
    lines.extend(f"- {label}：{html.escape(text, quote=False)}" for label, text in _gap_details(requirement))
    lines.extend(f"- {label}：{html.escape(text, quote=False)}" for label, text in _task_details(requirement))
    refs = _refs(requirement, evidence_by_id)
    if refs:
        lines.append("- 材料依据：")
        for ref in refs:
            material_id = ref.get("material_id")
            material = material_by_id.get(material_id, {})
            label = material.get("title") or material_id or "材料"
            excerpt = ref.get("quote") or "未提供原文摘录"
            ref_parts = []
            if material.get("kind"):
                ref_parts.append(str(material["kind"]))
            if ref.get("line_start") is not None:
                span = str(ref["line_start"])
                if ref.get("line_end") is not None and ref["line_end"] != ref["line_start"]:
                    span += f"–{ref['line_end']}"
                ref_parts.append(f"行 {span}")
            if material.get("snapshot_digest") or ref.get("snapshot_digest"):
                ref_parts.append(f"快照 {material.get('snapshot_digest') or ref.get('snapshot_digest')}")
            provenance = f"（{'；'.join(ref_parts)}）" if ref_parts else ""
            lines.append(f"  - {html.escape(_text(label), quote=False)}{html.escape(provenance, quote=False)}：\n\n    > {html.escape(_text(excerpt), quote=False).replace(chr(10), chr(10) + '    > ')}")
    else:
        lines.append("- 材料依据：暂无可引用的材料片段。")
    lines.append("")
    return lines


def render_markdown(diagnosis: dict) -> str:
    """Render a readable Chinese report without interpreting input as markup."""
    requirements = diagnosis.get("requirements") or []
    if not isinstance(requirements, list):
        requirements = []
    evidence_by_id, material_by_id = _maps(diagnosis)
    summary = diagnosis.get("summary") or {}
    counts = summary.get("status_counts", {}) if isinstance(summary, dict) else {}
    qualification = [r for r in requirements if isinstance(r, dict) and r.get("kind") == "qualification"]
    other = [r for r in requirements if isinstance(r, dict) and r.get("kind") != "qualification"]
    skills = diagnosis.get("skills") or []
    generation_label, generation_value = _generation_label_value(diagnosis)

    lines = [
        "# 职业条件与材料关联报告", "",
        f"- 报告编号：{html.escape(_text(diagnosis.get('diagnosis_id') or diagnosis.get('id'), '未提供'), quote=False)}",
        f"- {generation_label}：{html.escape(generation_value, quote=False)}",
        *_scope_lines(diagnosis),
        *_freshness_lines(diagnosis),
        "",
        "> 本报告呈现职位条件与已选材料的关联及限定任务观察，不构成完整能力测评或录用判断。规则关联不等于能力已证明；资格条件单独列示，缺少证据时保持未知。历史职位仅供分析，不能据此认定职位当前开放或可投。",
        "",
        "## 状态概览", "",
    ]
    if counts:
        lines += [f"- {html.escape(_status(key), quote=False)}：{html.escape(_text(value), quote=False)}" for key, value in counts.items()]
    else:
        lines.append(f"- 条件数：{len(requirements)}")
    lines += ["", "## 资格条件", "", "资格属于独立条件；本报告不会将材料关联换算为资格结论。", ""]
    if qualification:
        for requirement in qualification:
            lines.extend(_requirement_markdown(requirement, evidence_by_id, material_by_id))
    else:
        lines.append("当前样本未提取到资格条件；这不代表资格已确认。\n")
    lines += ["## 技能、职责及其他条件", ""]
    if other:
        for requirement in other:
            lines.extend(_requirement_markdown(requirement, evidence_by_id, material_by_id))
    else:
        lines.append("当前样本没有其他已提取条件。\n")
    lines += ["## 技能目录", "", "技能主题与材料的关联不是技能测评；级别为 null 时表示未测，不能解读为零级。", ""]
    if skills:
        for skill in skills:
            if isinstance(skill, dict):
                level = "未测（null）" if skill.get("level") is None else _text(skill.get("level"))
                lines.append(f"- {html.escape(_text(skill.get('name_zh') or skill.get('name') or skill.get('id')), quote=False)}：{html.escape(level, quote=False)}")
            else:
                lines.append(f"- {html.escape(_text(skill), quote=False)}：未测（null）")
    else:
        lines.append("- 未提供技能目录。")
    boundaries = diagnosis.get("boundaries") or []
    lines += ["", "## 解读边界", ""]
    lines += [f"- {html.escape(_text(item), quote=False)}" for item in boundaries] or ["- 未附加其他边界说明。"]
    lines.append("")
    return "\n".join(lines)


def _html_quote(text: Any) -> str:
    return html.escape(_text(text), quote=True).replace("\n", "<br>")


def _requirement_html(requirement: dict, evidence_by_id: dict[str, dict], material_by_id: dict[str, dict]) -> str:
    job = requirement.get("job") or {}
    title = _requirement_title(requirement)
    job_title = job.get("title") or job.get("company") or requirement.get("job_id") if isinstance(job, dict) else requirement.get("job_id")
    url = _safe_url(job.get("url")) if isinstance(job, dict) else None
    job_html = _html_quote(job_title)
    if url:
        job_html = f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{job_html}</a>'
    fields = [
        ("条件类别", requirement.get("kind") or requirement.get("raw_kind")),
        ("强度", requirement.get("strength")),
        ("判读状态", _status(requirement.get("status"))),
        ("解析状态", requirement.get("parse_status")),
    ]
    if requirement.get("reason_zh"):
        fields.append(("说明", requirement["reason_zh"]))
    action = requirement.get("next_action")
    if action:
        fields.append(("下一步", _action_text(action)))
        if isinstance(action, dict) and action.get("anchor_ids"):
            fields.append(("目录锚点", ", ".join(map(str, action["anchor_ids"]))))
    fields.extend(_gap_details(requirement))
    fields.extend(_task_details(requirement))
    if isinstance(job, dict) and job.get("observed_at"):
        fields.append(("职位记录时间", job["observed_at"]))
    source = requirement.get("source") or {}
    if requirement.get("source_snapshot_id") or source:
        parts = []
        if requirement.get("source_snapshot_id") or (isinstance(source, dict) and source.get("snapshot_id")):
            parts.append(f"快照 {requirement.get('source_snapshot_id') or source.get('snapshot_id')}")
        if isinstance(source, dict):
            if source.get("section"):
                parts.append(f"章节 {source['section']}")
            if source.get("line_start") is not None:
                span = str(source["line_start"])
                if source.get("line_end") is not None and source["line_end"] != source["line_start"]:
                    span += f"–{source['line_end']}"
                parts.append(f"行 {span}")
        fields.append(("职位来源", "；".join(parts) or "来源信息未提供"))
    detail_list = "".join(f"<dt>{_html_quote(label)}</dt><dd>{_html_quote(value)}</dd>" for label, value in fields)
    quote = _html_quote(requirement.get("quote"))
    source_details = []
    for ref in _refs(requirement, evidence_by_id):
        material_id = ref.get("material_id")
        material = material_by_id.get(material_id, {})
        label = material.get("title") or material_id or "材料"
        excerpt = ref.get("quote") or "未提供原文摘录"
        ref_parts = []
        if material.get("kind"):
            ref_parts.append(str(material["kind"]))
        if ref.get("line_start") is not None:
            span = str(ref["line_start"])
            if ref.get("line_end") is not None and ref["line_end"] != ref["line_start"]:
                span += f"–{ref['line_end']}"
            ref_parts.append(f"行 {span}")
        snapshot = material.get("snapshot_digest") or ref.get("snapshot_digest")
        if snapshot:
            ref_parts.append(f"快照 {snapshot}")
        provenance = f"（{'；'.join(ref_parts)}）" if ref_parts else ""
        source_details.append(f"<li><strong>{_html_quote(label)}</strong><span> {_html_quote(provenance)}</span><details><summary>展开材料原句</summary><blockquote>{_html_quote(excerpt)}</blockquote></details></li>")
    sources_html = "<ul>" + "".join(source_details) + "</ul>" if source_details else "<p>暂无可引用的材料片段。</p>"
    if isinstance(job, dict) and job.get("historical") is True:
        historical = '<p class="notice">历史职位：此记录仅供条件分析，不表示当前开放或可投。</p>'
    else:
        historical = ""
    return (
        '<article class="requirement">'
        f"<h3>{_html_quote(title)}</h3>{historical}<p><strong>职位：</strong>{job_html}</p>"
        f"<dl>{detail_list}</dl><details><summary>展开职位原句</summary><blockquote>{quote}</blockquote></details>"
        f"<h4>材料依据</h4>{sources_html}</article>"
    )


def render_html(diagnosis: dict) -> str:
    """Render a self-contained static report; all supplied values are escaped."""
    requirements = diagnosis.get("requirements") or []
    if not isinstance(requirements, list):
        requirements = []
    evidence_by_id, material_by_id = _maps(diagnosis)
    qualifications = [r for r in requirements if isinstance(r, dict) and r.get("kind") == "qualification"]
    others = [r for r in requirements if isinstance(r, dict) and r.get("kind") != "qualification"]
    skills = diagnosis.get("skills") or []
    summary = diagnosis.get("summary") or {}
    counts = summary.get("status_counts", {}) if isinstance(summary, dict) else {}
    summary_html = "".join(f"<li>{_html_quote(_status(key))}：{_html_quote(value)}</li>" for key, value in counts.items())
    if not summary_html:
        summary_html = f"<li>条件数：{len(requirements)}</li>"
    scope_html = "".join(f"<li>{_html_quote(line[2:])}</li>" for line in _scope_lines(diagnosis))
    freshness_html = "".join(f"<li>{_html_quote(line[2:])}</li>" for line in _freshness_lines(diagnosis))
    skill_html = "".join(
        f"<li>{_html_quote(skill.get('name_zh') or skill.get('name') or skill.get('id'))}："
        f"{'未测（null）' if skill.get('level') is None else _html_quote(skill.get('level'))}</li>"
        for skill in skills if isinstance(skill, dict)
    )
    if not skill_html:
        skill_html = "<li>未提供技能目录。</li>"
    boundaries = diagnosis.get("boundaries") or []
    boundaries_html = "".join(f"<li>{_html_quote(item)}</li>" for item in boundaries) or "<li>未附加其他边界说明。</li>"
    qualification_html = "".join(_requirement_html(r, evidence_by_id, material_by_id) for r in qualifications)
    other_html = "".join(_requirement_html(r, evidence_by_id, material_by_id) for r in others)
    generation_label, generation_value = _generation_label_value(diagnosis)
    if not qualification_html:
        qualification_html = "<p>当前样本未提取到资格条件；这不代表资格已确认。</p>"
    if not other_html:
        other_html = "<p>当前样本没有其他已提取条件。</p>"
    report_id = _html_quote(diagnosis.get("diagnosis_id") or diagnosis.get("id"))
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>职业条件与材料关联报告</title><style>
:root{{--ink:#0B1F3A;--muted:#526477;--line:#d9e1ea;--paper:#fff;--wash:#f3f6f9;--accent:#176b87}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--wash);color:var(--ink);font:16px/1.65 "Aptos", "Segoe UI", "Microsoft YaHei", sans-serif}}
main{{max-width:980px;margin:32px auto;padding:36px;background:var(--paper);border:1px solid var(--line);border-radius:14px}}
h1,h2,h3{{line-height:1.3}}h1{{font-size:30px;margin:0 0 18px}}h2{{font-size:22px;margin-top:36px;border-bottom:1px solid var(--line);padding-bottom:9px}}
h3{{font-size:18px}}h4{{margin-bottom:6px}}a{{color:var(--accent);overflow-wrap:anywhere}}.scope,.notice{{background:#edf4f7;padding:12px 16px;border-radius:8px}}
.notice{{border-left:4px solid #b36b00}}.requirement{{margin:18px 0;padding:18px;border:1px solid var(--line);border-radius:10px}}
dl{{display:grid;grid-template-columns:130px 1fr;gap:4px 14px}}dt{{color:var(--muted)}}dd{{margin:0;overflow-wrap:anywhere}}
blockquote{{margin:10px 0;padding:10px 14px;border-left:3px solid #9bb3c2;background:#f7f9fb;white-space:pre-wrap;overflow-wrap:anywhere}}
details{{margin:10px 0}}summary{{cursor:pointer;color:var(--accent)}}@media(max-width:650px){{main{{margin:0;padding:22px 16px;border-radius:0}}dl{{grid-template-columns:1fr}}dd{{margin-bottom:8px}}}}
</style></head><body><main>
<h1>职业条件与材料关联报告</h1><p>报告编号：{report_id} · {_html_quote(generation_label)}：{_html_quote(generation_value)}</p>
<ul class="scope">{scope_html}</ul><h2>输入时效</h2><ul>{freshness_html}</ul>
<p class="notice">本报告呈现职位条件与已选材料的关联及限定任务观察，不构成完整能力测评或录用判断。规则关联不等于能力已证明；资格条件单独列示，缺少证据时保持未知。历史职位仅供分析，不能据此认定职位当前开放或可投。</p>
<h2>状态概览</h2><ul>{summary_html}</ul>
<h2>资格条件</h2><p>资格属于独立条件；材料关联不等于资格结论。</p>{qualification_html}
<h2>技能、职责及其他条件</h2>{other_html}
<h2>技能目录</h2><p>技能主题与材料的关联不是技能测评；级别为 null 时表示未测，不能解读为零级。</p><ul>{skill_html}</ul>
<h2>解读边界</h2><ul>{boundaries_html}</ul>
</main></body></html>'''


def write_report(diagnosis: dict, output_dir: Path) -> dict[str, str]:
    """Write a Markdown, standalone HTML, and raw JSON copy of one diagnosis."""
    if not isinstance(diagnosis, dict):
        raise TypeError("diagnosis must be a dict")
    output_dir = Path(output_dir).expanduser().absolute()
    report_id = diagnosis.get("diagnosis_id") or diagnosis.get("id")
    if not report_id:
        encoded = json.dumps(diagnosis, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        report_id = hashlib.sha256(encoded).hexdigest()[:12]
    stem = f"career-report-{_slug(report_id)}"
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "markdown": output_dir / f"{stem}.md",
        "html": output_dir / f"{stem}.html",
        "json": output_dir / f"{stem}.json",
    }
    paths["markdown"].write_text(render_markdown(diagnosis), encoding="utf-8")
    paths["html"].write_text(render_html(diagnosis), encoding="utf-8")
    paths["json"].write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}
