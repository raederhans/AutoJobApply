"""Synthetic public bundles only; no candidate records or copied job descriptions."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from applypilot import database
from applypilot.career.contracts import BundleValidationError, InputChangedError
from applypilot.career.importer import import_bundle
from applypilot.storage import career
from applypilot.storage.database_migrations import (
    DATABASE_SCHEMA_COMPONENT,
    read_database_schema_version,
    run_database_migrations,
)
from applypilot.storage.transactions import write_transaction


def _json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_bundle(root: Path) -> Path:
    """Build a tiny reusable public research bundle in the caller's temporary root."""
    job = {"id": "example-one", "company": "Example", "platform_job_id": "one",
           "title": "Example developer", "url": "https://jobs.example.test/one",
           "role_family": "software", "locations": ["Example City"], "region": "EX",
           "native_level": "Junior", "employment_type": "full-time",
           "observed_at": "2026-01-01", "content_status": "full_text",
           "availability": "listed_at_observation",
           "snapshot_path": "companies/example/snapshots/one.txt",
           "raw_source_paths": ["companies/example/raw/one.json"],
           "requirements": [{"id": "example-one-r001", "text": "Build small tools.",
                             "source_section": "Responsibilities", "skill_ids": ["programming"]}]}
    _json(root / "companies/jobs.json", {"schema_version": "1.0", "companies": [
        {"company_id": "example", "name": "Example", "aliases": ["Example", "Example Company"],
         "domains": ["example.test"]}], "jobs": [job]})
    _json(root / "companies/example/raw/one.json", {"description": "<p>Build small tools.</p>"})
    (root / job["snapshot_path"]).parent.mkdir(parents=True, exist_ok=True)
    (root / job["snapshot_path"]).write_text("Build small tools.\n", encoding="utf-8")
    stages = ["understand", "guided", "routine", "adaptive"]
    skill = {"id": "programming", "name_zh": "Programming", "role_families": ["software"],
             "provenance": "pilot_authored", "source_refs": [{"source_id": "framework"}],
             "native_mappings": [], "prerequisites": [], "anchors": [
                 {"id": f"programming.{stage}", "stage": stage, "task": "Explain a small tool",
                  "context": "Synthetic example", "deliverable": "A note", "acceptance": ["Names the input"],
                  "support": "Record assistance", "limits": "Only this task", "provenance": "pilot_authored"}
                 for stage in stages], "learning_task": {"anchor_id": "programming.guided"}}
    _json(root / "skills/rubrics.json", {"schema_version": "1.0", "provenance": "pilot_authored",
          "sources": [{"id": "framework", "snapshot_paths": ["skills/snapshots/framework.txt"]}],
          "skills": [skill]})
    (root / "skills/snapshots").mkdir(parents=True, exist_ok=True)
    (root / "skills/snapshots/framework.txt").write_text("Synthetic task framework", encoding="utf-8")
    # These files must never be discovered or copied into SQLite.
    _json(root / "candidate/evidence.json", {"private_marker": "DO_NOT_IMPORT_CANDIDATE"})
    (root / "run.py").write_text("raise RuntimeError('DO_NOT_RUN_SCRIPT')", encoding="utf-8")
    return root


@pytest.fixture
def bundle(tmp_path):
    return make_bundle(tmp_path / "bundle")


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "test.db"
    connection = database.init_db(path)
    yield connection
    database.close_connection(path)


def _edit(root: Path, relative: str, mutate) -> None:
    path = root / relative
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    _json(path, payload)


def _counts(connection):
    return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
        "career_companies", "career_source_snapshots", "career_catalogs", "career_targets", "career_diagnoses"))


def test_import_is_idempotent_preserves_public_sources_and_application_boundary(conn, bundle):
    first = import_bundle(conn, bundle)
    counts = _counts(conn)
    second = import_bundle(conn, bundle)
    assert first["jobs_inserted"] == 1 and first["catalog_inserted"]
    assert first["skills"] == 1 and first["anchors"] == 4
    assert second["snapshots_inserted"] == 0 and not second["catalog_inserted"]
    assert counts == _counts(conn)
    job = career.get_job(conn, "example-one")
    assert job["availability"] == "listed_at_observation"
    assert job["content_status"] == "full_text" and "verification_status" not in job
    assert job["company_id"] == "example"
    assert career.list_jobs(conn, company_id="other") == []
    source_id = job["public_source_snapshot_ids"]["companies/example/raw/one.json"]
    assert career.get_snapshot(conn, source_id)["raw_bytes"] == (bundle / "companies/example/raw/one.json").read_bytes()
    assert career.load_catalog(conn)["skills"][0]["provenance"] == "pilot_authored"
    for row in conn.execute("SELECT raw_bytes,payload_json FROM career_source_snapshots"):
        assert b"DO_NOT_IMPORT_CANDIDATE" not in row[0]
        assert "DO_NOT_RUN_SCRIPT" not in row[1]
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_changes_append_versions_and_old_input_remains_readable(conn, bundle):
    first = import_bundle(conn, bundle)
    old_job = career.get_job(conn, "example-one")
    _edit(bundle, "companies/jobs.json", lambda data: data["jobs"][0].update(title="Changed title"))
    _edit(bundle, "skills/rubrics.json", lambda data: data["skills"][0].update(name_zh="Updated label"))
    changed = import_bundle(conn, bundle)
    assert changed["catalog_id"] != first["catalog_id"]
    assert changed["jobs_inserted"] == 1
    assert len(career.list_jobs(conn)) == 1
    assert career.get_job(conn, "example-one")["title"] == "Changed title"
    assert career.get_job(conn, "example-one", snapshot_id=old_job["source_snapshot_id"])["title"] == "Example developer"
    assert career.load_catalog(conn, first["catalog_id"])["skills"][0]["name_zh"] == "Programming"


@pytest.mark.parametrize("relative", ["../outside.txt", "C:/outside.txt", "companies/../candidate/evidence.json",
                                     "candidate/evidence.json", "run.py", "companies/example/raw/evil.py"])
def test_path_boundary_rejects_unsafe_or_private_files(conn, bundle, relative):
    _edit(bundle, "companies/jobs.json", lambda data: data["jobs"][0].update(snapshot_path=relative))
    before = _counts(conn)
    with pytest.raises(BundleValidationError):
        import_bundle(conn, bundle)
    assert _counts(conn) == before


def test_symlink_cannot_escape_or_redirect_to_private_data(conn, bundle, tmp_path):
    source = bundle / "companies/example/snapshots/one.txt"
    outside = tmp_path / "outside.txt"
    outside.write_text("Build small tools.", encoding="utf-8")
    source.unlink()
    try:
        source.symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks requires privileges on this Windows host")
    with pytest.raises(BundleValidationError, match="escapes"):
        import_bundle(conn, bundle)
    source.unlink()
    source.symlink_to(bundle / "candidate/evidence.json")
    with pytest.raises(BundleValidationError, match="public source boundary"):
        import_bundle(conn, bundle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory junction")
def test_windows_junction_cannot_escape_bundle(conn, bundle, tmp_path):
    """Junctions need no symlink privilege and exercise Windows resolve directly."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "one.txt").write_text("Build small tools.", encoding="utf-8")
    source_dir = bundle / "companies/example/snapshots"
    source_dir.rename(source_dir.with_name("original-snapshots"))
    result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(source_dir), str(outside)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        pytest.skip("Windows host did not permit a temporary directory junction")
    with pytest.raises(BundleValidationError, match="escapes"):
        import_bundle(conn, bundle)


@pytest.mark.parametrize("kind", ["job", "skill", "source", "requirement", "anchor"])
def test_duplicate_ids_fail_before_any_write(conn, bundle, kind):
    if kind in {"job", "requirement"}:
        def mutate(data):
            rows = data["jobs"] if kind == "job" else data["jobs"][0]["requirements"]
            rows.append(rows[0].copy())
        _edit(bundle, "companies/jobs.json", mutate)
    else:
        def mutate(data):
            rows = data[{"skill": "skills", "source": "sources"}.get(kind)] if kind != "anchor" else data["skills"][0]["anchors"]
            rows.append(rows[0].copy())
        _edit(bundle, "skills/rubrics.json", mutate)
    with pytest.raises(BundleValidationError, match="duplicate"):
        import_bundle(conn, bundle)
    assert _counts(conn) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("kind", ["prerequisite", "cycle", "source", "anchor", "skill", "quote", "company", "availability"])
def test_bad_references_and_unearned_authority_rejected(conn, bundle, kind):
    if kind in {"prerequisite", "cycle", "source", "anchor"}:
        def mutate(data):
            skill = data["skills"][0]
            if kind in {"prerequisite", "cycle"}:
                skill["prerequisites"] = ["missing" if kind == "prerequisite" else "programming"]
            elif kind == "source":
                skill["source_refs"][0]["source_id"] = "missing"
            else:
                skill["learning_task"]["anchor_id"] = "missing"
        _edit(bundle, "skills/rubrics.json", mutate)
    else:
        def mutate(data):
            job = data["jobs"][0]
            if kind == "skill":
                job["requirements"][0]["skill_ids"] = ["missing"]
            elif kind == "quote":
                job["requirements"][0]["text"] = "Invented requirement."
            elif kind == "company":
                job["company"] = "Example-ish"
            else:
                job["availability"] = "official_open"
        _edit(bundle, "companies/jobs.json", mutate)
    with pytest.raises(BundleValidationError):
        import_bundle(conn, bundle)
    assert _counts(conn) == (0, 0, 0, 0, 0)


def test_html_and_json_quotation_normalization(conn, bundle):
    _json(bundle / "companies/example/raw/one.json", {"description": "<p>Build <b>small</b>\u00a0tools.</p>"})
    assert import_bundle(conn, bundle)["jobs_inserted"] == 1
    _json(bundle / "companies/example/raw/one.json", {"description": "<p>Build <b>sm</b>all tools.</p>"})
    assert import_bundle(conn, bundle)["jobs_inserted"] == 1
    # The derived body alone cannot authorize an invented raw-source quotation.
    _json(bundle / "companies/example/raw/one.json", {"description": "Unrelated text."})
    with pytest.raises(BundleValidationError, match="quotation"):
        import_bundle(conn, bundle)


def test_transaction_failure_rolls_back_import_and_preserves_caller_work(conn, bundle):
    conn.execute("CREATE TABLE caller_work (note TEXT)")
    conn.commit()
    conn.execute("INSERT INTO caller_work VALUES ('keep')")
    conn.execute("""CREATE TEMP TRIGGER fail_career_import BEFORE INSERT ON career_catalogs
                    BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        import_bundle(conn, bundle)
    assert conn.in_transaction
    assert conn.execute("SELECT note FROM caller_work").fetchone()[0] == "keep"
    assert _counts(conn) == (0, 0, 0, 0, 0)
    conn.rollback()


def test_existing_company_alias_cannot_be_merged(conn, bundle):
    import_bundle(conn, bundle)
    before = _counts(conn)
    _edit(bundle, "companies/jobs.json", lambda data: data["companies"][0].update(company_id="another"))
    with pytest.raises(BundleValidationError, match="alias"):
        import_bundle(conn, bundle)
    assert _counts(conn) == before


def test_legacy_upgrade_and_future_schema_reject_writes(tmp_path, bundle):
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    run_database_migrations(legacy, database._DATABASE_MIGRATIONS[:3])
    assert read_database_schema_version(legacy) == 3
    assert not career.list_jobs(legacy)
    legacy.close()
    conn = database.init_db(path)
    try:
        assert read_database_schema_version(conn) == database.DATABASE_SCHEMA_VERSION
        assert import_bundle(conn, bundle)["jobs_inserted"] == 1
        with write_transaction(conn):
            conn.execute("UPDATE applypilot_schema_version SET version=? WHERE component=?",
                         (database.DATABASE_SCHEMA_VERSION + 1, DATABASE_SCHEMA_COMPONENT))
        before = _counts(conn)
        with pytest.raises(RuntimeError, match="newer"):
            import_bundle(conn, bundle)
        assert _counts(conn) == before
    finally:
        database.close_connection(path)
    probe = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    before = probe.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
    probe.close()
    with pytest.raises(RuntimeError, match="newer"):
        database.init_db(path)
    probe = sqlite3.connect(path)
    assert before == probe.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
    probe.close()


def test_reads_never_bootstrap_schema_and_work_on_readonly_database(tmp_path, conn, bundle):
    blank = sqlite3.connect(":memory:")
    before = blank.total_changes
    assert career.load_catalog(blank) == {} and career.get_catalog_id(blank) is None
    assert career.list_jobs(blank) == [] and career.get_job(blank, "example-one") is None
    assert career.list_companies(blank) == [] and career.list_targets(blank) == []
    assert career.get_snapshot(blank, "missing") is None
    assert career.get_target(blank, "missing") is None
    assert career.list_diagnoses(blank, "missing") == [] and career.get_diagnosis(blank, "missing") is None
    assert blank.total_changes == before
    assert blank.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    blank.close()
    import_bundle(conn, bundle)
    path = tmp_path / "readonly.db"
    copy = sqlite3.connect(path)
    conn.backup(copy)
    copy.close()
    reader = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    statements = []
    reader.set_trace_callback(statements.append)
    assert len(career.list_jobs(reader)) == 1
    assert career.load_catalog(reader)["skills"][0]["id"] == "programming"
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    reader.close()


def test_target_cas_idempotence_and_old_diagnosis_rejection(conn, bundle):
    imported = import_bundle(conn, bundle)
    evidence_id = career.put_snapshot(conn, kind="candidate_evidence", source_id="fictional-person",
        payload={"evidence": []}, raw_bytes=b"Synthetic material")
    assert career.get_snapshot(conn, evidence_id)["payload"] == {"evidence": []}
    target = {"target_id": "target-one", "input_fingerprint": "semantics-v1", "mode": "exact_job",
              "bindings": {"job_ids": ["example-one"], "catalog_id": imported["catalog_id"],
                           "evidence_snapshot_id": evidence_id}}
    first = career.save_target(conn, target)
    assert first["revision"] == 1
    assert career.save_target(conn, target, expected_revision=1) == first
    diagnosis = career.save_diagnosis(conn, {"input_digest": "all-inputs-v1", "items": []},
        target_id="target-one", input_revision=1, input_fingerprint="semantics-v1", run_id="run-one")
    assert not diagnosis["stale"] and diagnosis["input_digest"] == "all-inputs-v1"
    assert career.save_diagnosis(conn, {"input_digest": "all-inputs-v1", "items": []},
        target_id="target-one", input_revision=1, input_fingerprint="semantics-v1", run_id="run-one") == diagnosis
    newer = career.save_target(conn, {**target, "input_fingerprint": "semantics-v2"}, expected_revision=1)
    assert newer["revision"] == 2
    with pytest.raises(InputChangedError):
        career.save_target(conn, target, expected_revision=1)
    with pytest.raises(InputChangedError):
        career.save_diagnosis(conn, {}, target_id="target-one", input_revision=1, input_fingerprint="semantics-v1")
    with pytest.raises(InputChangedError):
        career.save_diagnosis(conn, {}, target_id="target-one", input_revision=2, input_fingerprint="semantics-v1")
    assert career.get_diagnosis(conn, diagnosis["diagnosis_id"])["stale"]
    assert career.get_target(conn, "target-one") == newer
    assert len(career.list_diagnoses(conn, "target-one")) == 1


def test_target_invalid_bindings_rejected(conn):
    with pytest.raises(ValueError, match="catalog"):
        career.save_target(conn, {"target_id": "bad", "input_fingerprint": "v1", "bindings": {"catalog_id": "missing"}})
    assert career.list_targets(conn) == []
