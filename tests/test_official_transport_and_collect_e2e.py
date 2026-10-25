"""Offline coverage for the official-source transport layer and ``radar collect``.

Only ``urllib.request.urlopen`` and the watchlist loader are replaced for the
CLI-level cases, so the real default transport, adapters and ingestion run.
"""
from __future__ import annotations

import email.message
import functools
import io
import json
import urllib.error
from pathlib import Path

import pytest
from typer.testing import CliRunner

from applypilot import cli, config, database
from applypilot.discovery import official

FIXTURES = Path(__file__).parent / "fixtures" / "radar" / "official"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _company(provider: str, **extra):
    company = {"id": "example", "name": "Example Tech", "provider": provider}
    company.update(extra)
    return company


def _ok(body, status=200):
    return lambda url, headers=None: {"status_code": status, "text": body}


class _FakeResponse:
    """Minimal urlopen() result usable as a context manager."""

    def __init__(self, status=200, body=b"", headers=None):
        self._status = status
        self._body = body
        self.headers = email.message.Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getcode(self):
        return self._status

    def read(self):
        return self._body


# --- _default_transport -----------------------------------------------------


def test_default_transport_returns_status_body_and_headers(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None, **_kwargs):
        seen["request"] = request
        seen["timeout"] = timeout
        return _FakeResponse(200, b'{"ok": true}', {"Content-Type": "application/json"})

    monkeypatch.setattr(official.urllib.request, "urlopen", fake_urlopen)

    response = official._default_transport(
        "https://example.test/jobs", {"X-Trace": "1", "User-Agent": "custom-agent"}
    )

    assert response == {
        "status_code": 200,
        "body": b'{"ok": true}',
        "headers": {"Content-Type": "application/json"},
    }
    request = seen["request"]
    assert request.get_method() == "GET"
    assert request.full_url == "https://example.test/jobs"
    assert request.get_header("X-trace") == "1"
    # Caller-supplied headers override the conservative defaults.
    assert request.get_header("User-agent") == "custom-agent"
    assert request.get_header("Accept").startswith("application/json")
    assert seen["timeout"] == 20


def test_default_transport_sends_read_only_user_agent_by_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        official.urllib.request,
        "urlopen",
        lambda request, timeout=None, **_kwargs: seen.setdefault("request", request) and _FakeResponse(),
    )
    official._default_transport("https://example.test/jobs")
    assert seen["request"].get_header("User-agent") == official._USER_AGENT


def test_default_transport_converts_http_error_to_mapping(monkeypatch):
    def fake_urlopen(request, timeout=None, **_kwargs):
        headers = email.message.Message()
        headers["Retry-After"] = "30"
        raise urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", headers, io.BytesIO(b"denied")
        )

    monkeypatch.setattr(official.urllib.request, "urlopen", fake_urlopen)

    response = official._default_transport("https://example.test/jobs")

    assert response["status_code"] == 403
    assert response["body"] == b"denied"
    assert response["headers"] == {"Retry-After": "30"}


def test_default_transport_lets_network_errors_propagate(monkeypatch):
    def fake_urlopen(request, timeout=None, **_kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(official.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(urllib.error.URLError):
        official._default_transport("https://example.test/jobs")


# --- _call_transport --------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("plain text", (200, "plain text", {})),
        (b"bytes \xff body", (200, "bytes � body", {})),
        ({"status": 404, "body": b"gone"}, (404, "gone", {})),
        ({"status_code": 202, "text": "queued"}, (202, "queued", {})),
        ({"text": 12345}, (200, "12345", {})),
        (
            {"status_code": 200, "text": "x", "headers": {"X-Rate": 5}},
            (200, "x", {"x-rate": "5"}),
        ),
        ({}, (200, "", {})),
    ],
)
def test_call_transport_normalises_supported_response_shapes(response, expected):
    assert official._call_transport(lambda url, headers=None: response, "https://e.test") == expected


@pytest.mark.parametrize("response", [None, 42, ["a"], object()])
def test_call_transport_rejects_unsupported_response_types(response):
    with pytest.raises(TypeError, match="transport must return"):
        official._call_transport(lambda url, headers=None: response, "https://e.test")


def test_call_transport_passes_user_agent_header():
    calls = []

    def transport(url, headers=None):
        calls.append((url, headers))
        return "ok"

    official._call_transport(transport, "https://e.test")

    assert calls == [("https://e.test", {"User-Agent": official._USER_AGENT})]


def test_call_transport_supports_transport_without_headers_parameter():
    calls = []

    def legacy(url):
        calls.append(url)
        return "legacy"

    assert official._call_transport(legacy, "https://e.test") == (200, "legacy", {})
    assert calls == ["https://e.test"]


def test_call_transport_supports_kwargs_partials_and_callable_objects():
    seen = []

    def with_kwargs(url, **kwargs):
        seen.append(("kwargs", kwargs))
        return "a"

    def base(prefix, url, headers=None):
        seen.append((prefix, headers))
        return "b"

    class Callable:
        def __call__(self, url, *, headers=None):
            seen.append(("object", headers))
            return "c"

    for transport in (with_kwargs, functools.partial(base, "partial"), Callable()):
        status, _body, _headers = official._call_transport(transport, "https://e.test")
        assert status == 200

    assert [name for name, _ in seen] == ["kwargs", "partial", "object"]
    assert all(value == {"User-Agent": official._USER_AGENT} or value == {"headers": {"User-Agent": official._USER_AGENT}}
               for _, value in seen)


def test_call_transport_does_not_swallow_internal_type_error_or_repeat_request():
    """Regression: a TypeError raised inside the transport must not trigger a second,
    header-less request."""
    calls = []

    def transport(url, headers=None):
        calls.append((url, headers))
        raise TypeError("internal failure")

    with pytest.raises(TypeError, match="internal failure"):
        official._call_transport(transport, "https://e.test")

    assert len(calls) == 1
    assert calls[0][1] == {"User-Agent": official._USER_AGENT}


def test_call_transport_internal_type_error_with_kwargs_transport_is_not_repeated():
    calls = []

    def transport(url, **kwargs):
        calls.append(kwargs)
        raise TypeError("boom")

    with pytest.raises(TypeError, match="boom"):
        official._call_transport(transport, "https://e.test")

    assert len(calls) == 1


def test_call_transport_internal_type_error_from_legacy_transport_propagates():
    calls = []

    def legacy(url):
        calls.append(url)
        raise TypeError("legacy internal")

    with pytest.raises(TypeError, match="legacy internal"):
        official._call_transport(legacy, "https://e.test")

    assert calls == ["https://e.test"]


def test_call_transport_falls_back_to_legacy_path_when_signature_is_unavailable(monkeypatch):
    def no_signature(_callable):
        raise ValueError("no signature found for builtin")

    monkeypatch.setattr(official.inspect, "signature", no_signature)
    calls = []

    def legacy(url):
        calls.append(url)
        return "text"

    assert official._call_transport(legacy, "https://e.test") == (200, "text", {})
    assert calls == ["https://e.test"]


# --- load_company_watchlist -------------------------------------------------


def _watchlist(tmp_path, text):
    path = tmp_path / "company_watchlist.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(
    ("text", "error", "match"),
    [
        ("companies:\n  name: not-a-list\n", TypeError, "must contain a companies list"),
        ("companies:\n  - name: Missing Id\n", ValueError, "missing id"),
        (
            "companies:\n  - {id: Dup, active: false}\n  - {id: dup, active: false}\n",
            ValueError,
            "duplicate id: dup",
        ),
        (
            "companies:\n  - {id: a, active: true, provider: taleo, cadence: daily}\n",
            ValueError,
            "unsupported provider: taleo",
        ),
        (
            "companies:\n  - {id: a, active: true, cadence: daily}\n",
            ValueError,
            "unsupported provider: missing",
        ),
        (
            "companies:\n  - {id: a, active: true, provider: greenhouse, cadence: daily}\n",
            ValueError,
            "requires canonical key board",
        ),
        (
            "companies:\n  - {id: a, active: true, provider: lever, site: ' ', cadence: daily}\n",
            ValueError,
            "requires canonical key site",
        ),
        (
            "companies:\n  - {id: a, active: true, provider: greenhouse, board: b, cadence: weekly}\n",
            ValueError,
            "must use daily cadence",
        ),
    ],
)
def test_load_watchlist_rejects_invalid_entries(tmp_path, text, error, match):
    with pytest.raises(error, match=match):
        official.load_company_watchlist(_watchlist(tmp_path, text))


def test_load_watchlist_accepts_inactive_placeholders_and_drops_non_mappings(tmp_path):
    path = _watchlist(
        tmp_path,
        "companies:\n"
        "  - just a string\n"
        "  - {id: parked, active: false, provider: taleo}\n"
        "  - {id: live, active: true, provider: GreenHouse, board: b, cadence: daily}\n",
    )

    companies = official.load_company_watchlist(path)

    assert [company["id"] for company in companies] == ["parked", "live"]


def test_load_watchlist_treats_empty_file_as_no_companies(tmp_path):
    assert official.load_company_watchlist(_watchlist(tmp_path, "")) == []


# --- collect_rss ------------------------------------------------------------


def test_rss_items_are_normalised_and_linkless_entries_are_reported():
    company = _company("rss", feed_url="https://careers.example.test/feed.xml")

    run = official.collect_rss(company, _ok(_fixture("feed.xml")))

    assert run["status"] == "partial"
    assert run["raw_count"] == 3
    assert run["normalised_count"] == 2
    assert run["errors"] == ["skipped RSS entry without title or public URL"]
    first, second = run["jobs"]
    assert first["title"] == "Data Analyst Intern"
    assert first["url"] == "https://careers.example.test/jobs/rss-1"
    assert first["external_id"] == "rss-1"
    assert first["location"] == "Singapore"
    assert first["description"] == "Build dashboards with SQL."
    assert first["published_at"] == "Mon, 24 Aug 2026 09:00:00 GMT"
    # A relative link is resolved against the feed URL; city/country form the location.
    assert second["url"] == "https://careers.example.test/jobs/rss-2"
    assert second["location"] == "Singapore, SG"
    assert run["metadata"]["inventory_complete"] is False


def test_atom_entries_use_link_href_and_content():
    company = _company("rss", feed_url="https://careers.example.test/atom.xml")

    run = official.collect_rss(company, _ok(_fixture("atom.xml")))

    assert run["status"] == "complete"
    (job,) = run["jobs"]
    assert job["title"] == "Product Operations Intern"
    assert job["url"] == "https://careers.example.test/jobs/atom-1"
    assert job["external_id"] == "urn:job:atom-1"
    assert job["description"] == "Support launches."
    assert job["published_at"] == "2026-08-25T10:00:00Z"


def test_rss_falls_back_to_job_nodes_and_career_url():
    company = _company("rss", career_url="https://careers.example.test/jobs.xml")
    seen = []

    def transport(url, headers=None):
        seen.append(url)
        return _fixture("job_nodes.xml")

    run = official.collect_rss(company, transport)

    assert seen == ["https://careers.example.test/jobs.xml"]
    assert run["status"] == "complete"
    (job,) = run["jobs"]
    assert job["title"] == "Strategy Analyst Intern"
    assert job["external_id"] == "REQ-77"
    assert job["location"] == "Singapore, Singapore"
    assert job["published_at"] == "2026-08-26"


def test_rss_latest_only_coverage_is_never_complete():
    company = _company(
        "rss", feed_url="https://careers.example.test/atom.xml", coverage_mode="latest_only"
    )

    run = official.collect_rss(company, _ok(_fixture("atom.xml")))

    assert run["status"] == "partial"
    assert run["metadata"]["inventory_complete"] is False
    assert run["errors"] == ["source exposes latest items only; full pagination unavailable"]
    assert len(run["jobs"]) == 1


def test_rss_invalid_xml_is_partial_with_parser_message():
    company = _company("rss", feed_url="https://careers.example.test/feed.xml")

    run = official.collect_rss(company, _ok("<rss><channel><item></rss>"))

    assert run["status"] == "partial"
    assert run["jobs"] == []
    assert run["errors"][0].startswith("invalid RSS/Atom XML:")


def test_rss_missing_feed_url_http_errors_and_transport_failures():
    missing = official.collect_rss(_company("rss"), _ok(""))
    assert (missing["status"], missing["errors"]) == ("partial", ["RSS company is missing feed_url"])

    company = _company("rss", feed_url="https://careers.example.test/feed.xml")
    assert official.collect_rss(company, _ok("", status=403))["status"] == "blocked"
    assert official.collect_rss(company, _ok("", status=500))["status"] == "partial"

    def failing(url, headers=None):
        raise OSError("socket closed")

    failed = official.collect_rss(company, failing)
    assert (failed["status"], failed["errors"]) == ("partial", ["socket closed"])


# --- JSON-LD ----------------------------------------------------------------


def test_extract_jobposting_handles_graph_type_lists_and_bad_blocks():
    company = _company("jobposting_jsonld")

    jobs = official.extract_jobposting_jsonld(
        _fixture("jobposting.html"), "https://careers.example.test/careers", company
    )

    assert [job["title"] for job in jobs] == ["Graph Wrapped Data Intern", "List Typed Operations Intern"]
    graph_job, list_job = jobs
    assert graph_job["url"] == "https://careers.example.test/jobs/graph-1"
    assert graph_job["external_id"] == "G-1"
    assert graph_job["location"] == "Singapore, SG"
    assert graph_job["published_at"] == "2026-08-20"
    assert graph_job["description"] == "Analyse data."
    # Without its own URL the page URL stands in; ``name`` stands in for ``title``.
    assert list_job["url"] == "https://careers.example.test/careers"
    assert all(job["provider"] == "jobposting_jsonld" for job in jobs)
    assert all(job["company_name"] == "Example Tech" for job in jobs)


def test_collect_jobposting_jsonld_end_to_end_and_error_paths():
    company = _company("jobposting_jsonld", career_url="https://careers.example.test/careers")

    run = official.collect_jobposting_jsonld(company, _ok(_fixture("jobposting.html")))
    assert run["status"] == "complete"
    assert run["pages_scanned"] == 1
    assert run["raw_count"] == run["normalised_count"] == 2

    missing = official.collect_jobposting_jsonld(_company("jobposting_jsonld"), _ok(""))
    assert (missing["status"], missing["errors"]) == ("partial", ["JSON-LD company is missing career_url"])

    assert official.collect_jobposting_jsonld(company, _ok("", status=429))["status"] == "blocked"
    assert official.collect_jobposting_jsonld(company, _ok("", status=502))["status"] == "partial"

    def failing(url, headers=None):
        raise RuntimeError("tls handshake failed")

    failed = official.collect_jobposting_jsonld(company, failing)
    assert (failed["status"], failed["errors"]) == ("partial", ["tls handshake failed"])


# --- collect_workable -------------------------------------------------------


def test_workable_collects_valid_records_and_reports_unusable_ones():
    company = _company("workable", subdomain="example")

    run = official.collect_workable(company, _ok(_fixture("workable_jobs.json")))

    assert run["status"] == "partial"
    assert run["raw_count"] == 3
    assert [job["title"] for job in run["jobs"]] == ["Operations Intern"]
    assert run["jobs"][0]["location"] == "Singapore"
    assert run["errors"] == [
        "skipped non-object Workable record",
        "skipped Workable record without title or public URL",
    ]


def test_workable_error_paths():
    company = _company("workable", subdomain="example")

    missing = official.collect_workable(_company("workable"), _ok("{}"))
    assert (missing["status"], missing["pages_scanned"]) == ("partial", 0)
    assert "missing subdomain/account/site" in missing["errors"][0]

    assert official.collect_workable(company, _ok("", status=403))["status"] == "blocked"
    server_error = official.collect_workable(company, _ok("", status=500))
    assert server_error["status"] == "partial"
    assert server_error["errors"][0].startswith("HTTP 500 for https://www.workable.com/api/accounts/example")

    def failing(url, headers=None):
        raise OSError("dns failure")

    assert official.collect_workable(company, failing)["errors"] == ["dns failure"]

    bad_json = official.collect_workable(company, _ok("{not json"))
    assert bad_json["status"] == "partial"
    assert bad_json["errors"][0].startswith("invalid JSON:")

    no_jobs = official.collect_workable(company, _ok(json.dumps({"jobs": "nope"})))
    assert no_jobs["errors"] == ["Workable response is missing jobs list"]

    paged = official.collect_workable(
        company,
        _ok(json.dumps({"jobs": [{"shortcode": "A", "title": "T", "url": "https://w.test/a"}], "next": "x"})),
    )
    assert paged["status"] == "partial"
    assert paged["errors"] == ["Workable public response unexpectedly indicated pagination"]
    assert len(paged["jobs"]) == 1


# --- paged JSON adapters (greenhouse / lever / ashby) ------------------------


def _job(identifier, title="Data Intern", **extra):
    record = {"id": identifier, "title": title, "absolute_url": f"https://boards.example.test/jobs/{identifier}"}
    record.update(extra)
    return record


def _pages(*pages):
    """Transport serving ``pages`` in order, one per request."""
    queue = list(pages)
    requested = []

    def transport(url, headers=None):
        requested.append(url)
        return queue.pop(0)

    transport.requested = requested
    return transport


def _json_page(payload, status=200):
    return {"status_code": status, "text": json.dumps(payload)}


@pytest.mark.parametrize(
    ("response", "status", "message"),
    [
        (_json_page({}, status=403), "blocked", "HTTP 403"),
        (_json_page({}, status=503), "partial", "HTTP 503"),
        ({"status_code": 200, "text": "<html>"}, "partial", "invalid JSON"),
        (_json_page({"jobs": "nope"}), "partial", "missing jobs list"),
        (_json_page({"jobs": [1, 2]}), "partial", "skipped non-object job record"),
        (_json_page({"jobs": [], "hasMore": True}), "partial", "unrecognised pagination metadata"),
    ],
)
def test_greenhouse_response_problems_are_never_reported_as_complete(response, status, message):
    run = official.collect_greenhouse(_company("greenhouse", board="b"), _pages(response))

    assert run["status"] == status
    assert message in run["error"]
    assert run["metadata"]["inventory_complete"] is False


def test_json_adapter_transport_exception_is_partial():
    def failing(url, headers=None):
        raise OSError("connection reset")

    run = official.collect_greenhouse(_company("greenhouse", board="b"), failing)

    assert (run["status"], run["errors"], run["pages_scanned"]) == ("partial", ["connection reset"], 0)


def test_json_adapter_follows_same_origin_cursor_and_flags_duplicates():
    transport = _pages(
        _json_page({"data": [_job(1), {"title": "No url"}, "junk"], "next": "?page=2"}),
        _json_page({"data": [_job(1), _job(2)]}),
    )

    run = official.collect_lever(_company("lever", site="s"), transport)

    assert transport.requested == [
        "https://api.lever.co/v0/postings/s?mode=json",
        "https://api.lever.co/v0/postings/s?page=2",
    ]
    assert run["pages_scanned"] == 2
    assert [job["external_id"] for job in run["jobs"]] == ["1", "1", "2"]
    assert run["status"] == "partial"
    assert set(run["errors"]) == {
        "skipped record without title or public URL",
        "skipped non-object job record",
        "duplicate job identity in inventory",
    }
    # A legacy wrapper object cannot attest to a complete Lever inventory.
    assert run["metadata"]["inventory_validated"] is False


@pytest.mark.parametrize(
    "successor",
    ["http://api.lever.co/v0/postings/s?page=2", "https://evil.example.test/next"],
)
def test_json_adapter_refuses_cross_origin_or_non_https_cursors(successor):
    transport = _pages(_json_page({"data": [_job(1)], "next": successor}))

    run = official.collect_lever(_company("lever", site="s"), transport)

    assert len(transport.requested) == 1
    assert run["status"] == "partial"
    assert "refused cross-origin or non-HTTPS pagination URL" in run["errors"]


def test_json_adapter_stops_on_pagination_loop_and_page_limit():
    looping = _pages(
        _json_page({"data": [_job(1)], "next": "?mode=json"}),
    )
    run = official.collect_lever(_company("lever", site="s"), looping)
    assert run["status"] == "partial" and "pagination loop detected" in run["errors"]

    limited = _pages(
        _json_page({"data": [_job(1)], "next": "?page=2"}),
        _json_page({"data": [_job(2)], "next": "?page=3"}),
    )
    run = official.collect_lever(_company("lever", site="s", max_pages=2), limited)
    assert run["status"] == "partial"
    assert run["pages_scanned"] == 2
    assert "pagination stopped at configured limit (2 pages)" in run["errors"]


# --- collect_company / run_official_discovery -------------------------------


@pytest.mark.parametrize("provider", ["taleo", "", None])
def test_collect_company_unsupported_provider_is_blocked(provider):
    run = official.collect_company(
        {"id": "x", "name": "X", "provider": provider}, lambda url, headers=None: pytest.fail("no network")
    )

    assert run["status"] == "blocked"
    assert run["jobs"] == []
    assert run["errors"] == [f"unsupported official provider: {provider or 'missing'}"]
    assert run["metadata"]["inventory_complete"] is False


def test_run_official_discovery_skips_inactive_and_counts_statuses():
    companies = [
        _company("greenhouse", id="gh", board="gh", active=True),
        _company("lever", id="lv", site="lv", active=True),
        _company("rss", id="rs", feed_url="https://careers.example.test/feed.xml", active=True),
        _company("greenhouse", id="parked", board="parked", active=False),
        _company("greenhouse", id="implicit-inactive", board="x"),
    ]
    requested = []

    def transport(url, headers=None):
        requested.append(url)
        if "greenhouse" in url:
            return {"status_code": 200, "text": _fixture("greenhouse_jobs.json")}
        if "lever" in url:
            return {"status_code": 403, "text": ""}
        return {"status_code": 200, "text": "<rss><broken"}

    result = official.run_official_discovery(companies, transport)

    assert [run["company_id"] for run in result["runs"]] == ["gh", "lv", "rs"]
    assert result["skipped"] == [
        {"company_id": "parked", "reason": "inactive"},
        {"company_id": "implicit-inactive", "reason": "inactive"},
    ]
    assert (result["complete"], result["partial"], result["blocked"]) == (1, 1, 1)
    assert [job["external_id"] for job in result["jobs"]] == ["101", "102"]
    assert result["read_only"] is True
    assert len(requested) == 3


def test_run_official_discovery_can_include_inactive_and_uses_watchlist_by_default(monkeypatch):
    company = _company("greenhouse", board="gh", active=False)
    monkeypatch.setattr(official, "load_company_watchlist", lambda: [company])
    transport = _ok(_fixture("greenhouse_jobs.json"))

    assert official.run_official_discovery(transport=transport)["runs"] == []

    result = official.run_official_discovery(transport=transport, active_only=False)
    assert result["skipped"] == []
    assert result["complete"] == 1


# --- radar collect, from the CLI down to urlopen ----------------------------


@pytest.fixture
def conn(tmp_path, monkeypatch):
    database.close_connection()
    connection = database.init_db(tmp_path / "radar.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "radar.db")
    monkeypatch.setattr(database, "get_connection", lambda *a, **k: connection)
    monkeypatch.setattr(cli, "_radar_bootstrap", lambda: None)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    (tmp_path / "linkedin_searches.yaml").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "load_radar_config", dict)
    monkeypatch.setattr(config, "radar_location_is_accepted", lambda location, *a, **k: location == "Singapore")
    yield connection
    database.close_connection(tmp_path / "radar.db")


def _watchlist_entry(**extra):
    entry = {"id": "example", "name": "Example", "provider": "greenhouse", "board": "example",
             "active": True, "cadence": "daily"}
    entry.update(extra)
    return entry


def test_radar_collect_runs_real_transport_adapter_and_ingestion(conn, monkeypatch):
    monkeypatch.setattr(official, "load_company_watchlist", lambda: [_watchlist_entry()])
    requests = []

    def fake_urlopen(request, timeout=None, **_kwargs):
        requests.append((request.full_url, request.get_header("User-agent"), timeout))
        return _FakeResponse(200, _fixture("greenhouse_jobs.json").encode("utf-8"))

    # collect_company binds the default transport at definition time, so the real
    # transport is exercised by replacing urlopen rather than ``_default_transport``.
    monkeypatch.setattr(official.urllib.request, "urlopen", fake_urlopen)

    result = CliRunner().invoke(cli.app, ["radar", "collect", "--company", "example"])

    assert result.exit_code == 0, result.output
    assert requests == [
        ("https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true",
         official._USER_AGENT, 20)
    ]
    (summary,) = json.loads(result.output)["sources"]
    assert summary["status"] == "complete"
    assert (summary["raw"], summary["normalised"], summary["accepted"]) == (2, 2, 1)
    assert summary["error"] is None
    rows = conn.execute("SELECT url, title FROM jobs").fetchall()
    assert [row["url"] for row in rows] == ["https://boards.greenhouse.io/example/jobs/101"]
    run = conn.execute("SELECT * FROM radar_fetch_runs").fetchone()
    assert run["status"] == "complete"
    metadata = json.loads(run["metadata_json"])
    assert metadata["inventory_complete"] is True
    assert metadata["accepted_count"] == 1
    assert metadata["location_title_filtered_count"] == 1


def test_radar_collect_records_blocked_http_status_in_run_ledger(conn, monkeypatch):
    monkeypatch.setattr(official, "load_company_watchlist", lambda: [_watchlist_entry()])

    def fake_urlopen(request, timeout=None, **_kwargs):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", email.message.Message(), io.BytesIO(b""))

    monkeypatch.setattr(official.urllib.request, "urlopen", fake_urlopen)

    result = CliRunner().invoke(cli.app, ["radar", "collect"])

    assert result.exit_code == 0, result.output
    (summary,) = json.loads(result.output)["sources"]
    assert summary["status"] == "blocked"
    assert summary["accepted"] == 0
    assert "HTTP 403" in summary["error"]
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    run = conn.execute("SELECT status, error FROM radar_fetch_runs").fetchone()
    assert run["status"] == "blocked"
    assert "HTTP 403" in run["error"]
    assert json.loads(
        conn.execute("SELECT metadata_json FROM radar_fetch_runs").fetchone()[0]
    )["inventory_complete"] is False


def test_radar_collect_closes_run_when_network_error_escapes_the_adapter(conn, monkeypatch):
    monkeypatch.setattr(official, "load_company_watchlist", lambda: [_watchlist_entry()])

    def fake_urlopen(request, timeout=None, **_kwargs):
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(official.urllib.request, "urlopen", fake_urlopen)

    result = CliRunner().invoke(cli.app, ["radar", "collect"])

    assert result.exit_code == 0, result.output
    (summary,) = json.loads(result.output)["sources"]
    assert summary["status"] == "partial"
    assert "name resolution failed" in summary["error"]
    run = conn.execute("SELECT status, finished_at FROM radar_fetch_runs").fetchone()
    assert run["status"] == "partial" and run["finished_at"]


def test_radar_collect_rejects_unknown_company_and_inactive_without_dry_run(conn, monkeypatch):
    monkeypatch.setattr(official, "load_company_watchlist", lambda: [_watchlist_entry()])
    monkeypatch.setattr(
        official.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network must not be used")
    )

    unknown = CliRunner().invoke(cli.app, ["radar", "collect", "--company", "nobody"])
    assert unknown.exit_code == 1
    assert "Unknown company IDs" in unknown.output and "nobody" in unknown.output

    inactive = CliRunner().invoke(cli.app, ["radar", "collect", "--include-inactive"])
    assert inactive.exit_code == 2
    assert "requires --dry-run" in inactive.output


def test_radar_collect_with_no_active_sources_and_dry_run_never_touch_the_network(conn, monkeypatch):
    monkeypatch.setattr(
        official, "load_company_watchlist", lambda: [_watchlist_entry(id="parked", active=False)]
    )
    monkeypatch.setattr(
        official.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network must not be used")
    )

    empty = CliRunner().invoke(cli.app, ["radar", "collect"])
    assert empty.exit_code == 0
    assert "No matching radar sources" in empty.output

    dry = CliRunner().invoke(cli.app, ["radar", "collect", "--include-inactive", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    payload = json.loads(dry.output)
    assert payload["read_only"] is True
    assert [item["company_id"] for item in payload["selected"]] == ["parked"]
    assert conn.execute("SELECT COUNT(*) FROM radar_fetch_runs").fetchone()[0] == 0
