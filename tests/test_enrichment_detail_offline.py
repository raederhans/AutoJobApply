"""Offline coverage for detail-page enrichment (applypilot.enrichment.detail).

No network, browser or LLM is involved: Playwright is replaced by duck-typed
fakes, the LLM client by an object implementing ``ask``, and every sleep is
stubbed out.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from applypilot import database
from applypilot.enrichment import detail

LONG_DESC = (
    "We are hiring a data analyst to build dashboards, maintain SQL pipelines and "
    "analyse the hiring funnel for the whole recruiting team every week."
)
assert len(LONG_DESC) > 100


# -- fakes -------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int = 200, url: str = "", payload: object = None) -> None:
        self.status = status
        self.url = url
        self._payload = payload

    def text(self) -> str:
        return json.dumps(self._payload)


class FakeElement:
    def __init__(self, text: str = "", html: str = "", attrs: dict | None = None,
                 tag: str = "a", parent_href: str | None = None) -> None:
        self._text = text
        self._html = html
        self._attrs = attrs or {}
        self._tag = tag
        self._parent_href = parent_href

    def inner_text(self) -> str:
        return self._text

    def inner_html(self) -> str:
        return self._html

    def get_attribute(self, name: str):
        return self._attrs.get(name)

    def evaluate(self, script: str):
        if "tagName" in script:
            return self._tag
        return self._parent_href


@dataclass
class PageSpec:
    status: int | None = 200
    goto_error: Exception | None = None
    networkidle_error: Exception | None = None
    title: str = "Data Analyst"
    title_error: Exception | None = None
    final_url: str | None = None
    json_ld: list[str] = field(default_factory=list)
    elements: dict[str, FakeElement] = field(default_factory=dict)
    links: list[FakeElement] = field(default_factory=list)
    body_html: str | None = None
    evaluate_error: Exception | None = None


class FakePage:
    """Duck-typed Playwright page; behaviour is chosen per navigated URL."""

    def __init__(self, specs: dict[str, PageSpec] | None = None, default: PageSpec | None = None) -> None:
        self.specs = specs or {}
        self.default = default or PageSpec()
        self.spec = self.default
        self.url = ""
        self.visited: list[str] = []
        self.waits: list[str] = []
        self.handlers: dict[str, object] = {}

    def goto(self, url: str, timeout: int | None = None):
        self.visited.append(url)
        self.spec = self.specs.get(url, self.default)
        self.url = self.spec.final_url or url
        if self.spec.goto_error:
            raise self.spec.goto_error
        if self.spec.status is None:
            return None
        return FakeResponse(self.spec.status)

    def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
        self.waits.append(state)
        if state == "networkidle" and self.spec.networkidle_error:
            raise self.spec.networkidle_error

    def title(self) -> str:
        if self.spec.title_error:
            raise self.spec.title_error
        return self.spec.title

    def query_selector(self, selector: str):
        return self.spec.elements.get(selector)

    def query_selector_all(self, selector: str):
        if selector == 'script[type="application/ld+json"]':
            return [FakeElement(text=raw) for raw in self.spec.json_ld]
        if selector == "a":
            return self.spec.links
        return []

    def evaluate(self, script: str):
        if self.spec.evaluate_error:
            raise self.spec.evaluate_error
        return self.spec.body_html

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler


class FakeBrowser:
    def __init__(self, page: FakePage, record: dict) -> None:
        self._page = page
        self._record = record

    def new_context(self, **kwargs):
        self._record["context_opts"] = kwargs
        page = self._page

        class _Context:
            def new_page(self_inner):
                return page

        return _Context()

    def new_page(self, **kwargs):
        self._record["page_opts"] = kwargs
        return self._page

    def close(self) -> None:
        self._record["closed"] = True


def install_fake_playwright(monkeypatch, page: FakePage) -> dict:
    record: dict = {"launches": 0}

    class _Chromium:
        def launch(self, **kwargs):
            record["launches"] += 1
            record["launch_opts"] = kwargs
            return FakeBrowser(page, record)

    class _Playwright:
        chromium = _Chromium()

    class _Manager:
        def __enter__(self):
            return _Playwright()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(detail, "sync_playwright", lambda: _Manager())
    return record


class FakeLLM:
    def __init__(self, reply: str | None = None, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def ask(self, prompt: str, temperature: float = 0.0, max_tokens: int = 0) -> str:
        self.calls.append((prompt, {"temperature": temperature, "max_tokens": max_tokens}))
        if self.error:
            raise self.error
        return self.reply or ""


def job_posting_ld(**overrides) -> str:
    posting = {"@type": "JobPosting", "description": LONG_DESC, "url": "https://example.com/jobs/1"}
    posting.update(overrides)
    return json.dumps(posting)


# -- fixtures ----------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "enrich.db"
    database.init_db(path).close()
    return path


@pytest.fixture
def conn(db_path: Path, monkeypatch):
    connection = database.init_db(db_path)
    monkeypatch.setattr(detail, "init_db", lambda *a, **k: database.init_db(db_path))
    yield connection
    connection.close()


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    calls: list[float] = []
    monkeypatch.setattr(detail.time, "sleep", lambda seconds=0: calls.append(seconds))
    return calls


def add_job(conn, url: str, site: str = "RemoteOK", title: str = "Data Analyst", **cols) -> None:
    values = {"url": url, "title": title, "site": site, "source_site": site, **cols}
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO jobs ({names}) VALUES ({marks})", tuple(values.values()))
    conn.commit()


def job_row(conn, url: str):
    return conn.execute(
        "SELECT full_description, application_url, detail_scraped_at, detail_error FROM jobs WHERE url = ?",
        (url,),
    ).fetchone()


# -- resolve_url -------------------------------------------------------------

BASES = {
    "SiteA": "https://a.example.com/",
    "Randstad Canada": "https://www.randstad.ca/",
    "4DayWeek": "https://4dayweek.io/",
    "WelcomeToTheJungle": "https://www.welcometothejungle.com/",
    "NoBase": None,
}


@pytest.mark.parametrize(
    ("raw", "site", "expected"),
    [
        ("", "SiteA", None),
        ("https://x.example.com/job/1", "SiteA", "https://x.example.com/job/1"),
        ("http://x.example.com/job/1", "Unknown", "http://x.example.com/job/1"),
        ("/jobs/1", "SiteA", "https://a.example.com/jobs/1"),
        ("jobs/1", "SiteA", "https://a.example.com/jobs/1"),
        ("/jobs/9;jsessionid=ABC123?x=1", "SiteA", "https://a.example.com/jobs/9"),
        ("some-slug", "WelcomeToTheJungle", None),
        ("some-slug", "Randstad Canada", "https://www.randstad.ca/jobs/search/some-slug"),
        ("/en/job/1", "Randstad Canada", "https://www.randstad.ca/en/job/1"),
        ("/", "4DayWeek", None),
        ("/jobs", "4DayWeek", None),
        ("/jobs/42", "4DayWeek", "https://4dayweek.io/jobs/42"),
        ("/jobs/1", "NoBase", None),
        ("/jobs/1", "NotConfigured", None),
    ],
)
def test_resolve_url_site_rules(monkeypatch, raw, site, expected) -> None:
    monkeypatch.setattr(detail, "_load_base_urls", lambda: BASES)

    assert detail.resolve_url(raw, site) == expected


def test_load_base_urls_delegates_to_config(monkeypatch) -> None:
    monkeypatch.setattr("applypilot.config.load_base_urls", lambda: {"X": "https://x.example/"})

    assert detail._load_base_urls() == {"X": "https://x.example/"}


def test_resolve_all_urls_upgrades_relative_and_dedupes_collisions(conn, monkeypatch) -> None:
    monkeypatch.setattr(detail, "_load_base_urls", lambda: BASES)
    # Absolute row that a relative row will collide with after resolution.
    add_job(conn, "https://a.example.com/jobs/1", site="SiteA", full_description="kept")
    add_job(conn, "/jobs/1", site="SiteA")
    add_job(conn, "/jobs/2", site="SiteA", application_url="apply/2")
    add_job(conn, "some-slug", site="WelcomeToTheJungle")  # unresolvable
    add_job(conn, "/jobs/3", site="NotConfigured")  # no base URL
    add_job(conn, "https://other.example.com/jobs/7", site="SiteA", application_url="/apply/7")

    stats = detail.resolve_all_urls(conn)

    assert stats == {"resolved": 2, "failed": 2, "already_absolute": 2, "app_resolved": 2}
    urls = {row[0] for row in conn.execute("SELECT url FROM jobs")}
    assert urls == {
        "https://a.example.com/jobs/1",
        "https://a.example.com/jobs/2",
        "some-slug",
        "/jobs/3",
        "https://other.example.com/jobs/7",
    }
    # The pre-existing absolute row survives the primary-key collision untouched.
    assert job_row(conn, "https://a.example.com/jobs/1")[0] == "kept"
    assert job_row(conn, "https://a.example.com/jobs/2")[1] == "https://a.example.com/apply/2"
    assert job_row(conn, "https://other.example.com/jobs/7")[1] == "https://a.example.com/apply/7"


# -- resolve_wttj_urls -------------------------------------------------------


def _algolia_payload(hits: list[dict]) -> dict:
    return {"results": [{"hits": hits}]}


def _wttj_page(payload: object, url: str = "https://x.algolia.net/1/indexes/*/queries") -> FakePage:
    page = FakePage()
    original_goto = page.goto

    def goto(target, timeout=None):
        response = original_goto(target, timeout=timeout)
        handler = page.handlers.get("response")
        if handler and payload is not None:
            handler(FakeResponse(url=url, payload=payload))
        # Unrelated and malformed responses must be ignored.
        if handler:
            handler(FakeResponse(url="https://cdn.example.com/app.js", payload={}))
            handler(_BrokenResponse(url))
        return response

    page.goto = goto
    return page


class _BrokenResponse:
    def __init__(self, url: str) -> None:
        self.url = url

    def text(self) -> str:
        return "<not json>"


def test_resolve_wttj_urls_maps_slugs_and_handles_collisions(conn, monkeypatch) -> None:
    add_job(conn, "alpha-slug_DFNS_abc", site="WelcomeToTheJungle", title="old-slug-title")
    add_job(conn, "beta-slug", site="WelcomeToTheJungle", title="Keep Title")
    add_job(conn, "xx-gamma-slug-yy", site="WelcomeToTheJungle", title="Fuzzy")  # fuzzy substring match
    add_job(conn, "unknown-slug", site="WelcomeToTheJungle")
    add_job(conn, "https://www.welcometothejungle.com/en/companies/acme/jobs/dup", site="WelcomeToTheJungle")
    add_job(conn, "dup", site="WelcomeToTheJungle", title="Dup old")  # collides after mapping
    add_job(conn, "xx-delta-slug-yy", site="WelcomeToTheJungle")  # fuzzy match that collides
    add_job(conn, "https://www.welcometothejungle.com/en/companies/acme/jobs/delta-slug", site="WelcomeToTheJungle")
    payload = _algolia_payload([
        {"slug": "alpha-slug", "organization": {"slug": "acme"}, "name": "Alpha Role"},
        {"slug": "beta-slug", "organization": {"slug": "acme"}, "name": ""},
        {"slug": "gamma-slug", "organization": {"slug": "acme"}, "name": "Gamma Role"},
        {"slug": "dup", "organization": {"slug": "acme"}, "name": "Dup Role"},
        {"slug": "delta-slug", "organization": {"slug": "acme"}, "name": "Delta"},
        {"slug": "", "organization": {"slug": "acme"}, "name": "No Slug"},
        {"slug": "no-org", "organization": "acme", "name": "No Org"},
    ])
    page = _wttj_page(payload)
    record = install_fake_playwright(monkeypatch, page)

    updated = detail.resolve_wttj_urls(conn)

    assert updated == 7
    assert record["closed"] is True
    assert record["page_opts"] == {"user_agent": detail.UA}
    urls = {row[0]: row[1] for row in conn.execute("SELECT url, title FROM jobs")}
    assert urls["https://www.welcometothejungle.com/en/companies/acme/jobs/alpha-slug"] == "Alpha Role"
    assert urls["https://www.welcometothejungle.com/en/companies/acme/jobs/beta-slug"] == "Keep Title"
    assert urls["https://www.welcometothejungle.com/en/companies/acme/jobs/gamma-slug"] == "Gamma Role"
    assert "unknown-slug" in urls
    assert "dup" not in urls and "xx-delta-slug-yy" not in urls


def test_resolve_wttj_urls_without_wttj_rows_or_response(conn, monkeypatch) -> None:
    record = install_fake_playwright(monkeypatch, _wttj_page(None))

    assert detail.resolve_wttj_urls(conn) == 0
    assert record["launches"] == 0  # no WTTJ rows: the browser is never started

    add_job(conn, "alpha", site="WelcomeToTheJungle")
    assert detail.resolve_wttj_urls(conn) == 0  # browser ran but captured no Algolia payload
    assert record["launches"] == 1


# -- collect_detail_intelligence / JSON-LD ------------------------------------


def test_collect_detail_intelligence_skips_malformed_json_ld() -> None:
    page = FakePage(default=PageSpec(json_ld=['{"@type": "JobPosting"}', "not json"], title="T"))
    page.goto("https://example.com/job")

    intel = detail.collect_detail_intelligence(page)

    assert intel == {
        "json_ld": [{"@type": "JobPosting"}],
        "page_title": "T",
        "final_url": "https://example.com/job",
    }


def test_extract_from_json_ld_basic_and_apply_url_priority() -> None:
    posting = {"@type": "JobPosting", "description": f"<p>{LONG_DESC}</p>", "url": "https://x.example/job"}

    result = detail.extract_from_json_ld({"json_ld": [posting]})
    assert result == {"full_description": LONG_DESC, "application_url": "https://x.example/job"}

    # directApply wins over applicationContact
    direct = {**posting, "directApply": True, "applicationContact": {"url": "https://x.example/contact"}}
    assert detail.extract_from_json_ld({"json_ld": [direct]})["application_url"] == "https://x.example/job"

    # applicationContact is the fallback when directApply is not set
    contact = {**posting, "applicationContact": {"url": "https://x.example/contact"}}
    assert detail.extract_from_json_ld({"json_ld": [contact]})["application_url"] == "https://x.example/contact"

    # a non-dict applicationContact is ignored
    odd = {**posting, "applicationContact": "mailto:hr@x.example"}
    assert detail.extract_from_json_ld({"json_ld": [odd]})["application_url"] == "https://x.example/job"

    # directApply without a URL still yields None rather than an exception
    bare = {"@type": "JobPosting", "description": LONG_DESC, "directApply": True}
    assert detail.extract_from_json_ld({"json_ld": [bare]})["application_url"] is None


def test_extract_from_json_ld_skips_unusable_candidates() -> None:
    short = {"@type": "JobPosting", "description": "Too short to be a real description."}
    empty = {"@type": "JobPosting", "description": ""}
    other = {"@type": "Organization", "description": LONG_DESC}
    good = {"@type": "JobPosting", "description": LONG_DESC, "url": "https://x.example/job"}

    assert detail.extract_from_json_ld({"json_ld": [short, empty, other]}) is None
    assert detail.extract_from_json_ld({}) is None
    # the first usable posting wins, even if earlier scripts were unusable
    result = detail.extract_from_json_ld({"json_ld": [short, other, good]})
    assert result["full_description"] == LONG_DESC


def test_extract_from_json_ld_finds_postings_in_lists_and_graphs() -> None:
    posting = {"@type": "JobPosting", "description": LONG_DESC, "url": "https://x.example/job"}

    in_list = detail.extract_from_json_ld({"json_ld": [[{"@type": "WebSite"}, posting]]})
    in_graph = detail.extract_from_json_ld({"json_ld": [{"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite"}, posting,
    ]}]})
    in_nested_graph = detail.extract_from_json_ld({"json_ld": [{"@graph": [{"@graph": [posting]}]}]})

    for result in (in_list, in_graph, in_nested_graph):
        assert result is not None
        assert result["full_description"] == LONG_DESC


@pytest.mark.parametrize("type_value", [["JobPosting"], ["Thing", "JobPosting"], "jobposting"])
def test_extract_from_json_ld_accepts_list_and_case_variant_types(type_value) -> None:
    posting = {"@type": type_value, "description": LONG_DESC, "url": "https://x.example/job"}

    direct = detail.extract_from_json_ld({"json_ld": [posting]})
    in_graph = detail.extract_from_json_ld({"json_ld": [{"@graph": [posting]}]})

    assert direct is not None and direct["application_url"] == "https://x.example/job"
    assert in_graph is not None and in_graph["full_description"] == LONG_DESC


# -- cleaning ---------------------------------------------------------------


def test_clean_description_html_to_text() -> None:
    html = "<div>About<br>the role</div><ul><li>Build SQL</li><li>Ship dashboards</li></ul><p>  Apply now  </p>"

    assert detail.clean_description(html) == "About\nthe role\n- Build SQL\n- Ship dashboards\nApply now"


def test_clean_description_plain_text_and_empty() -> None:
    assert detail.clean_description("") == ""
    assert detail.clean_description("  first  \n\n\n\n   \nsecond\t\n") == "first\nsecond"
    # a bare comparison is not treated as markup
    assert detail.clean_description("salary > 5k and < 9k") == "salary > 5k and < 9k"


def test_clean_content_html_drops_noise_and_attributes() -> None:
    html = (
        "<header>Site header</header><nav>menu</nav>"
        "<main><script>alert(1)</script><style>p{}</style>"
        '<div id="job" class="short another a-12 third fourth-one this-class-name-is-way-too-long-to-keep" '
        'style="color:red" onclick="x()" data-x="1" aria-hidden="true" role="main" lang="en">'
        '<a href="/apply" target="_blank" title="t">Apply</a><svg><path/></svg></div></main>'
        "<footer>Footer</footer>"
    )

    cleaned = detail.clean_content_html(html)

    for noise in ("Site header", "menu", "alert", "p{}", "Footer", "<svg", "style=", "onclick", "target=", "title="):
        assert noise not in cleaned
    assert 'id="job"' in cleaned
    assert 'class="short another third"' in cleaned  # a-12 and the long class are dropped, max 3 kept
    assert 'data-x="1"' in cleaned and 'aria-hidden="true"' in cleaned
    assert 'role="main"' in cleaned and 'href="/apply"' in cleaned
    assert "lang=" not in cleaned


def test_clean_content_html_drops_class_attribute_when_nothing_survives() -> None:
    cleaned = detail.clean_content_html('<div class="a-1 b-2">x</div>')

    assert cleaned == "<div>x</div>"


# -- Tier 2 helpers ----------------------------------------------------------


def test_extract_apply_url_deterministic_variants() -> None:
    def page_with(**kwargs) -> FakePage:
        page = FakePage(default=PageSpec(**kwargs))
        page.goto("https://example.com/job/1")
        return page

    href = FakeElement(attrs={"href": "https://apply.example.com/1"})
    assert detail.extract_apply_url_deterministic(
        page_with(elements={'a[href*="apply"]': href})
    ) == "https://apply.example.com/1"

    # '#' href on a button: fall back to the neighbouring link, then to the page URL
    button = FakeElement(attrs={"href": "#"}, tag="button", parent_href="https://apply.example.com/2")
    assert detail.extract_apply_url_deterministic(
        page_with(elements={'a[href*="apply"]': button})
    ) == "https://apply.example.com/2"
    orphan = FakeElement(attrs={}, tag="button")
    assert detail.extract_apply_url_deterministic(
        page_with(elements={'a[data-testid*="apply"]': orphan})
    ) == "https://example.com/job/1"

    # an anchor with '#' is not a button: keep searching, then use the text scan
    anchor = FakeElement(attrs={"href": "#"}, tag="a")
    links = [
        FakeElement(text="Apply for newsletter", attrs={"href": "javascript:void(0)"}),
        FakeElement(text="x" * 60 + " apply", attrs={"href": "https://too-long.example.com"}),
        FakeElement(text="Apply now", attrs={"href": "#"}),
        FakeElement(text="Apply now", attrs={"href": "https://apply.example.com/3"}),
    ]
    assert detail.extract_apply_url_deterministic(
        page_with(elements={'a[href*="apply"]': anchor}, links=links)
    ) == "https://apply.example.com/3"

    assert detail.extract_apply_url_deterministic(page_with()) is None


def test_extract_apply_url_deterministic_survives_broken_selectors() -> None:
    class ExplodingElement(FakeElement):
        def get_attribute(self, name):
            raise RuntimeError("detached")

    page = FakePage(default=PageSpec(
        elements={'a[href*="apply"]': ExplodingElement()},
        links=[ExplodingElement(text="Apply")],
    ))
    page.goto("https://example.com/job")

    assert detail.extract_apply_url_deterministic(page) is None


def test_extract_description_deterministic_threshold_and_errors() -> None:
    class ExplodingElement(FakeElement):
        def inner_text(self):
            raise RuntimeError("detached")

    page = FakePage(default=PageSpec(elements={
        "#job-description": ExplodingElement(),
        "#job_description": FakeElement(text="too short"),
        ".job-description": FakeElement(text=f"<p>{LONG_DESC}</p>"),
    }))
    page.goto("https://example.com/job")

    assert detail.extract_description_deterministic(page) == LONG_DESC
    assert detail.extract_description_deterministic(FakePage()) is None


def test_extract_main_content_prefers_main_then_falls_back_to_body() -> None:
    big = FakeElement(text="x" * 300, html="<div class='a-1' id='k'>kept</div><nav>menu</nav>")
    page = FakePage(default=PageSpec(elements={"main": big}))
    page.goto("https://example.com/job")
    assert detail.extract_main_content(page) == '<div id="k">kept</div>'

    # too large: skipped; falls through to the body evaluation
    huge = FakeElement(text="x" * 300, html="y" * 50001)
    page = FakePage(default=PageSpec(elements={"main": huge}, body_html="<p>body text</p>"))
    page.goto("https://example.com/job")
    assert detail.extract_main_content(page) == "<p>body text</p>"

    # a short main element is ignored, and an evaluation failure yields an empty string
    page = FakePage(default=PageSpec(elements={"main": FakeElement(text="tiny")},
                                     evaluate_error=RuntimeError("closed")))
    page.goto("https://example.com/job")
    assert detail.extract_main_content(page) == ""


def test_extract_main_content_survives_a_failing_selector() -> None:
    class ExplodingElement(FakeElement):
        def inner_text(self):
            raise RuntimeError("detached")

    page = FakePage(default=PageSpec(elements={"main": ExplodingElement()}, body_html="<p>fallback</p>"))
    page.goto("https://example.com/job")

    assert detail.extract_main_content(page) == "<p>fallback</p>"


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (None, None),
        ("   ", None),
        ("mailto:hr@example.com", None),
        ("javascript:void(0)", None),
        ("/apply/1", "https://example.com/apply/1"),
        ("https://www.linkedin.com/login", None),
        ("https://www.linkedin.com/jobs/view/1", "https://www.linkedin.com/jobs/view/1"),
    ],
)
def test_sanitize_application_url(candidate, expected) -> None:
    assert detail.sanitize_application_url("https://example.com/job/1", candidate) == expected


# -- Tier 3 ------------------------------------------------------------------


def _llm_page(**kwargs) -> FakePage:
    spec = PageSpec(elements={"main": FakeElement(text="x" * 300, html="<p>Job body</p>")}, **kwargs)
    page = FakePage(default=spec)
    page.goto("https://example.com/job/1")
    return page


def test_extract_with_llm_parses_fenced_json(monkeypatch) -> None:
    llm = FakeLLM('Here you go:\n```json\n{"full_description": "<p>Line one</p><p>Line two</p>", '
                  '"application_url": "https://apply.example.com/9"}\n```')
    monkeypatch.setattr(detail, "get_client", lambda: llm)

    result = detail.extract_with_llm(_llm_page(), "https://example.com/job/1")

    assert result == {"full_description": "Line one\nLine two", "application_url": "https://apply.example.com/9"}
    prompt, kwargs = llm.calls[0]
    assert "PAGE URL: https://example.com/job/1" in prompt and "Job body" in prompt
    assert kwargs == {"temperature": 0.0, "max_tokens": 4096}


def test_extract_with_llm_failure_modes(monkeypatch) -> None:
    # no content on the page: the LLM is never consulted
    llm = FakeLLM("{}")
    monkeypatch.setattr(detail, "get_client", lambda: llm)
    empty_page = FakePage(default=PageSpec(evaluate_error=RuntimeError("closed")))
    empty_page.goto("https://example.com/job/1")
    assert detail.extract_with_llm(empty_page, "u") == {"full_description": None, "application_url": None}
    assert llm.calls == []

    # transport error, unparsable output and non-object output all degrade to "nothing found"
    nothing = {"full_description": None, "application_url": None}
    for client in (FakeLLM(error=RuntimeError("boom")), FakeLLM("no json at all"), FakeLLM("[1, 2]")):
        monkeypatch.setattr(detail, "get_client", lambda client=client: client)
        assert detail.extract_with_llm(_llm_page(), "u") == nothing

    # an unreadable title does not stop the extraction
    monkeypatch.setattr(detail, "get_client", lambda: FakeLLM('{"full_description": null, "application_url": null}'))
    assert detail.extract_with_llm(_llm_page(title_error=RuntimeError("gone")), "u") == nothing


# -- scrape_detail_page cascade -----------------------------------------------

JOB_URL = "https://example.com/jobs/1"


def test_scrape_detail_page_tier1_json_ld() -> None:
    page = FakePage(default=PageSpec(json_ld=[job_posting_ld(directApply=True)]))

    result = detail.scrape_detail_page(page, JOB_URL)

    assert (result["tier_used"], result["status"]) == (1, "ok")
    assert result["full_description"] == LONG_DESC
    assert result["application_url"] == "https://example.com/jobs/1"
    assert "domcontentloaded" in page.waits and "networkidle" in page.waits
    assert result["elapsed"] >= 0


def test_scrape_detail_page_tier1_uses_css_apply_url_when_json_ld_has_none() -> None:
    ld = json.dumps({"@type": "JobPosting", "description": LONG_DESC})
    apply_link = FakeElement(attrs={"href": "https://apply.example.com/1"})
    page = FakePage(default=PageSpec(json_ld=[ld], elements={'a[href*="apply"]': apply_link}))
    result = detail.scrape_detail_page(page, JOB_URL)
    assert (result["tier_used"], result["status"], result["application_url"]) == (
        1, "ok", "https://apply.example.com/1")

    page = FakePage(default=PageSpec(json_ld=[ld]))
    result = detail.scrape_detail_page(page, JOB_URL)
    assert (result["tier_used"], result["status"], result["application_url"]) == (1, "partial", None)


def test_scrape_detail_page_tier2_css() -> None:
    elements = {
        "#job-description": FakeElement(text=LONG_DESC),
        'a[href*="apply"]': FakeElement(attrs={"href": "https://apply.example.com/2"}),
    }
    result = detail.scrape_detail_page(FakePage(default=PageSpec(elements=elements)), JOB_URL)
    assert (result["tier_used"], result["status"]) == (2, "ok")
    assert result["full_description"] == LONG_DESC
    assert result["application_url"] == "https://apply.example.com/2"

    elements.pop('a[href*="apply"]')
    result = detail.scrape_detail_page(FakePage(default=PageSpec(elements=elements)), JOB_URL)
    assert (result["tier_used"], result["status"], result["application_url"]) == (2, "partial", None)


def test_scrape_detail_page_tier3_llm(monkeypatch) -> None:
    llm = FakeLLM('```json\n{"full_description": "Full text from the model", "application_url": null}\n```')
    monkeypatch.setattr(detail, "get_client", lambda: llm)
    elements = {
        "main": FakeElement(text="x" * 300, html="<p>body</p>"),
        'a[class*="apply-btn"]': FakeElement(attrs={"href": "https://apply.example.com/3"}),
    }

    result = detail.scrape_detail_page(FakePage(default=PageSpec(elements=elements)), JOB_URL)

    # the model returned no URL, so the Tier-2 apply link is kept
    assert (result["tier_used"], result["status"]) == (3, "ok")
    assert result["full_description"] == "Full text from the model"
    assert result["application_url"] == "https://apply.example.com/3"
    assert len(llm.calls) == 1


def test_scrape_detail_page_tier3_partial_and_failure(monkeypatch) -> None:
    main = {"main": FakeElement(text="x" * 300, html="<p>body</p>")}

    # only an apply URL found: partial
    monkeypatch.setattr(detail, "get_client", lambda: FakeLLM('{"application_url": "https://a.example/1"}'))
    result = detail.scrape_detail_page(FakePage(default=PageSpec(elements=dict(main))), JOB_URL)
    assert (result["tier_used"], result["status"], result["error"]) == (3, "partial", None)

    # LLM failure with nothing else on the page: error
    monkeypatch.setattr(detail, "get_client", lambda: FakeLLM(error=RuntimeError("llm down")))
    result = detail.scrape_detail_page(FakePage(default=PageSpec(elements=dict(main))), JOB_URL)
    assert (result["tier_used"], result["status"], result["error"]) == (3, "error", "no data extracted")
    assert result["full_description"] is None


@pytest.mark.parametrize("status", sorted(detail.PERMANENT_FAILURES))
def test_scrape_detail_page_permanent_http_failure(status) -> None:
    page = FakePage(default=PageSpec(status=status, json_ld=[job_posting_ld()]))

    result = detail.scrape_detail_page(page, JOB_URL)

    assert result["status"] == "error"
    assert result["error"] == f"HTTP {status}"
    assert result["tier_used"] is None
    assert page.waits == []  # nothing is extracted from a permanently failed page


def test_scrape_detail_page_navigation_errors() -> None:
    timed_out = FakePage(default=PageSpec(goto_error=RuntimeError("Page.goto: Timeout 45000ms exceeded.")))
    result = detail.scrape_detail_page(timed_out, JOB_URL)
    assert (result["status"], result["error"], result["tier_used"]) == ("error", "timeout", None)

    other = FakePage(default=PageSpec(goto_error=RuntimeError("net::ERR_NAME_NOT_RESOLVED " + "x" * 400)))
    result = detail.scrape_detail_page(other, JOB_URL)
    assert result["status"] == "error"
    assert result["error"].startswith("net::ERR_NAME_NOT_RESOLVED")
    assert len(result["error"]) == 200


def test_scrape_detail_page_tolerates_missing_response_and_networkidle_timeout() -> None:
    spec = PageSpec(status=None, networkidle_error=RuntimeError("Timeout 10000ms"), json_ld=[job_posting_ld()])
    page = FakePage(default=spec)

    result = detail.scrape_detail_page(page, JOB_URL)

    assert (result["tier_used"], result["status"]) == (1, "ok")


# -- scrape_site_batch --------------------------------------------------------


def _batch_specs() -> dict[str, PageSpec]:
    return {
        "https://example.com/ok": PageSpec(json_ld=[job_posting_ld(url="https://apply.example.com/ok", directApply=True)]),
        "https://example.com/gone": PageSpec(status=404),
        "https://example.com/css": PageSpec(elements={"#job-description": FakeElement(text=LONG_DESC)}),
        # LinkedIn login walls are not application targets, so the row degrades to "partial"
        "https://example.com/wall": PageSpec(json_ld=[job_posting_ld(
            url="https://www.linkedin.com/login?session_redirect=x", directApply=True)]),
    }


def test_scrape_site_batch_updates_rows_and_stats(conn, sleeps, monkeypatch) -> None:
    for name in ("ok", "gone", "css", "wall"):
        add_job(conn, f"https://example.com/{name}")
    monkeypatch.setattr(detail, "_PROXY_CONFIG", None)
    page = FakePage(specs=_batch_specs())
    record = install_fake_playwright(monkeypatch, page)
    jobs = [(f"https://example.com/{name}", name) for name in ("ok", "gone", "css", "wall")]

    stats = detail.scrape_site_batch(conn, "RemoteOK", jobs, delay=1.25)

    assert stats == {"processed": 4, "ok": 1, "partial": 2, "error": 1, "tiers": {1: 2, 2: 1, 3: 0}}
    assert sleeps == [1.25, 1.25, 1.25]  # one delay between jobs, none after the last
    assert record["launch_opts"] == {"headless": True}
    assert record["context_opts"] == {"user_agent": detail.UA}
    assert record["closed"] is True

    ok = job_row(conn, "https://example.com/ok")
    assert (ok[0], ok[1], ok[3]) == (LONG_DESC, "https://apply.example.com/ok", None)
    assert ok[2] is not None
    gone = job_row(conn, "https://example.com/gone")
    assert (gone[0], gone[1], gone[3]) == (None, None, "HTTP 404")
    assert gone[2] is not None
    css = job_row(conn, "https://example.com/css")
    assert (css[0], css[1], css[3]) == (LONG_DESC, None, None)
    wall = job_row(conn, "https://example.com/wall")
    assert (wall[0], wall[1]) == (LONG_DESC, None)


def test_scrape_site_batch_max_jobs_empty_and_proxy(conn, sleeps, monkeypatch) -> None:
    page = FakePage(default=PageSpec(json_ld=[job_posting_ld()]))
    record = install_fake_playwright(monkeypatch, page)

    empty = detail.scrape_site_batch(conn, "RemoteOK", [])
    assert empty == {"processed": 0, "ok": 0, "partial": 0, "error": 0, "tiers": {1: 0, 2: 0, 3: 0}}
    assert record["launches"] == 0

    assert record["launches"] == 0
    proxy = {"playwright": {"server": "http://proxy.example:8080"}}
    monkeypatch.setattr(detail, "_PROXY_CONFIG", proxy)
    for n in range(3):
        add_job(conn, f"https://example.com/m{n}")
    jobs = [(f"https://example.com/m{n}", None) for n in range(3)]  # None titles are tolerated

    stats = detail.scrape_site_batch(conn, "RemoteOK", jobs, delay=0.5, max_jobs=2)

    assert stats["processed"] == 2
    assert page.visited == ["https://example.com/m0", "https://example.com/m1"]
    assert record["launch_opts"] == {"headless": True, "proxy": {"server": "http://proxy.example:8080"}}
    assert job_row(conn, "https://example.com/m2")[2] is None


def test_scrape_site_batch_without_connection_opens_and_closes_its_own(db_path, conn, sleeps, monkeypatch) -> None:
    add_job(conn, "https://example.com/own")
    install_fake_playwright(monkeypatch, FakePage(default=PageSpec(json_ld=[job_posting_ld()])))

    stats = detail.scrape_site_batch(None, "RemoteOK", [("https://example.com/own", "t")])

    assert stats["ok"] == 1
    # The shared cached connection was closed by the batch; reopen to inspect the result.
    assert job_row(database.init_db(db_path), "https://example.com/own")[0] == LONG_DESC


def test_set_proxy_stores_parsed_config(monkeypatch) -> None:
    monkeypatch.setattr(detail, "_PROXY_CONFIG", None)

    detail.set_proxy(None)
    assert detail._PROXY_CONFIG is None

    detail.set_proxy("proxy.example:8080:user:secret")
    assert detail._PROXY_CONFIG["playwright"] == {
        "server": "http://proxy.example:8080", "username": "user", "password": "secret",
    }


# -- defect 7: observation-stage failures ------------------------------------


def test_scrape_site_batch_isolates_observation_failure_to_one_job(conn, sleeps, monkeypatch) -> None:
    for name in ("first", "boom", "last"):
        add_job(conn, f"https://example.com/{name}")
    specs = {
        "https://example.com/first": PageSpec(json_ld=[job_posting_ld()]),
        # the page is destroyed by a navigation after goto(): title() raises
        "https://example.com/boom": PageSpec(title_error=RuntimeError("Execution context was destroyed")),
        "https://example.com/last": PageSpec(json_ld=[job_posting_ld()]),
    }
    install_fake_playwright(monkeypatch, FakePage(specs=specs))
    jobs = [(f"https://example.com/{name}", name) for name in ("first", "boom", "last")]

    stats = detail.scrape_site_batch(conn, "RemoteOK", jobs, delay=0)

    assert (stats["processed"], stats["ok"], stats["error"]) == (3, 2, 1)
    boom = job_row(conn, "https://example.com/boom")
    assert boom[2] is not None  # marked as attempted, so it is not selected again forever
    assert "Execution context was destroyed" in boom[3]
    for name in ("first", "last"):
        row = job_row(conn, f"https://example.com/{name}")
        assert row[0] == LONG_DESC and row[2] is not None and row[3] is None


def test_scrape_site_batch_isolates_unexpected_extraction_errors(conn, sleeps, monkeypatch) -> None:
    for name in ("a", "b"):
        add_job(conn, f"https://example.com/{name}")
    install_fake_playwright(monkeypatch, FakePage(default=PageSpec(json_ld=[job_posting_ld()])))
    real = detail.extract_from_json_ld
    calls = {"n": 0}

    def flaky(intel):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("unexpected shape " + "x" * 400)
        return real(intel)

    monkeypatch.setattr(detail, "extract_from_json_ld", flaky)

    stats = detail.scrape_site_batch(
        conn, "RemoteOK", [("https://example.com/a", "a"), ("https://example.com/b", "b")], delay=0)

    assert (stats["ok"], stats["error"]) == (1, 1)
    failed = job_row(conn, "https://example.com/a")
    assert failed[3].startswith("extraction failed: ValueError: unexpected shape")
    assert len(failed[3]) < 300
    assert job_row(conn, "https://example.com/b")[0] == LONG_DESC


class _ClosingPage(FakePage):
    """A page that becomes unusable once a job fails on it, like a destroyed target."""

    def __init__(self, specs: dict[str, PageSpec]) -> None:
        super().__init__(specs=specs)
        self.dead = False
        self.closed = False

    def goto(self, url: str, timeout: int | None = None):
        if self.dead:
            raise RuntimeError("Target page, context or browser has been closed")
        return super().goto(url, timeout)

    def title(self) -> str:
        try:
            return super().title()
        except Exception:
            self.dead = True
            raise

    def close(self) -> None:
        self.closed = True


def _install_browser(monkeypatch, browser) -> None:
    class _Chromium:
        def launch(self, **kwargs):
            return browser

    class _Manager:
        def __enter__(self):
            return type("_Playwright", (), {"chromium": _Chromium()})()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(detail, "sync_playwright", lambda: _Manager())


def _failure_specs() -> dict[str, PageSpec]:
    return {
        "https://example.com/boom": PageSpec(title_error=RuntimeError("Execution context was destroyed")),
        "https://example.com/next": PageSpec(json_ld=[job_posting_ld()]),
        "https://example.com/last": PageSpec(json_ld=[job_posting_ld()]),
    }


def test_scrape_site_batch_replaces_a_page_destroyed_by_a_failure(conn, sleeps, monkeypatch) -> None:
    for name in ("boom", "next", "last"):
        add_job(conn, f"https://example.com/{name}")
    pages: list[_ClosingPage] = []

    class _Context:
        def new_page(self):
            pages.append(_ClosingPage(_failure_specs()))
            return pages[-1]

    class _Browser:
        def new_context(self, **kwargs):
            return _Context()

        def close(self) -> None:
            pass

    _install_browser(monkeypatch, _Browser())
    jobs = [(f"https://example.com/{name}", name) for name in ("boom", "next", "last")]

    stats = detail.scrape_site_batch(conn, "RemoteOK", jobs, delay=0)

    assert (stats["processed"], stats["ok"], stats["error"]) == (3, 2, 1)
    assert len(pages) == 2 and pages[0].closed and not pages[1].dead
    for name in ("next", "last"):
        assert job_row(conn, f"https://example.com/{name}")[0] == LONG_DESC


def test_scrape_site_batch_stops_without_consuming_jobs_when_the_browser_is_gone(
    conn, sleeps, monkeypatch
) -> None:
    for name in ("boom", "next", "last"):
        add_job(conn, f"https://example.com/{name}")
    opened = {"contexts": 0}

    class _Context:
        def __init__(self) -> None:
            self.pages = 0

        def new_page(self):
            self.pages += 1
            if self.pages > 1:
                raise RuntimeError("Target page, context or browser has been closed")
            return _ClosingPage(_failure_specs())

        def close(self) -> None:
            pass

    class _Browser:
        def new_context(self, **kwargs):
            opened["contexts"] += 1
            if opened["contexts"] > 1:
                raise RuntimeError("Browser has been closed")
            return _Context()

        def close(self) -> None:
            raise RuntimeError("Browser has been closed")

    _install_browser(monkeypatch, _Browser())
    jobs = [(f"https://example.com/{name}", name) for name in ("boom", "next", "last")]

    stats = detail.scrape_site_batch(conn, "RemoteOK", jobs, delay=0)

    assert (stats["processed"], stats["error"]) == (1, 1)
    assert job_row(conn, "https://example.com/boom")[2] is not None
    for name in ("next", "last"):
        row = job_row(conn, f"https://example.com/{name}")
        assert row[2] is None and row[3] is None  # left for the next run, not marked as scraped


# -- _run_detail_scraper ------------------------------------------------------


def _record_batches(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def fake_batch(conn, site, jobs, delay=2.0, max_jobs=None):
        calls.append({"conn": conn, "site": site, "urls": [u for u, _ in jobs], "delay": delay, "max_jobs": max_jobs})
        return {"processed": len(jobs), "ok": len(jobs) - 1, "partial": 0, "error": 1, "tiers": {1: 1, 2: 0, 3: 1}}

    monkeypatch.setattr(detail, "scrape_site_batch", fake_batch)
    return calls


def test_run_detail_scraper_with_nothing_pending(conn, monkeypatch) -> None:
    calls = _record_batches(monkeypatch)

    assert detail._run_detail_scraper(conn) == {"processed": 0, "ok": 0, "partial": 0, "error": 0}
    assert calls == []


def test_run_detail_scraper_orders_sites_and_filters(conn, monkeypatch) -> None:
    for site, url in [
        ("Zeta", "https://z.example.com/1"),
        ("Alpha", "https://a.example.com/1"),
        ("RemoteOK", "https://r.example.com/1"),
        ("Job Bank Canada", "https://j.example.com/1"),
        ("glassdoor", "https://g.example.com/1"),
        ("google", "https://google.example.com/1"),
        ("Workopolis", "https://w.example.com/1"),
    ]:
        add_job(conn, url, site=site)
    add_job(conn, "https://done.example.com/1", site="Alpha", detail_scraped_at="2026-01-01T00:00:00+00:00")
    calls = _record_batches(monkeypatch)

    stats = detail._run_detail_scraper(conn, max_per_site=5)

    assert [c["site"] for c in calls] == ["RemoteOK", "Job Bank Canada", "Alpha", "Zeta"]
    assert [c["delay"] for c in calls] == [3.0, 1.5, 2.0, 2.0]
    assert all(c["max_jobs"] == 5 and c["conn"] is conn for c in calls)
    assert stats == {"processed": 4, "ok": 0, "partial": 0, "error": 4, "skipped_manual": 0,
                     "tiers": {1: 4, 2: 0, 3: 4}}

    calls.clear()
    only = detail._run_detail_scraper(conn, sites=["Zeta"])
    assert [c["site"] for c in calls] == ["Zeta"]
    assert only["processed"] == 1


def test_run_detail_scraper_marks_manual_portal_listings(conn, monkeypatch) -> None:
    add_job(conn, "https://sg.jobstreet.com/jobs/1", site="JobStreet Singapore")
    add_job(conn, "https://a.example.com/1", site="Alpha")
    calls = _record_batches(monkeypatch)

    stats = detail._run_detail_scraper(conn)

    assert [c["site"] for c in calls] == ["Alpha"]
    assert stats["skipped_manual"] == 1
    gated = job_row(conn, "https://sg.jobstreet.com/jobs/1")
    assert gated[2] is not None and "automated portal enrichment is disabled" in gated[3]


def test_run_detail_scraper_runs_site_batches_in_parallel(conn, monkeypatch) -> None:
    add_job(conn, "https://a.example.com/1", site="Alpha")
    add_job(conn, "https://z.example.com/1", site="Zeta")
    calls = _record_batches(monkeypatch)

    stats = detail._run_detail_scraper(conn, workers=4)

    assert sorted(c["site"] for c in calls) == ["Alpha", "Zeta"]
    assert all(c["conn"] is None for c in calls)  # each worker opens its own connection
    assert stats["processed"] == 2 and stats["tiers"] == {1: 2, 2: 0, 3: 2}

    # a single site never needs the pool, even with several workers requested
    calls.clear()
    conn.execute("DELETE FROM jobs WHERE site = 'Zeta'")
    conn.commit()
    detail._run_detail_scraper(conn, workers=4)
    assert [c["conn"] is conn for c in calls] == [True]


# -- stream_detail ------------------------------------------------------------


class _Spin(BaseException):
    """Raised by test doubles to prove that a loop is not terminating on its own."""


def test_stream_detail_ends_immediately_when_nothing_is_pending(conn, sleeps, monkeypatch) -> None:
    proxies: list[str] = []
    monkeypatch.setattr(detail, "set_proxy", proxies.append)
    done = threading.Event()

    detail.stream_detail(None, done, proxy_str="proxy.example:8080")

    assert done.is_set() and proxies == ["proxy.example:8080"]
    assert sleeps == []


def test_stream_detail_scrapes_pending_jobs_by_site_and_skips_manual_portals(
    conn, db_path, sleeps, monkeypatch
) -> None:
    add_job(conn, "/rel/1", site="Alpha")  # upgraded by the initial URL resolution
    add_job(conn, "https://z.example.com/1", site="Zeta")
    add_job(conn, "https://sg.jobstreet.com/jobs/1", site="JobStreet Singapore")
    monkeypatch.setattr(detail, "_load_base_urls", lambda: {"Alpha": "https://a.example.com/"})
    seen: list[tuple[str, list[str], float]] = []

    def fake_batch(connection, site, jobs, delay=2.0, max_jobs=None):
        seen.append((site, [u for u, _ in jobs], delay))
        for url, _ in jobs:
            connection.execute("UPDATE jobs SET detail_scraped_at = 'now' WHERE url = ?", (url,))
        connection.commit()
        return {"processed": len(jobs), "ok": len(jobs), "partial": 0, "error": 0, "tiers": {}}

    monkeypatch.setattr(detail, "scrape_site_batch", fake_batch)
    done = threading.Event()

    detail.stream_detail(None, done)

    assert done.is_set()
    assert sorted(seen) == [("Alpha", ["https://a.example.com/rel/1"], 2.0), ("Zeta", ["https://z.example.com/1"], 2.0)]
    gated = job_row(database.init_db(db_path), "https://sg.jobstreet.com/jobs/1")
    assert gated[2] is not None and gated[3]


def test_stream_detail_waits_for_upstream_before_finishing(conn, sleeps, monkeypatch) -> None:
    upstream = threading.Event()
    done = threading.Event()

    def stop_after_polls(seconds=0):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            upstream.set()

    monkeypatch.setattr(detail.time, "sleep", stop_after_polls)

    detail.stream_detail(upstream, done, poll_interval=7.0)

    assert sleeps == [7.0, 7.0]
    assert done.is_set()


def test_stream_detail_does_not_spin_when_every_batch_crashes(conn, sleeps, monkeypatch) -> None:
    add_job(conn, "https://a.example.com/1", site="Alpha")
    add_job(conn, "https://z.example.com/1", site="Zeta")
    attempts = {"n": 0}

    def crashing_batch(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] > 10:
            raise _Spin("stream_detail kept re-selecting jobs whose batch crashed")
        raise RuntimeError("browser died")

    monkeypatch.setattr(detail, "scrape_site_batch", crashing_batch)
    done = threading.Event()

    detail.stream_detail(None, done)  # no upstream: a stalled run must give up rather than loop

    assert done.is_set()
    assert attempts["n"] == 2  # one attempt per site, then the stalled run terminates


def test_stream_detail_backs_off_while_upstream_is_still_running(conn, monkeypatch) -> None:
    add_job(conn, "https://a.example.com/1", site="Alpha")
    attempts = {"n": 0}
    upstream = threading.Event()
    waits: list[float] = []

    def crashing_batch(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] > 10:
            raise _Spin("no backoff between crashed batches")
        raise RuntimeError("browser died")

    def sleep(seconds=0):
        waits.append(seconds)
        if len(waits) == 3:
            upstream.set()

    monkeypatch.setattr(detail, "scrape_site_batch", crashing_batch)
    monkeypatch.setattr(detail.time, "sleep", sleep)
    done = threading.Event()

    detail.stream_detail(upstream, done, poll_interval=4.0)

    assert done.is_set()
    assert waits == [4.0, 4.0, 4.0]
    assert attempts["n"] == 4  # one attempt per poll, plus the final attempt after upstream finished


# -- bounded retry of transient detail failures -------------------------------

RETRY_URL = "https://example.com/retry/1"
_PAST = "2000-01-01T00:00:00+00:00"
_FUTURE = "2999-01-01T00:00:00+00:00"


def _retry_row(conn, url: str = RETRY_URL):
    return conn.execute(
        "SELECT detail_scraped_at, detail_attempts, detail_retry_after, detail_error FROM jobs WHERE url = ?",
        (url,),
    ).fetchone()


def _scrape_once(conn, monkeypatch, spec: PageSpec, url: str = RETRY_URL) -> dict:
    monkeypatch.setattr(detail, "_PROXY_CONFIG", None)
    install_fake_playwright(monkeypatch, FakePage(default=spec))
    return detail.scrape_site_batch(conn, "RemoteOK", [(url, "Data Analyst")], delay=0)


_TRANSIENT_SPECS = [
    pytest.param(PageSpec(status=429), "HTTP 429", id="429"),
    pytest.param(PageSpec(status=503), "HTTP 503", id="503"),
    pytest.param(PageSpec(goto_error=RuntimeError("Page.goto: Timeout 45000ms exceeded.")), "timeout", id="timeout"),
    pytest.param(PageSpec(goto_error=RuntimeError("net::ERR_CONNECTION_RESET")), "net::ERR_CONNECTION_RESET",
                 id="connection-reset"),
]


@pytest.mark.parametrize(("spec", "reason"), _TRANSIENT_SPECS)
def test_transient_failure_is_not_marked_permanent(conn, sleeps, monkeypatch, spec, reason) -> None:
    add_job(conn, RETRY_URL)

    stats = _scrape_once(conn, monkeypatch, spec)

    scraped_at, attempts, retry_after, error = _retry_row(conn)
    assert stats["error"] == 1
    assert scraped_at is None
    assert (attempts, error) == (1, reason)
    assert retry_after is not None and retry_after > "2026-01-01T00:00:00+00:00"


@pytest.mark.parametrize(
    ("spec", "retryable"),
    [
        (PageSpec(status=429, json_ld=[job_posting_ld()]), True),
        (PageSpec(status=504, json_ld=[job_posting_ld()]), True),
        (PageSpec(status=404, json_ld=[job_posting_ld()]), False),
        (PageSpec(goto_error=RuntimeError("Timeout 45000ms exceeded")), True),
        (PageSpec(goto_error=RuntimeError("net::ERR_CONNECTION_CLOSED")), True),
        (PageSpec(goto_error=RuntimeError("net::ERR_NAME_NOT_RESOLVED")), False),
    ],
)
def test_scrape_detail_page_flags_only_transient_failures_as_retryable(spec, retryable) -> None:
    page = FakePage(default=spec)

    result = detail.scrape_detail_page(page, JOB_URL)

    assert result["status"] == "error"
    assert bool(result.get("retryable")) is retryable
    if spec.status in detail.RETRYABLE_STATUSES:
        assert result["error"] == f"HTTP {spec.status}"
        assert page.waits == []  # a throttled page is not parsed (or sent to the LLM)


def test_transient_failure_is_retried_with_backoff_then_marked_permanent(conn, sleeps, monkeypatch) -> None:
    add_job(conn, RETRY_URL)
    throttled = PageSpec(status=429)

    for expected_attempts in (1, 2):
        _scrape_once(conn, monkeypatch, throttled)
        scraped_at, attempts, retry_after, _ = _retry_row(conn)
        assert (scraped_at, attempts) == (None, expected_attempts)
        assert retry_after is not None
        conn.execute("UPDATE jobs SET detail_retry_after = ? WHERE url = ?", (_PAST, RETRY_URL))
        conn.commit()

    _scrape_once(conn, monkeypatch, throttled)  # third and final attempt

    scraped_at, attempts, retry_after, error = _retry_row(conn)
    assert scraped_at is not None
    assert (attempts, retry_after, error) == (3, None, "HTTP 429")


def test_retry_backoff_grows_between_attempts(conn, sleeps, monkeypatch) -> None:
    add_job(conn, RETRY_URL)
    _scrape_once(conn, monkeypatch, PageSpec(status=503))
    first = _retry_row(conn)[2]
    conn.execute("UPDATE jobs SET detail_retry_after = ? WHERE url = ?", (_PAST, RETRY_URL))
    conn.commit()
    _scrape_once(conn, monkeypatch, PageSpec(status=503))
    second = _retry_row(conn)[2]

    assert second > first  # 6h after the second failure vs 1h after the first


def test_permanent_failure_is_still_marked_immediately(conn, sleeps, monkeypatch) -> None:
    add_job(conn, RETRY_URL)

    _scrape_once(conn, monkeypatch, PageSpec(status=404))

    scraped_at, _, retry_after, error = _retry_row(conn)
    assert scraped_at is not None
    assert (retry_after, error) == (None, "HTTP 404")


def test_success_after_a_transient_failure_clears_the_retry_state(conn, sleeps, monkeypatch) -> None:
    add_job(conn, RETRY_URL)
    _scrape_once(conn, monkeypatch, PageSpec(status=503))
    conn.execute("UPDATE jobs SET detail_retry_after = ? WHERE url = ?", (_PAST, RETRY_URL))
    conn.commit()

    stats = _scrape_once(conn, monkeypatch, PageSpec(json_ld=[job_posting_ld(url="https://apply.example.com/1")]))

    scraped_at, _, retry_after, error = _retry_row(conn)
    assert stats["ok"] == 1
    assert scraped_at is not None
    assert (retry_after, error) == (None, None)


def test_run_detail_scraper_skips_rows_in_backoff_until_they_are_due(conn, monkeypatch) -> None:
    add_job(conn, "https://a.example.com/waiting", site="Alpha", detail_attempts=1, detail_retry_after=_FUTURE)
    add_job(conn, "https://a.example.com/due", site="Alpha", detail_attempts=1, detail_retry_after=_PAST)
    add_job(conn, "https://a.example.com/new", site="Alpha")
    calls = _record_batches(monkeypatch)

    detail._run_detail_scraper(conn)

    assert sorted(calls[0]["urls"]) == ["https://a.example.com/due", "https://a.example.com/new"]


def test_stream_detail_ends_without_spinning_when_only_backoff_rows_remain(conn, db_path, sleeps, monkeypatch) -> None:
    add_job(conn, "https://a.example.com/waiting", site="Alpha", detail_attempts=1, detail_retry_after=_FUTURE)

    def no_batch(*args, **kwargs):
        raise _Spin("a row in backoff must not be scraped")

    monkeypatch.setattr(detail, "scrape_site_batch", no_batch)
    done = threading.Event()

    detail.stream_detail(None, done)  # nothing is due: exit now, a later run picks the row up

    assert done.is_set()
    assert sleeps == []
    assert _retry_row(database.init_db(db_path), "https://a.example.com/waiting")[1] == 1


def test_stream_detail_waits_for_upstream_when_only_backoff_rows_remain(conn, monkeypatch) -> None:
    add_job(conn, "https://a.example.com/waiting", site="Alpha", detail_attempts=1, detail_retry_after=_FUTURE)
    upstream = threading.Event()
    waits: list[float] = []

    def sleep(seconds=0):
        waits.append(seconds)
        if len(waits) == 2:
            upstream.set()

    monkeypatch.setattr(detail.time, "sleep", sleep)
    done = threading.Event()

    detail.stream_detail(upstream, done, poll_interval=3.0)

    assert done.is_set()
    assert waits == [3.0, 3.0]


def test_stream_detail_does_not_reselect_a_row_it_just_deferred(conn, db_path, sleeps, monkeypatch) -> None:
    add_job(conn, RETRY_URL)
    monkeypatch.setattr(detail, "_PROXY_CONFIG", None)
    page = FakePage(default=PageSpec(status=503))
    install_fake_playwright(monkeypatch, page)
    done = threading.Event()

    detail.stream_detail(None, done)

    assert done.is_set()
    assert page.visited == [RETRY_URL]  # one attempt this run, the retry waits for its backoff
    scraped_at, attempts, retry_after, _ = _retry_row(database.init_db(db_path))
    assert (scraped_at, attempts) == (None, 1)
    assert retry_after is not None


def test_stream_detail_retries_a_row_once_its_backoff_has_expired(conn, db_path, sleeps, monkeypatch) -> None:
    add_job(conn, RETRY_URL, detail_attempts=1, detail_retry_after=_PAST, detail_error="HTTP 503")
    monkeypatch.setattr(detail, "_PROXY_CONFIG", None)
    install_fake_playwright(monkeypatch, FakePage(default=PageSpec(json_ld=[job_posting_ld()])))
    done = threading.Event()

    detail.stream_detail(None, done)

    scraped_at, _, retry_after, error = _retry_row(database.init_db(db_path))
    assert scraped_at is not None
    assert (retry_after, error) == (None, None)


# -- run_enrichment -----------------------------------------------------------


def test_run_enrichment_resolves_urls_then_scrapes_with_limit(conn, sleeps, monkeypatch) -> None:
    add_job(conn, "/jobs/1", site="Alpha")
    add_job(conn, "https://example.com/css", site="RemoteOK")
    monkeypatch.setattr(detail, "_load_base_urls", lambda: {"Alpha": "https://a.example.com/"})
    specs = {
        "https://a.example.com/jobs/1": PageSpec(json_ld=[job_posting_ld()]),
        "https://example.com/css": PageSpec(status=410),
    }
    install_fake_playwright(monkeypatch, FakePage(specs=specs))

    stats = detail.run_enrichment(limit=1)

    assert (stats["processed"], stats["ok"], stats["error"]) == (2, 1, 1)
    assert stats["tiers"][1] == 1
    assert job_row(conn, "https://a.example.com/jobs/1")[0] == LONG_DESC
    assert job_row(conn, "https://example.com/css")[3] == "HTTP 410"


def test_run_enrichment_only_repairs_wttj_urls_when_they_are_relative(conn, monkeypatch) -> None:
    add_job(conn, "relative-slug", site="WelcomeToTheJungle")
    repaired: list[object] = []
    scraped: dict = {}
    monkeypatch.setattr(detail, "resolve_wttj_urls", lambda c: repaired.append(c) or 3)

    def fake_scraper(connection, max_per_site=None, workers=1):
        scraped.update(max_per_site=max_per_site, workers=workers)
        return {"processed": 0}

    monkeypatch.setattr(detail, "_run_detail_scraper", fake_scraper)

    assert detail.run_enrichment(limit=7, workers=3) == {"processed": 0}
    assert len(repaired) == 1
    assert scraped == {"max_per_site": 7, "workers": 3}

    conn.execute("UPDATE jobs SET url = 'https://www.welcometothejungle.com/en/x'")
    conn.commit()
    repaired.clear()
    detail.run_enrichment()
    assert repaired == []
