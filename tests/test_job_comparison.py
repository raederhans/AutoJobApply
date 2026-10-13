"""Narrow synthetic checks for public, read-only job comparisons."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from applypilot.job_comparison import (
    UNKNOWN,
    collect_comparison,
    export_comparison,
    list_comparison_jobs,
    render_comparison_html,
)

URL = "https://example.test/jobs/0?token=URL_SECRET"
CAREER_ID = "public-career-a"
TITLE = 'Research "quoted" | [unsafe](javascript:run) <img src=x onerror="CAREER_XSS">'


def ref(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:16]


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    with sqlite3.connect(root / "applypilot.db") as conn:
        conn.execute("""CREATE TABLE jobs (
            url TEXT PRIMARY KEY, platform_job_id TEXT, title TEXT, company_name TEXT,
            location TEXT, apply_status TEXT, salary TEXT, full_description TEXT, last_seen_at TEXT,
            closing_date TEXT, tailored_resume_path TEXT, unanswered_questions_json TEXT,
            fit_score INTEGER, score_reasoning TEXT
        )""")
        for i in range(7):
            url = URL if i == 0 else f"https://example.test/jobs/{i}"
            native = "shared-native" if i in (1, 2) else f"native-{i}"
            conn.execute("INSERT INTO jobs VALUES (?, ?, ?, 'Fictional Company', 'Singapore', 'failed', ?, ?, ?, ?, ?, ?, ?, ?)",
                         (url, native, f"Local role {i}", "S$ 1,234–2,345/month" if i == 0 else None,
                          ("LOCAL_DESCRIPTION_PUBLIC <form><textarea>FORM_ANSWER_SECRET</textarea></form> "
                           "<script>SCRIPT_SECRET</script> Visible duties https://private.test/?token=DESCRIPTION_URL_SECRET"),
                          "2026-10-13T00:00:00Z", "Apply by 2026-11-30" if i == 0 else None,
                          r"C:\private\RESUME_SECRET.docx", '[{"answer":"ANSWER_SECRET"}]',
                          987654321, "PRIVATE_SCORE_SECRET"))
        conn.execute("""CREATE TABLE career_source_snapshots (
            sequence INTEGER PRIMARY KEY, snapshot_id TEXT, kind TEXT, source_id TEXT,
            company_id TEXT, payload_json TEXT, raw_bytes BLOB, body_text TEXT, observed_at TEXT
        )""")
        conn.execute("CREATE TABLE career_companies (company_id TEXT, name TEXT, aliases_json TEXT)")
        conn.execute("INSERT INTO career_companies VALUES ('research-company', 'Research Company', 'PRIVATE_ALIAS_SECRET')")
        old = {"title": "OLD_SNAPSHOT_TITLE", "salary": "OLD_SALARY_SECRET", "requirements": []}
        current = {
            "id": CAREER_ID, "title": TITLE, "company": "Fallback Company", "locations": ["Singapore", "Remote"],
            "salary": "SGD 4,000 monthly", "deadline": "2026-11-01", "employment_type": "full-time",
            "native_level": "Associate", "availability": "closed_at_observation", "observed_at": "2026-10-12",
            "requirements": [{"id": f"r-{i}", "text": f"PUBLIC_REQUIREMENT_{i}",
                              "source_section": "Responsibilities"} for i in range(7)],
            "fit_score": 987654321, "score_reasoning": "PRIVATE_SCORE_SECRET",
            "evidence": "CAREER_EVIDENCE_SECRET", "resume": "CAREER_RESUME_SECRET",
        }
        lower_bound = {
            "title": "Lower-bound role", "company": "Research Company", "availability": "historical",
            "observed_at": "2026-12-12", "requirements": [{
                "text": "Applications accepted at least through November 15, 2026.",
                "temporal_condition": {"kind": "acceptance_lower_bound", "operator": "at_least_until"},
            }], "deadline_elapsed": True,
        }
        data = [
            (1, "old-public", "research_jd", CAREER_ID, old, "OLD_PUBLIC_BODY"),
            (2, "latest-public", "research_jd", CAREER_ID, current,
             ("CURRENT_PUBLIC_BODY <form><textarea>CAREER_FORM_SECRET</textarea></form> "
              "<style>STYLE_SECRET</style> Visible duties https://private.test/?token=CAREER_URL_SECRET")),
            (3, "lower-bound-public", "research_jd", "public-career-b", lower_bound, "LOWER_BOUND_PUBLIC_BODY"),
            (9, "private-same-id", "candidate_evidence", CAREER_ID,
             {"title": "PRIVATE_SNAPSHOT_SECRET", "salary": "PRIVATE_SALARY_SECRET"}, "PRIVATE_EVIDENCE_BODY"),
            (10, "private-only", "candidate_evidence", "private-not-a-job",
             {"title": "PRIVATE_ONLY_TITLE"}, "PRIVATE_ONLY_BODY"),
        ]
        for sequence, snapshot, kind, native, payload, body in data:
            conn.execute("INSERT INTO career_source_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (sequence, snapshot, kind, native, "research-company", json.dumps(payload),
                          b"RAW_BYTES_SECRET", body, "2026-10-12"))
        for table in ("career_targets", "career_diagnoses", "career_model_attempts", "career_evidence_registry"):
            conn.execute(f"CREATE TABLE {table} (payload_json TEXT)")
            conn.execute(f"INSERT INTO {table} VALUES ('PRIVATE_TABLE_SECRET')")
        conn.execute("CREATE TABLE application_receipts (receipt_id TEXT, payload TEXT)")
        conn.execute("INSERT INTO application_receipts VALUES ('synthetic', 'RECEIPT_SECRET')")
    return root


def state(root):
    database = root / "applypilot.db"
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as conn:
        schema = conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        statuses = conn.execute("SELECT url,apply_status FROM jobs ORDER BY url").fetchall()
        receipts = conn.execute("SELECT * FROM application_receipts").fetchall()
    return hashlib.sha256(database.read_bytes()).hexdigest(), schema, statuses, receipts


def collect(root, ids):
    with sqlite3.connect(root / "applypilot.db") as conn:
        return collect_comparison(conn, ids)


def test_safe_ids_and_unique_native_platform_and_career_aliases_only_return_canonical_ids(workspace):
    listing = list_comparison_jobs(workspace, limit=20)
    assert listing["count"] == len(listing["jobs"]) == 9
    for row in listing["jobs"]:
        assert row["job_id"].startswith(("job-", "career-job-"))
        assert len(row["job_id"].rsplit("-", 1)[1]) == 16
    selected = collect(workspace, ["platform:native-0", "career:" + CAREER_ID])
    assert [row["job_id"] for row in selected["jobs"]] == [ref("job-", URL), ref("career-job-", CAREER_ID)]
    raw = collect(workspace, ["native-0", CAREER_ID])
    assert raw["jobs"] == selected["jobs"]
    serialized = json.dumps(listing) + json.dumps(selected)
    for private in (URL, "URL_SECRET", "native-0", CAREER_ID, "PRIVATE_ONLY_TITLE", "PRIVATE_SNAPSHOT_SECRET"):
        assert private not in serialized


def test_ambiguous_aliases_and_duplicate_alias_for_one_job_are_rejected(workspace):
    for ambiguous in ("shared-native", "platform:shared-native"):
        with pytest.raises(ValueError, match="ambiguous"):
            collect(workspace, ["native-0", ambiguous])
    with pytest.raises(ValueError, match="same job"):
        collect(workspace, [ref("job-", URL), "platform:native-0"])


@pytest.mark.parametrize("number", [2, 6])
def test_two_to_six_distinct_jobs_keep_user_selection_order(workspace, number):
    ids = [f"platform:native-{i}" for i in (6, 5, 4, 3, 0)] + ["career:" + CAREER_ID]
    selected = ids[:number]
    report = collect(workspace, selected)
    expected = [ref("career-job-", CAREER_ID) if value.startswith("career:")
                else ref("job-", URL if value == "platform:native-0" else f"https://example.test/jobs/{value[-1]}")
                for value in selected]
    assert [row["job_id"] for row in report["jobs"]] == expected


@pytest.mark.parametrize("ids", [[], ["native-0"], ["native-0"] * 7])
def test_invalid_selection_size_is_rejected(workspace, ids):
    with pytest.raises(ValueError, match="2 to 6"):
        collect(workspace, ids)


def test_unknown_id_never_creates_a_partial_report_or_echoes_sensitive_input(workspace, tmp_path):
    output = tmp_path / "report"
    with pytest.raises(ValueError, match="missing or ambiguous") as caught:
        export_comparison(workspace, output, job_ids=["native-0", "career:token=UNKNOWN_SECRET"])
    assert "UNKNOWN_SECRET" not in str(caught.value)
    assert not output.exists()


def test_career_reads_only_latest_public_research_and_never_private_tables_or_raw_bytes(workspace):
    private_tables = {"career_targets", "career_diagnoses", "career_model_attempts", "career_evidence_registry"}
    reads = []

    def authorize(operation, table, column, database, source):
        if operation == sqlite3.SQLITE_READ:
            reads.append((table, column))
            if table in private_tables or (table == "career_source_snapshots" and column == "raw_bytes"):
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    with sqlite3.connect(workspace / "applypilot.db") as conn:
        conn.set_authorizer(authorize)
        report = collect_comparison(conn, ["career:" + CAREER_ID, "career:public-career-b"])
    assert report["jobs"][0]["title"] == TITLE
    assert report["jobs"][0]["salary"] == "SGD 4,000 monthly"
    assert report["jobs"][0]["description_excerpt"].startswith("CURRENT_PUBLIC_BODY")
    serialized = json.dumps(report)
    for secret in ("OLD_SNAPSHOT_TITLE", "OLD_SALARY_SECRET", "OLD_PUBLIC_BODY", "PRIVATE_TABLE_SECRET",
                   "PRIVATE_SNAPSHOT_SECRET", "PRIVATE_ONLY_BODY", "RAW_BYTES_SECRET", "PRIVATE_ALIAS_SECRET"):
        assert secret not in serialized
    assert not any(table in private_tables for table, _ in reads)
    assert ("career_source_snapshots", "raw_bytes") not in reads
    with pytest.raises(ValueError, match="missing or ambiguous"):
        collect(workspace, ["native-0", "career:private-not-a-job"])


def test_salary_deadline_requirements_and_descriptions_preserve_only_recorded_public_facts(workspace):
    report = collect(workspace, ["native-0", "career:" + CAREER_ID, "career:public-career-b", "platform:native-3"])
    local, research, lower_bound, missing = report["jobs"]
    assert local["salary"] == "S$ 1,234–2,345/month"
    assert local["deadline"] == "Apply by 2026-11-30"
    assert research["salary"] == "SGD 4,000 monthly" and research["deadline"] == "2026-11-01"
    assert research["company"] == "Research Company"
    assert research["availability"] == "closed_at_observation"
    assert lower_bound["availability"] == "historical"
    assert lower_bound["deadline"] == "" and missing["deadline"] == ""
    assert lower_bound["salary"] == missing["salary"] == ""
    assert "at least through November 15" in lower_bound["requirements"]
    requirements = research["requirements"].splitlines()
    assert len(requirements) == 5 and all("Responsibilities" in row for row in requirements)
    assert "PUBLIC_REQUIREMENT_4" in research["requirements"]
    assert "PUBLIC_REQUIREMENT_5" not in research["requirements"]
    assert local["requirements"] == ""
    assert "LOCAL_DESCRIPTION_PUBLIC" in local["description_excerpt"]
    assert len(local["description_excerpt"]) <= 240
    assert "CURRENT_PUBLIC_BODY" not in research["requirements"]
    assert UNKNOWN in render_comparison_html(report)
    assert all("fit_score" not in row and "score_reasoning" not in row for row in report["jobs"])


def test_html_and_markdown_are_inert_escaped_self_contained_and_private_values_are_omitted(workspace, tmp_path):
    before = state(workspace)
    result = export_comparison(workspace, tmp_path / "report", job_ids=["career:" + CAREER_ID, "native-0"])
    assert result["job_ids"] == [ref("career-job-", CAREER_ID), ref("job-", URL)]
    html_path = Path(result["files"]["html"])
    document = html_path.read_bytes().decode("utf-8")
    markdown = Path(result["files"]["markdown"]).read_text(encoding="utf-8")
    soup = BeautifulSoup(document, "html.parser")
    assert TITLE in soup.get_text()
    assert soup.find(["script", "img", "svg", "iframe", "object", "embed", "link", "form"]) is None
    assert soup.find("meta", charset=True)["charset"] == "utf-8"
    csp = soup.find("meta", attrs={"http-equiv": "Content-Security-Policy"})["content"]
    assert "default-src 'none'" in csp and "style-src 'unsafe-inline'" in csp
    for element in soup.find_all(True):
        assert not any(attribute.lower().startswith("on") for attribute in element.attrs)
        assert not ({"src", "srcset", "href", "action"} & element.attrs.keys())
    assert "@import" not in document and "url(" not in document
    assert "<img" not in markdown and "[unsafe](javascript:run)" not in markdown
    assert r"\|" in markdown and r"\[unsafe\]" in markdown
    assert sorted(path.name for path in html_path.parent.iterdir()) == ["job-comparison.html", "job-comparison.md"]
    for secret in (URL, "URL_SECRET", "DESCRIPTION_URL_SECRET", "CAREER_URL_SECRET", "RESUME_SECRET",
                   "ANSWER_SECRET", "FORM_ANSWER_SECRET", "CAREER_FORM_SECRET", "SCRIPT_SECRET", "STYLE_SECRET",
                   "PRIVATE_SCORE_SECRET", "987654321", "CAREER_EVIDENCE_SECRET", "CAREER_RESUME_SECRET",
                   "RAW_BYTES_SECRET", "PRIVATE_TABLE_SECRET", "OLD_SNAPSHOT_TITLE"):
        assert secret not in document + markdown
    assert state(workspace) == before


def test_missing_optional_columns_remain_unknown_without_schema_changes(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    with sqlite3.connect(root / "applypilot.db") as conn:
        conn.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title TEXT)")
        conn.executemany("INSERT INTO jobs VALUES (?, ?)", [("https://example.test/a", "Legacy A"),
                                                          ("https://example.test/b", "Legacy B")])
        before_schema = conn.execute("SELECT name,sql FROM sqlite_master").fetchall()
        report = collect_comparison(conn, [ref("job-", "https://example.test/a"), ref("job-", "https://example.test/b")])
        assert conn.execute("SELECT name,sql FROM sqlite_master").fetchall() == before_schema
    for row in report["jobs"]:
        assert row["company"] == row["salary"] == row["deadline"] == row["requirements"] == ""
        assert row["application_status"] == "unknown"
    assert UNKNOWN in render_comparison_html(report)


def test_existing_outputs_are_never_overwritten_and_sources_stay_unchanged(workspace, tmp_path):
    output = tmp_path / "report"
    before = state(workspace)
    result = export_comparison(workspace, output, job_ids=["native-0", "career:" + CAREER_ID])
    saved = {kind: Path(path).read_bytes() for kind, path in result["files"].items()}
    with pytest.raises(ValueError, match="already exist"):
        export_comparison(workspace, output, job_ids=["native-0", "career:" + CAREER_ID])
    assert {kind: Path(path).read_bytes() for kind, path in result["files"].items()} == saved
    assert state(workspace) == before


def test_missing_database_does_not_initialize_a_workspace_or_output(tmp_path):
    root = tmp_path / "absent"
    output = tmp_path / "report"
    with pytest.raises(ValueError, match="Existing applypilot.db"):
        list_comparison_jobs(root)
    with pytest.raises(ValueError, match="Existing applypilot.db"):
        export_comparison(root, output, job_ids=["job-a", "job-b"])
    assert not root.exists() and not output.exists()
