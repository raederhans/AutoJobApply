import ast
from pathlib import Path

import pytest

from applypilot.application_questions import (
    merge_question_set,
    normalize_question,
    questions_from_observation,
)


def question(**changes):
    return {"job_id": "job-1", "page_id": "page-1", "field_key": "answer", "text": "Why us?", **changes}


def test_lossless_long_compound_question_and_unicode_revision():
    raw = "  请说明经验😀\n" * 200 + "Describe your role AND its outcome."
    result = normalize_question(question(text=raw, help_text="  Help\n保留原文  "))
    assert result["text"] == raw
    assert result["help_text"] == "  Help\n保留原文  "
    assert normalize_question(result) == result
    assert normalize_question(question(text="lone \ud800"))["revision"]


def test_ids_include_page_and_revision_excludes_live_values_and_epoch():
    a = normalize_question(question(value="secret", observation_epoch="one"))
    b = normalize_question(question(value="changed", observation_epoch="two"))
    assert a == b
    other_page = normalize_question(question(page_id="page-2"))
    assert a["question_id"] != other_page["question_id"]
    drift = normalize_question(question(help_text="New instructions"))
    assert a["question_id"] == drift["question_id"]
    assert a["revision"] != drift["revision"]


@pytest.mark.parametrize(
    "changes",
    [
        {"required": "false"},
        {"text": 12},
        {"field_key": ""},
        {"schema_version": True},
        {"constraints": [{"kind": "max", "unit": "utf16", "value": True, "source": "native"}]},
        {"constraints": [{"kind": "max", "unit": "bytes", "value": 20, "source": "native"}]},
        {"options": [{"label": "yes", "value": "yes", "disabled": "no"}]},
        {"completeness": "complete"},
        {"revision": "forged"},
    ],
)
def test_strict_validation(changes):
    with pytest.raises(ValueError):
        normalize_question(question(**changes))


def test_observation_constraints_options_and_scope_are_preserved_without_values():
    observation = {
        "page_url": "https://example.test/step1",
        "coverage": {"scope": "current_document", "iframe_count": 1},
        "fields": [
            {
                "field_key": "answer",
                "label": "truncated",
                "value": "secret",
                "required": True,
                "application_question": {
                    "text": "Experience\nAND outcomes?",
                    "help_text": "At most 150 words. Maximum 300 characters.",
                    "constraints": [{"kind": "max", "unit": "utf16", "value": 500, "source": "native:maxlength"}],
                    "section_path": ["Motivation"],
                    "completeness": "known",
                    "options": [{"value": "a", "label": "a" * 300, "selected": True}],
                },
            }
        ],
    }
    result = questions_from_observation(observation, job_id="job-1")[0]
    assert result["text"] == "Experience\nAND outcomes?"
    assert result["constraints"] == [
        {"kind": "max", "unit": "utf16", "value": 500, "source": "native:maxlength"},
        {"kind": "max", "unit": "words", "value": 150, "source": "help_text"},
    ]
    assert result["unresolved_instructions"] == ["At most 150 words. Maximum 300 characters."]
    assert result["coverage"]["whole_form"] == "unknown"
    assert result["coverage"]["page_only"] is True
    assert result["completeness"] == "partial"
    assert result["section_path"] == ["Motivation"]
    assert len(result["options"][0]["label"]) == 300
    assert "value" not in result and "selected" not in result["options"][0]


def test_unknown_count_rules_are_retained_and_no_limits_are_invented():
    observation = {
        "form_fields": [{"field_key": "a", "label": "Write 150 words about 3 projects, within the character limit."}]
    }
    result = questions_from_observation(observation, job_id="job-1", page_id="a")[0]
    assert result["constraints"] == []
    assert result["unresolved_instructions"] == [result["text"]]
    assert result["completeness"] == "partial"


@pytest.mark.parametrize("payload", [{}, {"unexpected": "malformed payload"}, {"snapshot": {"url": "https://example.test"}}])
def test_observation_requires_an_explicit_field_list(payload):
    with pytest.raises(ValueError, match="explicitly contain"):
        questions_from_observation(payload, job_id="job-1", page_id="page-1")


@pytest.mark.parametrize("key", ["fields", "form_fields"])
def test_explicitly_empty_observation_can_remove_active_questions(key):
    original = normalize_question(question())
    existing = merge_question_set(None, [original], page_id="page-1")
    observed = questions_from_observation({key: []}, job_id="job-1", page_id="page-1")
    updated = merge_question_set(existing, observed, page_id="page-1")
    assert updated["pages"]["page-1"] == []
    assert updated["revisions"][original["question_id"]] == [original]


def test_pages_accumulate_and_conditional_removal_preserves_revisions():
    a = normalize_question(question())
    b = normalize_question(question(page_id="page-2"))
    merged = merge_question_set(None, [a], page_id="page-1")
    merged = merge_question_set(merged, [b], page_id="page-2")
    changed = normalize_question(question(help_text="At least 50 words"))
    merged = merge_question_set(merged, [changed], page_id="page-1")
    assert merged["pages"]["page-2"] == [b]
    assert merged["revisions"][a["question_id"]] == [a, changed]
    removed = merge_question_set(merged, [], page_id="page-1")
    assert removed["pages"] == {"page-1": [], "page-2": [b]}
    assert removed["revisions"][a["question_id"]] == [a, changed]
    assert merged["pages"]["page-1"] == [changed]  # no mutation of caller data
    assert removed["coverage"] == {"scope": "observed_pages", "whole_form": "unknown"}
    assert merge_question_set(removed, [changed], page_id="page-1")["revisions"] == removed["revisions"]


def test_merge_rejects_mixed_jobs_pages_and_duplicate_ids():
    a = normalize_question(question())
    for questions, page in (
        ([a], "wrong-page"),
        ([a, a], "page-1"),
        ([a, normalize_question(question(job_id="other"))], "page-1"),
    ):
        with pytest.raises(ValueError):
            merge_question_set(None, questions, page_id=page)


@pytest.mark.browser
def test_external_observer_lossless_metadata_before_structural_truncation():
    from playwright.sync_api import sync_playwright

    # Run the existing read-only page callback directly, without profile/DB/port setup.
    source = Path("src/applypilot/apply/page_observation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    callback = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("(expectedEducation) =>")
    )
    raw = "说明经验😀 AND outcomes. " * 30
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(f"""<html lang="zh"><body><fieldset><legend>Motivation</legend>
                <label for="answer">{raw}</label><p id="help">At most 150 words.</p>
                <textarea id="answer" aria-describedby="help" minlength="2" maxlength="500" required></textarea>
                </fieldset><iframe></iframe></body></html>""")
            observation = page.evaluate(callback, [])
            field = next(item for item in observation["form_fields"] if item["field_key"] == "answer")
            assert len(field["label"].encode("utf-16-le")) // 2 == 240
            assert field["application_question"]["text"] == raw
            normalized = questions_from_observation(observation, job_id="job-1", page_id="page-1")[0]
            assert normalized["help_text"] == "At most 150 words."
            assert normalized["language"] == "zh"
            assert normalized["section_path"] == ["Motivation"]
            assert {item["unit"] for item in normalized["constraints"]} == {"utf16", "words"}
            assert normalized["coverage"]["iframe_count"] == 1
            assert normalized["completeness"] == "partial"
            # Browser-native lengths count the emoji as two UTF-16 code units.
            assert page.evaluate("'中😀'.length") == 3
            page.set_content("""<label for="wrapped">Explain the result.
                <textarea id="wrapped">PRIVATE DEFAULT ANSWER</textarea></label>""")
            wrapped = page.evaluate(callback, [])["form_fields"][0]["application_question"]
            assert wrapped["text"] == "Explain the result.\n                "
            assert "PRIVATE DEFAULT ANSWER" not in str(wrapped)
            page.set_content('<select id="track" aria-label="Track"><option value="a" label="Software engineering">internal-track-a</option></select>')
            track = page.evaluate(callback, [])["form_fields"][0]["application_question"]
            assert track["options"][0]["label"] == "Software engineering"
            page.set_content(
                "".join(f'<label for="a-{i}">Question {i}</label><textarea id="a-{i}"></textarea>' for i in range(201))
            )
            partial = page.evaluate(callback, [])
            assert len(partial["form_fields"]) == 200
            assert partial["question_coverage"]["fields_truncated"] is True
            assert all(
                item["completeness"] == "partial"
                for item in questions_from_observation(partial, job_id="job-1", page_id="large-page")
            )
        finally:
            browser.close()
