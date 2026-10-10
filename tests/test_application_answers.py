import json
from copy import deepcopy

import pytest

from applypilot.application_answers import (
    build_answer_messages,
    build_answer_plan,
    classify_question,
    generate_answer,
)
from applypilot.application_questions import normalize_question
from applypilot.writing_common import content_digest


class FakeClient:
    model = "synthetic-model"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.last_response_meta = {}

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": deepcopy(messages), "kwargs": deepcopy(kwargs)})
        assert self.responses, "Unexpected model call"
        response = self.responses.pop(0)
        self.last_response_meta = {"finish_reason": "stop"}
        if isinstance(response, tuple):
            response, self.last_response_meta = response
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)

    def payload(self, index):
        return json.loads(self.calls[index]["messages"][1]["content"])


@pytest.fixture
def context():
    return {
        "schema_version": 1,
        "sources": [
            {
                "id": "candidate",
                "kind": "candidate_facts",
                "text": "Built Python reporting.",
                "boundaries": ["Prototype only; do not claim production deployment."],
            },
            {"id": "jd", "kind": "jd", "text": "Use Python reporting and data validation in client delivery."},
            {"id": "company", "kind": "company", "text": "Example delivers client projects."},
            {"id": "voice", "kind": "voice", "text": "I prefer direct explanations. I led 500 employees."},
            {"id": "resume", "kind": "resume", "text": "Led the entire Python department."},
        ],
        "candidate_evidence": [
            {"id": "python", "source_id": "candidate", "quote": "Built Python reporting.", "status": "confirmed"},
            {
                "id": "resume-claim",
                "source_id": "resume",
                "quote": "Led the entire Python department.",
                "status": "confirmed",
            },
        ],
        "role": {
            "job_id": "job-1",
            "title": "Python Analyst",
            "company_name": "Example",
            "jd_source_id": "jd",
            "language": "en",
        },
        "company": {
            "name": "Example",
            "facts": [
                {
                    "id": "delivery",
                    "source_id": "company",
                    "quote": "Example delivers client projects.",
                    "verification_status": "verified",
                }
            ],
        },
        "voice_examples": [
            {
                "id": "style",
                "source_id": "voice",
                "quote": "I led 500 employees.",
                "language": "en",
                "genre": "application_answer",
                "authorship": "user",
                "user_approved": False,
            }
        ],
    }


def question(**changes):
    return normalize_question(
        {
            "job_id": "job-1",
            "page_id": "page-1",
            "field_key": "answer",
            "text": "How is your Python reporting experience relevant to this role?",
            **changes,
        }
    )


def draft(text="Built Python reporting.", evidence_id="python", *, missing=()):
    return {
        "text": text,
        "claims": [{"text": text, "kind": "candidate", "evidence_ids": [evidence_id]}],
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


def test_compound_question_and_help_are_preserved_in_plan_writer_and_reviewer(context):
    raw = "请解释经验😀。\n" * 120 + "Why this role and how does your Python reporting experience help?"
    help_text = "Address both motivation AND personal contribution. Preserve team boundaries."
    q = question(text=raw, help_text=help_text)
    client = FakeClient(draft(), review())
    artifact = generate_answer(q, context, client=client, max_repairs=0, editorial=False)
    assert artifact["status"] == "reviewed_draft"
    for index in (0, 1):
        task = client.payload(index)["task"]
        assert task["question"]["text"] == raw
        assert task["question"]["help_text"] == help_text
        assert task["question_revision"] == q["revision"]
    assert {"role_motivation", "role_fit"} <= set(artifact["task"]["intents"])
    assert artifact["authority"] == "none" and artifact["submission_ready"] is False


@pytest.mark.parametrize(
    "text,route",
    [
        ("What is your expected salary?", "confirmed_fact"),
        ("What is your work authorization?", "confirmed_fact"),
        ("Please write a cover letter.", "cover_letter"),
        ("Complete this assessment", "assessment"),
    ],
)
def test_non_narrative_questions_are_routed_without_model_calls(context, text, route):
    q = question(text=text)
    assert classify_question(q)["route"] == route
    client = FakeClient()
    with pytest.raises(ValueError, match=route):
        generate_answer(q, context, client=client, editorial=False)
    assert client.calls == []


def test_exact_job_binding_precedes_generation(context):
    client = FakeClient()
    with pytest.raises(ValueError, match="same exact job"):
        generate_answer(question(job_id="job-2"), context, client=client, editorial=False)
    assert not client.calls


@pytest.mark.parametrize("text", [
    "Why this role? Do not paste a cover letter.",
    "No cover letter is needed; describe your own contribution.",
    "Please describe your experience without a cover letter.",
    "请介绍相关经历，不用写一封求职信。",
])
def test_negative_cover_mentions_remain_application_questions(text):
    assert classify_question(question(text=text))["route"] == "application_answer"


def test_answer_revision_requires_a_specific_edit_request(context):
    client = FakeClient()
    with pytest.raises(ValueError, match="explicit revision request"):
        generate_answer(question(), context, previous_draft={}, revision_request="  ", client=client, editorial=False)
    assert client.calls == []


def test_selected_sources_and_voice_do_not_become_candidate_facts(context):
    task, selected = build_answer_plan(question(), context)
    _, payload = build_answer_messages(task, selected)
    assert [item["id"] for item in payload["context"]["candidate_evidence"]] == ["python"]
    assert "Led the entire Python department" not in json.dumps(payload)
    assert payload["context"]["voice_examples"][0]["usage"] == "style_only"
    contaminated = draft("I led 500 employees.", "style")
    client = FakeClient(contaminated)
    result = generate_answer(question(), context, client=client, max_repairs=0, editorial=False)
    assert result["status"] == "needs_revision"
    assert "unsupported_reference" in {item["code"] for item in result["validation"]["errors"]}
    assert len(client.calls) == 1


def test_no_relevant_candidate_evidence_returns_needs_fact_without_client(context):
    context["candidate_evidence"] = []
    client = FakeClient()
    result = generate_answer(question(), context, client=client, editorial=False)
    assert result["status"] == "needs_fact"
    assert result["draft"]["missing_facts"]
    assert result["review"] is None
    assert client.calls == []


@pytest.mark.parametrize(
    "response",
    [
        "",
        "   ",
        "not JSON",
        '{"text":"a","text":"b","claims":[],"missing_facts":[]}',
        {"text": "Built Python reporting.", "claims": []},
        {"text": "Built Python reporting.", "claims": [], "missing_facts": [], "status": "approved"},
        ({"text": "Built Python reporting.", "claims": [], "missing_facts": []}, {"finish_reason": "length"}),
    ],
)
def test_invalid_or_truncated_model_output_is_not_accepted(context, response):
    client = FakeClient(response)
    with pytest.raises(ValueError):
        generate_answer(question(), context, client=client, editorial=False)
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "bad,code",
    [
        (draft(evidence_id="missing-source"), "unsupported_reference"),
        (draft("Built Python reporting for 900 clients."), "unsupported_number"),
        (draft("Dear Hiring Team, Built Python reporting."), "wrong_genre"),
        ({"text": "", "claims": [], "missing_facts": []}, "empty_text"),
    ],
)
def test_deterministically_bad_drafts_are_never_reviewed(context, bad, code):
    client = FakeClient(bad)
    result = generate_answer(question(), context, client=client, max_repairs=0, editorial=False)
    assert result["status"] == "needs_revision"
    assert result["review"] is None
    assert code in {item["code"] for item in result["validation"]["errors"]}
    assert len(client.calls) == 1


def test_one_repair_keeps_full_question_and_source_boundaries(context):
    q = question(
        text="Why this role?\n" + "Describe Python reporting AND personal contribution. " * 100,
        help_text="Do not omit the reflection. At most 150 words.",
    )
    client = FakeClient(draft("Built Python reporting for 900 clients."), draft(), review())
    result = generate_answer(q, context, client=client, editorial=False)
    assert result["status"] == "reviewed_draft"
    assert result["generation"]["repairs"] == 1 and result["generation"]["calls"] == 3
    repair = client.payload(1)
    assert repair["task"]["question"] == q
    evidence = repair["context"]["candidate_evidence"]
    assert evidence == client.payload(0)["context"]["candidate_evidence"]
    assert evidence[0]["boundaries"] == ["Prototype only; do not claim production deployment."]
    assert repair["repair_feedback"]["previous_text"] == "Built Python reporting for 900 clients."
    assert "Led the entire Python department" not in json.dumps(repair)


def test_semantic_claim_omission_is_reviewed_from_full_text_and_never_approved(context):
    text = "Built Python reporting. I also led every team and shipped the product to all clients."
    omitted = {"text": text, "claims": [], "missing_facts": []}
    verdict = review(
        verdict="revise",
        unsupported_claims=["I also led every team and shipped the product to all clients."],
        issues=[{"category": "ownership", "detail": "Prototype-only source cannot support ownership or shipping."}],
    )
    client = FakeClient(omitted, verdict)
    result = generate_answer(question(), context, client=client, max_repairs=0, editorial=False)
    assert client.payload(1)["draft"]["text"] == text
    assert client.payload(1)["draft"]["claims"] == []
    assert "EVERY sentence" in client.calls[1]["messages"][0]["content"]
    assert result["status"] == "needs_revision"
    assert result["review"]["unsupported_claims"]


def test_semantic_repair_reviews_new_text_again(context):
    first_review = review(
        verdict="revise",
        issues=[{"category": "coverage", "detail": "Explain reflection."}],
        missed_parts=["reflection"],
    )
    client = FakeClient(draft(), first_review, draft(), review())
    result = generate_answer(question(), context, client=client, editorial=False)
    assert result["generation"]["calls"] == 4
    assert result["generation"]["repairs"] == 1
    assert client.payload(2)["repair_feedback"]["review"] == first_review
    assert client.payload(3)["draft"] == result["draft"]


def test_missing_real_episode_never_becomes_reviewed_draft(context):
    q = question(text="Describe a real Python reporting conflict and your personal reflection.")
    missing = {"text": "", "claims": [], "missing_facts": ["A confirmed real conflict and reflection."]}
    client = FakeClient(missing)
    result = generate_answer(q, context, client=client, editorial=False)
    assert result["status"] == "needs_fact"
    assert result["review"] is None
    assert len(client.calls) == 1


@pytest.mark.parametrize("mutation", ["job", "genre", "question", "text", "context", "draft_text"])
def test_previous_draft_rejects_cross_scope_and_exact_unicode_tampering(context, mutation):
    q = question()
    previous = generate_answer(q, context, client=FakeClient(draft(), review()), editorial=False)
    previous = deepcopy(previous)
    if mutation == "job":
        previous["task"]["job_id"] = "job-2"
    elif mutation == "genre":
        previous["genre"] = "cover_letter"
    elif mutation == "question":
        previous["task"]["question_id"] = "another-question"
    elif mutation == "text":
        previous["text"] += "。"
    elif mutation == "context":
        previous["context_snapshot"]["sources"][0]["text"] += "！"
    else:
        previous["draft"]["text"] += "😀"
    client = FakeClient()
    with pytest.raises(ValueError, match="Previous answer"):
        generate_answer(q, context, previous_draft=previous, revision_request="Be more direct.", client=client, editorial=False)
    assert not client.calls


def test_revision_keeps_previous_id_and_draft_is_editing_material_only(context):
    q = question()
    previous = generate_answer(q, context, client=FakeClient(draft(), review()), editorial=False)
    client = FakeClient(draft(), review())
    updated = generate_answer(
        q, context, previous_draft=previous, revision_request="Clarify my contribution.", client=client
    , editorial=False)
    assert updated["previous_revision"] == previous["revision_id"]
    assert updated["revision_id"] != previous["revision_id"]
    assert client.payload(0)["revision"] == {
        "previous_text": previous["text"],
        "request": "Clarify my contribution.",
        "usage": "editing_material_only",
    }
    assert content_digest(previous["text"]) == previous["text_digest"]


def test_contradictory_semantic_pass_fails_closed(context):
    bad = review(issues=[{"category": "factuality", "detail": "A claim is unsupported."}])
    client = FakeClient(draft(), bad)
    with pytest.raises(ValueError, match="contradicts"):
        generate_answer(question(), context, client=client, editorial=False)
    assert len(client.calls) == 2


def test_repair_budget_stops_after_one_attempt_and_never_accepts_unknown_support(context):
    client = FakeClient(draft(evidence_id="unknown"), draft(evidence_id="unknown"))
    result = generate_answer(question(), context, client=client, editorial=False)
    assert result["status"] == "needs_revision"
    assert result["generation"]["repairs"] == 1
    assert result["generation"]["calls"] == 2
    assert len(client.calls) == 2


def test_semantic_needs_fact_stops_without_repair(context):
    client = FakeClient(draft(), review(verdict="needs_fact", missed_parts=["A confirmed real conflict episode."]))
    result = generate_answer(
        question(text="Describe Python reporting conflict and your reflection."), context, client=client
    , editorial=False)
    assert result["status"] == "needs_fact"
    assert result["generation"]["repairs"] == 0
    assert len(client.calls) == 2


def test_sibling_text_is_bound_to_job_and_used_only_for_repetition(context):
    sibling = generate_answer(question(field_key="other"), context, client=FakeClient(draft(), review()), editorial=False)
    task, _ = build_answer_plan(question(), context, siblings=[sibling])
    assert task["siblings"] == [
        {
            "question_id": sibling["task"]["question_id"],
            "text": sibling["text"],
            "usage": "avoid_redundancy_only_not_facts",
        }
    ]
    changed = deepcopy(sibling)
    changed["task"]["job_id"] = "job-2"
    client = FakeClient()
    with pytest.raises(ValueError, match="same job"):
        generate_answer(question(), context, siblings=[changed], client=client, editorial=False)
    assert client.calls == []


def test_unresolved_limit_text_is_preserved_without_invented_count_rule(context):
    q = question(
        help_text="Use the portal's own character limit; its counting rule is unspecified.",
        unresolved_instructions=["Use the portal's own character limit; its counting rule is unspecified."],
        completeness="partial",
    )
    client = FakeClient(draft(), review())
    result = generate_answer(q, context, client=client, max_repairs=0, editorial=False)
    assert result["task"]["constraints"] == []
    assert result["task"]["question"]["unresolved_instructions"] == q["unresolved_instructions"]
    assert client.payload(1)["task"]["question"]["help_text"] == q["help_text"]
    assert result["authority"] == "none" and result["submission_ready"] is False
