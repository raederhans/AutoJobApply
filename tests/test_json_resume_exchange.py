from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from applypilot.commands.json_resume import app
from applypilot.json_resume import (
    check_json_resume_data,
    export_json_resume,
    import_json_resume,
)


def test_export_maps_chinese_resume_and_all_supported_sections(tmp_path: Path) -> None:
    source = tmp_path / "resume.txt"
    source.write_text(
        "王小明\nData Analyst\nSingapore\n"
        "xiaoming@example.test | +65 6123 4567 | https://github.com/xiaoming | https://linkedin.com/in/xiaoming\n\n"
        "SUMMARY\n用 Python 和 SQL 建立分析流程。\n\n"
        "TECHNICAL SKILLS\nLanguages: Python, SQL\nVisualization: Tableau\n\n"
        "EXPERIENCE\nNorth Star Pte Ltd\nData Analyst | 2022-01 - 2023-02\n- Built a repeatable reporting workflow.\n"
        "Harbor Labs\nResearch Intern | 2024 - Present\n- Prepared research datasets.\n\n"
        "PROJECTS\nRoute Planner\nDeveloper | 2021-03 - 2021-12\n- Built a route planning prototype.\n\n"
        "EDUCATION\nNational University\nBSc | 2019 - 2023\n",
        encoding="utf-8",
    )
    output = tmp_path / "exchange" / "resume.json"

    report = export_json_resume(source, output)
    data = json.loads(output.read_text(encoding="utf-8"))

    assert report["ok"] is True
    assert set(report["mapped_fields"]) == {"basics", "work", "projects", "education", "skills"}
    assert data["basics"]["name"] == "王小明"
    assert data["basics"]["email"] == "xiaoming@example.test"
    assert data["basics"]["location"]["address"] == "Singapore"
    assert [item["name"] for item in data["work"]] == ["North Star Pte Ltd", "Harbor Labs"]
    assert data["work"][0]["position"] == "Data Analyst"
    assert data["work"][0]["startDate"] == "2022-01"
    assert data["work"][0]["endDate"] == "2023-02"
    assert data["work"][1]["startDate"] == "2024"
    assert "endDate" not in data["work"][1]
    assert data["work"][1]["x-applypilot-originalSubtitle"] == "Research Intern | 2024 - Present"
    assert data["projects"][0]["name"] == "Route Planner"
    assert data["education"][0]["institution"] == "National University"
    assert {item["name"] for item in data["skills"]} == {"Languages", "Visualization"}
    assert any("cannot be losslessly represented" in warning for warning in report["warnings"])


def test_import_keeps_full_source_unknown_fields_dates_and_generates_text(tmp_path: Path) -> None:
    source_data = {
        "$schema": "https://example.test/schema.json",
        "basics": {
            "name": "陈同学",
            "email": "chen@example.test",
            "location": {"city": "Singapore", "x-area-note": "north"},
            "x-pronouns": "they/them",
        },
        "work": [{
            "name": "Example Co",
            "position": "Analyst",
            "startDate": "2024",
            "endDate": "Present",
            "highlights": ["Created a bilingual report."],
            "x-source-date": "2024 - Present",
        }],
        "projects": [{"name": "地图工具", "description": "用于规划。", "keywords": ["Python"], "x-private-tag": "keep"}],
        "education": [{"institution": "示例大学", "studyType": "Bachelor", "endDate": "2026-06"}],
        "skills": [{"name": "Languages", "keywords": ["Python", "SQL"]}],
        "x-custom-section": {"unknown": ["preserve me"]},
    }
    source = tmp_path / "external.json"
    source.write_text(json.dumps(source_data, ensure_ascii=False, indent=2), encoding="utf-8")

    result = import_json_resume(source, tmp_path / "workspace" / "imports")
    draft = Path(result["draft_dir"])
    preserved = json.loads((draft / "resume.json").read_text(encoding="utf-8"))
    text = (draft / "resume.txt").read_text(encoding="utf-8")
    report = json.loads((draft / "report.json").read_text(encoding="utf-8"))

    assert preserved == source_data
    assert result["status"] == "unvalidated_draft"
    assert result["fact_validation"] == "not_performed"
    assert result["structural_check_ok"] is False
    assert report["unknown_fields_preserved_in_source_json"] is True
    assert "陈同学" in text and "地图工具" in text and "示例大学" in text
    assert "Python, SQL" in text
    assert "x-custom-section" in " ".join(report["unknown_extension_paths"])
    assert any("unknown extension" in warning.lower() for warning in report["warnings"])
    assert any("endDate" in warning for warning in report["warnings"])


@pytest.mark.parametrize("education,expected", [
    (
        "Example University\nBSc Computing | 2022 - 2026\n- Coursework: Database Systems\n",
        [{"institution": "Example University", "studyType": "BSc Computing", "startDate": "2022",
          "endDate": "2026", "courses": ["Coursework: Database Systems"]}],
    ),
    (
        "Example University\nBSc Computing | 2022 - 2026\n"
        "- Coursework: Database Systems\n- Courses: Software Engineering\n"
        "Harbor College\nMSc Analytics | 2026-08 - 2027-06\n• Coursework: Statistics\n",
        [{"institution": "Example University", "studyType": "BSc Computing", "startDate": "2022",
          "endDate": "2026", "courses": ["Coursework: Database Systems", "Courses: Software Engineering"]},
         {"institution": "Harbor College", "studyType": "MSc Analytics", "startDate": "2026-08",
          "endDate": "2027-06", "courses": ["Coursework: Statistics"]}],
    ),
])
def test_export_education_preserves_school_degree_dates_and_coursework(tmp_path: Path, education, expected) -> None:
    source = tmp_path / "education.txt"
    source.write_text("Candidate\n\nEDUCATION\n" + education, encoding="utf-8")
    output = tmp_path / "education.json"

    report = export_json_resume(source, output)

    assert report["ok"] is True
    assert json.loads(output.read_text(encoding="utf-8"))["education"] == expected


def test_export_unparsed_education_is_preserved_without_invented_institutions_or_dates(tmp_path: Path) -> None:
    source = tmp_path / "education.txt"
    source.write_text(
        "Candidate\n\nEDUCATION\nUnverified education note\n"
        "Example University\nBSc Computing | September 2022 - Summer 2026\n"
        "- Scholarship: pending confirmation\nGraduation ceremony TBD\n",
        encoding="utf-8",
    )
    output = tmp_path / "education.json"

    report = export_json_resume(source, output)
    data = json.loads(output.read_text(encoding="utf-8"))

    assert report["ok"] is True
    assert data["x-applypilot-unparsedEducationLines"] == ["Unverified education note"]
    assert data["education"] == [{"institution": "Example University", "x-applypilot-unparsedLines": [
        "BSc Computing | September 2022 - Summer 2026", "- Scholarship: pending confirmation",
        "Graduation ceremony TBD",
    ]}]
    assert any("Unparsed education lines retained" in warning for warning in report["warnings"])


@pytest.mark.parametrize("ambiguous_lines", [
    ["Harbor College | MSc Analytics | 2026 - 2027", "- Coursework: Statistics"],
    ["Harbor College | date uncertain", "MSc Analytics | 2026 - 2027", "- Coursework: Statistics"],
])
def test_ambiguous_new_school_ends_prior_school_coursework_ownership(tmp_path: Path, ambiguous_lines) -> None:
    source = tmp_path / "education.txt"
    source.write_text(
        "Candidate\n\nEDUCATION\nExample University\nBSc Computing | 2022 - 2026\n"
        "- Coursework: Database Systems\n" + "\n".join(ambiguous_lines) + "\n"
        "North University\nMSc Computing | 2027 - 2028\n- Coursework: Algorithms\n",
        encoding="utf-8",
    )
    output = tmp_path / "education.json"

    report = export_json_resume(source, output)
    data = json.loads(output.read_text(encoding="utf-8"))

    assert report["ok"] is True
    assert data["education"] == [
        {"institution": "Example University", "studyType": "BSc Computing", "startDate": "2022",
         "endDate": "2026", "courses": ["Coursework: Database Systems"]},
        {"institution": "North University", "studyType": "MSc Computing", "startDate": "2027",
         "endDate": "2028", "courses": ["Coursework: Algorithms"]},
    ]
    assert data["x-applypilot-unparsedEducationLines"] == ambiguous_lines
    assert any("Unparsed education lines retained" in warning for warning in report["warnings"])


def test_check_reports_invalid_url_and_structure_types(tmp_path: Path) -> None:
    result = check_json_resume_data({
        "basics": {"url": "not a uri", "email": 42},
        "work": {"name": "should be an array"},
        "skills": [{"name": "Tools", "keywords": "Python"}],
    })

    assert result["ok"] is False
    assert any("basics.url" in error for error in result["errors"])
    assert any("basics.email" in error for error in result["errors"])
    assert any("work must be list" in error for error in result["errors"])
    assert any("skills[0].keywords must be list" in error for error in result["errors"])
    assert "supported subset only" in result["validation"]

    source = tmp_path / "invalid.json"
    source.write_text(json.dumps({"basics": {"url": "not a uri"}}), encoding="utf-8")
    cli_result = CliRunner().invoke(app, ["check", str(source)])
    assert cli_result.exit_code == 1
    assert json.loads(cli_result.output)["ok"] is False


def test_cli_uses_selected_workspace_without_profile_or_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"basics": {"name": "本地候选人"}, "skills": []}), encoding="utf-8")
    workspace = tmp_path / "selected-workspace"
    monkeypatch.setenv("APPLYPILOT_DIR", str(workspace))

    result = CliRunner().invoke(app, ["import", "--input", str(source)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert Path(payload["draft_dir"]).is_relative_to(workspace / "imports")
    assert (Path(payload["draft_dir"]) / "resume.json").is_file()
    assert not (workspace / "profile.json").exists()
    assert not (workspace / "applypilot.db").exists()


def test_root_cli_workspace_option_routes_json_resume_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from applypilot.cli import app as root_app

    source = tmp_path / "input.json"
    source.write_text(json.dumps({"basics": {"name": "工作区测试"}}), encoding="utf-8")
    workspace = tmp_path / "chosen-workspace"
    monkeypatch.delenv("APPLYPILOT_DIR", raising=False)

    result = CliRunner().invoke(
        root_app,
        ["--workspace", str(workspace), "json-resume", "import", "--input", str(source)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert Path(payload["draft_dir"]).is_relative_to(workspace / "imports")
    assert not (workspace / "profile.json").exists()
    assert not (workspace / "applypilot.db").exists()


def test_importing_command_app_does_not_bind_config_paths() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import applypilot.commands.json_resume; assert 'applypilot.config' not in sys.modules"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_export_rejects_same_target_and_existing_target(tmp_path: Path) -> None:
    source = tmp_path / "resume.txt"
    source.write_text("Candidate\n\nSUMMARY\nA source summary.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be different"):
        export_json_resume(source, source)
    with pytest.raises(ValueError, match="must be different"):
        import_json_resume(source, source)

    output = tmp_path / "already-exists.json"
    output.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        export_json_resume(source, output)
    assert output.read_text(encoding="utf-8") == "keep"


def test_import_rejects_existing_draft_target_without_overwriting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "resume.json"
    source.write_text(json.dumps({"basics": {"name": "A"}}), encoding="utf-8")
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("APPLYPILOT_DIR", str(workspace))
    runner = CliRunner()
    first = runner.invoke(app, ["import", "--input", str(source)])
    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.output)
    existing_parent = Path(first_payload["draft_dir"]).parent
    existing_parent.mkdir(parents=True, exist_ok=True)
    # A forced UUID collision must fail at mkdir rather than replacing files.
    import applypilot.json_resume as exchange

    monkeypatch.setattr(exchange.uuid, "uuid4", lambda: type("FixedUUID", (), {"hex": Path(first_payload["draft_dir"]).name.rsplit("-", 1)[1]})())
    second = runner.invoke(app, ["import", "--input", str(source)])
    assert second.exit_code == 2
    assert json.loads((Path(first_payload["draft_dir"]) / "resume.json").read_text(encoding="utf-8")) == {"basics": {"name": "A"}}
