"""Deterministic quality gates and exported assertions, using synthetic inputs."""

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from applypilot import quality_eval

FIXTURES = Path(__file__).parents[1] / "scripts" / "evals"


@pytest.fixture
def suite():
    return json.loads((FIXTURES / "synthetic-pass.json").read_text(encoding="utf-8"))


def test_positive_and_adversarial_synthetic_suites(suite):
    result = quality_eval.run_suite(suite)
    assert result["passed"]
    assert result["score"] == 1.0
    assert result["case_count"] == 2
    assert result["failed_count"] == 0
    negative = quality_eval.run_suite(json.loads((FIXTURES / "synthetic-fail.json").read_text(encoding="utf-8")))
    assert not negative["passed"]
    assert negative["failed_count"] == 2
    checks = negative["results"][0]["checks"]
    assert not checks["support_levels"]["passed"]
    assert not checks["unsupported_claims"]["passed"]
    assert not negative["results"][1]["checks"]["submission_evidence"]["passed"]


@pytest.mark.parametrize("output", [None, "", "   ", "not JSON", "[]", "{}",
                                          '{"evidence_map": [], "evidence_map": []}',
                                          '{"evidence_map": NaN}'])
def test_empty_or_invalid_json_schema_fails(suite, output):
    result = quality_eval.evaluate_case(suite["cases"][0], output)
    assert not result["passed"]
    assert result["checks"]["fixture"]["passed"]
    assert not result["checks"]["schema"]["passed"]
    assert result["checks"]["citations"]["passed"] is None


@pytest.mark.parametrize("change,category", [
    ({"source_id": "nonexistent"}, "citations"),
    ({"source_id": "campus-report"}, "citations"),
    ({"source_quote": "Fabricated quotation that never appeared in the source"}, "citations"),
    ({"support_level": "transferable"}, "support_levels"),
    ({"statement": "Led 500 customer deployments using Kubernetes."}, "unsupported_claims"),
])
def test_each_evidence_field_is_verified(suite, change, category):
    case = suite["cases"][0]
    case["output"]["evidence_map"][0].update(change)
    result = quality_eval.evaluate_case(case)
    assert not result["passed"]
    assert not result["checks"][category]["passed"]


def test_all_items_are_checked_beyond_first_five(suite):
    case = suite["cases"][0]
    for index in range(3):
        requirement = copy.deepcopy(case["requirements"][0])
        requirement["id"] = f"extra-{index}"
        case["requirements"].append(requirement)
        item = copy.deepcopy(case["output"]["evidence_map"][0])
        item["requirement_id"] = requirement["id"]
        case["output"]["evidence_map"].append(item)
    case["output"]["evidence_map"][5]["source_quote"] = "Invented source on the sixth item"
    result = quality_eval.evaluate_case(case)
    assert not result["passed"]
    assert any("[5]" in error for error in result["checks"]["citations"]["errors"])


@pytest.mark.parametrize("mode", ["missing", "duplicate", "unknown"])
def test_requirements_must_be_complete_and_unique(suite, mode):
    case = suite["cases"][0]
    items = case["output"]["evidence_map"]
    if mode == "missing":
        items.pop()
    elif mode == "duplicate":
        items.append(copy.deepcopy(items[0]))
    else:
        items[0]["requirement_id"] = "not-in-jd"
    result = quality_eval.evaluate_case(case)
    assert not result["passed"]
    assert not result["checks"]["support_levels"]["passed"]


def test_gap_must_not_claim_positive_evidence(suite):
    case = suite["cases"][0]
    case["output"]["evidence_map"][2]["source_id"] = "sql-project"
    case["output"]["evidence_map"][2]["source_quote"] = case["sources"][0]["text"]
    assert not quality_eval.evaluate_case(case)["checks"]["citations"]["passed"]


def test_verbatim_quote_cannot_upgrade_transferable_to_direct(suite):
    case = suite["cases"][0]
    item = case["output"]["evidence_map"][1]
    assert item["source_quote"] in case["sources"][1]["text"]
    item["support_level"] = "direct"
    result = quality_eval.evaluate_case(case)
    assert result["checks"]["citations"]["passed"]
    assert not result["checks"]["support_levels"]["passed"]
    assert not result["passed"]


def test_new_numeric_claim_is_rejected_even_if_fixture_oracle_missed_it(suite):
    case = suite["cases"][0]
    claim = "Analysed 900 synthetic support tickets using SQL queries and documented recurring issues."
    case["requirements"][0]["allowed_statements"].append(claim)
    case["output"]["evidence_map"][0]["statement"] = claim
    result = quality_eval.evaluate_case(case)
    assert not result["checks"]["unsupported_claims"]["passed"]
    assert any("numeric claims" in error for error in result["checks"]["unsupported_claims"]["errors"])


def test_extra_narrative_fields_are_not_silently_unchecked(suite):
    case = suite["cases"][0]
    case["output"]["summary"] = "Candidate ran 50 Kubernetes deployments."
    result = quality_eval.evaluate_case(case)
    assert not result["passed"]
    assert not result["checks"]["schema"]["passed"]


@pytest.mark.parametrize("change", [
    {"evidence_refs": []}, {"evidence_refs": ["invented"]},
    {"evidence_refs": ["receipt-42", "receipt-42"]}, {"job_url": "https://example.test/jobs/43"},
])
def test_submission_requires_registered_exact_job_evidence(suite, change):
    case = suite["cases"][1]
    case["output"].update(change)
    assert not quality_eval.evaluate_case(case)["passed"]


@pytest.mark.parametrize("mode", ["click", "uncertain", "failed", "different_job"])
def test_submit_attempt_or_nonconfirmed_receipt_cannot_succeed(suite, mode):
    case = suite["cases"][1]
    source = case["sources"][0]
    if mode == "click":
        source["kind"] = "submit_attempt"
    elif mode == "different_job":
        source["job_url"] = "https://example.test/jobs/43"
    else:
        source["status"] = mode
    result = quality_eval.evaluate_case(case)
    assert not result["passed"]
    assert not result["checks"]["submission_evidence"]["passed"]


def test_uncertain_output_can_honestly_report_no_receipt(suite):
    case = suite["cases"][1]
    case["sources"] = []
    case["output"] = {"job_url": case["job_url"], "status": "submission_uncertain", "evidence_refs": []}
    assert quality_eval.evaluate_case(case)["passed"]


@pytest.mark.parametrize("mode", ["bad_version", "duplicate_source", "duplicate_requirement", "unknown_source", "jd_mismatch"])
def test_invalid_fixture_fails_before_output(suite, mode):
    case = suite["cases"][0]
    if mode == "bad_version":
        case["schema_version"] = 2
    elif mode == "duplicate_source":
        case["sources"].append(copy.deepcopy(case["sources"][0]))
    elif mode == "duplicate_requirement":
        case["requirements"].append(copy.deepcopy(case["requirements"][0]))
    elif mode == "unknown_source":
        case["requirements"][0]["source_ids"] = ["unknown"]
    else:
        case["requirements"][0]["text"] = "A requirement that is absent from this JD"
    result = quality_eval.evaluate_case(case)
    assert not result["passed"]
    assert not result["checks"]["fixture"]["passed"]
    assert result["checks"]["schema"]["passed"] is None


def test_exported_assertion_matches_evaluator_for_positive_and_negative_outputs(suite, tmp_path):
    output_dir = tmp_path / "export"
    result = quality_eval.export_promptfoo(suite, output_dir)
    assert result["model_calls"] == 0
    config = yaml.safe_load((output_dir / "promptfooconfig.yaml").read_text(encoding="utf-8"))
    assert config["providers"] == ["echo"]
    assert config["prompts"] == ["{{ output }}"]
    assert len(config["tests"]) == len(suite["cases"])
    spec = importlib.util.spec_from_file_location("exported_assertion", output_dir / "assertion.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for test in config["tests"]:
        assert test["assert"] == [{"type": "python", "value": "file://assertion.py"}]
        context = {"vars": test["vars"]}
        assertion = module.get_assert(test["vars"]["output"], context)
        expected = quality_eval.evaluate_case(test["vars"]["case"], test["vars"]["output"])
        assert assertion["pass"] == expected["passed"]
        assert assertion["score"] == expected["score"]
        assert assertion["pass"]
        assert not module.get_assert("{}", context)["pass"]
    case = copy.deepcopy(suite["cases"][0])
    case["output"]["evidence_map"][1]["support_level"] = "direct"
    context = {"vars": {"case": json.dumps(case)}}
    failure = module.get_assert(json.dumps(case["output"]), context)
    assert not failure["pass"]
    assert "support_levels" in failure["reason"]


@pytest.mark.parametrize("context", [None, {}, {"vars": {}}, {"vars": {"case": "bad JSON"}}])
def test_assertion_invalid_context_fails_closed(context):
    assert quality_eval.get_assert("{}", context)["pass"] is False


def test_export_does_not_overwrite_existing_directory(suite, tmp_path):
    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    sentinel = output_dir / "assertion.py"
    sentinel.write_text("existing content", encoding="utf-8")
    with pytest.raises(FileExistsError):
        quality_eval.export_promptfoo(suite, output_dir)
    assert sentinel.read_text(encoding="utf-8") == "existing content"
    assert list(output_dir.iterdir()) == [sentinel]


def test_cli_success_failure_and_export_codes(tmp_path):
    from applypilot.commands.quality import app

    runner = CliRunner()
    good = runner.invoke(app, ["run", "--file", str(FIXTURES / "synthetic-pass.json")])
    assert good.exit_code == 0, good.output
    assert json.loads(good.output)["passed"]
    bad = runner.invoke(app, ["run", "--file", str(FIXTURES / "synthetic-fail.json")])
    assert bad.exit_code == 1, bad.output
    assert json.loads(bad.output)["failed_count"] == 2
    output_dir = tmp_path / "export"
    exported = runner.invoke(app, ["export-promptfoo", "--file", str(FIXTURES / "synthetic-pass.json"),
                                  "--output-dir", str(output_dir)])
    assert exported.exit_code == 0, exported.output
    repeated = runner.invoke(app, ["export-promptfoo", "--file", str(FIXTURES / "synthetic-pass.json"),
                                  "--output-dir", str(output_dir)])
    assert repeated.exit_code == 2
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    assert runner.invoke(app, ["run", "--file", str(invalid)]).exit_code == 2


def test_registration_is_lazy_and_evaluation_does_not_access_workspace(tmp_path):
    env = {**os.environ, "APPLYPILOT_DIR": str(tmp_path / "never-created"),
           "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    code = "import sys,json; from pathlib import Path; import applypilot.commands.quality; "
    code += "assert 'applypilot.config' not in sys.modules; assert 'applypilot.database' not in sys.modules; "
    code += "from applypilot.quality_eval import run_suite; "
    code += "assert run_suite(json.loads(Path(sys.argv[1]).read_text()))['passed']"
    result = subprocess.run([sys.executable, "-c", code, str(FIXTURES / "synthetic-pass.json")],
                            env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "never-created").exists()


def test_root_cli_registered_group_with_disposable_workspace(tmp_path):
    env = {**os.environ, "APPLYPILOT_DIR": str(tmp_path / "unused"),
           "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    env.pop("APPLYPILOT_DISCOVERY_ONLY", None)
    workspace = tmp_path / "evaluation-workspace"
    result = subprocess.run([
        sys.executable, "-m", "applypilot", "--workspace", str(workspace),
        "quality", "run", "--file", str(FIXTURES / "synthetic-pass.json"),
    ], env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["passed"]
    assert not workspace.exists()
    assert not (tmp_path / "unused").exists()
