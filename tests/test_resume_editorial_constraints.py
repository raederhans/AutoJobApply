"""Selection and presentation rules must not reward padding over evidence."""
from pathlib import Path
from unittest.mock import MagicMock

from applypilot.scoring import pdf
from applypilot.scoring.tailor import assemble_resume_text
from applypilot.scoring.validator import validate_tailored_resume


def test_multiple_irrelevant_experiences_can_be_omitted_but_not_all():
    head = "Candidate\ncandidate@example.test\nTECHNICAL SKILLS\nCode: Python\nEXPERIENCE\n"
    entries = [f"Company {i}\nAnalyst | {year}\n- Built Python reports for recurring planning decisions and review.\n"
               for i, year in enumerate((2025, 2024, 2023))]
    education = "\nEDUCATION\nExample University, Computing, 2026\n"
    source = head + "\n".join(entries) + education
    result = validate_tailored_resume(head + entries[2] + education, {}, original_text=source)
    assert result["passed"], result["errors"]
    assert any("most recent" in message for message in result["warnings"])
    empty = validate_tailored_resume(head + education, {}, original_text=source)
    assert not empty["passed"]
    assert any("at least one substantive experience" in message for message in empty["errors"])


def test_role_relevant_section_order_and_internship_education_guard():
    data = {"skills": {"Code": "Python"}, "experience": [], "projects": [],
            "education": ["Example University"], "summary": "Source-supported summary.",
            "section_order": ["SUMMARY", "EDUCATION", "PROJECTS", "EXPERIENCE", "TECHNICAL SKILLS"]}
    text = assemble_resume_text(data, {}, job_profile={"employment_type": "internship"})
    assert text.index("EXPERIENCE") < text.index("TECHNICAL SKILLS")
    data["section_order"] = ["SUMMARY", "PROJECTS", "EXPERIENCE", "TECHNICAL SKILLS", "EDUCATION"]
    text = assemble_resume_text(data, {}, job_profile={"employment_type": "internship"})
    assert text.index("EDUCATION") < text.index("EXPERIENCE")
    data["section_order"] = ["SUMMARY"] * 5
    text = assemble_resume_text(data, {"tailoring": {"resume_layout": {"general_section_order": ["EDUCATION"]}}})
    assert all(section in text for section in ("SUMMARY", "EDUCATION", "TECHNICAL SKILLS", "EXPERIENCE"))


def test_short_tails_and_sparse_pages_warn_without_discard_or_compaction(tmp_path, monkeypatch):
    import playwright.sync_api

    page = MagicMock()
    page.eval_on_selector.return_value = [15, 2]
    page.eval_on_selector_all.return_value = [{"index": 0, "lineWordCounts": [12, 2], "text": "Supported skills"}]
    page.evaluate.return_value = 0.3
    page.pdf.side_effect = lambda **kwargs: Path(kwargs["path"]).write_bytes(b"preserved-render")
    browser = MagicMock()
    browser.new_page.return_value = page
    runtime = MagicMock()
    runtime.__enter__.return_value.chromium.launch.return_value = browser
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: runtime)
    monkeypatch.setattr(pdf, "_pdf_page_text_spans", lambda _: [700, 120])
    target = tmp_path / "resume.pdf"
    warnings = []
    pdf.render_pdf('<div class="summary">Supported summary</div>', str(target), layout_warnings=warnings)
    assert len(warnings) == 3
    assert target.read_bytes() == b"preserved-render"
    assert page.pdf.call_count == 1
    monkeypatch.setattr(pdf, "_pdf_page_text_spans", lambda _: [200])
    warnings.clear()
    pdf.render_pdf('<div class="summary">Supported summary</div>', str(target), layout_warnings=warnings)
    assert any("Sparse one-page" in warning for warning in warnings)
    assert target.exists()
    monkeypatch.setattr(pdf, "_pdf_page_text_spans", lambda _: [400, 300])
    warnings.clear()
    pdf.render_pdf("<div>Resume</div>", str(target), layout_warnings=warnings)
    assert any("Sparse first PDF page" in warning for warning in warnings)


def test_render_failures_are_still_fatal(tmp_path, monkeypatch):
    import playwright.sync_api
    import pytest

    runtime = MagicMock()
    runtime.__enter__.return_value.chromium.launch.side_effect = RuntimeError("browser unavailable")
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: runtime)
    with pytest.raises(RuntimeError, match="browser unavailable"):
        pdf.render_pdf("<div>Resume</div>", str(tmp_path / "resume.pdf"))
