import json

import pytest

from applypilot.discovery.advance import safe_public_get
from applypilot.discovery.career_entries import (
    candidate_to_source_config,
    discover_career_entries,
    extract_career_entries,
)
from applypilot.discovery.official import run_official_discovery

SITE = "https://example.com/"
COMPANY = {"id": "example", "name": "Example", "official_url": SITE}


def extract(html, page=SITE, **kwargs):
    return extract_career_entries(
        html, page, official_url=SITE, official_relationship_reviewed=True, **kwargs
    )


def discover(pages, **kwargs):
    calls = []

    def transport(url, headers=None):
        calls.append(url)
        response = pages[url]
        if isinstance(response, Exception):
            raise response
        return response

    return discover_career_entries(
        COMPANY, official_relationship_reviewed=True, transport=transport, **kwargs
    ), calls


@pytest.mark.parametrize("link,provider,key", [
    ("https://boards.greenhouse.io/acme/?utm_source=footer#jobs", "greenhouse", "acme"),
    ("https://job-boards.greenhouse.io/acme", "greenhouse", "acme"),
    ("https://boards.greenhouse.io/embed/job_board?b=acme&token=tracker", "greenhouse", "acme"),
    ("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true", "greenhouse", "acme"),
    ("https://jobs.lever.co/acme?lever-source=website", "lever", "acme"),
    ("https://jobs.ashbyhq.com/Acme?utm_medium=web", "ashby", "Acme"),
    ("https://careers.smartrecruiters.com/Acme/", "smartrecruiters", "Acme"),
    ("https://jobs.smartrecruiters.com/Acme", "smartrecruiters", "Acme"),
    ("https://api.smartrecruiters.com/v1/companies/Acme/postings", "smartrecruiters", "Acme"),
    ("https://apply.workable.com/acme/?lng=en", "workable", "acme"),
    ("https://acme.workable.com/", "workable", "acme"),
])
def test_recognises_only_explicit_board_identity(link, provider, key):
    result = extract(f'<a href="{link}">Careers</a>')
    candidate, = result["candidates"]
    assert (candidate["provider"], candidate["canonical_key"]) == (provider, key)
    assert candidate["source_page"] == SITE
    assert candidate["status"] == "pending"
    assert "?" not in candidate["canonical_url"]
    assert result["coverage"]["jobs_checked"] is False


@pytest.mark.parametrize("link", [
    "http://jobs.lever.co/acme",
    "https://user:secret@jobs.lever.co/acme",
    "https://jobs.lever.co.evil.com/acme",
    "https://eviljobs.lever.co/acme",
    "https://jobs.lever.co:8443/acme",
    "https://jobs.lever.co/acme/123/apply",
    "https://boards.greenhouse.io/acme/jobs/42",
    "https://boards.greenhouse.io/acme?gh_jid=42",
    "https://boards.greenhouse.io/embed/job_app?for=acme&token=42",
    "https://boards.greenhouse.io/embed/job_board?b=acme&b=other",
    "https://jobs.ashbyhq.com/acme/123",
    "https://jobs.smartrecruiters.com/Acme/123-title",
    "https://apply.workable.com/acme/j/123",
    "https://apply.workable.com/j/123",
    "https://www.workable.com/",
    "https://boards.greenhouse.io/acme%2Fjobs",
    "https://boards.greenhouse.io/..",
    "https://tracker.com/redirect?url=https://jobs.lever.co/acme",
    "https://127.0.0.1/careers",
    "https://10.0.0.1/careers",
    "https://localhost/careers",
])
def test_unsafe_lookalike_job_and_tracking_links_cannot_nominate_board(link):
    assert extract(f'<a href="{link}">Careers</a>')["candidates"] == []


def test_chinese_relative_link_and_one_hop_discovery_do_not_fetch_ats_or_recurse():
    result, calls = discover({
        SITE: '<a href="/加入我们">加入我们</a><a href="https://example.com.evil.com/careers">Jobs</a>',
        "https://example.com/加入我们": '<iframe src="https://jobs.ashbyhq.com/acme"></iframe><a href="/careers/next">Jobs</a>',
    })
    assert calls == [SITE, "https://example.com/加入我们"]
    assert result["status"] == "candidates_found"
    assert result["candidates"][0]["source_page"] == calls[-1]
    assert result["coverage"]["jobs_checked"] is False


def test_multiple_boards_deduplicate_tracking_and_preserve_all_evidence():
    result, calls = discover({SITE: '''
        <a href="https://jobs.lever.co/acme?utm_source=footer">Jobs</a>
        <a href="https://jobs.lever.co/acme?utm_source=nav">Careers</a>
        <iframe src="https://jobs.lever.co/acme"></iframe>
        <link rel="alternate" href="https://jobs.ashbyhq.com/acme">
        <a href="https://jobs.lever.co/other">Regional careers</a>
    '''})
    assert calls == [SITE]
    assert len(result["candidates"]) == 3
    assert len(result["candidates"][0]["evidence"]) == 3
    assert result["selection_required"] is True
    assert all(candidate["status"] == "pending" for candidate in result["candidates"])


def test_no_script_urls_base_redirect_or_third_party_company_label_trust():
    result = extract('''
        <base href="https://evil.com/">
        <script>window.board = "https://jobs.lever.co/acme";</script>
        <script type="application/ld+json">{"url":"https://jobs.ashbyhq.com/acme"}</script>
        <template><a href="https://jobs.lever.co/acme">Jobs</a></template>
        <a href="/careers">招聘</a>
        <a href="https://directory.com/acme">Official Acme</a>
    ''')
    assert result["candidates"] == []
    assert result["career_links"] == ["https://example.com/careers"]
    with pytest.raises(ValueError, match="reviewed"):
        extract_career_entries('<a href="https://jobs.lever.co/acme">Jobs</a>', SITE, official_url=SITE)
    with pytest.raises(ValueError, match="reviewed"):
        discover_career_entries(COMPANY, transport=lambda *_args, **_kwargs: "")
    with pytest.raises(ValueError, match="outside"):
        extract('<a href="https://jobs.lever.co/acme">Jobs</a>', page="https://evil.com/")


def test_career_navigation_avoids_editorial_links_and_language_replicas():
    result = extract('''
        <a href="/es-es/careers">Careers</a>
        <a href="/fr-fr/careers">Carrières</a>
        <a href="/careers?lang=zh">招聘</a>
        <link rel="alternate" hreflang="de" href="/de/karriere">
        <a href="/blog/ai-analytics-week">Join us</a>
        <a href="/resources/our-jobs">Jobs</a>
        <a href="/news/join-us">Join us</a>
        <a href="/events/careers">Careers</a>
        <a href="/team">Join us</a>
        <a href="/careers/open-positions">See jobs</a>
        <a href="/careers/jobs">招聘</a>
    ''', page="https://example.com/careers")
    assert result["career_links"] == [
        "https://example.com/careers/open-positions", "https://example.com/careers/jobs", "https://example.com/team",
    ]


def test_default_link_budget_reaches_board_after_large_navigation():
    html = "".join(f'<a href="/navigation/{index}">Menu</a>' for index in range(150))
    html += '<a href="https://jobs.lever.co/acme">Jobs</a>'
    result, _ = discover({SITE: html})
    assert result["status"] == "candidates_found"
    assert result["candidates"][0]["canonical_key"] == "acme"


def test_page_cap_prefers_concrete_career_path_to_generic_navigation():
    result, calls = discover({
        SITE: '<a href="/team">Join us</a><a href="/careers/jobs">Jobs</a>',
        "https://example.com/careers/jobs": '<a href="https://jobs.lever.co/acme">Jobs</a>',
    }, max_pages=2)
    assert calls == [SITE, "https://example.com/careers/jobs"]
    assert result["status"] == "partial"
    assert result["candidates"][0]["provider"] == "lever"


def test_career_attachment_links_do_not_consume_page_budget():
    result, calls = discover({
        SITE: '''<a href="/careers/.md">Careers</a>
                 <a href="/careers/benefits.pdf">Jobs</a>
                 <a href="/careers/open-positions.xml">Jobs</a>
                 <a href="/careers/team">Careers</a>
                 <a href="/careers/jobs/">Jobs</a>''',
        "https://example.com/careers/jobs/": '<a href="https://jobs.lever.co/acme">Jobs</a>',
    }, max_pages=2)
    assert calls == [SITE, "https://example.com/careers/jobs/"]
    assert result["candidates"][0]["canonical_key"] == "acme"


def test_non_html_response_does_not_claim_entry_absence():
    result, _ = discover({SITE: {"body": "file", "headers": {"Content-Type": "application/pdf"}}})
    assert result["status"] == "error"
    assert "non-HTML" in result["error"]


def test_cross_origin_redirect_is_rejected_before_transport_even_if_it_would_return():
    result, calls = discover({
        SITE: {"status": 302, "headers": {"Location": "https://evil.com/"}},
        "https://evil.com/": {"status": 302, "headers": {"Location": SITE}},
    })
    assert calls == [SITE]
    assert result["status"] == "error"
    assert result["candidates"] == []
    assert "outside the allowed origins" in result["error"]


def test_same_origin_redirect_keeps_actual_source_evidence():
    result, calls = discover({
        SITE: {"status": 302, "headers": {"Location": "/home"}},
        "https://example.com/home": '<a href="careers">招聘</a>',
        "https://example.com/careers": '<a href="https://jobs.lever.co/acme">Jobs</a>',
    })
    assert calls == [SITE, "https://example.com/home", "https://example.com/careers"]
    assert result["pages"][0]["source_page"] == "https://example.com/home"
    assert result["candidates"][0]["source_page"] == calls[-1]


@pytest.mark.parametrize("body,status", [
    ("", "no_entry_found"),
    ("<p>No current openings</p>", "no_entry_found"),
    ({"status": 403, "body": "blocked"}, "blocked"),
    ({"status": 429}, "blocked"),
    ("<title>Just a moment...</title><p>Checking your browser</p>", "blocked"),
    ({"status": 500}, "error"),
    (TimeoutError("connection timed out"), "error"),
    ('<a href="https://acme.wd5.myworkdayjobs.com/en-US/External">Jobs</a>', "unsupported"),
])
def test_empty_failed_blocked_and_unsupported_are_distinct_not_job_empty(body, status):
    result, _ = discover({SITE: body})
    assert result["status"] == status
    assert result["coverage"]["jobs_checked"] is False
    if status == "unsupported":
        assert result["candidates"][0]["status"] == "unsupported"
        with pytest.raises(ValueError, match="supported"):
            candidate_to_source_config(result["candidates"][0], COMPANY)


def test_page_and_link_caps_report_partial_instead_of_empty():
    result, calls = discover({SITE: '<a href="/careers">Careers</a>'}, max_pages=1)
    assert calls == [SITE]
    assert result["status"] == "partial"
    assert result["coverage"]["pages_capped"] is True
    assert result["coverage"]["complete"] is False
    result, _ = discover({SITE: '<a href="/about">About</a><a href="https://jobs.lever.co/acme">Jobs</a>'}, max_links=1)
    assert result["status"] == "partial"
    assert result["coverage"]["links_capped"] is True
    assert result["candidates"] == []


def test_candidate_survives_partial_career_read_with_error_evidence():
    result, _ = discover({
        SITE: '<a href="https://jobs.lever.co/acme">Jobs</a><a href="/careers">Careers</a>',
        "https://example.com/careers": {"status": 403},
    })
    assert result["status"] == "partial"
    assert result["candidates"][0]["provider"] == "lever"
    assert result["pages"][1]["status"] == "blocked"


def test_size_and_redirect_caps_propagate_unknown_error():
    result, _ = discover({SITE: "12345"}, max_body_bytes=4)
    assert result["status"] == "error"
    assert "size limit" in result["error"]
    result, calls = discover({SITE: {"status": 302, "headers": {"Location": "/careers"}}}, max_redirects=0)
    assert calls == [SITE]
    assert result["status"] == "error"
    assert "redirect limit" in result["error"]


@pytest.mark.parametrize("url", ["http://example.com", "https://127.0.0.1", "https://user@example.com"])
def test_invalid_company_url_never_reaches_transport(url):
    calls = []
    with pytest.raises(ValueError):
        discover_career_entries({**COMPANY, "official_url": url}, official_relationship_reviewed=True,
                                transport=lambda url, **_kwargs: calls.append(url))
    assert calls == []


def test_seed_career_url_requires_same_reviewed_origin():
    with pytest.raises(ValueError, match="outside"):
        discover_career_entries({**COMPANY, "careers_url": "https://directory.com/acme"},
                                official_relationship_reviewed=True, transport=lambda *_args: "")
    calls = []
    result = discover_career_entries(
        {**COMPANY, "company_key": "seed", "careers_url": "/careers"},
        official_relationship_reviewed=True,
        transport=lambda url, **_kwargs: calls.append(url) or "",
    )
    assert calls == [SITE, "https://example.com/careers"]
    assert result["status"] == "no_entry_found"


@pytest.mark.parametrize("provider,url,key_field,payload", [
    ("greenhouse", "https://boards.greenhouse.io/acme", "board", {"jobs": []}),
    ("lever", "https://jobs.lever.co/acme", "site", []),
    ("ashby", "https://jobs.ashbyhq.com/acme", "board", {"jobs": []}),
    ("smartrecruiters", "https://careers.smartrecruiters.com/Acme", "company_id", {"content": [], "totalFound": 0}),
    ("workable", "https://apply.workable.com/acme", "subdomain", {"jobs": []}),
])
def test_generated_pending_config_is_inactive_and_existing_collector_compatible(provider, url, key_field, payload):
    candidate, = extract(f'<a href="{url}">Jobs</a>')["candidates"]
    config = candidate_to_source_config(candidate, COMPANY)
    assert config["active"] is False
    assert config["provider"] == provider
    assert config[key_field] == candidate["canonical_key"]
    assert config["verification_status"] == "pending_official_source_review"
    assert run_official_discovery([config], transport=lambda *_args, **_kwargs: pytest.fail("inactive source fetched"))["runs"] == []
    result = run_official_discovery([config], active_only=False, transport=lambda *_args, **_kwargs: json.dumps(payload))
    assert result["runs"][0]["status"] == "complete"
    assert config["active"] is False


def test_safe_get_origin_constraint_is_opt_in_and_rechecked_per_hop():
    calls = []

    def transport(url, **_kwargs):
        calls.append(url)
        return {"status": 302, "headers": {"Location": "https://elsewhere.com/"}} if url == SITE else "ok"

    assert safe_public_get(SITE, transport=transport)["body"] == b"ok"
    assert calls == [SITE, "https://elsewhere.com/"]
    calls.clear()
    with pytest.raises(ValueError, match="outside"):
        safe_public_get(SITE, transport=transport, allowed_origins={SITE})
    assert calls == [SITE]
