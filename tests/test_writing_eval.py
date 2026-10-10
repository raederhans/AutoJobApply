from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from applypilot.application_answers import build_answer_plan
from applypilot.writing_common import build_artifact, content_digest, validate_draft
from applypilot.writing_eval import (
    SuiteValidationError,
    _task_input,
    build_comparison_prompt,
    compare_outputs,
    compare_outputs_balanced,
    evaluate_recorded,
    run_benchmark,
    summarize_comparisons,
    validate_suite,
)

SUITE_PATH = Path(__file__).parents[1] / "scripts" / "evals" / "writing-cases.json"


@pytest.fixture
def suite() -> dict:
    return json.loads(SUITE_PATH.read_text(encoding="utf-8"))


def _review() -> dict:
    return {"verdict": "pass", "issues": [], "unsupported_claims": [], "missed_parts": [], "voice_fit": "matched",
            "scores": {"relevance": 2, "specificity": 2, "naturalness": 2, "concision": 2}}


def _draft(quote: str, evidence_id: str) -> dict:
    return {"text": quote, "claims": [{"text": quote, "kind": "candidate", "evidence_ids": [evidence_id]}],
            "missing_facts": []}


class FakeClient:
    model = "synthetic-test-client"

    def __init__(self, outputs: list) -> None:
        self.outputs = list(outputs)
        self.messages = []
        self.last_response_meta = {"finish_reason": "stop"}

    def chat(self, messages, **kwargs):
        self.messages.append((messages, kwargs))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return json.dumps(output)


def _valid_record(suite: dict, identifier: str = "q-applied_ai-fit") -> dict:
    normalized = validate_suite(suite)
    case = next(case for case in normalized["cases"] if case["id"] == identifier)
    context = normalized["contexts"][case["context_id"]]
    task, selected = build_answer_plan(case["question"], context)
    evidence = next(item for item in selected["candidate_evidence"] if item["id"] == "python")
    draft = _draft(evidence["quote"], "python")
    artifact = build_artifact(genre="application_answer", task=task, context=selected, draft=draft,
                              validation=validate_draft(draft, selected, constraints=task["constraints"]), review=_review())
    return {"case_id": identifier, "variant": "new", "context_input_digest": content_digest(context),
            "task_input_digest": content_digest(_task_input(case)), "artifact": artifact}


def test_dataset_has_separate_genres_splits_and_synthetic_contexts(suite: dict) -> None:
    normalized = validate_suite(suite)
    assert len([case for case in normalized["cases"] if case["genre"] == "application_answer"]) == 24
    assert len([case for case in normalized["cases"] if case["genre"] == "cover_letter"]) == 8
    assert {case["split"] for case in normalized["cases"]} == {"development", "holdout"}
    for context in normalized["contexts"].values():
        assert context["role"]["job_id"].startswith("synthetic-")
        assert context["role"]["company_name"].startswith("Fictional ")
        assert all("path" not in source and "url" not in source for source in context["sources"])
    assert len(normalized["recorded_negatives"]) == 2
    assert validate_suite(normalized) == normalized


@pytest.mark.parametrize("mutation", ["context_ref", "job_binding", "duplicate", "unknown_field", "sibling_ref", "quote"])
def test_suite_rejects_stale_or_invalid_contracts(suite: dict, mutation: str) -> None:
    if mutation == "context_ref":
        suite["cases"][0]["context_id"] = "missing"
    elif mutation == "job_binding":
        suite["cases"][0]["question"]["job_id"] = "another-job"
    elif mutation == "duplicate":
        suite["cases"].append(copy.deepcopy(suite["cases"][0]))
    elif mutation == "unknown_field":
        suite["cases"][0]["question"]["current_value"] = "private"
    elif mutation == "sibling_ref":
        suite["cases"][0]["sibling_case_ids"] = ["q-product-why-fit"]
    else:
        suite["contexts"]["frontend"]["candidate_evidence"][0]["quote"] = "Invented source quotation."
    with pytest.raises(ValueError):
        validate_suite(suite)


def test_recorded_negative_checks_reject_metrics_and_voice_misuse(suite: dict) -> None:
    report = evaluate_recorded(suite, suite["recorded_negatives"])
    assert report["summary"]["contract_failed"] == 2
    codes = [{error["code"] for error in row["contract"]["errors"]} for row in report["records"]]
    assert "unsupported_number" in codes[0]
    assert "unsupported_reference" in codes[1]
    assert all(row["artifact"]["validation"]["passed"] is True for row in report["records"])
    assert all(row["contract"]["passed"] is False for row in report["records"])


def test_offline_success_is_contract_only_and_does_not_prove_naturalness(suite: dict) -> None:
    report = evaluate_recorded(suite, [_valid_record(suite)])
    row = report["records"][0]
    assert row["contract"]["passed"] is True
    assert row["contract"]["naturalness_verified"] is False
    assert row["semantic_review"]["provenance"] == "recorded_model_review_not_reexecuted"
    assert row["semantic_review"]["verified_truth"] is False
    assert report["comparison"]["status"] == "not_run"
    assert len(report["summary"]["missing"]) == 31


@pytest.mark.parametrize("mutation,expected", [
    ("source", "current_context_binding"), ("question", "current_task_binding"),
    ("text", "text_integrity"), ("authority", "authority"),
])
def test_recorded_artifacts_bind_to_current_original_inputs(suite: dict, mutation: str, expected: str) -> None:
    record = _valid_record(suite)
    if mutation == "source":
        suite["contexts"]["applied_ai"]["sources"][0]["text"] += "\nA new source observation."
    elif mutation == "question":
        case = next(case for case in suite["cases"] if case["id"] == record["case_id"])
        case["question"]["help_text"] = "Also explain the limitations."
    elif mutation == "text":
        record["artifact"]["text"] += " Changed."
    else:
        record["artifact"]["authority"] = "submit"
    result = evaluate_recorded(suite, [record])["records"][0]
    assert not result["contract"]["passed"]
    assert expected in {error["code"] for error in result["contract"]["errors"]}


def test_record_schema_unknown_refs_and_duplicates_are_rejected(suite: dict) -> None:
    with pytest.raises(SuiteValidationError, match="unknown case"):
        evaluate_recorded(suite, [{"case_id": "missing"}])
    record = _valid_record(suite)
    with pytest.raises(SuiteValidationError, match="Duplicate"):
        evaluate_recorded(suite, [record, record])
    record["artifact"] = "malformed artifact"
    assert evaluate_recorded(suite, [record])["summary"]["contract_failed"] == 1


def test_benchmark_new_reuses_question_plan_and_records_partial_errors(suite: dict) -> None:
    quote = suite["contexts"]["product"]["candidate_evidence"][2]["quote"]
    client = FakeClient([RuntimeError("synthetic provider failure"), _draft(quote, "research"), _review()])
    report = run_benchmark(suite, client=client, case_ids=["q-applied_ai-fit", "q-product-why-fit"], editorial=False)
    assert report["summary"]["errors"] == 1
    assert report["summary"]["contract_passed"] == 1
    assert report["records"][0]["error"].startswith("RuntimeError:")
    assert report["records"][1]["artifact"]["genre"] == "application_answer"
    assert report["authority"] == "none" and report["submission_ready"] is False


def test_baseline_is_raw_only_and_uses_same_client_settings(suite: dict) -> None:
    client = FakeClient([{"text": "A generic but relevant draft."}])
    result = run_benchmark(suite, client=client, case_ids=["q-applied_ai-fit"], variant="baseline")
    assert result["summary"]["baseline_raw_only"] == 1
    assert result["summary"]["contract_passed"] == 0
    assert result["records"][0]["contract"]["passed"] is None
    assert "claims" not in result["records"][0]
    assert "2-3 sentences" in client.messages[0][0][0]["content"]
    payload = json.loads(client.messages[0][0][1]["content"])
    assert all(item["id"] != "approval" for item in payload["context"]["candidate_evidence"])
    assert client.messages[0][1]["temperature"] == 0.25


def test_benchmark_never_constructs_an_implicit_client(suite: dict) -> None:
    with pytest.raises(SuiteValidationError, match="explicitly injected"):
        run_benchmark(suite, client=None, case_ids=["q-applied_ai-fit"])


def test_cross_question_siblings_are_shared_and_rechecked(suite: dict) -> None:
    context = suite["contexts"]["frontend"]
    react = context["candidate_evidence"][0]["quote"]
    team = context["candidate_evidence"][4]["quote"]
    client = FakeClient([_draft(react, "react"), _review(), _draft(team, "team"), _review()])
    report = run_benchmark(suite, client=client, case_ids=["q-frontend-why-fit", "q-frontend-ownership"], editorial=False)
    assert report["summary"]["contract_passed"] == 2
    sibling_row = report["records"][1]
    assert sibling_row["artifact"]["task"]["siblings"][0]["text"] == react
    sibling_row["siblings"][0]["artifact"]["text"] += " Corrupted."
    assert not evaluate_recorded(suite, [sibling_row])["records"][0]["contract"]["passed"]
    missing = run_benchmark(suite, client=FakeClient([]), case_ids=["q-frontend-ownership"], editorial=False)
    assert "earlier sibling" in missing["records"][0]["error"]


def test_cover_revision_runs_initial_then_revision_and_checks_both(suite: dict) -> None:
    quote = suite["contexts"]["applied_ai"]["candidate_evidence"][1]["quote"]
    client = FakeClient([_draft(quote, "python"), _review(), _draft(quote, "python"), _review()])
    report = run_benchmark(suite, client=client, case_ids=["cover-applied_ai-revision"], editorial=False)
    row = report["records"][0]
    assert report["summary"]["contract_passed"] == 1
    assert row["artifact"]["previous_revision"] == row["previous_artifact"]["revision_id"]
    assert row["artifact"]["task"]["operation"] == "revise"
    row["previous_artifact"]["text_digest"] = "corrupted"
    rechecked = evaluate_recorded(suite, [row])["records"][0]
    assert not rechecked["contract"]["passed"]
    assert "previous_binding" in {error["code"] for error in rechecked["contract"]["errors"]}
    row["previous_artifact"] = ["invalid artifact"]
    assert not evaluate_recorded(suite, [row])["records"][0]["contract"]["passed"]


def test_current_length_limits_are_recomputed(suite: dict) -> None:
    record = _valid_record(suite)
    case = next(case for case in suite["cases"] if case["id"] == record["case_id"])
    case["question"]["constraints"] = [{"kind": "max", "unit": "utf16", "value": 10, "source": "HTML maxlength"}]
    checked = evaluate_recorded(suite, [record])["records"][0]
    assert "length" in {error["code"] for error in checked["contract"]["errors"]}


def test_pairwise_order_is_explicit_and_counts_do_not_claim_truth(suite: dict) -> None:
    normalized = validate_suite(suite)
    case = next(case for case in normalized["cases"] if case["id"] == "q-applied_ai-fit")
    context = normalized["contexts"][case["context_id"]]
    with pytest.raises(SuiteValidationError, match="explicitly injected"):
        compare_outputs(None, case, context, "new prose", "baseline prose")
    client = FakeClient([{"winner": "A", "reason": "More specific supported evidence.", "issues_a": [], "issues_b": []}])
    comparison = compare_outputs(client, case, context, "new prose", "baseline prose", order="ba")
    assert comparison["winner_original"] == "B" and comparison["result"] == "loss"
    sent = json.loads(client.messages[0][0][1]["content"])
    assert sent["A"] == "baseline prose" and sent["B"] == "new prose"
    assert comparison["judge"]["provenance"] == "live_injected_model_preference_not_truth"
    assert summarize_comparisons([comparison]) == {"win": 0, "tie": 0, "loss": 1, "both_fail": 0,
                                                 "evaluated": 1, "status": "model_preference_only"}
    assert summarize_comparisons([])["status"] == "not_run"
    with pytest.raises(SuiteValidationError, match="Duplicate"):
        summarize_comparisons([comparison, comparison])


def test_comparison_uses_selected_sources_not_full_snapshots(suite: dict) -> None:
    normalized = validate_suite(suite)
    case = next(case for case in normalized["cases"] if case["id"] == "q-mismatched_voice-why-zh")
    system, payload = build_comparison_prompt(case, normalized["contexts"][case["context_id"]], "甲", "乙")
    assert payload["context"]["voice_examples"] == []
    assert "Deployment approval remains unconfirmed." not in json.dumps(payload)
    assert "both_fail" in system and "not proof" in system


def _comparison_inputs(suite: dict) -> tuple[dict, dict]:
    normalized = validate_suite(suite)
    case = next(case for case in normalized["cases"] if case["id"] == "q-applied_ai-fit")
    return case, normalized["contexts"][case["context_id"]]


def _judgment(winner: str) -> dict:
    return {"winner": winner, "reason": "Synthetic judge response for normalization testing.",
            "issues_a": [], "issues_b": []}


@pytest.mark.parametrize("ab,ba,result,winner_original", [
    ("A", "B", "win", "A"), ("B", "A", "loss", "B"),
    ("tie", "tie", "tie", "tie"), ("both_fail", "both_fail", "both_fail", "both_fail"),
])
def test_balanced_comparison_normalizes_both_orders_before_consensus(
    suite: dict, ab: str, ba: str, result: str, winner_original: str,
) -> None:
    case, context = _comparison_inputs(suite)

    class MetadataClient(FakeClient):
        def chat(self, messages, **kwargs):
            output = super().chat(messages, **kwargs)
            self.last_response_meta["request_id"] = f"synthetic-{len(self.messages)}"
            return output

    client = MetadataClient([_judgment(ab), _judgment(ba)])
    compared = compare_outputs_balanced(client, case, context, "candidate revision", "original draft")
    assert compared["direction"] == "a_relative_to_b"
    assert compared["result"] == result and compared["winner_original"] == winner_original
    assert compared["consensus"] == "consistent"
    assert compared["normalized_results"] == {"ab": result, "ba": result}
    assert [item["order"] for item in compared["reviews"]] == ["ab", "ba"]
    assert compared["reviews"][0]["judge"]["response"]["request_id"] == "synthetic-1"
    assert compared["reviews"][1]["judge"]["response"]["request_id"] == "synthetic-2"
    first, second = [json.loads(messages[0][1]["content"]) for messages in client.messages]
    assert (first["A"], first["B"]) == ("candidate revision", "original draft")
    assert (second["A"], second["B"]) == ("original draft", "candidate revision")
    assert compared["authority"] == "none" and compared["submission_ready"] is False
    assert summarize_comparisons([compared])[result] == 1


@pytest.mark.parametrize("ab,ba", [("A", "A"), ("tie", "A"), ("both_fail", "tie")])
def test_balanced_disagreement_is_inconclusive_never_an_implicit_tie(suite: dict, ab: str, ba: str) -> None:
    case, context = _comparison_inputs(suite)
    compared = compare_outputs_balanced(FakeClient([_judgment(ab), _judgment(ba)]), case, context, "a", "b")
    assert compared["result"] == "inconclusive" and compared["consensus"] == "order_sensitive"
    assert compared["winner_original"] is None
    assert compared["normalized_results"]["ab"] != compared["normalized_results"]["ba"]
    counts = summarize_comparisons([compared])
    assert counts["inconclusive"] == counts["order_sensitive"] == 1
    assert counts["tie"] == counts["win"] == counts["loss"] == counts["both_fail"] == 0
    assert counts["evaluated"] == 1


@pytest.mark.parametrize("bad", [
    {"winner": "A", "reason": "Specific", "issues_a": [], "issues_b": [], "status": "approved"},
    {"winner": ["A"], "reason": "Specific", "issues_a": [], "issues_b": []},
    {"winner": "win", "reason": "Specific", "issues_a": [], "issues_b": []},
    {"winner": "A", "reason": "", "issues_a": [], "issues_b": []},
    {"winner": "A", "reason": "Specific", "issues_a": [1], "issues_b": []},
    {"winner": "A", "reason": "Specific", "issues_a": []},
])
def test_balanced_judge_schema_is_strict_even_when_other_order_passes(suite: dict, bad: dict) -> None:
    case, context = _comparison_inputs(suite)
    client = FakeClient([_judgment("A"), bad])
    with pytest.raises(SuiteValidationError):
        compare_outputs_balanced(client, case, context, "candidate", "original")
    assert len(client.messages) == 2


def test_summary_keeps_old_reports_and_rejects_contradictory_direction_or_consensus() -> None:
    legacy = {"case_id": "legacy-case", "result": "win"}
    assert summarize_comparisons([legacy]) == {"win": 1, "tie": 0, "loss": 0, "both_fail": 0,
                                             "evaluated": 1, "status": "model_preference_only"}
    for invalid in (
        {**legacy, "direction": "b_relative_to_a"},
        {**legacy, "result": "tie", "consensus": "order_sensitive"},
        {**legacy, "result": "inconclusive", "consensus": "consistent"},
    ):
        with pytest.raises(SuiteValidationError):
            summarize_comparisons([invalid])


def test_comparison_rubric_keeps_general_guidance_and_weak_style_signals(suite: dict) -> None:
    case, context = _comparison_inputs(suite)
    system, _ = build_comparison_prompt(case, context, "a", "b")
    assert "factuality, ownership boundaries and full question coverage first" in system
    assert "role-fit inferences" in system and "future transfer" in system
    assert "soft target word ranges are guidance, never a failure rule" in system
    assert "Do not reward length, tool inventories" in system
    assert "Do not require the company name or praise in every answer" in system
    assert "Punctuation and stylistic markers are weak cues" in system
    assert "complete personal-voice imitation" in system


def test_synthetic_calibration_fixture_is_source_grounded_and_not_a_model_result() -> None:
    path = Path(__file__).parent / "fixtures" / "writing-editor-calibration.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    normalized = validate_suite({key: fixture[key] for key in ("schema_version", "contexts", "cases")})
    assert fixture["provenance"] == "synthetic_hand_authored_examples_not_model_evaluation"
    case_ids = {case["id"] for case in normalized["cases"]}
    assert len(fixture["pairs"]) == len(case_ids) == 5
    assert {pair["case_id"] for pair in fixture["pairs"]} == case_ids
    assert {pair["expected_result"] for pair in fixture["pairs"]} == {"win", "loss", "tie"}
    for pair in fixture["pairs"]:
        assert set(pair) == {"case_id", "a", "b", "expected_result", "rationale"}
        assert pair["a"] and pair["b"] and pair["rationale"]
    context = normalized["contexts"]["calibration"]
    assert context["role"]["company_name"].startswith("Fictional ")
    assert context["voice_examples"] == []
    assert all("path" not in source and "url" not in source for source in context["sources"])


@pytest.mark.parametrize("function,case_id", [
    ("generate_answer", "q-applied_ai-fit"),
    ("generate_cover_draft", "cover-applied_ai-initial"),
    ("generate_cover_draft", "cover-applied_ai-revision"),
])
def test_benchmark_passes_editorial_setting_to_both_writers(
    suite: dict, monkeypatch: pytest.MonkeyPatch, function: str, case_id: str,
) -> None:
    settings = []

    def capture(*args, **kwargs):
        settings.append(kwargs["editorial"])
        raise RuntimeError("Synthetic stop after observing generator settings")

    monkeypatch.setattr(f"applypilot.writing_eval.{function}", capture)
    default = run_benchmark(suite, client=FakeClient([]), case_ids=[case_id])
    disabled = run_benchmark(suite, client=FakeClient([]), case_ids=[case_id], editorial=False)
    assert settings == [True, False]
    assert default["records"][0]["provenance"]["editorial"] is True
    assert disabled["records"][0]["provenance"]["editorial"] is False
    assert default["summary"]["errors"] == disabled["summary"]["errors"] == 1
    with pytest.raises(SuiteValidationError, match="editorial must be a boolean"):
        run_benchmark(suite, client=FakeClient([]), case_ids=[case_id], editorial=1)
