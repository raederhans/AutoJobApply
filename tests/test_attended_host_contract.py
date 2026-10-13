"""Adversarial owner/transport contracts; browser acceptance is in test_attended_host."""
import hashlib
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_attended_recovery_consistency import env, inspection, observation, snapshot  # noqa: F401

from applypilot.apply.visual_bridge import write_host_metadata


@pytest.fixture
def transport(env, tmp_path):  # noqa: F811 - reuse the shared pytest fixture
    conn, run, profile, pdf, base = env
    root = tmp_path / "bridge"
    host = write_host_metadata(root, session_id="host-1", token_epoch="epoch-1", surface="browser",
        target={"runtime": "iab", "tab_id": "tab-1", "application_url": base["job_url"]})
    host["attended_continuity"] = 1
    (root / "host.json").write_text(json.dumps(host))
    for name in ("reviews", "permits", "used", "completed"):
        (root / "continuity" / name).mkdir(parents=True)
    run("host-attach", bridge_directory=str(root))
    return conn, run, profile, pdf, base, root, host


def review(transport, change=None):
    _, _, _, _, base, root, host = transport
    request_id, review_id = str(uuid4()), str(uuid4())
    request = {**host, "request_id": request_id, "operation": "fill_control", "observation_id": "observed",
               "arguments": {"field_key": "email", "value": "test@example.test"}, "deadline_at": time.time() + 60}
    request.update(change or {})
    raw = json.dumps(request)
    (root / "pending" / f"{request_id}.json").write_text(raw)
    envelope = {"schema_version": 1, "review_id": review_id, "attempt_id": base["attempt_id"],
        "job_url": base["job_url"], "host_token_epoch": "epoch-1", "request": request,
        "request_sha256": hashlib.sha256(raw.encode()).hexdigest(), "inspection": inspection(),
        "observed_at": datetime.now(UTC).isoformat()}
    (root / "continuity/reviews" / f"{review_id}.json").write_text(json.dumps(envelope))
    return request_id, review_id


def admit(transport, review_id):
    return transport[1]("host-review", bridge_directory=str(transport[5]), review_id=review_id,
                        bindings=[{"field_key": "email", "source": {"kind": "profile", "path": ["personal", "email"]}}])


@pytest.mark.parametrize("change", [
    {"token_epoch": "other"}, {"session_id": "other"}, {"phase": "submit"},
    {"deadline_at": 1}, {"target": {"runtime": "iab", "tab_id": "other"}},
    {"arguments": {"field_key": "email", "value": "invented@example.test"}},
])
def test_changed_binding_or_invented_fact_never_publishes_a_permit(transport, change):
    _, review_id = review(transport, change)
    with pytest.raises(ValueError):
        admit(transport, review_id)
    assert not list((transport[5] / "continuity/permits").glob("*.json"))


def test_request_change_after_host_review_cannot_receive_a_permit(transport):
    request_id, review_id = review(transport)
    pending = transport[5] / "pending" / f"{request_id}.json"
    pending.write_text(pending.read_text() + " ")
    with pytest.raises(ValueError, match="binding mismatch"):
        admit(transport, review_id)


def test_single_outstanding_request_blocks_checkpoint_and_unused_permit_is_revoked(transport):
    conn, run, _, _, _, root, _ = transport
    request_id, review_id = review(transport)
    state = admit(transport, review_id)
    assert state["host_continuity"]["pending"]["request_id"] == request_id
    _, another = review(transport)
    with pytest.raises(ValueError, match="outstanding"):
        admit(transport, another)
    with pytest.raises(ValueError, match="outstanding"):
        run("checkpoint", snapshot=snapshot(), **observation())
    with pytest.raises(ValueError, match="outstanding"):
        run("recover")  # Direct JSON cannot bypass this transport's outstanding permit.
    state = run("host-reconcile", bridge_directory=str(root), request_id=request_id)
    assert state["recovery"]["unknown_effect_latched"] is True
    assert not (root / "continuity/permits" / f"{request_id}.json").exists()
    assert conn.execute("SELECT COUNT(*) FROM application_submission_gates").fetchone()[0] == 0


def test_changed_frozen_materials_revoke_outstanding_permission(transport):
    _, run, _, pdf, _, root, _ = transport
    request_id, review_id = review(transport)
    admit(transport, review_id)
    pdf.write_bytes(b"different synthetic bytes")
    state = run("host-complete", bridge_directory=str(root), request_id=request_id)
    assert state["phase"] == "material_changed"
    assert not (root / "continuity/permits" / f"{request_id}.json").exists()


def test_claimed_checkpoint_and_existing_episode_cannot_grant_new_input(transport):
    conn, run, *_ = transport
    run("checkpoint", snapshot=snapshot(), **observation())
    run("claim", snapshot=snapshot())
    _, review_id = review(transport)
    assert "immutable" in admit(transport, review_id)["next_action"]
    conn.execute("CREATE TABLE application_episodes(attempt_id TEXT,state TEXT,command_sequence INTEGER,result_sequence INTEGER)")
    conn.execute("INSERT INTO application_episodes VALUES (?, 'recovering', 2, 1)", (transport[4]["attempt_id"],))
    conn.commit()
    assert admit(transport, review_id)["next_action"] == "episode_effect_unresolved"
    assert not list((transport[5] / "continuity/permits").glob("*.json"))


def test_existing_cli_dispatches_host_review_and_commits_before_permit(transport, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from applypilot import cli, config

    conn, _, profile, _, base, root, _ = transport
    request_id, review_id = review(transport)
    request = tmp_path / "review-request.json"
    request.write_text(json.dumps({**base, "action": "host-review", "bridge_directory": str(root),
        "review_id": review_id, "bindings": [{"field_key": "email", "source": {"kind": "profile", "path": ["personal", "email"]}}]}))
    monkeypatch.setattr(config, "load_profile", lambda: profile)
    db = conn.execute("PRAGMA database_list").fetchone()[2]
    result = CliRunner().invoke(cli.app, ["attended-application", "--db", db, "--request-file", str(request)])
    assert result.exit_code == 0, result.output
    state = json.loads(conn.execute("SELECT payload FROM attended_applications").fetchone()[0])
    permit = json.loads((root / "continuity/permits" / f"{request_id}.json").read_text())
    assert state["host_continuity"]["pending"] == permit
    assert state["consistency_contract"]["items"][0]["field_key"] == "email"


@pytest.mark.parametrize("interruption", ["reconcile", "submit_latch"])
def test_delayed_publisher_cannot_resurrect_permission_after_another_owner_parks(transport, monkeypatch, interruption):
    import sqlite3

    from applypilot.apply import attended_application, attended_host

    conn, _, profile, _, base, root, _ = transport
    request_id, review_id = review(transport)
    publish = attended_host.publish

    def interrupted_publish(connection, delivery, current_profile):
        # A genuinely separate SQLite connection wins the post-commit window.
        other = sqlite3.connect(conn.execute("PRAGMA database_list").fetchone()[2])
        other.row_factory = sqlite3.Row
        assert other is not connection
        try:
            if interruption == "reconcile":
                attended_application.execute(other, {**base, "action": "host-reconcile",
                    "bridge_directory": str(root), "request_id": request_id}, profile)
            else:
                other.execute("UPDATE application_attempts SET submit_started=1")
                other.commit()
        finally:
            other.close()
        publish(connection, delivery, current_profile)

    monkeypatch.setattr(attended_host, "publish", interrupted_publish)
    with pytest.raises(ValueError, match="before publication"):
        admit(transport, review_id)
    assert not (root / "continuity/permits" / f"{request_id}.json").exists()
    state = transport[1]("resume")
    assert state["next_action"] in {"manual_effect_reconciliation_no_replay", "reconcile_receipt_only"}
