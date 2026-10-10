"""Editorial proposals must survive the same factual and task checks as drafts."""

import json
from copy import deepcopy

import pytest

from applypilot.application_answers import classify_question, generate_answer
from applypilot.cover_letter_drafts import generate_cover_draft
from applypilot.writing_common import save_artifact, validate_review
from applypilot.writing_editor import edit_draft, validate_edit

FACT = "I built a Python dashboard as a prototype."
BOILERPLATE = " This experience demonstrates my ability to create meaningful solutions."


def context():
    return {
        "schema_version": 1,
        "sources": [{"id": "project", "kind": "project_evidence", "text": FACT},
                    {"id": "jd", "kind": "jd", "text": "Build Python dashboards for operations."}],
        "candidate_evidence": [{"id": "dashboard", "source_id": "project", "quote": FACT,
                                "status": "confirmed", "boundaries": ["Prototype, not production deployment"]}],
        "role": {"job_id": "fictional", "title": "Analyst", "company_name": "Example", "jd_source_id": "jd"},
        "company": None, "voice_examples": [],
    }


def question():
    return {"job_id": "fictional", "page_id": "one", "field_key": "fit",
            "text": "What makes you particularly well suited for this role?", "language": "en"}


def draft(text=FACT):
    return {"text": text, "claims": [{"text": FACT, "kind": "candidate", "evidence_ids": ["dashboard"]}],
            "missing_facts": []}


def review(**overrides):
    return {"verdict": "pass", "issues": [], "unsupported_claims": [], "missed_parts": [],
            "voice_fit": "uncalibrated", "scores": {"relevance": 2, "specificity": 1, "naturalness": 1, "concision": 2},
            **overrides}


def edit(original, revised):
    return {"decision": "revise", "edits": [{"before": original["text"], "after": revised["text"],
             "reason": "Remove the self-appraisal which repeats the concrete action."}], "draft": revised}


class Client:
    model = "fixture"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.last_response_meta = {}

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.last_response_meta = {"usage": {"total_tokens": len(self.calls)}}
        return json.dumps(self.responses.pop(0))

    def payload(self, index):
        return json.loads(self.calls[index][1]["content"])


def generate(genre, client, **kwargs):
    if genre == "application_answer":
        return generate_answer(question(), context(), client=client, **kwargs)
    return generate_cover_draft(context(), client=client, **kwargs)


@pytest.mark.parametrize("genre", ["application_answer", "cover_letter"])
def test_default_editor_preserves_before_after_and_reviews_final_text(genre, tmp_path):
    original, revised = draft(FACT + BOILERPLATE), draft()
    client = Client(original, edit(original, revised), review())
    result = generate(genre, client)
    assert result["text"] == FACT
    assert result["status"] == "reviewed_draft"
    assert result["authority"] == "none" and not result["submission_ready"]
    assert result["generation"]["calls"] == 3
    record = result["editing"]["passes"][0]
    assert record["before"] == original and record["after"] == revised
    assert record["edits"][0]["reason"]
    assert client.payload(2)["draft"] == revised
    assert client.payload(2)["before_edit"] == {"text": original["text"], "usage": "editing_material_only_not_facts"}
    assert result["review"]["voice_fit"] == "uncalibrated"
    assert len(result["attempts"]) == 1
    paths = save_artifact(result, tmp_path)
    with open(paths["artifact_path"], encoding="utf-8") as handle:
        assert json.load(handle)["editing"]["passes"][0] == record
    client.last_response_meta["usage"]["total_tokens"] = 999
    assert result["generation"]["responses"][-1]["usage"]["total_tokens"] == 3


@pytest.mark.parametrize("genre", ["application_answer", "cover_letter"])
def test_good_text_can_be_kept_without_forced_rewrite(genre):
    original = draft()
    client = Client(original, {"decision": "keep", "edits": [], "draft": original}, review())
    result = generate(genre, client)
    assert result["text"] == original["text"]
    assert result["editing"]["passes"][0]["decision"] == "keep"
    assert result["generation"]["repairs"] == 0


@pytest.mark.parametrize("genre", ["application_answer", "cover_letter"])
def test_editor_cannot_promote_new_numbers_even_if_semantic_reviewer_passes(genre):
    original, revised = draft(FACT + BOILERPLATE), draft(FACT + " It saved 90% of reporting time.")
    client = Client(original, edit(original, revised), review())
    result = generate(genre, client, max_repairs=0)
    assert result["status"] == "needs_revision"
    assert "uncited_number" in {item["code"] for item in result["validation"]["errors"]}
    assert result["editing"]["passes"][0]["after"] == revised
    assert not result["submission_ready"]


@pytest.mark.parametrize("genre", ["application_answer", "cover_letter"])
def test_lost_ownership_qualifier_is_reviewed_and_repaired_once(genre):
    original = draft(FACT + BOILERPLATE)
    inflated = {"text": "I deployed a Python dashboard in production.", "claims": [], "missing_facts": []}
    problem = review(verdict="revise", issues=[{"category": "ownership", "detail": "Restore the prototype boundary."}],
                     unsupported_claims=[inflated["text"]])
    client = Client(original, edit(original, inflated), problem, draft(), review())
    result = generate(genre, client)
    assert result["status"] == "reviewed_draft" and result["text"] == FACT
    assert result["generation"]["calls"] == 5 and result["generation"]["repairs"] == 1
    assert len(result["editing"]["passes"]) == 1
    assert result["attempts"][0]["review"]["unsupported_claims"]
    assert client.payload(4)["draft"]["text"] == FACT


@pytest.mark.parametrize("genre", ["application_answer", "cover_letter"])
def test_missing_fact_is_never_hidden_by_editor(genre):
    incomplete = {"text": FACT, "claims": draft()["claims"], "missing_facts": ["A real conflict episode"]}
    client = Client(incomplete, review(verdict="needs_fact"))
    result = generate(genre, client)
    assert result["status"] == "needs_fact"
    assert result["editing"]["passes"] == []
    assert not any(call[0]["content"].startswith("Edit application prose") for call in client.calls)


def test_editor_receives_question_and_evidence_but_no_internal_planning_labels():
    original = draft()
    client = Client({"decision": "keep", "edits": [], "draft": original})
    task = {"question": question(), "language": "en", "constraints": [],
            "recipes": ["INTERNAL_RECIPE"], "content_plan": "INTERNAL_PLAN"}
    edit_draft(client, draft=original, context=context(), task=task, genre="application_answer")
    payload = client.payload(0)
    assert payload["task"]["question"] == question()
    assert "INTERNAL_" not in json.dumps(payload)
    assert payload["context"]["candidate_evidence"][0]["boundaries"] == ["Prototype, not production deployment"]
    assert payload["context"]["voice_examples"] == []


@pytest.mark.parametrize("mutation", ["extra_key", "bad_decision", "changed_keep", "empty_revise", "absent_before", "absent_after"])
def test_malformed_editorial_records_are_rejected(mutation):
    original, revised = draft(FACT + BOILERPLATE), draft()
    value = edit(original, revised)
    if mutation == "extra_key":
        value["approved"] = True
    elif mutation == "bad_decision":
        value["decision"] = "approved"
    elif mutation == "changed_keep":
        value.update(decision="keep", edits=[])
    elif mutation == "empty_revise":
        value["edits"] = []
    elif mutation == "absent_before":
        value["edits"][0]["before"] = "Not present in original"
    else:
        value["edits"][0]["after"] = "Not present in revision"
    with pytest.raises(ValueError):
        validate_edit(value, original)


def test_pass_cannot_hide_a_zero_rubric_dimension():
    value = review()
    value["scores"]["naturalness"] = 0
    with pytest.raises(ValueError, match="contradicts"):
        validate_review(value)


def test_real_form_suited_wording_is_classified_as_role_fit():
    assert classify_question(question())["intents"] == ["role_fit"]
