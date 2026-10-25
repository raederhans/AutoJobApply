"""Offline tests for smartextract's pure logic (no browser, no LLM, no network)."""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import sys
from pathlib import Path

import pytest

from applypilot.discovery import smartextract

# -- helpers -----------------------------------------------------------------

JOB_LD = {
    "@type": "JobPosting",
    "title": "Data Analyst",
    "description": "Analyse things",
    "jobLocation": {"address": {"addressLocality": "Singapore"}},
    "url": "https://example.test/jobs/1",
}
JSON_LD_PLAN = {
    "strategy": "json_ld",
    "reasoning": "ld",
    "extraction": {
        "title": "title", "salary": None, "description": "description",
        "location": "jobLocation.address.addressLocality", "url": "url",
    },
}


@pytest.fixture
def conn(tmp_path: Path):
    connection = smartextract.init_db(tmp_path / "jobs.db")
    yield connection
    connection.close()


def _intel(**overrides) -> dict:
    intel = {
        "url": "https://example.test/jobs",
        "json_ld": [],
        "api_responses": [],
        "data_testids": [],
        "page_title": "Careers",
        "dom_stats": {},
        "card_candidates": [],
    }
    intel.update(overrides)
    return intel


def _llm(*responses):
    """Fake ask_llm: returns or raises each scripted response in order."""
    queue = list(responses)
    prompts: list[str] = []

    def fake(prompt):
        prompts.append(prompt)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, 0.01, {"finish_reason": "stop", "prompt_chars": len(prompt), "response_chars": len(item)}

    fake.prompts = prompts
    return fake


class _FakeClient:
    def __init__(self, *verdicts):
        self._verdicts = list(verdicts)
        self.prompts: list[str] = []

    def ask(self, prompt, temperature, max_tokens):
        self.prompts.append(prompt)
        verdict = self._verdicts.pop(0)
        if isinstance(verdict, Exception):
            raise verdict
        return verdict


# -- module import side effects ----------------------------------------------

def _load_fresh_copy():
    spec = importlib.util.spec_from_file_location("smartextract_fresh_copy", smartextract.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_reconfigures_a_legacy_console_in_place_without_replacing_it(monkeypatch) -> None:
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    _load_fresh_copy()

    # The very same stream objects stay installed (pytest capture is not displaced), now UTF-8.
    assert sys.stdout is out and sys.stderr is err
    assert out.encoding == "utf-8" and err.encoding == "utf-8"


def test_import_leaves_utf8_streams_alone_and_survives_a_failing_reconfigure(monkeypatch) -> None:
    class Utf8Stream:
        encoding = "UTF-8"

        def reconfigure(self, **_kwargs):
            raise AssertionError("must not reconfigure a UTF-8 stream")

    class BrokenStream:
        encoding = "cp1252"

        def reconfigure(self, **_kwargs):
            raise OSError("detached")

    monkeypatch.setattr(sys, "stdout", Utf8Stream())
    _load_fresh_copy()

    monkeypatch.setattr(sys, "stdout", BrokenStream())
    monkeypatch.setattr(sys, "stderr", BrokenStream())
    _load_fresh_copy()  # must not raise


# -- extract_json ------------------------------------------------------------

def test_extract_json_plain_and_fenced_payloads() -> None:
    assert smartextract.extract_json('{"a": 1}') == {"a": 1}
    assert smartextract.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert smartextract.extract_json('```\n{"a": 2}\n```') == {"a": 2}
    assert smartextract.extract_json('  {"a": 3}\n') == {"a": 3}


def test_extract_json_drops_the_think_block() -> None:
    assert smartextract.extract_json('<think>maybe {"x": 0}</think>\n{"a": 1}') == {"a": 1}
    assert smartextract.extract_json('<think>reasoning</think>```json\n{"a": 4}\n```') == {"a": 4}


def test_extract_json_repairs_invalid_escapes_and_trailing_closers() -> None:
    assert smartextract.extract_json(r'{"selector": "a\:hover", "n": "x\_y"}') == {"selector": "a:hover", "n": "x_y"}
    # Valid escapes are preserved.
    assert smartextract.extract_json(r'{"t": "line\nbreak", "q": "say \"hi\""}') == {"t": "line\nbreak", "q": 'say "hi"'}
    assert smartextract.extract_json('{"a": 1}}') == {"a": 1}
    assert smartextract.extract_json('{"a": [1, 2]}]}') == {"a": [1, 2]}


@pytest.mark.parametrize("text", ["", "not json at all", '{"a": ', '{"a": 1'])
def test_extract_json_raises_decode_error_for_unparseable_text(text) -> None:
    with pytest.raises(json.JSONDecodeError):
        smartextract.extract_json(text)


def test_extract_json_with_leading_prose_currently_fails() -> None:
    # Documents current behaviour (not endorsed): explanatory prose before bare JSON is not recovered.
    with pytest.raises(json.JSONDecodeError):
        smartextract.extract_json('Here is the plan: {"strategy": "json_ld"}')


# -- JSON path resolution ----------------------------------------------------

DATA = {
    "title": "Analyst",
    "count": 3,
    "ratio": 1.5,
    "flag": True,
    "nothing": None,
    "empty": "",
    "company": {"name": "Acme", "id": 7},
    "contact": {"text": "hello"},
    "other": {"foo": "bar"},
    "locations": [{"name": "Singapore"}, {"name": "Remote"}, {"text": "Hybrid"}, {"name": "Fourth"}],
    "tags": ["a", "b", "c", "d"],
    "mixed": [{"x": 1}],
    "groups": [{"items": [{"t": "one"}, {"t": "two"}]}],
}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("title", "Analyst"),
        ("count", "3"),
        ("ratio", "1.5"),
        ("flag", "True"),
        ("company", "Acme"),
        ("contact", "hello"),
        ("other", "{'foo': 'bar'}"),
        ("locations", "Singapore, Remote, Hybrid"),
        ("tags", "a, b, c"),
        ("mixed", ""),
        ("groups[0].items[1].t", "two"),
        ("groups.[0].items.[0].t", "one"),
        ("nothing", None),
        ("empty", ""),
        ("missing", None),
        ("locations[9]", None),
        ("title.deeper", None),
        ("", None),
    ],
)
def test_resolve_json_path_coerces_values_for_display(path, expected) -> None:
    assert smartextract.resolve_json_path(DATA, path) == expected


def test_resolve_json_path_requires_non_empty_data() -> None:
    assert smartextract.resolve_json_path({}, "title") is None
    assert smartextract.resolve_json_path(None, "title") is None


def test_resolve_json_path_raw_returns_native_structures() -> None:
    assert smartextract.resolve_json_path_raw(DATA, "groups[0].items") == [{"t": "one"}, {"t": "two"}]
    assert smartextract.resolve_json_path_raw(DATA, "company") == {"name": "Acme", "id": 7}
    assert smartextract.resolve_json_path_raw(DATA, "count") == 3
    assert smartextract.resolve_json_path_raw(DATA, "groups[5].items") is None
    assert smartextract.resolve_json_path_raw(DATA, "missing.path") is None
    assert smartextract.resolve_json_path_raw(DATA, "") is None
    assert smartextract.resolve_json_path_raw([], "[0]") is None
    assert smartextract.resolve_json_path_raw([{"a": 1}], "[0].a") == 1


# -- execute_json_ld / execute_api_response ----------------------------------

def test_execute_json_ld_extracts_fields_and_nulls_unrequested_ones() -> None:
    intel = _intel(json_ld=[
        JOB_LD,
        {"@type": "Organization", "name": "Acme"},
        "not a dict",
        {**JOB_LD, "title": "ML Engineer", "url": "https://example.test/jobs/2"},
    ])
    plan = {"extraction": {**JSON_LD_PLAN["extraction"], "salary": "null"}}

    jobs = smartextract.execute_json_ld(intel, plan)

    assert jobs == [
        {"title": "Data Analyst", "salary": None, "description": "Analyse things",
         "location": "Singapore", "url": "https://example.test/jobs/1"},
        {"title": "ML Engineer", "salary": None, "description": "Analyse things",
         "location": "Singapore", "url": "https://example.test/jobs/2"},
    ]


def test_execute_json_ld_finds_postings_with_list_type_and_graph_wrappers() -> None:
    listed_type = {**JOB_LD, "@type": ["Thing", "JobPosting"], "title": "Listed type"}
    wrapped = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "name": "Careers"},
            {**JOB_LD, "title": "In graph", "url": "https://example.test/jobs/3"},
        ],
    }
    top_level_array = [{**JOB_LD, "title": "In array", "url": "https://example.test/jobs/4"}]
    intel = _intel(json_ld=[listed_type, wrapped, top_level_array, {"@type": "WebSite", "name": "x"}])

    jobs = smartextract.execute_json_ld(intel, {"extraction": JSON_LD_PLAN["extraction"]})

    assert [job["title"] for job in jobs] == ["Listed type", "In graph", "In array"]


def test_strategy_briefing_reports_jobpostings_found_in_graph_or_list_type() -> None:
    intel = _intel(json_ld=[
        {"@graph": [{**JOB_LD, "title": "Graph job"}]},
        {**JOB_LD, "@type": ["JobPosting"], "title": "Listed job"},
        {"@type": "Organization", "name": "Acme"},
    ])

    briefing = smartextract.format_strategy_briefing(intel)

    assert "2 JobPosting entries found (usable!)" in briefing
    assert "NO JobPosting" not in briefing
    assert "Graph job" in briefing
    assert "Other JSON-LD types (NOT job data): ['Organization']" in briefing


def test_execute_api_response_follows_url_pattern_and_items_path() -> None:
    raw = {"results": [{"hits": [
        {"_source": {"Title": "Analyst", "City": {"name": "Singapore"}}, "slug": "a"},
        "skipped-non-dict",
        {"_source": {"Title": "Engineer"}, "slug": "b"},
    ]}]}
    intel = _intel(api_responses=[
        {"url": "https://example.test/api/other", "_raw_data": {"x": 1}},
        {"url": "https://example.test/api/search?q=1", "_raw_data": raw},
    ])
    plan = {"extraction": {
        "url_pattern": "/api/search", "items_path": "results[0].hits",
        "title": "_source.Title", "location": "_source.City", "url": "slug",
        "salary": "null", "description": None,
    }}

    jobs = smartextract.execute_api_response(intel, plan)

    assert jobs == [
        {"title": "Analyst", "salary": None, "description": None, "location": "Singapore", "url": "a"},
        {"title": "Engineer", "salary": None, "description": None, "location": None, "url": "b"},
    ]


def test_execute_api_response_returns_empty_for_unmatched_or_non_list_data(caplog) -> None:
    intel = _intel(api_responses=[{"url": "https://example.test/api/search", "_raw_data": {"results": {"a": 1}}}])

    with caplog.at_level(logging.WARNING, logger=smartextract.log.name):
        assert smartextract.execute_api_response(intel, {"extraction": {"url_pattern": "/nope", "items_path": "x"}}) == []
        assert smartextract.execute_api_response(
            intel, {"extraction": {"url_pattern": "/api/search", "items_path": "results"}}
        ) == []
    assert "Could not find stored API response" in caplog.text
    assert "did not resolve to a list (got dict)" in caplog.text


# -- execute_css_selectors ---------------------------------------------------

CARD_HTML = "<main><ul>" + "".join(
    f'<li><article data-testid="job-card"><h3>Job {n}</h3><span class="loc">Singapore {n}</span>'
    f'<a href="/jobs/{n}">View</a></article></li>'
    for n in range(1, 6)
) + "</ul></main>"
SELECTORS = {"job_card": '[data-testid="job-card"]', "title": "h3", "salary": None,
             "description": "missing-tag", "location": ".loc", "url": "a"}


def test_execute_css_selectors_extracts_cards_with_llm_selectors(monkeypatch) -> None:
    fake = _llm(json.dumps(SELECTORS))
    monkeypatch.setattr(smartextract, "ask_llm", fake)

    selectors, jobs = smartextract.execute_css_selectors(_intel(full_html=CARD_HTML))

    assert selectors == SELECTORS
    assert len(jobs) == 5
    assert jobs[0] == {
        "title": "Job 1", "salary": None, "description": None, "location": "Singapore 1", "url": "/jobs/1",
    }
    assert "PAGE HTML" in fake.prompts[0] and 'data-testid="job-card"' in fake.prompts[0]


def test_execute_css_selectors_without_html_does_not_call_the_llm(monkeypatch) -> None:
    monkeypatch.setattr(smartextract, "ask_llm", lambda _prompt: pytest.fail("LLM must not be called"))

    assert smartextract.execute_css_selectors(_intel()) == ({}, [])
    assert smartextract.execute_css_selectors(_intel(full_html="")) == ({}, [])


@pytest.mark.parametrize(
    ("scripted", "expected_selectors"),
    [
        (RuntimeError("quota"), {}),
        ("no json here", {}),
        ('{"error": "no job listings found"}', {"error": "no job listings found"}),
        (json.dumps({**SELECTORS, "job_card": "div["}), {**SELECTORS, "job_card": "div["}),
    ],
    ids=["llm-error", "parse-error", "llm-reports-no-listings", "invalid-card-selector"],
)
def test_execute_css_selectors_degrades_to_no_jobs(monkeypatch, scripted, expected_selectors) -> None:
    monkeypatch.setattr(smartextract, "ask_llm", _llm(scripted))

    selectors, jobs = smartextract.execute_css_selectors(_intel(full_html=CARD_HTML))

    assert selectors == expected_selectors
    assert jobs == []


def test_execute_css_selectors_tolerates_an_invalid_field_selector(monkeypatch) -> None:
    monkeypatch.setattr(smartextract, "ask_llm", _llm(json.dumps({**SELECTORS, "title": "h3[", "salary": "null"})))

    _, jobs = smartextract.execute_css_selectors(_intel(full_html=CARD_HTML))

    assert len(jobs) == 5
    assert jobs[0]["title"] is None and jobs[0]["salary"] is None and jobs[0]["url"] == "/jobs/1"


# -- HTML cleaning -----------------------------------------------------------

def test_clean_card_html_keeps_semantic_attributes_and_drops_layout_noise() -> None:
    html = (
        '<div class="job-card mt-2 css-ab12cd text-bold" id="c1" data-testid="job-card" style="color:red" '
        'onclick="x()" aria-label="Job"><a href="/j/1" class="title col-6" target="_blank">Analyst</a></div>'
    )

    cleaned = smartextract.clean_card_html(html)

    assert 'class="job-card"' in cleaned
    assert 'id="c1"' in cleaned and 'data-testid="job-card"' in cleaned and 'aria-label="Job"' in cleaned
    assert 'href="/j/1"' in cleaned
    for dropped in ("style=", "onclick", "target=", "mt-2", "css-ab12cd", "text-bold", "col-6"):
        assert dropped not in cleaned
    assert "class" not in smartextract.clean_card_html('<p class="mt-2">x</p>')


def test_clean_page_html_prefers_a_large_main_and_strips_noise() -> None:
    items = "".join(f'<li class="row mt-1" data-id="{n}"><a href="/j/{n}">Job number {n} title</a></li>' for n in range(40))
    html = (
        "<html><head><title>T</title></head><body><header>HEADER-TEXT</header>"
        f"<main><nav>NAV-TEXT</nav><ul>{items}</ul><span></span><footer>FOOT-TEXT</footer>"
        "<script>var tracking=1</script><style>.a{}</style><svg><g/></svg></main></body></html>"
    )

    cleaned = smartextract.clean_page_html(html)

    assert "Job number 39 title" in cleaned
    for dropped in ("HEADER-TEXT", "NAV-TEXT", "FOOT-TEXT", "tracking", "<svg", "<style", "mt-1", "<span"):
        assert dropped not in cleaned
    assert 'data-id="3"' in cleaned and 'class="row"' in cleaned


def test_clean_page_html_uses_whole_page_when_main_is_small_and_truncates() -> None:
    html = "<html><body><header>HEADER-TEXT</header><main><p>tiny</p></main><div>body text</div></body></html>"

    cleaned = smartextract.clean_page_html(html)
    assert "HEADER-TEXT" in cleaned and "body text" in cleaned

    long_html = "<body>" + "<p>filler paragraph</p>" * 50 + "</body>"
    truncated = smartextract.clean_page_html(long_html, max_chars=100)
    assert truncated.endswith("\n<!-- TRUNCATED -->")
    assert len(truncated) == 100 + len("\n<!-- TRUNCATED -->")


def test_clean_page_html_finds_role_main() -> None:
    items = "".join(f"<li><a href='/j/{n}'>Role number {n} description</a></li>" for n in range(60))
    html = f"<body><div>OUTSIDE-TEXT</div><div role='main'><ul>{items}</ul></div></body>"

    cleaned = smartextract.clean_page_html(html)

    assert "OUTSIDE-TEXT" not in cleaned and "Role number 59" in cleaned


# -- judge_api_responses -----------------------------------------------------

def test_judge_api_responses_keeps_drops_and_keeps_on_error(monkeypatch) -> None:
    client = _FakeClient(
        '{"relevant": true, "reason": "jobs"}',
        '<think>hmm</think>```json\n{"relevant": false, "reason": "auth"}\n```',
        RuntimeError("rate limited"),
        '{"reason": "missing flag"}',
    )
    monkeypatch.setattr(smartextract, "get_client", lambda task=None: client)
    responses = [
        {"url": "https://x.test/api/jobs", "status": 200, "size": 10, "type": "array",
         "first_item_keys": ["title"], "first_item_sample": {"title": "Analyst"}},
        {"url": "https://x.test/api/auth", "status": 200, "size": 5, "keys": ["token"],
         "nested_jobs": {"count": 2, "first_item_keys": ["t"], "first_item_sample": {"t": 1}}},
        {"url": "https://x.test/api/flaky", "status": 200, "size": 5},
        {"url": "https://x.test/api/unflagged", "status": 200, "size": 5},
    ]

    kept = smartextract.judge_api_responses(responses)

    assert [r["url"] for r in kept] == ["https://x.test/api/jobs", "https://x.test/api/flaky"]
    assert "['title']" in client.prompts[0] and "Analyst" in client.prompts[0]
    assert ".jobs: 2 items" in client.prompts[1]
    assert "no structured data" in client.prompts[2]


def test_judge_api_responses_skips_client_creation_for_empty_input(monkeypatch) -> None:
    monkeypatch.setattr(smartextract, "get_client", lambda task=None: pytest.fail("client must not be created"))

    assert smartextract.judge_api_responses([]) == []


# -- format_strategy_briefing ------------------------------------------------

def test_format_strategy_briefing_summarises_every_signal() -> None:
    intel = _intel(
        json_ld=[{"@type": "Organization"}, "junk"],
        api_responses=[{
            "url": "https://x.test/api/jobs", "status": 200, "size": 12345, "type": "object",
            "keys": ["results"],
            "nested_results": {
                "count": 4, "first_item_keys": ["title"], "first_item_sample": {"title": "A"},
                "first_item.company": {"keys": ["name"], "sample": {"name": "Acme"}},
                "first_item.tags": {"count": 2, "first_item_keys": ["t"], "first_item_sample": {"t": 1}},
            },
        }],
        data_testids=[{"tag": "div", "testid": "job-card", "text": "Analyst\nSingapore"}],
        dom_stats={"total_elements": 900, "links": 40},
        card_candidates=[{"parent_selector": "ul", "child_selector": "li", "total_children": 10,
                          "with_text": 10, "with_links": 9}],
    )

    briefing = smartextract.format_strategy_briefing(intel)

    assert "JSON-LD: NO JobPosting entries" in briefing
    assert "['Organization', '?']" in briefing
    assert "Size: 12,345 chars" in briefing
    assert ".results: array of 4 items" in briefing
    assert ".results[0].company: object with keys ['name']" in briefing
    assert ".results[0].tags: array of 2 items" in briefing
    assert '<div data-testid="job-card"> Analyst Singapore' in briefing
    assert "DOM STATS: 900 elements, 40 links" in briefing
    assert "[0] parent=ul child=li count=10 with_text=10 with_links=9" in briefing


def test_format_strategy_briefing_reports_missing_signals() -> None:
    briefing = smartextract.format_strategy_briefing(_intel())

    for expected in ("JSON-LD: none", "API RESPONSES: none intercepted", "DATA-TESTID: none found",
                     "REPEATING ELEMENTS: none detected"):
        assert expected in briefing


# -- site registry, targets, storage -----------------------------------------

def test_load_sites_handles_missing_valid_and_empty_files(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(smartextract, "CONFIG_DIR", tmp_path)
    assert smartextract.load_sites() == []

    path = tmp_path / "sites.yaml"
    path.write_text("sites:\n  - name: Acme\n    url: https://acme.test\n    type: static\n", encoding="utf-8")
    assert smartextract.load_sites() == [{"name": "Acme", "url": "https://acme.test", "type": "static"}]

    for content in ("", "# nothing\n", "sites:\n", "portal_policies: []\n"):
        path.write_text(content, encoding="utf-8")
        assert smartextract.load_sites() == []


def test_build_scrape_targets_expands_search_sites_and_encodes_placeholders() -> None:
    sites = [
        {"name": "Board", "url": "https://board.test/s?q={query_encoded}&l={location_encoded}&raw={query}",
         "type": "search"},
        {"name": "Static", "url": "https://static.test/jobs?l={location_encoded}", "type": "static"},
        {"name": "Untyped", "url": "https://untyped.test/", },
    ]
    cfg = {
        "queries": [{"query": "data analyst"}, {"query": "C++ & ML"}],
        "locations": [{"location": "Singapore, SG"}, {"location": "Remote"}],
    }

    targets = smartextract.build_scrape_targets(sites, cfg)

    assert targets == [
        {"name": "Board", "url": "https://board.test/s?q=data+analyst&l=Singapore%2C+SG&raw=data+analyst",
         "query": "data analyst"},
        {"name": "Board", "url": "https://board.test/s?q=C%2B%2B+%26+ML&l=Singapore%2C+SG&raw=C%2B%2B+%26+ML",
         "query": "C++ & ML"},
        {"name": "Static", "url": "https://static.test/jobs?l=Singapore%2C+SG", "query": None},
        {"name": "Untyped", "url": "https://untyped.test/", "query": None},
    ]


def test_build_scrape_targets_search_site_without_queries_is_scraped_once() -> None:
    sites = [{"name": "Board", "url": "https://board.test/s?q={query_encoded}&l={location_encoded}", "type": "search"}]

    targets = smartextract.build_scrape_targets(sites, {})

    assert targets == [{"name": "Board", "url": "https://board.test/s?q={query_encoded}&l=", "query": None}]


def test_build_scrape_targets_loads_registry_and_config_when_not_given(monkeypatch) -> None:
    monkeypatch.setattr(smartextract, "load_sites", lambda: [{"name": "S", "url": "https://s.test/{location_encoded}"}])
    monkeypatch.setattr(smartextract.config, "load_search_config", lambda: {"locations": [{"location": "SG"}]})

    assert smartextract.build_scrape_targets() == [{"name": "S", "url": "https://s.test/SG", "query": None}]


def test_store_jobs_filtered_applies_location_title_and_url_rules(conn) -> None:
    jobs = [
        {"url": "https://x.test/1", "title": "Data Analyst", "location": "Singapore"},
        {"url": "https://x.test/2", "title": "Data Analyst", "location": "Toronto, Canada"},
        {"url": "https://x.test/3", "title": "Senior Staff Engineer", "location": "Singapore"},
        {"url": "", "title": "No URL", "location": "Singapore"},
        {"url": "https://x.test/5", "title": "Unknown place", "location": None},
    ]

    result = smartextract._store_jobs_filtered(
        conn, jobs, "Board", "json_ld", ["singapore"], ["canada"], ["senior staff"],
    )

    assert result == (2, 0)
    stored = {row["url"] for row in conn.execute("SELECT url FROM jobs")}
    assert stored == {"https://x.test/1", "https://x.test/5"}
    row = conn.execute("SELECT strategy, source_site FROM jobs WHERE url = 'https://x.test/1'").fetchone()
    assert tuple(row) == ("json_ld", "Board")
    assert smartextract._store_jobs_filtered(conn, jobs, "Board", "json_ld", ["singapore"], ["canada"]) == (1, 2)


# -- _run_one_site -----------------------------------------------------------

def _patch_collect(monkeypatch, *intels):
    queue = list(intels)
    calls: list[tuple[str, bool]] = []

    def fake_collect(url, headless=True):
        calls.append((url, headless))
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(smartextract, "collect_page_intelligence", fake_collect)
    return calls


def test_run_one_site_json_ld_pass(monkeypatch) -> None:
    calls = _patch_collect(monkeypatch, _intel(json_ld=[JOB_LD, {**JOB_LD, "title": "Other", "url": "u2"}]))
    ask = _llm(json.dumps(JSON_LD_PLAN))
    monkeypatch.setattr(smartextract, "ask_llm", ask)

    result = smartextract._run_one_site("Acme", "https://example.test/jobs")

    assert result["status"] == "PASS" and result["strategy"] == "json_ld"
    assert (result["total"], result["titles"]) == (2, 2)
    assert result["sample"] == result["jobs"][:5]
    assert calls == [("https://example.test/jobs", True)]
    assert "JSON-LD: 2 JobPosting entries found (usable!)" in ask.prompts[0]


def test_run_one_site_reports_partial_when_few_jobs_have_titles(monkeypatch) -> None:
    postings = [JOB_LD, {**JOB_LD, "title": None, "url": "u2"}]
    _patch_collect(monkeypatch, _intel(json_ld=postings))
    monkeypatch.setattr(smartextract, "ask_llm", _llm(json.dumps(JSON_LD_PLAN)))

    result = smartextract._run_one_site("Acme", "https://example.test/jobs")

    assert (result["status"], result["total"], result["titles"]) == ("PARTIAL", 2, 1)


def test_run_one_site_reports_fail_for_zero_jobs_and_unknown_strategy(monkeypatch) -> None:
    _patch_collect(monkeypatch, _intel())
    monkeypatch.setattr(smartextract, "ask_llm", _llm(json.dumps(JSON_LD_PLAN), '{"strategy": "magic"}'))

    no_postings = smartextract._run_one_site("Acme", "https://example.test/jobs")
    unknown = smartextract._run_one_site("Acme", "https://example.test/jobs")

    assert (no_postings["status"], no_postings["total"]) == ("FAIL", 0)
    assert (unknown["status"], unknown["strategy"]) == ("FAIL", "magic")


def test_run_one_site_reports_llm_and_parse_errors(monkeypatch) -> None:
    _patch_collect(monkeypatch, _intel())
    monkeypatch.setattr(smartextract, "ask_llm", _llm(RuntimeError("quota exceeded"), "sorry, no JSON"))

    llm_error = smartextract._run_one_site("Acme", "https://example.test/jobs")
    parse_error = smartextract._run_one_site("Acme", "https://example.test/jobs")

    assert llm_error == {"name": "Acme", "status": "LLM_ERROR", "error": "quota exceeded"}
    assert parse_error["status"] == "PARSE_ERROR" and parse_error["raw"] == "sorry, no JSON"


def test_run_one_site_reports_execution_errors_with_the_plan(monkeypatch) -> None:
    _patch_collect(monkeypatch, _intel(json_ld=[JOB_LD]))
    plan_without_extraction = '{"strategy": "json_ld"}'
    monkeypatch.setattr(smartextract, "ask_llm", _llm(plan_without_extraction))

    result = smartextract._run_one_site("Acme", "https://example.test/jobs")

    assert result["status"] == "EXEC_ERROR"
    assert result["plan"] == {"strategy": "json_ld"}
    assert "extraction" in result["error"]


def test_run_one_site_judges_api_responses_then_extracts_from_the_kept_ones(monkeypatch) -> None:
    api = [
        {"url": "https://x.test/api/jobs", "status": 200, "size": 9, "type": "array",
         "_raw_data": [{"name": "Analyst", "link": "a"}, {"name": "Engineer", "link": "b"}]},
        {"url": "https://x.test/api/auth", "status": 200, "size": 9, "_raw_data": {"token": "t"}},
    ]
    _patch_collect(monkeypatch, _intel(api_responses=api))
    client = _FakeClient('{"relevant": true}', '{"relevant": false}')
    monkeypatch.setattr(smartextract, "get_client", lambda task=None: client)
    plan = {"strategy": "api_response", "reasoning": "api", "extraction": {
        "url_pattern": "/api/jobs", "items_path": "", "title": "name", "url": "link"}}
    monkeypatch.setattr(smartextract, "ask_llm", _llm(json.dumps(plan)))

    result = smartextract._run_one_site("Acme", "https://example.test/jobs")

    # items_path "" resolves to None (no list), so judge filtering is what this exercises.
    assert result["status"] == "FAIL" and result["strategy"] == "api_response"
    assert len(client.prompts) == 2


def test_run_one_site_css_strategy_uses_second_llm_call_for_selectors(monkeypatch) -> None:
    _patch_collect(monkeypatch, _intel(full_html=CARD_HTML * 40))
    ask = _llm(
        '{"strategy": "css_selectors", "reasoning": "cards", "extraction": {}}',
        json.dumps(SELECTORS),
    )
    monkeypatch.setattr(smartextract, "ask_llm", ask)

    result = smartextract._run_one_site("Acme", "https://example.test/jobs")

    assert result["status"] == "PASS" and result["strategy"] == "css_selectors"
    assert result["total"] == 200  # the selector matches every card in the repeated markup
    assert result["plan"]["extraction"] == SELECTORS
    assert len(ask.prompts) == 2


def _big_html(extra: str = "") -> str:
    return "<main>" + "".join(f'<article><a href="/j/{n}">Job title number {n}</a></article>' for n in range(300)) + extra + "</main>"


def test_run_one_site_retries_headful_only_for_small_non_captcha_pages(monkeypatch) -> None:
    small = _intel(full_html="<p>tiny page</p>")
    calls = _patch_collect(monkeypatch, small, _intel(json_ld=[JOB_LD]))
    monkeypatch.setattr(smartextract, "ask_llm", _llm(json.dumps(JSON_LD_PLAN)))

    result = smartextract._run_one_site("Acme", "https://example.test/jobs")

    assert calls == [("https://example.test/jobs", True), ("https://example.test/jobs", False)]
    assert result["status"] == "PASS"


@pytest.mark.parametrize(
    "full_html",
    ["<p>Please verify you are a human: CAPTCHA</p>", _big_html(), ""],
    ids=["captcha-page", "large-page", "no-html"],
)
def test_run_one_site_skips_headful_retry(monkeypatch, full_html) -> None:
    calls = _patch_collect(monkeypatch, _intel(full_html=full_html))
    monkeypatch.setattr(smartextract, "ask_llm", _llm('{"strategy": "css_selectors", "extraction": {}}',
                                                       '{"error": "no job listings found"}'))

    result = smartextract._run_one_site("Acme", "https://example.test/jobs")

    assert calls == [("https://example.test/jobs", True)]
    assert result["status"] == "FAIL"


# -- _run_all / run_smart_extract --------------------------------------------

def _fake_site_runner(results: dict):
    calls: list[tuple[str, str]] = []

    def fake(name, url):
        calls.append((name, url))
        return results[url]

    fake.calls = calls
    return fake


def _pass_result(name, strategy, jobs):
    return {"name": name, "status": "PASS", "strategy": strategy, "total": len(jobs), "titles": len(jobs), "jobs": jobs}


def test_run_all_sequential_stores_filtered_jobs_and_counts_passes(monkeypatch, conn) -> None:
    results = {
        "https://a.test": _pass_result("A", "json_ld", [
            {"url": "https://a.test/1", "title": "Analyst", "location": "Singapore"},
            {"url": "https://a.test/2", "title": "Analyst", "location": "Toronto, Canada"},
        ]),
        "https://b.test": {"name": "B", "status": "LLM_ERROR", "error": "quota"},
        "https://c.test": {"name": "C", "status": "FAIL", "strategy": "css_selectors", "total": 0, "titles": 0,
                           "jobs": []},
    }
    runner = _fake_site_runner(results)
    monkeypatch.setattr(smartextract, "_run_one_site", runner)
    monkeypatch.setattr(smartextract, "init_db", lambda: conn)
    targets = [
        {"name": "A", "url": "https://a.test", "query": "analyst"},
        {"name": "B", "url": "https://b.test", "query": None},
        {"name": "C", "url": "https://c.test", "query": None},
    ]

    stats = smartextract._run_all(targets, ["singapore"], ["canada"])

    assert stats == {"total_new": 1, "total_existing": 0, "passed": 1, "total": 3}
    assert [c[0] for c in runner.calls] == ["A", "B", "C"]
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_run_all_parallel_processes_every_target(monkeypatch, conn) -> None:
    results = {
        f"https://{n}.test": _pass_result(n, "api_response", [{"url": f"https://{n}.test/job", "title": "T"}])
        for n in "abc"
    }
    runner = _fake_site_runner(results)
    monkeypatch.setattr(smartextract, "_run_one_site", runner)
    monkeypatch.setattr(smartextract, "init_db", lambda: conn)
    targets = [{"name": n, "url": f"https://{n}.test", "query": None} for n in "abc"]

    stats = smartextract._run_all(targets, [], [], workers=3)

    assert stats == {"total_new": 3, "total_existing": 0, "passed": 3, "total": 3}
    assert sorted(c[0] for c in runner.calls) == ["a", "b", "c"]


def test_run_smart_extract_returns_zero_stats_without_targets(monkeypatch) -> None:
    monkeypatch.setattr(smartextract.config, "load_search_config", dict)
    monkeypatch.setattr(smartextract, "_run_all", lambda *_a, **_k: pytest.fail("nothing to run"))

    assert smartextract.run_smart_extract(sites=[]) == {"total_new": 0, "total_existing": 0, "passed": 0, "total": 0}


def test_run_smart_extract_builds_targets_and_passes_search_policy(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(smartextract.config, "load_search_config", lambda: {
        "queries": [{"query": "analyst"}],
        "locations": [{"location": "Singapore"}],
        "location": {"accept_patterns": ["singapore"], "reject_patterns": ["canada"]},
        "exclude_titles": ["Senior Staff"],
    })

    def fake_run_all(targets, accept, reject, excluded_titles=None, workers=1):
        captured.update(targets=targets, accept=accept, reject=reject, excluded=excluded_titles, workers=workers)
        return {"total_new": 0, "total_existing": 0, "passed": 0, "total": len(targets)}

    monkeypatch.setattr(smartextract, "_run_all", fake_run_all)
    sites = [{"name": "Board", "url": "https://b.test/?q={query_encoded}", "type": "search"},
             {"name": "Static", "url": "https://s.test/"}]

    result = smartextract.run_smart_extract(sites=sites, workers=2)

    assert result["total"] == 2
    assert [t["url"] for t in captured["targets"]] == ["https://b.test/?q=analyst", "https://s.test/"]
    assert captured["accept"] == ["singapore"] and captured["reject"] == ["canada"]
    assert captured["excluded"] == ["senior staff"] and captured["workers"] == 2


def test_run_smart_extract_treats_empty_searches_yaml_as_no_config(monkeypatch, tmp_path) -> None:
    # yaml.safe_load returns None for an empty searches.yaml.
    searches = tmp_path / "searches.yaml"
    searches.write_text("", encoding="utf-8")
    monkeypatch.setattr(smartextract.config, "SEARCH_CONFIG_PATH", searches)
    monkeypatch.setattr(smartextract, "_run_all", lambda *_a, **_k: pytest.fail("nothing to run"))

    assert smartextract.run_smart_extract(sites=[]) == {"total_new": 0, "total_existing": 0, "passed": 0, "total": 0}
    static = [{"name": "Static", "url": "https://s.test/"}]
    assert smartextract.build_scrape_targets(static, search_cfg=None) == [
        {"name": "Static", "url": "https://s.test/", "query": None},
    ]


def test_format_strategy_briefing_shows_top_level_item_keys_and_samples() -> None:
    intel = _intel(api_responses=[{
        "url": "https://x.test/api/jobs", "status": 200, "size": 3, "type": "array",
        "first_item_keys": ["title", "slug"], "first_item_sample": {"title": "Analyst"},
    }])

    briefing = smartextract.format_strategy_briefing(intel)

    assert "Item keys: ['title', 'slug']" in briefing and '"title": "Analyst"' in briefing


# -- ask_llm / location filter -----------------------------------------------

def test_ask_llm_returns_text_timing_and_metadata(monkeypatch) -> None:
    client = _FakeClient("hello")
    monkeypatch.setattr(smartextract, "get_client", lambda task=None: client)

    text, elapsed, meta = smartextract.ask_llm("prompt text")

    assert text == "hello" and elapsed >= 0
    assert meta == {"finish_reason": "stop", "prompt_chars": len("prompt text"), "response_chars": 5}


def test_location_filter_helpers_load_config_when_not_given(monkeypatch) -> None:
    monkeypatch.setattr(smartextract.config, "load_search_config", lambda: {
        "location_accept": ["Singapore"], "location_reject_non_remote": ["Canada"],
    })

    accept, reject = smartextract._load_location_filter()

    assert (accept, reject) == (["Singapore"], ["Canada"])
    assert smartextract._location_ok("Singapore", accept, reject)
    assert not smartextract._location_ok("Canada", accept, reject)
