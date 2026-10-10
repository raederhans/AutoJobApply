"""Draft and revision boundaries for the standalone cover-letter lifecycle."""

import json
from copy import deepcopy

import pytest

from applypilot.cover_letter_drafts import build_cover_messages, generate_cover_draft
from applypilot.writing_common import content_digest

FACT = "I built a Python dashboard for weekly operational reporting."


def context():
    return {
        "schema_version": 1,
        "sources": [
            {"id": "candidate", "kind": "project_evidence", "text": FACT},
            {"id": "jd", "kind": "jd", "text": "Analyst internship: build Python dashboards and operational reporting."},
            {"id": "company", "kind": "company", "text": "Example serves logistics teams."},
        ],
        "candidate_evidence": [{
            "id": "project", "source_id": "candidate", "quote": FACT, "status": "confirmed",
            "boundaries": ["No measured productivity gains"], "tags": ["Python", "dashboard"],
        }],
        "role": {"job_id": "job-1", "title": "Analyst Intern", "company_name": "Example", "jd_source_id": "jd"},
        "company": {"name": "Example", "facts": [{
            "id": "company-fact", "source_id": "company", "quote": "Example serves logistics teams.",
            "verification_status": "verified",
        }]},
        "voice_examples": [],
    }


def draft(text=None):
    return {
        "text": text or FACT + " I would bring the same practical focus to this analyst internship.",
        "claims": [{"text": FACT, "kind": "candidate", "evidence_ids": ["project"]}],
        "missing_facts": [],
    }


def review(verdict="pass", detail="Revise the requested closing"):
    return {
        "verdict": verdict,
        "issues": [] if verdict == "pass" else [{"category": "style", "detail": detail}],
        "unsupported_claims": [], "missed_parts": [], "voice_fit": "uncalibrated",
        "scores": {"relevance": 2, "specificity": 2, "naturalness": 2, "concision": 2},
    }


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": deepcopy(messages), "options": kwargs})
        response = self.responses.pop(0)
        return response if isinstance(response, str) else json.dumps(response)

    def payload(self, index=0):
        return json.loads(self.calls[index]["messages"][1]["content"])


def previous_artifact():
    return generate_cover_draft(context(), client=FakeClient(draft(), review()), max_repairs=0, editorial=False)


def test_body_draft_is_evaluated_and_has_no_application_authority():
    client = FakeClient(draft(), review())
    result = generate_cover_draft(context(), client=client, editorial=False)
    assert result["status"] == "reviewed_draft"
    assert result["genre"] == "cover_letter"
    assert result["authority"] == "none"
    assert result["submission_ready"] is False
    assert result["task"]["surface"] == "body"
    assert result["previous_revision"] is None
    assert result["text_digest"] == content_digest(result["text"])
    assert len(client.calls) == 2
    assert result["generation"] == {"calls": 2, "repairs": 0, "model": "injected", "responses": [{}, {}]}
    assert client.payload(1)["draft"]["text"] == result["text"]
    assert "question_type" not in client.payload()["task"]
    assert "EVERY sentence" in client.calls[1]["messages"][0]["content"]


def test_formal_surface_uses_generic_greeting_and_signoff():
    formal = draft("Dear Hiring Manager,\n\n" + FACT + "\n\nSincerely")
    result = generate_cover_draft(context(), surface="formal", client=FakeClient(formal, review()), editorial=False)
    assert result["validation"]["passed"]
    assert result["task"]["surface"] == "formal"
    system, payload = build_cover_messages(context(), surface="formal")
    assert "no invented recipient or candidate name" in system
    assert payload["task"]["surface"] == "formal"


def test_formal_text_is_rejected_on_body_surface():
    formal = draft("Dear Hiring Manager,\n\n" + FACT + "\n\nSincerely")
    result = generate_cover_draft(context(), client=FakeClient(formal, review()), max_repairs=0, editorial=False)
    assert result["status"] == "needs_revision"
    assert not result["validation"]["passed"]


def test_chinese_formal_and_body_surfaces():
    formal = draft("尊敬的招聘经理：\n\n" + FACT + "\n\n此致\n敬礼")
    accepted = generate_cover_draft(context(), language="zh", surface="formal", client=FakeClient(formal, review()), editorial=False)
    assert accepted["validation"]["passed"]
    rejected = generate_cover_draft(context(), language="zh", client=FakeClient(formal, review()), max_repairs=0, editorial=False)
    assert not rejected["validation"]["passed"]


def test_revision_carries_request_old_text_and_links_distinct_revision():
    previous = previous_artifact()
    revised = draft(FACT + " I would welcome a discussion about this team's reporting needs.")
    client = FakeClient(revised, review())
    result = generate_cover_draft(
        context(), client=client, previous_draft=previous,
        revision_request="Make the closing a direct invitation to discuss reporting.",
     editorial=False)
    task = client.payload()["task"]
    assert task["operation"] == "revise"
    assert task["previous_text"] == previous["text"]
    assert "direct invitation" in task["revision_request"]
    assert client.payload(1)["task"] == task
    assert result["previous_revision"] == previous["revision_id"]
    assert result["revision_id"] != previous["revision_id"]
    assert result["text"] == revised["text"]


def test_prior_text_is_editing_material_never_current_fact_evidence():
    previous = previous_artifact()
    invented = "I managed a team of fifty people."
    previous["text"] += " " + invented
    previous["draft"]["text"] = previous["text"]
    previous["text_digest"] = content_digest(previous["text"])
    client = FakeClient(draft(), review())
    generate_cover_draft(context(), client=client, previous_draft=previous, revision_request="Tighten the wording.", editorial=False)
    assert invented in client.payload()["task"]["previous_text"]
    assert invented not in json.dumps(client.payload()["context"])
    assert "not evidence" in client.calls[0]["messages"][0]["content"]


def test_changed_source_runs_complete_fresh_review():
    previous = previous_artifact()
    current = context()
    current["sources"][0]["text"] += " The prototype was used in a class exercise."
    client = FakeClient(draft(), review())
    result = generate_cover_draft(
        current, client=client, previous_draft=previous, revision_request="Use the current sources.",
     editorial=False)
    assert client.payload()["task"]["context_changed"]
    assert len(client.calls) == 2
    assert result["context_digest"] != previous["context_digest"]
    assert client.payload(1)["draft"]["text"] == result["text"]


@pytest.mark.parametrize("field,value", [("genre", "application_answer"), ("revision_id", ""), ("context_digest", "bad"), ("text_digest", "bad")])
def test_invalid_previous_binding_is_rejected_before_model_call(field, value):
    previous = previous_artifact()
    previous[field] = value
    client = FakeClient()
    with pytest.raises(ValueError):
        generate_cover_draft(context(), client=client, previous_draft=previous, revision_request="Shorten.", editorial=False)
    assert not client.calls


def test_cross_job_previous_draft_is_rejected():
    previous = previous_artifact()
    previous["task"]["job_id"] = "other-job"
    with pytest.raises(ValueError, match="different job"):
        build_cover_messages(context(), previous_draft=previous, revision_request="Shorten.")


def test_previous_text_cannot_diverge_from_recorded_draft():
    previous = previous_artifact()
    previous["text"] = "Tampered text"
    previous["text_digest"] = content_digest(previous["text"])
    with pytest.raises(ValueError, match="draft.text"):
        build_cover_messages(context(), previous_draft=previous, revision_request="Shorten.")


def test_one_targeted_repair_is_reviewed_again():
    client = FakeClient(draft(), review("revise"), draft(), review())
    result = generate_cover_draft(context(), client=client, editorial=False)
    assert result["status"] == "reviewed_draft"
    assert len(client.calls) == 4
    assert result["generation"]["calls"] == 4
    assert result["generation"]["repairs"] == 1
    repair = client.payload(2)["repair"]
    assert repair["review"]["issues"][0]["detail"] == "Revise the requested closing"
    assert repair["draft"] == draft()
    assert client.payload(3)["draft"] == draft()


def test_failed_repair_stops_with_visible_failure():
    client = FakeClient(draft(), review("revise"), draft(), review("revise"))
    result = generate_cover_draft(context(), client=client, editorial=False)
    assert result["status"] == "needs_revision"
    assert len(client.calls) == 4
    assert result["review"]["verdict"] == "revise"


def test_missing_fact_does_not_trigger_blind_repair():
    incomplete = draft()
    incomplete["missing_facts"] = ["A confirmed example of logistics work"]
    client = FakeClient(incomplete, review("needs_fact", "No supported logistics experience"))
    result = generate_cover_draft(context(), client=client, editorial=False)
    assert result["status"] == "needs_fact"
    assert len(client.calls) == 2


@pytest.mark.parametrize("raw", ["not json", "{} trailing", '{"text":"a","text":"b"}'])
def test_malformed_model_output_is_not_reported_as_a_draft(raw):
    client = FakeClient(raw)
    with pytest.raises(ValueError):
        generate_cover_draft(context(), client=client, editorial=False)
    assert len(client.calls) == 1


def test_wrong_model_object_schema_is_rejected_before_review():
    client = FakeClient({"text": FACT})
    with pytest.raises(ValueError, match="draft schema"):
        generate_cover_draft(context(), client=client, editorial=False)
    assert len(client.calls) == 1


def test_no_eligible_evidence_returns_needs_fact_without_model_call(monkeypatch):
    import applypilot.llm

    monkeypatch.setattr(applypilot.llm, "get_client", lambda: pytest.fail("No facts must not load a client"))
    value = context()
    value["candidate_evidence"][0]["status"] = "unresolved"
    result = generate_cover_draft(value, editorial=False)
    assert result["status"] == "needs_fact"
    assert result["draft"]["missing_facts"]
    assert result["review"] is None
    assert result["generation"] == {"calls": 0, "repairs": 0, "model": None, "responses": []}


def test_response_cost_metadata_is_snapshotted_per_call():
    class MetadataClient(FakeClient):
        model = "fixture-model"

        def chat(self, messages, **kwargs):
            result = super().chat(messages, **kwargs)
            self.last_response_meta = {"response_id": str(len(self.calls)), "usage": {"total_tokens": 7}}
            return result

    client = MetadataClient(draft(), review())
    result = generate_cover_draft(context(), client=client, editorial=False)
    assert result["generation"]["model"] == "fixture-model"
    assert result["generation"]["responses"] == [
        {"response_id": "1", "usage": {"total_tokens": 7}},
        {"response_id": "2", "usage": {"total_tokens": 7}},
    ]
    client.last_response_meta["usage"]["total_tokens"] = 999
    assert result["generation"]["responses"][-1]["usage"]["total_tokens"] == 7


def test_company_and_voice_are_never_personal_evidence():
    bad = draft()
    bad["claims"][0]["evidence_ids"] = ["company-fact"]
    result = generate_cover_draft(context(), client=FakeClient(bad, review()), max_repairs=0, editorial=False)
    assert not result["validation"]["passed"]
    assert result["status"] == "needs_revision"


def test_selection_excludes_wrong_scope_resume_and_wrong_language_voice():
    value = context()
    value["sources"].extend([
        {"id": "resume", "kind": "resume", "text": "Python dashboard owner"},
        {"id": "voice", "kind": "voice", "text": "Voice sample"},
    ])
    value["candidate_evidence"].extend([
        {"id": "old-resume", "source_id": "resume", "quote": "Python dashboard owner", "status": "confirmed"},
        {"id": "wrong-job", "source_id": "candidate", "quote": FACT, "status": "confirmed", "scope": {"job_id": "other"}},
    ])
    value["voice_examples"] = [{
        "id": "zh-voice", "source_id": "voice", "quote": "Voice sample", "language": "zh",
        "genre": "cover_letter", "authorship": "user", "user_approved": True,
    }]
    _, payload = build_cover_messages(value)
    assert [item["id"] for item in payload["context"]["candidate_evidence"]] == ["project"]
    assert payload["context"]["voice_examples"] == []
    assert "resume_positioning" not in payload["context"]


def test_explicit_native_length_constraint_overrides_soft_word_preference():
    constraint = {"kind": "max", "unit": "words", "value": 10, "source": "native"}
    client = FakeClient(draft(), review())
    result = generate_cover_draft(context(), constraints=[constraint], client=client, max_repairs=0, editorial=False)
    assert client.payload()["task"]["constraints"] == [constraint]
    assert not result["validation"]["passed"]
    assert result["status"] == "needs_revision"


@pytest.mark.parametrize("kwargs", [
    {"surface": "email"}, {"language": ""}, {"max_repairs": 2}, {"max_repairs": True},
    {"revision_request": "Shorten"}, {"constraints": "short"},
])
def test_invalid_options_do_not_call_model(kwargs):
    client = FakeClient()
    with pytest.raises(ValueError):
        generate_cover_draft(context(), client=client, **kwargs, editorial=False)
    assert not client.calls


def test_missing_role_is_explicit_error():
    value = context()
    value["role"] = None
    with pytest.raises(ValueError, match="role context"):
        build_cover_messages(value)


def test_default_client_is_loaded_only_when_needed(monkeypatch):
    import applypilot.llm

    fake = FakeClient(draft(), review())
    monkeypatch.setattr(applypilot.llm, "get_client", lambda: fake)
    assert generate_cover_draft(context(), editorial=False)["status"] == "reviewed_draft"
    assert len(fake.calls) == 2
