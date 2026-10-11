from __future__ import annotations

import json
from pathlib import Path

from applypilot.career.report import render_html, render_markdown, write_report


def _fake_diagnosis() -> dict:
    return {
        "schema_version": "career-diagnosis-v1",
        "diagnosis_id": "fixture-01",
        "created_at": "2026-01-31T15:04:05+08:00",
        "target": {
            "mode": "company_direction",
            "label": "数据实习",
            "company_id": "company-7",
            "role_family": "data_analytics",
            "region": "Singapore",
            "employment_track": "internship",
            "native_level": "junior",
            "bindings": {"catalog_id": "catalog-secret", "job_ids": ["job-1"]},
            "input_fingerprint": "target-hash-secret",
        },
        "input_manifest": {
            "sample_range": "虚构样本 A–B",
            "date_range": {"from": "2026-01-01", "to": "2026-01-31"},
            "job_ids": ["job-1"],
        },
        "requirements": [
            {
                "id": "req-1",
                "requirement_id": "req-1",
                "job_id": "job-1",
                "job": {
                    "title": '<img src=x onerror="alert(1)">',
                    "url": "javascript:alert(1)",
                    "observed_at": "2026-01-15",
                },
                "source_snapshot_id": "snapshot-1",
                "source": {"snapshot_id": "snapshot-1", "section": "Requirements", "line_start": 12, "line_end": 13},
                "quote": '<script>alert("xss")</script> & retain this quote',
                "kind": "skill",
                "strength": "required",
                "condition_tree": {"all": ["Python"]},
                "parse_status": "parsed",
                "status": "related_evidence",
                "evidence_refs": [{
                    "evidence_id": "ev-1",
                    "source_refs": [{
                        "material_id": "mat-1",
                        "quote": '<svg onload="alert(2)">material excerpt</svg>',
                    }],
                }],
                "reason_zh": "材料可供核对。",
                "next_action": {"kind": "verify_task", "reason_zh": "查看课程项目锚点。", "anchor_ids": ["course-project"]},
            },
            {
                "id": "req-2",
                "requirement_id": "req-2",
                "job_id": "job-1",
                "job": {"title": "测试岗位"},
                "quote": "须具备工作许可",
                "kind": "qualification",
                "strength": "required",
                "parse_status": "parsed",
                "status": "qualification_unknown",
                "evidence_refs": [],
                "reason_zh": "资格无法由简历材料确认。",
                "next_action": {"kind": "verify", "reason_zh": "自行核实资格。", "anchor_ids": []},
            },
        ],
        "summary": {"status_counts": {"related_evidence": 1, "qualification_unknown": 1}},
        "skills": [{"id": "python", "name_zh": "Python", "level": None, "assessment_refs": []}],
        "evidence": {
            "materials": [{"id": "mat-1", "kind": "resume", "title": "虚构材料"}],
            "evidence": [{"id": "ev-1", "source_refs": []}],
        },
        "boundaries": ["规则关联不是能力测评。"],
        "input_digest": "input-hash-secret",
        "job_contexts": [{
            "id": "job-1",
            "title": "数据分析实习生",
            "company": "虚构公司",
            "locations": ["Singapore"],
            "native_level": "junior",
            "observed_at": "2026-01-15",
        }],
        "stale": True,
        "stale_reasons": ["evidence_snapshot_changed", "target_revision_changed"],
    }


def test_report_html_escapes_untrusted_values_and_preserves_unknown_level() -> None:
    diagnosis = _fake_diagnosis()

    rendered = render_html(diagnosis)
    markdown = render_markdown(diagnosis)

    assert "<script>alert" not in rendered
    assert "<img src=x" not in rendered
    assert "<svg onload=" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&lt;img src=x" in rendered
    assert "&lt;svg onload=" in rendered
    assert "javascript:alert(1)\"" not in rendered
    assert "null 时表示未测" in rendered
    assert "Python：未测（null）" in rendered
    assert "资格条件" in rendered and "须具备工作许可" in rendered
    assert "规则关联不等于能力已证明" in rendered
    assert "历史职位仅供分析" in rendered
    assert "snapshot-1" in rendered
    assert "Requirements" in rendered
    assert "未测（null）" in markdown
    assert "须具备工作许可" in markdown
    assert "2026-01-31T15:04:05+08:00" in markdown
    assert "2026-01-31T15:04:05+08:00" in rendered
    assert "有关联经历材料" in rendered
    assert "可回源材料" not in rendered
    assert "安排小型任务验证" in rendered
    assert "course-project" in rendered
    assert "数据分析实习生" in rendered
    assert "虚构公司" in rendered
    assert "target-hash-secret" not in rendered
    assert "catalog-secret" not in rendered
    assert "input-hash-secret" not in rendered
    assert "已过期" in rendered and "evidence_snapshot_changed" in rendered
    assert "不得将本报告作为当前输入的结论" in markdown


def test_write_report_creates_markdown_html_and_json(tmp_path) -> None:
    diagnosis = _fake_diagnosis()

    paths = write_report(diagnosis, tmp_path / "reports")

    assert set(paths) == {"markdown", "html", "json"}
    assert all((tmp_path / "reports" / f"career-report-fixture-01.{suffix}").is_file() for suffix in ("md", "html", "json"))
    assert "职业条件与材料关联报告" in Path(paths["markdown"]).read_text(encoding="utf-8")
    assert "<!doctype html>" in Path(paths["html"]).read_text(encoding="utf-8").lower()
    assert json.loads(Path(paths["json"]).read_text(encoding="utf-8")) == diagnosis


def test_report_keeps_language_alternatives_out_of_mandatory_gap_list() -> None:
    diagnosis = _fake_diagnosis()
    row = diagnosis["requirements"][0]
    row.update(quote="Experience in Python or Java", condition_tree={"op": "any", "children": []},
               condition_observations=[{"missing_technical_tags": ["Java"]}],
               related_conditions_supported=True)
    for render in (render_html, render_markdown):
        assert "尚缺原始材料支持的技术条件" not in render(diagnosis)
        assert "待核对的技术记录" not in render(diagnosis)
    row["related_conditions_supported"] = False
    for render in (render_html, render_markdown):
        output = render(diagnosis)
        assert "待核对的技术记录（含替代选项）" in output
        assert "不要求补齐所有列出的技术" in output


def test_report_task_reviews_keep_subject_self_review_scope_sources_and_escape_xss():
    diagnosis = _fake_diagnosis()
    observation = {
        "assessment_id": "review-1", "task_id": "guided-task-1", "route_id": "route-1",
        "anchor_id": "guided-anchor-1", "requirement_ids": ["req-1"],
        "subject": "candidate", "method": "self_task_review", "reviewer": '<img src=x onerror="alert(3)">',
        "candidate_task_result": "passed", "outcome": "passed",
        "context": {"runtime": '<script>alert("context")</script>'},
        "assistance": ["guided examples"], "limits": ["one finite task only"],
        "artifact_evidence_snapshot_id": "artifact-snapshot-1", "artifact_fingerprint": "artifact-fingerprint-1",
        "acceptance_results": [{"criterion": '<svg onload="alert(4)">input check</svg>', "status": "pass",
                                "evidence_refs": [{"material_id": "source-task-1", "snapshot_digest": "source-digest-1",
                                                   "line_start": 3, "line_end": 4, "quote": '<script>alert("quote")</script>'}]}],
    }
    diagnosis["requirements"][0]["task_observations"] = [observation]
    for render in (render_html, render_markdown):
        output = render(diagnosis)
        assert "本任务已观察到通过" in output
        assert "整条岗位要求仍按材料关联状态判读" in output
        assert "自评（self_task_review）" in output and "对象 candidate" in output
        assert "guided examples" in output and "one finite task only" in output
        assert "artifact-snapshot-1" in output and "artifact-fingerprint-1" in output
        assert "source-task-1" in output and "source-digest-1" in output and "行 3–4" in output
        assert "实际检查验收项" in output and "input check" in output
        assert "<script>alert" not in output and "<svg onload" not in output and "<img src=x" not in output
        assert "&lt;script&gt;" in output and "&lt;svg onload=" in output
    observation.update(subject="artifact", method="artifact_review", candidate_task_result=None)
    for render in (render_html, render_markdown):
        output = render(diagnosis)
        assert "产物验收记录；不表示候选人任务通过或能力已证明" in output
        assert "本任务已观察到通过" not in output
        assert "产物评审（artifact_review）" in output
