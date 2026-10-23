"""Offline tests for the interactive ``applypilot init`` wizard.

``Prompt.ask`` / ``Confirm.ask`` are replaced by a scripted answer table, and the
workspace path constants are redirected into ``tmp_path`` (they are bound with
``from ... import`` in the wizard module, so they must be patched there).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from dotenv import dotenv_values

import applypilot.wizard.init as wizard
from applypilot import config, llm
from applypilot.discovery import jobspy


class Script:
    """Scripted stand-in for ``Prompt.ask`` / ``Confirm.ask``.

    ``answers`` maps a prompt prefix to an answer. A list answer is consumed one
    item per matching prompt (for retries). A prompt without an entry returns its
    ``default``; a prompt with neither raises, so unexpected questions fail loudly.
    """

    def __init__(self, answers: dict[str, object] | None = None) -> None:
        self.answers = {key: (list(val) if isinstance(val, list) else val) for key, val in (answers or {}).items()}
        self.asked: list[str] = []

    def __call__(self, prompt: str, *args: object, default: object = ..., **kwargs: object) -> object:
        self.asked.append(prompt)
        for key, value in self.answers.items():
            if prompt.startswith(key):
                if isinstance(value, list):
                    assert value, f"script exhausted for prompt: {prompt!r}"
                    return value.pop(0)
                return value
        if default is not ...:
            return default
        raise AssertionError(f"unscripted prompt without default: {prompt!r}")

    def assert_consumed(self) -> None:
        leftovers = {key: val for key, val in self.answers.items() if isinstance(val, list) and val}
        assert not leftovers, f"unused scripted answers: {leftovers}"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workspace"
    monkeypatch.setattr(wizard, "APP_DIR", root)
    monkeypatch.setattr(wizard, "PROFILE_PATH", root / "profile.json")
    monkeypatch.setattr(wizard, "RESUME_PATH", root / "resume.txt")
    monkeypatch.setattr(wizard, "RESUME_PDF_PATH", root / "resume.pdf")
    monkeypatch.setattr(wizard, "SEARCH_CONFIG_PATH", root / "searches.yaml")
    monkeypatch.setattr(wizard, "ENV_PATH", root / ".env")
    monkeypatch.setattr(config, "APP_DIR", root)
    monkeypatch.setattr(wizard, "ensure_dirs", lambda: root.mkdir(parents=True, exist_ok=True))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _prompts(monkeypatch: pytest.MonkeyPatch, answers: dict[str, object] | None = None) -> Script:
    script = Script(answers)
    monkeypatch.setattr(wizard.Prompt, "ask", script)
    return script


def _confirms(monkeypatch: pytest.MonkeyPatch, answers: dict[str, object] | None = None) -> Script:
    script = Script(answers)
    monkeypatch.setattr(wizard.Confirm, "ask", script)
    return script


# ---------------------------------------------------------------------------
# _setup_resume
# ---------------------------------------------------------------------------


def test_setup_resume_copies_txt(workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "cv.txt"
    source.write_text("Plain resume", encoding="utf-8")
    _prompts(monkeypatch, {"Resume file path": f'  "{source}"  '})

    wizard._setup_resume()

    assert (workspace / "resume.txt").read_text(encoding="utf-8") == "Plain resume"
    assert not (workspace / "resume.pdf").exists()


def test_setup_resume_pdf_with_optional_plain_text(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "cv.PDF"
    pdf.write_bytes(b"%PDF-1.4 fake")
    txt = tmp_path / "cv-plain.txt"
    txt.write_text("Plain version", encoding="utf-8")
    _prompts(monkeypatch, {"Resume file path": str(pdf), "Plain-text version": f"'{txt}'"})

    wizard._setup_resume()

    assert (workspace / "resume.pdf").read_bytes() == b"%PDF-1.4 fake"
    assert (workspace / "resume.txt").read_text(encoding="utf-8") == "Plain version"


@pytest.mark.parametrize("txt_answer", ["", "   ", "missing-plain.txt"])
def test_setup_resume_pdf_without_usable_plain_text_copy(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, txt_answer: str
) -> None:
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    answer = str(tmp_path / txt_answer) if txt_answer.strip() else txt_answer
    _prompts(monkeypatch, {"Resume file path": str(pdf), "Plain-text version": answer})

    wizard._setup_resume()

    assert (workspace / "resume.pdf").is_file()
    assert not (workspace / "resume.txt").exists()


def test_setup_resume_retries_missing_and_unsupported_files(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    unsupported = tmp_path / "cv.docx"
    unsupported.write_text("not supported", encoding="utf-8")
    good = tmp_path / "cv.txt"
    good.write_text("Finally", encoding="utf-8")
    script = _prompts(
        monkeypatch,
        {"Resume file path": [str(tmp_path / "nope.txt"), str(unsupported), str(good)]},
    )

    wizard._setup_resume()

    script.assert_consumed()
    assert (workspace / "resume.txt").read_text(encoding="utf-8") == "Finally"
    output = " ".join(capsys.readouterr().out.split())
    assert "File not found" in output
    assert "Unsupported format" in output


# ---------------------------------------------------------------------------
# _setup_profile
# ---------------------------------------------------------------------------

_PROFILE_ANSWERS = {
    "Full name": "Ada Lovelace",
    "Email address": "ada@example.test",
    "City": "Singapore",
    "Country": "Singapore",
    "Phone number": "+65 8000 0000",
    "Expected annual salary": "100000",
    "Currency": "SGD",
    "Current/most recent job title": "Analyst",
    "Programming languages": " Python, SQL ,, ",
    "Frameworks": "pandas",
    "Tools": "Docker,Git",
    "Companies to always keep": "Acme, Globex",
    "Projects to always keep": "Engine",
    "School name": "  NUS  ",
    "Real metrics": "99.9% uptime, 50k users",
}


def test_setup_profile_builds_expected_schema_and_saves_it(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prompts(monkeypatch, {**_PROFILE_ANSWERS, "Acceptable range": "80000 - 120000"})
    _confirms(monkeypatch, {"Are you legally authorized": True, "Will you now": False})

    profile = wizard._setup_profile()

    assert json.loads((workspace / "profile.json").read_text(encoding="utf-8")) == profile
    assert set(profile) == {
        "personal",
        "work_authorization",
        "compensation",
        "experience",
        "skills_boundary",
        "resume_facts",
        "eeo_voluntary",
        "availability",
    }
    assert profile["personal"]["full_name"] == "Ada Lovelace"
    assert profile["personal"]["email"] == "ada@example.test"
    assert profile["personal"]["country"] == "Singapore"
    assert profile["work_authorization"] == {
        "legally_authorized_to_work": True,
        "require_sponsorship": False,
        "work_permit_type": "",
    }
    assert profile["compensation"] == {
        "salary_expectation": "100000",
        "salary_currency": "SGD",
        "salary_range_min": "80000",
        "salary_range_max": "120000",
    }
    # target role defaults to the current title when left blank
    assert profile["experience"]["current_title"] == "Analyst"
    assert profile["experience"]["target_role"] == "Analyst"
    assert profile["skills_boundary"] == {
        "programming_languages": ["Python", "SQL"],
        "frameworks": ["pandas"],
        "tools": ["Docker", "Git"],
    }
    assert profile["resume_facts"] == {
        "preserved_companies": ["Acme", "Globex"],
        "preserved_projects": ["Engine"],
        "preserved_school": "NUS",
        "real_metrics": ["99.9% uptime", "50k users"],
    }
    assert set(profile["eeo_voluntary"].values()) == {"Decline to self-identify"}
    assert profile["availability"] == {"earliest_start_date": "Immediately"}


def test_setup_profile_empty_range_falls_back_to_expected_salary(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prompts(monkeypatch, _PROFILE_ANSWERS)
    _confirms(monkeypatch, {"Are you legally authorized": False, "Will you now": True})

    profile = wizard._setup_profile()

    assert profile["compensation"]["salary_range_min"] == "100000"
    assert profile["compensation"]["salary_range_max"] == "100000"
    assert profile["work_authorization"]["legally_authorized_to_work"] is False
    assert profile["work_authorization"]["require_sponsorship"] is True


# ---------------------------------------------------------------------------
# _setup_searches
# ---------------------------------------------------------------------------


def _run_searches(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
    *,
    location: str = "Singapore",
    radius: str = "0",
    roles: str = "Data Analyst",
    remote_only: bool | None = None,
    indeed_country: str | None = None,
) -> tuple[dict, Script]:
    answers: dict[str, object] = {"Target location": location, "Search radius": radius, "Target job titles": roles}
    if indeed_country is not None:
        answers["Indeed country"] = indeed_country
    _prompts(monkeypatch, answers)
    confirms = _confirms(monkeypatch, {} if remote_only is None else {"Search remote jobs only": remote_only})
    wizard._setup_searches()
    text = (workspace / "searches.yaml").read_text(encoding="utf-8")
    assert text.startswith("# Job Apply Pilot search configuration")
    return yaml.safe_load(text), confirms


def test_setup_searches_generates_parseable_yaml_with_tiers(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, _ = _run_searches(
        monkeypatch, workspace, location="Singapore", radius="5", roles="Role A, Role B, Role C, Role D, , "
    )

    assert list(cfg) == ["defaults", "country", "locations", "queries"]
    assert cfg["country"] == "singapore"
    assert cfg["defaults"] == {"location": "Singapore", "distance": 5, "hours_old": 72, "results_per_site": 50}
    assert cfg["locations"] == [{"location": "Singapore", "remote": False}]
    assert cfg["queries"] == [
        {"query": "Role A", "tier": 1},
        {"query": "Role B", "tier": 2},
        {"query": "Role C", "tier": 3},
        {"query": "Role D", "tier": 3},
    ]


def test_setup_searches_invalid_radius_and_empty_roles_use_defaults(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = _run_searches(monkeypatch, workspace, radius="far", roles=" , ")

    assert cfg["defaults"]["distance"] == 0
    assert cfg["queries"] == [{"query": "Software Engineer", "tier": 1}]


@pytest.mark.parametrize(
    ("location", "role"),
    [
        ('Singapore "Central"', 'Data "Analyst"'),
        ("Singapore: CBD", "Analyst: Growth"),
        ("Back\\slash # hash", "C# Developer"),
        ("'Single' quoted", "- leading dash"),
        ("Zürich", "Développeur"),
    ],
)
def test_setup_searches_special_characters_stay_valid_yaml(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, location: str, role: str
) -> None:
    cfg, _ = _run_searches(monkeypatch, workspace, location=location, roles=role)

    assert cfg["defaults"]["location"] == location
    assert cfg["locations"][0]["location"] == location
    assert cfg["queries"][0]["query"] == role


def test_setup_searches_default_radius_does_not_force_remote_for_a_named_city(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pressing Enter at every question after the city must not restrict a new
    # user's first search to remote-only jobs.
    cfg, confirms = _run_searches(monkeypatch, workspace, location="Singapore", radius="0")

    assert cfg["locations"] == [{"location": "Singapore", "remote": False}]
    assert any(prompt.startswith("Search remote jobs only") for prompt in confirms.asked)


def test_setup_searches_remote_only_requires_explicit_choice(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, _ = _run_searches(monkeypatch, workspace, location="Singapore", radius="0", remote_only=True)

    assert cfg["locations"] == [{"location": "Singapore", "remote": True}]


def test_setup_searches_remote_location_defaults_to_remote_only(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = _run_searches(monkeypatch, workspace, location=" Remote ")

    assert cfg["locations"][0]["remote"] is True


def test_generated_search_config_is_consumed_by_discovery(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = _run_searches(
        monkeypatch, workspace, location='Singapore: "CBD"', roles='Data "Analyst", Product Analyst'
    )
    captured: list[dict] = []
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE jobs (url TEXT, detail_scraped_at TEXT)")
    monkeypatch.setattr(jobspy, "init_db", lambda *a, **k: connection)
    monkeypatch.setattr(jobspy, "get_connection", lambda *a, **k: connection)

    def fake_run_one_search(search: dict, *args: object) -> dict:
        captured.append(search)
        return {"new": 0, "existing": 0, "errors": 0}

    monkeypatch.setattr(jobspy, "_run_one_search", fake_run_one_search)

    result = jobspy._full_crawl(search_cfg=cfg, sites=["indeed"])

    assert result["queries"] == 2
    assert [(s["query"], s["location"], s["remote"], s["tier"]) for s in captured] == [
        ('Data "Analyst"', 'Singapore: "CBD"', False, 1),
        ("Product Analyst", 'Singapore: "CBD"', False, 2),
    ]


# ---------------------------------------------------------------------------
# _setup_ai_features
# ---------------------------------------------------------------------------

_PROVIDER_ANSWERS = {
    "gemini": ({"Gemini API key": "g-key"}, {"GEMINI_API_KEY": "g-key", "LLM_MODEL": "gemini-2.0-flash"}),
    "openai": ({"OpenAI API key": "o-key"}, {"OPENAI_API_KEY": "o-key", "LLM_MODEL": "gpt-4o-mini"}),
    "local": (
        {"Local LLM endpoint URL": "http://localhost:8080/v1/"},
        {"LLM_URL": "http://localhost:8080/v1/", "LLM_MODEL": "local-model"},
    ),
}
_LLM_ENV_NAMES = ("GEMINI_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "LLM_URL", "LLM_MODEL", "LLM_API_KEY")


def _choose_provider(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    answers, _ = _PROVIDER_ANSWERS[provider]
    _prompts(monkeypatch, {"Provider": provider, **answers})
    _confirms(monkeypatch, {"Enable AI scoring": True})


def test_setup_ai_features_decline_leaves_existing_env_untouched(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = workspace / ".env"
    env_path.write_text("# mine\nGEMINI_API_KEY=keep\n", encoding="utf-8")
    _confirms(monkeypatch, {"Enable AI scoring": False})
    prompts = _prompts(monkeypatch)

    wizard._setup_ai_features()

    assert env_path.read_text(encoding="utf-8") == "# mine\nGEMINI_API_KEY=keep\n"
    assert prompts.asked == []


@pytest.mark.parametrize("provider", ["gemini", "openai", "local"])
def test_setup_ai_features_writes_keys_that_llm_module_reads(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    _choose_provider(monkeypatch, provider)

    wizard._setup_ai_features()

    written = dotenv_values(workspace / ".env")
    assert written == _PROVIDER_ANSWERS[provider][1]
    # The names the wizard writes must be the names llm.py actually reads.
    for name in _LLM_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in written.items():
        monkeypatch.setenv(name, value or "")
    base_url, model, api_key = llm._detect_provider()
    expected_url = {
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
        "openai": "https://api.openai.com/v1",
        "local": "http://localhost:8080/v1",
    }[provider]
    assert base_url == expected_url
    assert model == written["LLM_MODEL"]
    assert api_key == {"gemini": "g-key", "openai": "o-key", "local": ""}[provider]
    assert config.has_llm_provider() is True


def test_setup_ai_features_preserves_unrelated_env_lines_and_comments(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = workspace / ".env"
    env_path.write_text(
        "# my notes\n"
        "CAPSOLVER_KEY=abc123\n"
        "\n"
        "LLM_MODEL=old-model\n"
        "export HTTP_PROXY=http://proxy.test:3128\n"
        "DEEPSEEK_BASE_URL=https://deepseek.test\n",
        encoding="utf-8",
    )
    _choose_provider(monkeypatch, "openai")

    wizard._setup_ai_features()

    lines = env_path.read_text(encoding="utf-8").splitlines()
    assert lines[:3] == ["# my notes", "CAPSOLVER_KEY=abc123", ""]
    assert "export HTTP_PROXY=http://proxy.test:3128" in lines
    assert "DEEPSEEK_BASE_URL=https://deepseek.test" in lines
    assert lines.count("LLM_MODEL=gpt-4o-mini") == 1
    assert "LLM_MODEL=old-model" not in lines
    assert "OPENAI_API_KEY=o-key" in lines


def test_setup_ai_features_disables_other_providers_so_the_chosen_one_wins(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = workspace / ".env"
    env_path.write_text(
        "GEMINI_API_KEY=old-gemini\nLLM_URL=http://old.test/v1\nOPENAI_API_KEY=old-openai\nLLM_API_KEY=local-secret\n",
        encoding="utf-8",
    )
    _choose_provider(monkeypatch, "openai")

    wizard._setup_ai_features()

    text = env_path.read_text(encoding="utf-8")
    # Replaced in place, and the shadowing providers are commented out, not deleted.
    assert "OPENAI_API_KEY=o-key" in text.splitlines()
    assert "# disabled by applypilot init: GEMINI_API_KEY=old-gemini" in text
    assert "# disabled by applypilot init: LLM_URL=http://old.test/v1" in text
    assert "LLM_API_KEY=local-secret" in text.splitlines()
    parsed = dotenv_values(env_path)
    assert parsed["OPENAI_API_KEY"] == "o-key"
    assert "GEMINI_API_KEY" not in parsed
    assert "LLM_URL" not in parsed


# ---------------------------------------------------------------------------
# _setup_auto_apply
# ---------------------------------------------------------------------------


def test_setup_auto_apply_declined_does_not_probe_for_codex(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _confirms(monkeypatch, {"Enable autonomous": False})
    monkeypatch.setattr(wizard.shutil, "which", lambda *_: pytest.fail("should not probe PATH"))

    wizard._setup_auto_apply()

    assert "apply manually" in " ".join(capsys.readouterr().out.split())


@pytest.mark.parametrize(("found", "expected"), [(r"C:\bin\codex.exe", "Codex CLI detected"), (None, "not found on PATH")])
def test_setup_auto_apply_reports_codex_presence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], found: str | None, expected: str
) -> None:
    _confirms(monkeypatch, {"Enable autonomous": True})
    monkeypatch.setattr(wizard.shutil, "which", lambda name: found)

    wizard._setup_auto_apply()

    output = " ".join(capsys.readouterr().out.split())
    assert expected in output
    assert "external solver keys are not configured" in output


# ---------------------------------------------------------------------------
# run_wizard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tier", "hint"),
    [(1, "To unlock Tier 2"), (2, "To unlock Tier 3"), (3, None)],
)
def test_run_wizard_end_to_end_in_a_temporary_workspace(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tier: int,
    hint: str | None,
) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text("Analyst resume", encoding="utf-8")
    prompts = _prompts(
        monkeypatch,
        {
            **_PROFILE_ANSWERS,
            "Resume file path": str(resume),
            "Target location": "Singapore",
            "Target job titles": "Data Analyst, Product Analyst",
            "Provider": "gemini",
            "Gemini API key": "g-key",
        },
    )
    _confirms(
        monkeypatch,
        {"Are you legally authorized": True, "Will you now": False, "Enable AI scoring": True, "Enable autonomous": True},
    )
    monkeypatch.setattr(wizard.shutil, "which", lambda name: None)
    monkeypatch.setattr(config, "get_tier", lambda: tier)

    wizard.run_wizard()

    assert (workspace / "resume.txt").read_text(encoding="utf-8") == "Analyst resume"
    assert json.loads((workspace / "profile.json").read_text(encoding="utf-8"))["personal"]["email"] == "ada@example.test"
    searches = yaml.safe_load((workspace / "searches.yaml").read_text(encoding="utf-8"))
    assert [q["query"] for q in searches["queries"]] == ["Data Analyst", "Product Analyst"]
    assert searches["locations"] == [{"location": "Singapore", "remote": False}]
    assert searches["country"] == "singapore"
    assert dotenv_values(workspace / ".env")["GEMINI_API_KEY"] == "g-key"
    assert prompts.asked[0] == "Resume file path"

    output = " ".join(capsys.readouterr().out.split())
    assert "Setup complete!" in output
    assert f"Your tier: Tier {tier}" in output
    if hint:
        assert hint in output
    else:
        assert "To unlock" not in output


# ---------------------------------------------------------------------------
# _resolved_input / initialize_from_files error branches
# ---------------------------------------------------------------------------


def test_resolved_input_rejects_missing_file_and_wrong_suffix(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Resume file not found"):
        wizard._resolved_input(tmp_path / "missing.txt", suffixes={".txt"}, label="Resume")

    wrong = tmp_path / "resume.docx"
    wrong.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match=r"Resume must use one of these formats: \.pdf, \.txt"):
        wizard._resolved_input(wrong, suffixes={".txt", ".pdf"}, label="Resume")


def test_resolved_input_accepts_suffix_case_insensitively(tmp_path: Path) -> None:
    upper = tmp_path / "resume.PDF"
    upper.write_bytes(b"%PDF")

    assert wizard._resolved_input(upper, suffixes={".pdf"}, label="Resume") == upper.resolve()


def _inputs(tmp_path: Path, *, profile: str, searches: str) -> SimpleNamespace:
    resume = tmp_path / "resume.txt"
    profile_path = tmp_path / "profile.json"
    searches_path = tmp_path / "searches.yaml"
    resume.write_text("Resume", encoding="utf-8")
    profile_path.write_text(profile, encoding="utf-8")
    searches_path.write_text(searches, encoding="utf-8")
    return SimpleNamespace(resume=resume, profile=profile_path, searches=searches_path)


@pytest.mark.parametrize(
    ("profile", "searches"),
    [("{not json", "queries: []\n"), ("{}", "queries: [unclosed\n")],
)
def test_initialize_from_files_reports_unparseable_inputs(
    workspace: Path, tmp_path: Path, profile: str, searches: str
) -> None:
    inputs = _inputs(tmp_path, profile=profile, searches=searches)

    with pytest.raises(ValueError, match="Unable to parse onboarding input"):
        wizard.initialize_from_files(resume=inputs.resume, profile=inputs.profile, searches=inputs.searches)

    assert not (workspace / "profile.json").exists()


def test_initialize_from_files_rejects_non_mapping_search_yaml(workspace: Path, tmp_path: Path) -> None:
    inputs = _inputs(tmp_path, profile="{}", searches="- just\n- a list\n")

    with pytest.raises(TypeError, match="Search YAML must contain a mapping"):
        wizard.initialize_from_files(resume=inputs.resume, profile=inputs.profile, searches=inputs.searches)


def test_initialize_from_files_imports_pdf_and_force_replaces(workspace: Path, tmp_path: Path) -> None:
    inputs = _inputs(tmp_path, profile='{"personal": {"full_name": "New"}}', searches="queries: []\n")
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    (workspace / "profile.json").write_text('{"personal": {"full_name": "Old"}}', encoding="utf-8")

    with pytest.raises(FileExistsError, match="profile.json"):
        wizard.initialize_from_files(resume=pdf, profile=inputs.profile, searches=inputs.searches)

    wizard.initialize_from_files(resume=pdf, profile=inputs.profile, searches=inputs.searches, force=True)

    assert (workspace / "resume.pdf").read_bytes() == b"%PDF-1.4"
    assert json.loads((workspace / "profile.json").read_text(encoding="utf-8"))["personal"]["full_name"] == "New"


# ---------------------------------------------------------------------------
# Credentials are not collected; Indeed country is written to searches.yaml
# ---------------------------------------------------------------------------


def test_setup_profile_does_not_ask_for_or_store_a_password(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _prompts(monkeypatch, {**_PROFILE_ANSWERS, "Job site password": "hunter2"})
    _confirms(monkeypatch, {"Are you legally authorized": True, "Will you now": False})

    profile = wizard._setup_profile()

    assert not [question for question in script.asked if "password" in question.casefold()]
    assert "password" not in profile["personal"]
    saved = (workspace / "profile.json").read_text(encoding="utf-8")
    assert "password" not in saved and "hunter2" not in saved
    # The removed question points at the real, existing browser-session workflow instead.
    assert "browser-session" in " ".join(capsys.readouterr().out.split())


def test_setup_profile_drops_a_plaintext_password_left_by_an_older_profile(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace / "profile.json").write_text(
        json.dumps({"personal": {"full_name": "Old", "password": "legacy-secret"}}), encoding="utf-8"
    )
    _prompts(monkeypatch, _PROFILE_ANSWERS)
    _confirms(monkeypatch, {"Are you legally authorized": True, "Will you now": False})

    profile_path = (workspace / "profile.json").resolve()
    real_read_text = Path.read_text

    def guarded_read_text(self, *args, **kwargs):
        if Path(self).resolve() == profile_path:
            raise AssertionError("the wizard must not read the old profile (it may hold a password)")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    profile = wizard._setup_profile()

    monkeypatch.setattr(Path, "read_text", real_read_text)
    assert "password" not in profile["personal"]
    saved = (workspace / "profile.json").read_text(encoding="utf-8")
    assert "password" not in json.loads(saved)["personal"] and "legacy-secret" not in saved


@pytest.mark.parametrize(("value", "expected"), [("Czechia", "czech republic"), ("Czech Republic", "czech republic")])
def test_czechia_resolves_without_jobspy_installed(monkeypatch: pytest.MonkeyPatch, value, expected) -> None:
    monkeypatch.setattr(wizard, "_jobspy_country_name", lambda _value: None)

    assert wizard._normalize_indeed_country(value) == expected


def test_wizard_profile_without_password_still_builds_the_application_prompt_summary(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from applypilot.apply import prompt as apply_prompt

    _prompts(monkeypatch, _PROFILE_ANSWERS)
    _confirms(monkeypatch, {"Are you legally authorized": True, "Will you now": False})
    profile = wizard._setup_profile()

    summary = apply_prompt._build_profile_summary(profile)

    assert "Ada Lovelace" in summary
    assert "password" not in summary.casefold()


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Singapore", "singapore"),
        ("  singapore ", "singapore"),
        ("Raffles Place, Singapore", "singapore"),
        ("Toronto, Canada", "canada"),
        ("United States", "usa"),
        ("London, United Kingdom", "uk"),
        ("Remote", None),
        ("", None),
        ("Zürich", None),
        ("New York, NY", None),
    ],
)
def test_infer_indeed_country_from_the_target_location(location: str, expected: str | None) -> None:
    assert wizard._infer_indeed_country(location) == expected


def test_setup_searches_writes_the_inferred_indeed_country(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, _ = _run_searches(monkeypatch, workspace, location="Singapore")

    assert cfg["country"] == "singapore"
    assert "country_indeed" not in cfg["defaults"]


def test_setup_searches_does_not_guess_a_country_for_remote(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, _ = _run_searches(monkeypatch, workspace, location="Remote")

    assert "country" not in cfg


def test_setup_searches_remote_user_can_still_name_a_country(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, _ = _run_searches(monkeypatch, workspace, location="Remote", indeed_country="Malaysia")

    assert cfg["country"] == "malaysia"


def test_setup_searches_rejects_an_unknown_country_instead_of_writing_it(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(wizard, "_jobspy_country_name", lambda _value: None)
    cfg, _ = _run_searches(monkeypatch, workspace, location="Singapore", indeed_country="Narnia")

    assert "country" not in cfg
    assert "not a supported Indeed country" in " ".join(capsys.readouterr().out.split())


def test_setup_searches_blank_answer_keeps_the_default_country_fallback(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = _run_searches(monkeypatch, workspace, location="Singapore", indeed_country="")

    assert "country" not in cfg


def test_wizard_written_country_reaches_the_jobspy_indeed_defaults(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = _run_searches(monkeypatch, workspace, location="Singapore")
    captured: dict = {}
    monkeypatch.setattr(jobspy, "require_jobboards", lambda: object())
    monkeypatch.setattr(jobspy, "_full_crawl", lambda **kwargs: captured.update(kwargs) or {"new": 0})

    jobspy.run_discovery(cfg)

    assert captured["search_cfg"]["defaults"]["country_indeed"] == "singapore"
