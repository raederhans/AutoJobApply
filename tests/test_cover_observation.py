from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from applypilot import single_job
from applypilot.apply.visual_bridge import VisualBridgeError, write_host_metadata
from applypilot.database import init_db

URL = "https://www.careers-page.com/example/job/ABC123/apply"
JOB = "https://www.linkedin.com/jobs/view/123456789"


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    conn = init_db(db)
    conn.execute(
        "INSERT INTO jobs (url, application_url, title, company_name, eligibility_status, cover_letter_status) "
        "VALUES (?, ?, 'Intern', 'Example', 'eligible', 'stale')", (JOB, URL),
    )
    conn.commit()
    conn.close()

    def connect():
        result = sqlite3.connect(db)
        result.row_factory = sqlite3.Row
        return result

    monkeypatch.setattr(single_job, "get_connection", connect)
    bridge = tmp_path / "bridge"
    target = {"runtime": "iab", "tab_id": "tab-1", "application_url": URL}
    write_host_metadata(bridge, session_id="host-1", token_epoch="epoch-1", surface="browser", target=target)
    data = {
        "source": "attending_host", "observed_at": datetime.now(UTC).isoformat(),
        "evidence_refs": ["capture-fixture"], "all_form_checked": True,
        "session_id": "host-1", "target": target, "phase": "prepare", "submission_authorized": False,
        "content": [
            {"type": "text", "text": json.dumps({"tab_id": "tab-1", "page_url": URL})},
            {"type": "text", "text": '- generic: "Name: *"\n- generic: "Email: *"\n- generic: "Cover Letter:"'},
            {"type": "text", "text": json.dumps({"form_state": {
                "page_url": URL, "coverage": {"scope": "visible_top_document_open_shadow", "iframe_count": 0},
                "fields": [
                    {"field_key": "name", "label": "Full Name", "required": True, "required_source": "visible_label"},
                    {"field_key": "email", "label": "Email", "required": True, "required_source": "visible_label"},
                    {"field_key": "cover", "label": "Cover Letter", "required": False, "required_source": "not_asserted"},
                ],
            }})},
        ],
        "cover_letter": {"status": "optional", "operator_attested": True, "field_keys": ["cover"],
                         "basis": "visible_required_marker_convention", "evidence_text": "Cover Letter:"},
    }
    observation = tmp_path / "observation.json"

    def save():
        observation.write_text(json.dumps(data), encoding="utf-8")

    def stored():
        connection = connect()
        result = dict(connection.execute("SELECT * FROM jobs WHERE url=?", (JOB,)).fetchone())
        connection.close()
        return result

    save()
    return bridge, observation, data, save, stored, connect


def change_form(data, mutate):
    report = json.loads(data["content"][2]["text"])
    mutate(report["form_state"])
    data["content"][2]["text"] = json.dumps(report)


def test_bound_optional_observation_resolves_stale_cover_without_fake_preview(env):
    bridge, observation, _, _, stored, _ = env
    result = single_job.mark_cover_letter_not_required_for_url(
        JOB, verified_by="codex_root", bridge_dir=bridge, observation_file=observation,
    )
    assert result["status"] == "not_required"
    assert result["observation"]["status"] == "optional"
    row = stored()
    assert row["apply_status"] is None
    assert row["cover_letter_status"] == "not_required"
    assert row["cover_letter_error"] is None
    refs = json.loads(row["cover_letter_evidence_sources"])
    assert "capture-fixture" in refs
    assert any("host-1" in ref and "observation_sha256" in ref for ref in refs)
    assert "Full Name" not in row["cover_letter_evidence_sources"]


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(source="worker"),
    lambda d: d.update(all_form_checked=False),
    lambda d: d.update(session_id="other-host"),
    lambda d: d["target"].update(tab_id="other-tab"),
    lambda d: d.update(observed_at=(datetime.now(UTC) - timedelta(minutes=6)).isoformat()),
    lambda d: d.update(evidence_refs=[]),
    lambda d: d["cover_letter"].update(operator_attested=False),
    lambda d: d["cover_letter"].update(field_keys=["other"]),
    lambda d: d["cover_letter"].update(evidence_text="Cover Letter optional"),
    lambda d: d["cover_letter"].update(basis="explicit_optional_text"),
    lambda d: change_form(d, lambda f: f.update(page_url=URL + "?otherjob=1")),
    lambda d: change_form(d, lambda f: f["coverage"].update(iframe_count=1)),
    lambda d: change_form(d, lambda f: f["fields"][2].update(required=True)),
    lambda d: change_form(d, lambda f: f["fields"][2].pop("required_source")),
    lambda d: change_form(d, lambda f: f["fields"][0].update(required_source="native")),
    lambda d: d["cover_letter"].update(status="absent", field_keys=[]),
])
def test_unbound_stale_incomplete_or_unreviewed_observations_leave_cover_stale(env, mutate):
    bridge, observation, data, save, stored, _ = env
    mutate(data)
    save()
    with pytest.raises((TypeError, ValueError, VisualBridgeError)):
        single_job.mark_cover_letter_not_required_for_url(JOB, bridge_dir=bridge, observation_file=observation)
    assert stored()["cover_letter_status"] == "stale"
    assert stored()["apply_status"] is None


@pytest.mark.parametrize("status", ["paused", "stopped"])
def test_inactive_host_does_not_admit_cover_observation(env, status):
    bridge, observation, data, _, stored, _ = env
    write_host_metadata(bridge, session_id="host-1", token_epoch="epoch-1", surface="browser",
                        target=data["target"], status=status)
    with pytest.raises(VisualBridgeError, match="not active"):
        single_job.mark_cover_letter_not_required_for_url(JOB, bridge_dir=bridge, observation_file=observation)
    assert stored()["cover_letter_status"] == "stale"


def test_explicit_optional_wording_also_requires_the_actual_page_text(env):
    bridge, observation, data, save, _, _ = env
    data["content"][1]["text"] = '- generic: "Cover Letter (optional):"'
    data["cover_letter"].update(basis="explicit_optional_text", evidence_text="Cover Letter (optional):")
    save()
    assert single_job.mark_cover_letter_not_required_for_url(
        JOB, bridge_dir=bridge, observation_file=observation,
    )["observation"]["basis"] == "explicit_optional_text"


def test_absent_cover_requires_complete_form_without_cover_fields_or_page_text(env):
    bridge, observation, data, save, _, _ = env
    change_form(data, lambda f: f["fields"].pop())
    data["content"][1]["text"] = '- generic: "Name: *"\n- generic: "Email: *"'
    data["cover_letter"] = {"status": "absent", "operator_attested": True, "field_keys": []}
    save()
    assert single_job.mark_cover_letter_not_required_for_url(
        JOB, bridge_dir=bridge, observation_file=observation,
    )["observation"]["status"] == "absent"


@pytest.mark.parametrize("status", ["applied", "applying", "in_progress", "submission_uncertain"])
def test_active_or_submitted_jobs_do_not_change_cover_readiness(env, status):
    bridge, observation, _, _, stored, connect = env
    conn = connect()
    conn.execute("UPDATE jobs SET apply_status=? WHERE url=?", (status, JOB))
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="active applications"):
        single_job.mark_cover_letter_not_required_for_url(JOB, bridge_dir=bridge, observation_file=observation)
    assert stored()["cover_letter_status"] == "stale"


def test_cli_uses_bound_observation_before_authorization(env, monkeypatch):
    from typer.testing import CliRunner

    from applypilot import cli

    bridge, observation, _, _, stored, _ = env
    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    result = CliRunner().invoke(cli.app, ["mark-cover-not-required", "--url", JOB,
                                        "--bridge-dir", str(bridge), "--observation-file", str(observation)])
    assert result.exit_code == 0, result.output
    assert stored()["cover_letter_status"] == "not_required"
    assert stored()["apply_status"] is None


def test_observation_options_are_paired_and_old_preview_requirement_remains(env):
    bridge, _, _, _, _, _ = env
    with pytest.raises(ValueError, match="supplied together"):
        single_job.mark_cover_letter_not_required_for_url(JOB, bridge_dir=bridge)
    with pytest.raises(ValueError, match="only after a successful browser preview"):
        single_job.mark_cover_letter_not_required_for_url(JOB)


@pytest.mark.parametrize("change", ["owner", "description", "cover"])
def test_job_change_during_observation_is_not_overwritten(env, monkeypatch, change):
    from applypilot.apply import cover_observation

    bridge, observation, _, _, stored, connect = env
    validate = cover_observation.validate_cover_observation

    def validate_with_concurrent_change(*args):
        result = validate(*args)
        other = connect()
        if change == "owner":
            other.execute(
                "UPDATE jobs SET apply_status='in_progress', agent_id='other-owner', apply_task_id='other-attempt' "
                "WHERE url=?", (JOB,),
            )
        elif change == "description":
            other.execute("UPDATE jobs SET full_description='Revised job requirements' WHERE url=?", (JOB,))
        else:
            other.execute(
                "UPDATE jobs SET cover_letter_status='human_approved', cover_letter_approved_by='other-reviewer' "
                "WHERE url=?", (JOB,),
            )
        other.commit()
        other.close()
        return result

    monkeypatch.setattr(cover_observation, "validate_cover_observation", validate_with_concurrent_change)
    with pytest.raises(ValueError, match="Job changed during cover observation"):
        single_job.mark_cover_letter_not_required_for_url(JOB, bridge_dir=bridge, observation_file=observation)
    row = stored()
    assert row["cover_letter_status"] == ("human_approved" if change == "cover" else "stale")
    assert row["cover_letter_evidence_sources"] is None
    if change == "owner":
        assert row["apply_status"] == "in_progress"
        assert row["agent_id"] == "other-owner"
        assert row["apply_task_id"] == "other-attempt"
    elif change == "description":
        assert row["full_description"] == "Revised job requirements"
    else:
        assert row["cover_letter_approved_by"] == "other-reviewer"
