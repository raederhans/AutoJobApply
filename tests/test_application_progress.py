"""Evidence contracts, golden counts and cross-language dashboard parity."""
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from applypilot import followup
from applypilot.application_progress import collect_progress, register_application, summarize, sync_jobs
from applypilot.progress_import import import_bundle, import_legacy, preview


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def application(conn, key="a", **extra):
    return register_application(conn, source_key=key, company="Example", title="Analyst " + key,
                                submitted_at=extra.pop("submitted_at", "2026-09-01"),
                                submission_basis=extra.pop("submission_basis", "user_confirmed"), **extra)["application_id"]


def event(identity, kind, date="2026-09-02T00:00:00Z", **extra):
    return dict(provider="test", message_id=extra.pop("message_id", (identity or "pending") + kind + date),
                application_id=identity, event_type=kind, occurred_at=date,
                evidence_ref="private-evidence", summary="private-mail-body", **extra)


def golden(conn):
    events = []
    for i in range(100):
        identity = application(conn, str(i))
        if i >= 10:
            events.append(event(identity, "rejected"))
            continue
        for round_, count in [(1, 10), (2, 5), (3, 3)]:
            if i < count:
                events.append(event(identity, "interview_invited", f"2026-09-0{round_ + 2}T00:00:00Z", round=round_))
        if i < 2:
            events.append(event(identity, "offer", "2026-09-06T00:00:00Z"))
        if i == 0:
            events.append(event(identity, "offer_accepted", "2026-09-07T00:00:00Z"))
    followup.import_events(conn, events)
    return events


def test_hundred_application_example_and_replay(conn):
    events = golden(conn)
    before = collect_progress(conn)
    result = summarize(before)
    assert [c["count"] for c in result["funnel"]] == [100, 10, 5, 3, 2, 1]
    assert [c["conversion_rate"] for c in result["funnel"]] == [None, .1, .5, .6, 2 / 3, .5]
    assert result["current"] == {"rejected": 90, "interview_1": 5, "interview_2": 2,
                                  "interview_3": 1, "offer": 1, "accepted": 1}
    assert followup.import_events(conn, events)["duplicates"] == len(events)
    assert summarize(collect_progress(conn))["funnel"] == result["funnel"]
    assert sum(result["current"].values()) == 100
    serialized = json.dumps(before)
    assert "private-mail-body" not in serialized and "private-evidence" not in serialized
    earlier = summarize(before, as_of="2026-09-04T12:00:00Z")
    assert [c["count"] for c in earlier["funnel"]] == [100, 10, 5, 0, 0, 0]


def test_unknown_round_and_missing_intermediate_stages_are_not_invented(conn):
    a, b = application(conn), application(conn, "b")
    followup.import_events(conn, [event(a, "interview"), event(b, "interview", round=3),
                                  event(a, "offer", "2026-09-04T00:00:00Z")])
    result = summarize(collect_progress(conn))
    assert result["unknown_round_count"] == 1
    assert [c["count"] for c in result["funnel"]] == [2, 0, 0, 1, 1, 0]
    assert result["funnel"][4]["conversion_rate"] == 0
    assert result["funnel"][4]["missing_previous"] == 1


def test_sgt_dates_scope_and_unknown_dates(conn):
    application(conn, "a", submitted_at="2026-08-31T16:30:00Z")
    application(conn, "b", submitted_at=None)
    application(conn, "c", submission_basis="reported")
    application(conn, "d", submission_basis="uncertain")
    data = collect_progress(conn)
    assert summarize(data)["total"] == 2
    assert summarize(data, since="2026-09-01", until="2026-09-01")["total"] == 1
    assert summarize(data, until="2026-08-31")["total"] == 0
    assert summarize(data, scope="all")["total"] == 4
    assert summarize(data, scope="verified")["total"] == 0
    with pytest.raises(ValueError):
        summarize(data, since="2026-10-01", until="2026-09-01")


def test_terminal_late_events_conflict_reopen_and_correction(conn):
    a = application(conn)
    followup.import_events(conn, [event(a, "rejected"), event(a, "submission_observed", "2026-09-03T00:00:00Z")])
    assert summarize(collect_progress(conn))["current"] == {"rejected": 1}
    followup.import_events(conn, [event(a, "offer_accepted", "2026-09-04T00:00:00Z")])
    assert summarize(collect_progress(conn))["current"] == {"conflict": 1}
    rejected = next(e for e in followup._events(conn) if e["event_type"] == "rejected")
    followup.import_events(conn, [event(a, "retracted", "2026-09-05T00:00:00Z", supersedes_event_id=rejected["event_id"])])
    assert summarize(collect_progress(conn))["current"] == {"accepted": 1}
    assert "rejected" not in collect_progress(conn)["applications"][0]["reached"]
    followup.import_events(conn, [event(a, "reopened", "2026-09-06T00:00:00Z")])
    assert summarize(collect_progress(conn))["current"] == {"applied": 1}


def test_reschedule_cancel_calendar_uses_only_latest_round(conn):
    conn.execute("CREATE TABLE jobs(url TEXT,company_name TEXT,title TEXT,apply_status TEXT)")
    conn.execute("INSERT INTO jobs VALUES ('https://example.test/1','Example','Analyst','applied')")
    conn.commit()
    a = application(conn, job_url="https://example.test/1")
    followup.import_events(conn, [event(a, "interview_invited", round=1, scheduled_at="2026-10-10T00:00:00Z"),
        event(a, "interview_rescheduled", "2026-09-03T00:00:00Z", round=1, scheduled_at="2026-10-11T00:00:00Z")])
    calendar = followup.export_ics(conn)
    assert len(collect_progress(conn)["applications"]) == 1
    assert calendar.count("BEGIN:VEVENT") == 1 and "20261011T000000Z" in calendar
    followup.import_events(conn, [event(a, "interview_cancelled", "2026-09-04T00:00:00Z", round=1)])
    assert "BEGIN:VEVENT" not in followup.export_ics(conn)


def test_reviewed_receipt_can_confirm_analytics_without_mutating_submission_status(conn):
    conn.execute("CREATE TABLE jobs(url TEXT,company_name TEXT,title TEXT,apply_status TEXT)")
    conn.execute("INSERT INTO jobs VALUES ('https://example.test/1','Example','Analyst','failed')")
    conn.commit()
    sync_jobs(conn)
    a = collect_progress(conn)["applications"][0]["id"]
    assert summarize(collect_progress(conn))["total"] == 0
    followup.import_events(conn, [event(a, "submission_confirmed", submitted_at="2026-09-01")])
    assert summarize(collect_progress(conn))["total"] == 1
    assert conn.execute("SELECT apply_status FROM jobs").fetchone()[0] == "failed"
    old = followup._events(conn)[0]
    followup.import_events(conn, [event(a, "retracted", supersedes_event_id=old["event_id"], message_id="correction")])
    assert summarize(collect_progress(conn))["total"] == 0


def test_legacy_event_schema_upgrade_preserves_identity_and_custom_index(conn):
    conn.execute("""CREATE TABLE followup_events(event_id TEXT PRIMARY KEY,provider TEXT,message_id TEXT,
        occurred_at TEXT,event_type TEXT,payload TEXT,job_url TEXT,resolved_at TEXT,created_at TEXT,
        UNIQUE(provider,message_id))""")
    conn.execute("CREATE INDEX custom_event_date ON followup_events(occurred_at)")
    raw = event(None, "manual_note")
    normalized = followup._normalise_event(raw)
    conn.execute("INSERT INTO followup_events VALUES('old','test',?,?,?,?,NULL,NULL,?)",
                 (raw["message_id"],raw["occurred_at"],raw["event_type"],json.dumps(normalized),raw["occurred_at"]))
    conn.commit()
    followup.import_events(conn, [raw, {**raw, "fact_key": "second"}])
    assert conn.execute("SELECT COUNT(*) FROM followup_events").fetchone()[0] == 2
    assert conn.execute("SELECT event_id FROM followup_events WHERE fact_key=''").fetchone()[0] == "old"
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='custom_event_date'").fetchone()


def test_correction_can_explicitly_reassign_without_double_counting(conn):
    a, b = application(conn), application(conn, "b")
    followup.import_events(conn, [event(a, "offer")])
    original = followup._events(conn)[0]
    followup.import_events(conn, [event(b, "offer", supersedes_event_id=original["event_id"], message_id="correction")])
    data = collect_progress(conn)
    assert summarize(data)["funnel"][4]["count"] == 1
    assert next(item for item in data["applications"] if item["id"] == a)["current"] == "applied"


def test_corrections_inherit_identity_and_reject_conflicting_url(conn):
    a = application(conn)
    with pytest.raises(ValueError, match="disagree"):
        followup.import_events(conn, [event(a, "offer", job_url="https://example.test/other")])
    followup.import_events(conn, [event(a, "offer")])
    old = followup._events(conn)[0]
    correction = event(None, "retracted", supersedes_event_id=old["event_id"])
    assert followup.import_events(conn, [correction])["pending"] == 0
    assert next(e for e in followup._events(conn) if e["event_type"] == "retracted")["application_id"] == a
    assert followup.import_events(conn, [correction])["duplicates"] == 1


def run(status="success", **extra):
    return dict(provider="gmail", run_id=extra.pop("run_id", "one"), status=status,
                attempted_at=extra.pop("attempted_at", "2026-09-05T00:00:00Z"),
                cutoff=extra.pop("cutoff", "2026-09-04T00:00:00Z"), complete=status == "success", **extra)


def test_checkpoint_atomicity_and_failed_provider_does_not_advance(conn):
    a = application(conn)
    import_bundle(conn, {"events": [event(a, "offer")], "sync_runs": [run()]})
    import_bundle(conn, {"sync_runs": [run("failed", run_id="two", attempted_at="2026-09-06T00:00:00Z", cutoff=None)]})
    health = collect_progress(conn)["sources"][0]
    assert health["status"] == "failed" and health["last_successful_cutoff"] == "2026-09-04T00:00:00+00:00"
    with pytest.raises(ValueError):
        import_bundle(conn, {"events": [event(a, "rejected")], "sync_runs": [run(cutoff=None, run_id="bad")]})
    assert len(followup._events(conn)) == 1


def test_preview_does_not_create_or_mutate_live_tables(conn):
    result = preview(conn, import_bundle, {"applications": [{"source_key": "a", "company": "C", "title": "T"}], "sync_runs": [run()]})
    assert result["dry_run"]
    assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_pending_can_resolve_to_external_identity_but_never_silently_reassign(conn):
    a = application(conn)
    followup.import_events(conn, [event(None, "offer")])
    e = followup.pending_events(conn)[0]
    followup.resolve_event(conn, e["event_id"], application_id=a)
    assert followup.pending_events(conn) == []
    assert summarize(collect_progress(conn))["funnel"][4]["count"] == 1
    conn.execute("CREATE TABLE jobs(url TEXT)")
    conn.execute("INSERT INTO jobs VALUES ('https://example.test/elsewhere')")
    with pytest.raises(ValueError, match="cannot be reassigned"):
        followup.resolve_event(conn, e["event_id"], 'https://example.test/elsewhere')


def test_legacy_normalized_url_pending_idempotence_and_jobs_unchanged(conn, tmp_path):
    conn.execute("CREATE TABLE jobs(url TEXT,company_name TEXT,title TEXT,apply_status TEXT)")
    conn.execute("INSERT INTO jobs VALUES ('https://example.test/jobs/1','C','T','applied')")
    conn.commit()
    before = conn.execute("SELECT * FROM jobs").fetchall()
    events = [{"provider": "gmail", "message_id": "one", "kind": "interview", "occurred_at": "2026-09-01T00:00:00Z",
               "matched_job_url": "https://example.test/jobs/1?utm_source=x"},
              {"provider": "gmail", "message_id": "two", "kind": "offer", "occurred_at": "2026-09-02T00:00:00Z", "needs_review": True}]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    first = import_legacy(conn, tmp_path)
    assert first["imported"] == 2 and first["pending"] == 1
    assert import_legacy(conn, tmp_path)["duplicates"] == 2
    assert collect_progress(conn)["pending_count"] == 1
    assert conn.execute("SELECT * FROM jobs").fetchall() == before


def test_multiple_facts_per_message_remain_idempotent(conn):
    a = application(conn)
    one = event(a, "offer", message_id="same", fact_key="offer")
    two = event(a, "recruiter_feedback", message_id="same", fact_key="details")
    assert followup.import_events(conn, [one, two])["imported"] == 2
    assert followup.import_events(conn, [one, two])["duplicates"] == 2


def test_dashboard_model_matches_python_and_flow_conserves_cases(conn, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for dashboard model parity")
    golden(conn)
    data = collect_progress(conn)
    payload = tmp_path / "input.json"
    payload.write_text(json.dumps(data), encoding="utf-8")
    script = Path(__file__).parents[1] / "src/applypilot/frontend/assets/job-apply-pilot/progress.js"
    js = """const model=require(process.argv[1]); const data=JSON.parse(require('fs').readFileSync(process.argv[2]));
      const apps=model.select(data); const flow=model.flow(apps);
      console.log(JSON.stringify({funnel:model.funnel(apps), roots:flow.links.filter(l=>l.source.startsWith('0:')).reduce((a,b)=>a+b.value,0),
      weekly:model.weekly(apps), earlier:model.funnel(model.select(data,{asOf:'2026-09-04T12:00:00Z'}))}));"""
    result = json.loads(subprocess.run([node, "-e", js, str(script), str(payload)], check=True, capture_output=True, text=True).stdout)
    assert [c["count"] for c in result["funnel"]] == [100, 10, 5, 3, 2, 1]
    assert result["roots"] == 100
    assert sum(len(w["applied"]) for w in result["weekly"]) == 100
    assert sum(len(w["interview"]) for w in result["weekly"]) == 10
    assert [c["count"] for c in result["earlier"]] == [100, 10, 5, 0, 0, 0]
    expected = summarize(data)["funnel"]
    for index, actual in enumerate(result["funnel"]):
        for field in ("count", "previous_count", "converted_count", "conversion_rate", "missing_previous"):
            assert actual[field] == expected[index][field]
