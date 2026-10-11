"""Synthetic progress exercises report semantics, privacy and escaping."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from applypilot.career.learning_report import render_html, render_markdown, write_report


def progress():
    first = {
        "task_id": "evidence-a", "title": "先整理产物", "kind": "evidence_collection",
        "skill_id": "programming", "anchor_id": None, "requirement_ids": ["req-a"],
        "depends_on": [], "priority_reasons": ["已有证据优先"], "context": "有限本地情境",
        "deliverable": "产物索引", "assistance_policy": "允许工具整理，保留本人贡献",
        "acceptance": ["标注贡献", "注明未知条件"], "limits": ["不证明整条要求"],
        "estimate_hours": {"min": 1, "max": 3}, "scheduled": False,
        "deferred_reason": "budget_unknown_order_only", "resources": [], "resources_note": "阅读不等于通过",
        "state": "completion_reported", "ready": True, "blocked_by": [], "submission": None, "assessment": None,
    }
    second = {**deepcopy(first), "task_id": "verify-a", "title": "再验证任务", "kind": "verification",
              "anchor_id": "programming.guided", "depends_on": ["evidence-a"], "state": "pending",
              "ready": False, "blocked_by": ["evidence-a"]}
    return {
        "route": {"route_id": "route:a", "target_id": "target-a", "diagnosis_id": "diagnosis-a",
                  "parent_route_id": "route-old", "created_at": "2026-01-31", "tasks": [first, second],
                  "topological_order": ["evidence-a", "verify-a"], "budget": {"mode": "order_only", "total_hours": None},
                  "boundaries": ["作者任务量规不测量通用等级"], "private": "route-secret"},
        "tasks": [second, first], "revision": 3, "stale": False, "stale_reasons": [],
        "diagnosis_refresh_needed": False, "summary": {"completion_reported": 1, "pending": 1},
        "history": [{"event_type": "marked_done", "task_id": "evidence-a", "reason": "整理完成",
                     "payload": {"registry_path": "PRIVATE-REGISTRY-PATH", "api_key": "PRIVATE-SECRET"}}],
        "historical_routes": [{"route_id": "route-old", "diagnosis_id": "diagnosis-old", "created_at": "2026-01-01"}],
    }


def test_report_topological_order_keeps_dependencies_self_report_and_unknown_budget():
    data = progress()
    for render in (render_html, render_markdown):
        output = render(data)
        assert output.index("先整理产物") < output.index("再验证任务")
        assert "已自报完成，尚未通过验收" in output
        assert "当前阻塞" in output and "evidence" in output
        assert "仅提供依赖与优先顺序" in output and "不生成日历或默认学习期限" in output
        assert "不授予通用技能等级" in output and "整条岗位要求或资格" in output
        assert "原始验收项" in output and "标注贡献" in output
        assert "route-secret" not in output and "PRIVATE-REGISTRY-PATH" not in output and "PRIVATE-SECRET" not in output
        assert "历史" in output and "diagnosis" in output
    rendered = render_html(data)
    assert '<details class="task">' in rendered and "<summary>" in rendered
    assert "<script" not in rendered and "<iframe" not in rendered and "<form" not in rendered


def test_report_review_scope_sources_self_review_and_artifact_boundary():
    data = progress()
    task = data["tasks"][0]
    task.update(state="passed", assessment={
        "id": "review-a", "subject": "candidate", "method": "self_task_review", "reviewer": "candidate",
        "outcome": "passed", "context": "one fixture", "assistance": ["guided example"],
        "artifact_evidence_snapshot_id": "snapshot-artifact-a", "artifact_fingerprint": "artifact-fingerprint-a",
        "acceptance_results": [{"criterion": "标注贡献", "status": "pass", "evidence_refs": [
            {"material_id": "output-a", "quote": "fictional output", "line_start": 3, "line_end": 4,
             "snapshot_digest": "material-digest-a"}]}],
    })
    for render in (render_html, render_markdown):
        output = render(data)
        assert "自评" in output and "对象" in output and "candidate" in output
        assert "guided example" in output and "实际检查验收项" in output
        assert "snapshot" in output and "fictional output" in output and "output" in output
        assert "不等于整条岗位要求满足" in output
    task["assessment"].update(subject="artifact", method="artifact_review", reviewer="assistant")
    for render in (render_html, render_markdown):
        output = render(data)
        assert "产物验收记录；不能证明候选人个人能力" in output
        assert "候选人任务观察仅限本任务" not in output


def test_report_escapes_task_review_resources_and_markdown_syntax():
    data = progress()
    task = data["tasks"][0]
    task.update(title='<script>alert("task")</script>', context='<img src=x onerror="alert(1)">',
                acceptance=['[danger](javascript:alert(1))', '<svg onload="alert(2)">'],
                resources=[{"title": '<img src=x onerror="alert(3)">', "url": "javascript:alert(4)"},
                           {"title": "Official reference", "url": "https://example.org/docs", "limits": "limited reading"}])
    task["assessment"] = {"subject": "artifact", "method": "artifact_review", "reviewer": '<script>alert("review")</script>',
                          "context": {"fixture": '<img src=x onerror="alert(5)">'}, "acceptance_results": [
                              {"criterion": '<svg onload="alert(6)">', "status": "pass", "evidence_refs": [
                                  {"material_id": "fake-output", "quote": '<script>alert("quote")</script>'}]}]}
    output = render_html(data)
    assert '<script>alert' not in output and '<img src=x' not in output and '<svg onload=' not in output
    assert '&lt;script&gt;' in output and '&lt;img src=x' in output and '&lt;svg onload=' in output
    assert 'href="javascript:' not in output and 'href="https://example.org/docs"' in output
    assert 'src="https:' not in output and '<link ' not in output
    markdown = render_markdown(data)
    assert "<script>alert" not in markdown and "<img src=x" not in markdown
    assert "[danger](javascript:" not in markdown and r"\[danger\]" in markdown


def test_stale_refresh_budget_and_empty_route_are_explicit():
    data = progress()
    data.update(stale=True, stale_reasons=["target_changed"], diagnosis_refresh_needed=True)
    data["route"]["budget"] = {"total_hours": 20, "weekly_hours": 5, "weeks": 4,
                               "reserved_max_hours": 16, "remaining_hours": 4}
    for render in (render_html, render_markdown):
        output = render(data)
        assert "已过期" in output and "不能作为当前输入的结论" in output
        assert "需要重新诊断" in output and "每周小时" in output and "剩余预算小时" in output
    data["tasks"] = []
    for render in (render_html, render_markdown):
        assert "当前路线没有已编排任务；这不表示岗位要求已满足" in render(data)


def test_write_report_complete_json_safe_name_and_no_input_mutation(tmp_path):
    data = progress()
    data["route"]["route_id"] = "../../outside/route"
    before = deepcopy(data)
    paths = write_report(data, str(tmp_path / "reports"))
    assert set(paths) == {"markdown", "html", "json"}
    assert all(Path(path).parent == tmp_path / "reports" for path in paths.values())
    assert all(Path(path).is_file() for path in paths.values())
    assert json.loads(Path(paths["json"]).read_text(encoding="utf-8")) == data
    assert "PRIVATE-REGISTRY-PATH" in Path(paths["json"]).read_text(encoding="utf-8")
    assert data == before
    with pytest.raises(TypeError, match="progress must be a dict"):
        write_report([], tmp_path)


def test_historical_review_inherited_source_and_unplanned_requirements_are_visible():
    data = progress()
    data["tasks"][0].update(assessment_current=False, inherited_from="previous-route", assessment={
        "assessment_id": "historical-review", "route_id": "review-route", "method": "self_task_review",
        "subject": "candidate", "outcome": "passed", "acceptance_results": []})
    data["route"].update(excluded_requirements=[{"requirement_id": "unknown-condition", "reason": "system_coverage_issue"}],
                         branch_choices=[{"requirement_id": "alternative-condition", "op": "any", "selected_children": [0],
                                          "limits": "路线只选一个学习分支，不证明要求满足"}])
    for render in (render_html, render_markdown):
        output = render(data)
        assert "历史评审；当前输入不适用" in output
        assert "继承评审来源路线" in output and "评审原路线" in output
        assert "未编排要求" in output and "学习替代分支" in output
        assert "路线只选一个学习分支，不证明要求满足" in output
