import json
from copy import deepcopy
from pathlib import Path

import pytest

from applypilot.writing_common import (
    build_artifact,
    call_json,
    content_digest,
    parse_json_object,
    prompt_context,
    review_draft,
    save_artifact,
    text_counts,
    validate_draft,
    validate_review,
)


class FakeClient:
    model = "synthetic-model"

    def __init__(self, response, *, finish_reason="stop"):
        self.response = response
        self.last_response_meta = {"finish_reason": finish_reason}
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": deepcopy(messages), "kwargs": deepcopy(kwargs)})
        return self.response if isinstance(self.response, str) else json.dumps(self.response, ensure_ascii=False)


@pytest.fixture
def context():
    return {
        "sources": [
            {"id": "candidate", "kind": "candidate_facts", "text": "Built Python reporting for 2 teams."},
            {"id": "jd", "kind": "jd", "text": "Build Python reporting."},
            {"id": "company", "kind": "company", "text": "The company employs 500 people."},
            {"id": "voice", "kind": "voice", "text": "I led 500 people."},
        ],
        "candidate_evidence": [
            {
                "id": "python",
                "source_id": "candidate",
                "quote": "Built Python reporting for 2 teams.",
                "status": "confirmed",
                "boundaries": ["Prototype only."],
            }
        ],
        "role": {"job_id": "job-1", "jd_source_id": "jd"},
        "company": {
            "name": "Example",
            "facts": [
                {
                    "id": "headcount",
                    "source_id": "company",
                    "quote": "The company employs 500 people.",
                    "verification_status": "verified",
                }
            ],
        },
        "voice_examples": [
            {
                "id": "style",
                "source_id": "voice",
                "quote": "I led 500 people.",
                "language": "en",
                "genre": "application_answer",
                "authorship": "user",
                "user_approved": False,
            }
        ],
    }


def draft(text="Built Python reporting for 2 teams.", *, kind="candidate", ref="python", missing=()):
    return {
        "text": text,
        "claims": [{"text": text, "kind": kind, "evidence_ids": [ref]}],
        "missing_facts": list(missing),
    }


def review(**changes):
    return {
        "verdict": "pass",
        "issues": [],
        "unsupported_claims": [],
        "missed_parts": [],
        "voice_fit": "matched",
        "scores": {"relevance": 2, "specificity": 2, "naturalness": 2, "concision": 2},
        **changes,
    }


def test_review_cannot_claim_voice_calibration_without_samples(context):
    context["voice_examples"] = []
    checked = review_draft(FakeClient(review()), draft=draft(), context=context, task={}, genre="application_answer")
    assert checked["voice_fit"] == "uncalibrated"


def test_internal_workflow_note_cannot_pass_as_cover_prose(context):
    text = draft()["text"] + "\n\nThis is a draft only, not approved or submitted."
    value = {"text": text, "claims": draft()["claims"], "missing_facts": []}
    checked = validate_draft(value, context, genre="cover_letter")
    assert not checked["passed"]
    assert "internal_note" in {item["code"] for item in checked["errors"]}


@pytest.mark.parametrize(
    "raw",
    [
        "{} trailing",
        "prefix {}",
        "[{}]",
        '{"a":1,"a":2}',
        '{"nested":{"a":1,"a":2}}',
        '{"number":NaN}',
        '{"number":Infinity}',
        "```json\n{}\n```\n{}",
    ],
)
def test_json_parser_rejects_ambiguous_or_non_json_responses(raw):
    with pytest.raises(ValueError):
        parse_json_object(raw)


def test_json_parser_allows_one_fence_and_exact_unicode():
    assert parse_json_object('```json\n{"text":"中文，😀！"}\n```') == {"text": "中文，😀！"}


@pytest.mark.parametrize("response,finish", [("", "stop"), ("  ", "stop"), ("{}", "length")])
def test_call_json_rejects_empty_and_exhausted_output(response, finish):
    client = FakeClient(response, finish_reason=finish)
    with pytest.raises(ValueError):
        call_json(client, "Fixed rules", {"question": "完整问题😀"})
    assert len(client.calls) == 1
    assert json.loads(client.calls[0]["messages"][1]["content"]) == {"question": "完整问题😀"}


def test_length_counters_distinguish_words_unicode_and_utf16(context):
    text = "中😀 next\tword\nlast"
    counts = text_counts(text)
    assert counts["words"] == 4
    assert counts["characters"] == 17
    assert counts["utf16"] == 18
    no_claim = {"text": "中😀", "claims": [], "missing_facts": []}
    constraint = {"kind": "max", "unit": "utf16", "value": 2, "source": "native:maxlength"}
    checked = validate_draft(no_claim, context, constraints=[constraint])
    assert checked["passed"] is False
    assert checked["counts"]["utf16"] == 3
    assert checked["errors"][0]["code"] == "length"
    assert validate_draft(no_claim, context, constraints=[{**constraint, "unit": "characters"}])["passed"]


@pytest.mark.parametrize("source,text,passed", [
    ("Used 20+ curated examples.", "Used more than 20 curated examples.", True),
    ("Used 20+ curated examples.", "Used over 20 curated examples.", True),
    ("Used 20+ curated examples.", "Used 20 curated examples.", False),
    ("Used 20+ curated examples.", "Used more than 21 curated examples.", False),
    ("Used 20 curated examples.", "Used more than 20 curated examples.", False),
])
def test_lower_bound_numbers_accept_equivalent_prose_without_changing_the_amount(context, source, text, passed):
    context["sources"][0]["text"] = source
    context["candidate_evidence"][0]["quote"] = source
    assert validate_draft(draft(text), context)["passed"] is passed


@pytest.mark.parametrize("model,budget", [("deepseek-v4-pro", 16384), ("synthetic-model", 4096)])
def test_writing_preserves_provider_reasoning_and_leaves_room_for_output(model, budget):
    client = FakeClient({"text": "ok"})
    client.model = model
    assert call_json(client, "Rules", {}) == {"text": "ok"}
    options = client.calls[0]["kwargs"]
    assert "thinking" not in options
    assert "reasoning_effort" not in options
    assert options["max_tokens"] == budget
    call_json(client, "Rules", {}, max_tokens=6000)
    assert client.calls[1]["kwargs"]["max_tokens"] == 6000


def test_explicit_word_bounds_and_counter_rule_are_recorded(context):
    text = {"text": "a b", "claims": [], "missing_facts": []}
    checked = validate_draft(
        text, context, constraints=[{"kind": "min", "unit": "words", "value": 3, "source": "question:at_least"}]
    )
    assert checked["passed"] is False
    assert checked["counts"]["words"] == 2
    assert checked["counts"]["word_count_rule"] == "whitespace_tokens"
    assert any(item["code"] == "word_counter" for item in checked["warnings"])


@pytest.mark.parametrize("replacement", ["中文,😀！", "中文，😀!", "中文，😀！ ", "中文，😀。"])
def test_exact_digest_detects_chinese_punctuation_and_whitespace(replacement):
    assert content_digest("中文，😀！") != content_digest(replacement)


def test_exact_digest_distinguishes_unicode_normalization_and_line_endings():
    assert content_digest("é") != content_digest("e\u0301")
    assert content_digest("第一行\r\n第二行") != content_digest("第一行\n第二行")


@pytest.mark.parametrize("ref", ["missing", "style", "headcount"])
def test_candidate_claims_cannot_borrow_voice_or_company_evidence(context, ref):
    checked = validate_draft(draft("I led 500 people.", ref=ref), context)
    assert not checked["passed"]
    assert "unsupported_reference" in {item["code"] for item in checked["errors"]}


def test_company_number_is_not_support_for_candidate_ownership(context):
    checked = validate_draft(draft("Built Python reporting for 500 teams."), context)
    assert not checked["passed"]
    assert "unsupported_number" in {item["code"] for item in checked["errors"]}
    assert "uncited_number" in {item["code"] for item in checked["errors"]}
    checked = validate_draft(draft("The company employs 500 people.", kind="company", ref="headcount"), context)
    assert checked["passed"]


def test_role_statements_use_only_current_jd_and_never_prove_personal_history(context):
    context["sources"].append({"id": "other-jd", "kind": "jd", "text": "Manage 500 people."})
    assert prompt_context(context)["claim_reference_ids"]["role"] == ["jd"]
    assert validate_draft(draft("Build Python reporting.", kind="role", ref="jd"), context)["passed"]
    for kind, ref in [("candidate", "jd"), ("company", "jd"), ("role", "other-jd"), ("role", "python")]:
        checked = validate_draft(draft("Build Python reporting.", kind=kind, ref=ref), context)
        assert "unsupported_reference" in {item["code"] for item in checked["errors"]}
    assert checked["entailment_verified"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"issues": [{"category": "ownership", "detail": "Inflated role."}]},
        {"unsupported_claims": ["Made up history."]},
        {"missed_parts": ["Reflection"]},
        {"voice_fit": "mismatch"},
        {"scores": {"relevance": True, "specificity": 2, "naturalness": 2, "concision": 2}},
    ],
)
def test_semantic_pass_with_open_issues_or_malformed_scores_is_rejected(changes):
    with pytest.raises(ValueError):
        validate_review(review(**changes))


def test_semantic_review_receives_every_sentence_and_selected_boundaries(context):
    undeclared = {
        "text": "I built reporting. I led every team. 我还负责所有上线。😀",
        "claims": [],
        "missing_facts": [],
    }
    client = FakeClient(review(verdict="revise", unsupported_claims=["I led every team."]))
    task = {
        "job_id": "job-1",
        "question": {"text": "Describe contribution AND reflection.", "help_text": "Retain team ownership boundaries."},
    }
    checked = review_draft(client, draft=undeclared, context=context, task=task, genre="application_answer")
    payload = json.loads(client.calls[0]["messages"][1]["content"])
    assert payload["draft"] == undeclared
    assert payload["task"] == task
    assert payload["context"]["candidate_evidence"][0]["boundaries"] == ["Prototype only."]
    assert payload["context"]["voice_examples"][0]["usage"] == "style_only"
    assert checked["verdict"] == "revise"


def test_prompt_context_excludes_unconfirmed_evidence_and_unverified_company_facts(context):
    context["candidate_evidence"][0]["status"] = "conditional"
    context["company"]["facts"][0]["verification_status"] = "unverified"
    context["voice_examples"][0].update(authorship="assistant", user_approved=False)
    payload = prompt_context(context)
    assert payload["candidate_evidence"] == []
    assert payload["company"]["facts"] == []
    assert payload["voice_examples"] == []


def artifact(context, *, missing=(), previous_revision=None):
    candidate = draft(missing=missing)
    return build_artifact(
        genre="application_answer",
        task={"job_id": "job-1", "question_id": "q-one"},
        context=context,
        draft=candidate,
        validation=validate_draft(candidate, context),
        review=review(),
        previous_revision=previous_revision,
    )


def test_missing_facts_cannot_be_overruled_by_semantic_pass(context):
    incomplete = artifact(context, missing=["A real example of handling conflict."])
    assert incomplete["status"] == "needs_fact"
    assert incomplete["authority"] == "none"
    assert incomplete["submission_ready"] is False


def test_revision_artifacts_persist_exact_text_and_never_overwrite(context, tmp_path):
    first = artifact(context)
    second = artifact(context, previous_revision=first["revision_id"])
    assert first["revision_id"] != second["revision_id"]
    assert second["previous_revision"] == first["revision_id"]
    paths1 = save_artifact(first, tmp_path)
    paths2 = save_artifact(second, tmp_path)
    original_bytes = Path(paths1["artifact_path"]).read_bytes()
    assert Path(paths1["text_path"]).read_text(encoding="utf-8") == first["text"]
    saved = json.loads(Path(paths2["artifact_path"]).read_text(encoding="utf-8"))
    assert saved["authority"] == "none" and saved["submission_ready"] is False
    assert saved["status"] == "reviewed_draft"
    with pytest.raises(FileExistsError):
        save_artifact(first, tmp_path)
    assert Path(paths1["artifact_path"]).read_bytes() == original_bytes


@pytest.mark.parametrize("mutation", ["text", "context", "authority", "submission_ready"])
def test_changed_or_authorized_artifacts_are_rejected_before_persistence(context, tmp_path, mutation):
    changed = artifact(context)
    if mutation == "text":
        changed["text"] += "，😀"
    elif mutation == "context":
        changed["context_snapshot"]["sources"][0]["text"] += "。"
    elif mutation == "authority":
        changed["authority"] = "submit"
    else:
        changed["submission_ready"] = True
    with pytest.raises(ValueError):
        save_artifact(changed, tmp_path)
    assert list(tmp_path.iterdir()) == []
