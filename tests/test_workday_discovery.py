"""Offline tests for the Workday CXS discovery path (workday_search/workday_detail are faked)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from applypilot.discovery import workday

EMPLOYER = {
    "name": "Acme Corp",
    "base_url": "https://acme.wd1.myworkdayjobs.com",
    "tenant": "acme",
    "site_id": "Careers",
}


@pytest.fixture
def conn(tmp_path: Path):
    connection = workday.init_db(tmp_path / "jobs.db")
    yield connection
    connection.close()


def _posting(n: int, location: str = "Singapore") -> dict:
    return {
        "title": f"Analyst {n}",
        "locationsText": location,
        "postedOn": "Posted Today",
        "externalPath": f"/job/Singapore/Analyst-{n}_R{n}",
    }


def _job(n: int = 1, **overrides) -> dict:
    job = {
        "title": f"Analyst {n}",
        "location": "Singapore",
        "posted": "Posted Today",
        "external_path": f"/job/Singapore/Analyst-{n}_R{n}",
        "employer_key": "acme",
        "employer_name": "Acme Corp",
    }
    job.update(overrides)
    return job


# -- strip_html --------------------------------------------------------------

def test_strip_html_converts_block_tags_to_newlines_and_drops_script_and_style() -> None:
    html = (
        "<style>.x{color:red}</style><h2>About</h2><p>Build   models</p>"
        "<ul><li>SQL</li><li>Python</li></ul><script>alert(1)</script><div>Apply&nbsp;now &amp; win</div>"
    )

    text = workday.strip_html(html)

    assert "color:red" not in text and "alert" not in text
    # Runs of spaces collapse, &nbsp; becomes a plain space, and block tags become line breaks.
    assert text.splitlines() == ["About", "Build models", "", "SQL", "", "Python", "", "Apply now & win"]


def test_strip_html_handles_empty_input() -> None:
    assert workday.strip_html("") == ""
    assert workday.strip_html(None) == ""


# -- load_employers ----------------------------------------------------------

def test_load_employers_returns_empty_dict_for_missing_file(monkeypatch, tmp_path, caplog) -> None:
    monkeypatch.setattr(workday, "CONFIG_DIR", tmp_path)

    with caplog.at_level(logging.WARNING, logger=workday.log.name):
        assert workday.load_employers() == {}
    assert "employers.yaml not found" in caplog.text


def test_load_employers_reads_registry(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(workday, "CONFIG_DIR", tmp_path)
    (tmp_path / "employers.yaml").write_text(
        "employers:\n  acme:\n    name: Acme Corp\n    base_url: https://acme.test\n"
        "    tenant: acme\n    site_id: Careers\n",
        encoding="utf-8",
    )

    assert workday.load_employers()["acme"]["name"] == "Acme Corp"


def test_workspace_employers_replace_shipped_registry(monkeypatch, tmp_path) -> None:
    shipped = tmp_path / "shipped"
    workspace = tmp_path / "workspace"
    shipped.mkdir()
    workspace.mkdir()
    (shipped / "employers.yaml").write_text("employers:\n  td:\n    name: TD Bank\n", encoding="utf-8")
    (workspace / "employers.yaml").write_text("employers:\n  acme:\n    name: Acme Corp\n", encoding="utf-8")
    monkeypatch.setattr(workday, "CONFIG_DIR", shipped)
    monkeypatch.setattr(workday.config, "APP_DIR", workspace)

    assert workday.load_employers() == {"acme": {"name": "Acme Corp"}}


def test_shipped_employers_used_without_workspace_registry(monkeypatch, tmp_path) -> None:
    shipped = tmp_path / "shipped"
    shipped.mkdir()
    (shipped / "employers.yaml").write_text("employers:\n  td:\n    name: TD Bank\n", encoding="utf-8")
    monkeypatch.setattr(workday, "CONFIG_DIR", shipped)
    monkeypatch.setattr(workday.config, "APP_DIR", tmp_path / "empty-workspace")

    assert workday.load_employers() == {"td": {"name": "TD Bank"}}


@pytest.mark.parametrize("content", ["", "# only a comment\n", "employers:\n", "other: 1\n"])
def test_load_employers_treats_empty_config_as_no_employers(monkeypatch, tmp_path, content) -> None:
    monkeypatch.setattr(workday, "CONFIG_DIR", tmp_path)
    (tmp_path / "employers.yaml").write_text(content, encoding="utf-8")

    assert workday.load_employers() == {}


# -- setup_proxy / HTTP helpers ----------------------------------------------

def test_setup_proxy_configures_opener_for_each_format(monkeypatch) -> None:
    monkeypatch.setattr(workday, "_opener", None)

    workday.setup_proxy("proxy.test:8080:alice:s3cret")
    proxies = next(h for h in workday._opener.handlers if isinstance(h, workday.urllib.request.ProxyHandler))
    assert proxies.proxies["https"] == "http://alice:s3cret@proxy.test:8080"

    workday.setup_proxy("proxy.test:8080")
    proxies = next(h for h in workday._opener.handlers if isinstance(h, workday.urllib.request.ProxyHandler))
    assert proxies.proxies["http"] == "http://proxy.test:8080"


@pytest.mark.parametrize("value", [None, "", "not-a-proxy", "a:b:c"])
def test_setup_proxy_falls_back_to_plain_opener(monkeypatch, value) -> None:
    monkeypatch.setattr(workday, "_opener", None)
    built: list[tuple] = []

    def fake_build_opener(*handlers):
        built.append(handlers)
        return "plain-opener"

    monkeypatch.setattr(workday.urllib.request, "build_opener", fake_build_opener)

    workday.setup_proxy(value)

    assert built == [()]
    assert workday._opener == "plain-opener"


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_workday_search_and_detail_build_cxs_requests(monkeypatch) -> None:
    requests = []

    def fake_urlopen(req, timeout=30):
        requests.append((req, timeout))
        return _FakeResponse({"total": 1, "jobPostings": []})

    monkeypatch.setattr(workday, "_urlopen", fake_urlopen)

    assert workday.workday_search(EMPLOYER, "analyst", limit=5, offset=10) == {"total": 1, "jobPostings": []}
    search_req, _ = requests[0]
    assert search_req.full_url == "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Careers/jobs"
    assert search_req.get_method() == "POST"
    assert json.loads(search_req.data) == {
        "appliedFacets": {}, "limit": 5, "offset": 10, "searchText": "analyst",
    }

    workday.workday_detail(EMPLOYER, "/job/Singapore/Analyst-1_R1")
    detail_req, timeout = requests[1]
    assert detail_req.full_url == (
        "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Careers/job/Singapore/Analyst-1_R1"
    )
    assert detail_req.get_method() == "GET"
    assert timeout == 30


def test_urlopen_uses_configured_opener_else_module_urlopen(monkeypatch) -> None:
    class Opener:
        def open(self, req, timeout):
            return ("opener", req, timeout)

    monkeypatch.setattr(workday, "_opener", Opener())
    assert workday._urlopen("req", timeout=7) == ("opener", "req", 7)

    monkeypatch.setattr(workday, "_opener", None)
    monkeypatch.setattr(workday.urllib.request, "urlopen", lambda req, timeout: ("plain", req, timeout))
    assert workday._urlopen("req", timeout=9) == ("plain", "req", 9)


# -- search_employer ---------------------------------------------------------

def test_search_employer_paginates_until_total_is_reached(monkeypatch) -> None:
    offsets: list[tuple[int, int]] = []

    def fake_search(employer, search_text, limit=20, offset=0):
        offsets.append((offset, limit))
        count = min(limit, 45 - offset)
        return {"total": 45, "jobPostings": [_posting(offset + i) for i in range(count)]}

    monkeypatch.setattr(workday, "workday_search", fake_search)

    jobs = workday.search_employer("acme", EMPLOYER, "analyst")

    assert offsets == [(0, 20), (20, 20), (40, 20)]
    assert len(jobs) == 45
    assert jobs[0] == {
        "title": "Analyst 0", "location": "Singapore", "posted": "Posted Today",
        "external_path": "/job/Singapore/Analyst-0_R0", "employer_key": "acme", "employer_name": "Acme Corp",
    }


def test_search_employer_stops_on_empty_page(monkeypatch) -> None:
    pages = iter([{"total": 100, "jobPostings": [_posting(1)]}, {"total": 100, "jobPostings": []}])
    monkeypatch.setattr(workday, "workday_search", lambda *_a, **_k: next(pages))

    assert len(workday.search_employer("acme", EMPLOYER, "analyst")) == 1


def test_search_employer_filters_locations_when_both_lists_are_given(monkeypatch) -> None:
    postings = [_posting(1, "Singapore"), _posting(2, "Toronto, Canada"), _posting(3, "")]
    monkeypatch.setattr(workday, "workday_search", lambda *_a, **_k: {"total": 3, "jobPostings": postings})

    filtered = workday.search_employer(
        "acme", EMPLOYER, "analyst", accept_locs=["singapore"], reject_locs=["canada"],
    )
    unfiltered_flag = workday.search_employer(
        "acme", EMPLOYER, "analyst", location_filter=False, accept_locs=["singapore"], reject_locs=["canada"],
    )
    no_lists = workday.search_employer("acme", EMPLOYER, "analyst")

    # Unknown (empty) locations are kept by the shared policy.
    assert [j["title"] for j in filtered] == ["Analyst 1", "Analyst 3"]
    assert len(unfiltered_flag) == 3
    assert len(no_lists) == 3


def test_search_employer_max_results_truncates_and_stops_paging(monkeypatch) -> None:
    calls: list[int] = []

    def fake_search(employer, search_text, limit=20, offset=0):
        calls.append(offset)
        return {"total": 1000, "jobPostings": [_posting(offset + i) for i in range(20)]}

    monkeypatch.setattr(workday, "workday_search", fake_search)

    jobs = workday.search_employer("acme", EMPLOYER, "analyst", max_results=25)

    assert calls == [0, 20]
    assert len(jobs) == 25


def test_search_employer_caps_at_25_pages(monkeypatch) -> None:
    calls: list[int] = []

    def fake_search(employer, search_text, limit=20, offset=0):
        calls.append(offset)
        return {"total": 100000, "jobPostings": [_posting(offset + i) for i in range(20)]}

    monkeypatch.setattr(workday, "workday_search", fake_search)

    jobs = workday.search_employer("acme", EMPLOYER, "analyst")

    assert len(calls) == 25
    assert len(jobs) == 500


def test_search_employer_keeps_partial_results_when_a_later_page_fails(monkeypatch, caplog) -> None:
    def fake_search(employer, search_text, limit=20, offset=0):
        if offset > 0:
            raise OSError("connection reset")
        return {"total": 100, "jobPostings": [_posting(1), _posting(2)]}

    monkeypatch.setattr(workday, "workday_search", fake_search)

    with caplog.at_level(logging.ERROR, logger=workday.log.name):
        jobs = workday.search_employer("acme", EMPLOYER, "analyst")

    assert len(jobs) == 2
    assert "API error at offset 20" in caplog.text


def test_search_employer_returns_empty_when_first_page_fails(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise OSError("down")

    monkeypatch.setattr(workday, "workday_search", boom)

    assert workday.search_employer("acme", EMPLOYER, "analyst") == []


# -- details -----------------------------------------------------------------

def test_fetch_details_maps_fields_and_strips_description_html(monkeypatch) -> None:
    def fake_detail(employer, external_path):
        return {
            "jobPostingInfo": {
                "jobDescription": "<p>Build <b>models</b></p>",
                "externalUrl": "https://acme.test/apply/1",
                "jobReqId": "R1",
                "timeType": "Full time",
                "remoteType": "Hybrid",
            }
        }

    monkeypatch.setattr(workday, "workday_detail", fake_detail)
    jobs = [_job(1)]

    result = workday.fetch_details(EMPLOYER, jobs)

    assert result is jobs
    assert jobs[0]["full_description"] == "Build models"
    assert jobs[0]["apply_url"] == "https://acme.test/apply/1"
    assert jobs[0]["job_req_id"] == "R1"
    assert jobs[0]["time_type"] == "Full time"
    assert jobs[0]["remote_type"] == "Hybrid"
    assert "detail_error" not in jobs[0]


def test_fetch_details_records_errors_per_job_and_continues(monkeypatch) -> None:
    def fake_detail(employer, external_path):
        if external_path.endswith("R1"):
            raise OSError("HTTP 500")
        return {}

    monkeypatch.setattr(workday, "workday_detail", fake_detail)
    jobs = [_job(1), _job(2)]

    workday.fetch_details(EMPLOYER, jobs)

    assert jobs[0]["detail_error"] == "HTTP 500"
    assert jobs[0]["full_description"] == "" and jobs[0]["apply_url"] == ""
    assert "detail_error" not in jobs[1]
    assert jobs[1]["full_description"] == ""


# -- store_results -----------------------------------------------------------

def test_store_results_url_fallback_description_threshold_and_exclusion(conn) -> None:
    employers = {"acme": EMPLOYER}
    long_description = "Own the forecasting stack. " * 12
    jobs = [
        _job(1, apply_url="https://acme.test/apply/1", full_description=long_description),
        _job(2, full_description="short text"),  # no apply_url -> built from base_url/site_id/path
        _job(3, employer_key="unknown"),  # no apply_url and no employer entry -> skipped
        _job(4, title="Senior Staff Engineer", apply_url="https://acme.test/apply/4"),
        _job(5, apply_url="https://acme.test/apply/5", full_description="", detail_error="HTTP 500"),
    ]

    result = workday.store_results(conn, jobs, employers, excluded_titles=["senior staff"])

    assert result == (3, 0)
    rows = {r["url"]: r for r in conn.execute("SELECT * FROM jobs")}
    assert set(rows) == {
        "https://acme.test/apply/1",
        "https://acme.wd1.myworkdayjobs.com/Careers/job/Singapore/Analyst-2_R2",
        "https://acme.test/apply/5",
    }
    long_row = rows["https://acme.test/apply/1"]
    assert long_row["full_description"] == long_description
    assert long_row["description"] == long_description[:500]
    assert long_row["detail_scraped_at"]
    assert long_row["application_url"] == "https://acme.test/apply/1"
    assert long_row["company_name"] == "Acme Corp"
    assert long_row["strategy"] == "workday_api"
    assert long_row["source_site"] == "Acme Corp"
    short_row = rows["https://acme.wd1.myworkdayjobs.com/Careers/job/Singapore/Analyst-2_R2"]
    assert short_row["full_description"] is None and short_row["detail_scraped_at"] is None
    assert short_row["description"] == "short text"
    assert short_row["application_url"] == short_row["url"]
    failed_row = rows["https://acme.test/apply/5"]
    assert failed_row["detail_error"] == "HTTP 500"
    assert failed_row["description"] is None and failed_row["detail_scraped_at"] is None


def test_store_results_counts_duplicates_on_second_run(conn) -> None:
    jobs = [_job(1, apply_url="https://acme.test/apply/1")]

    assert workday.store_results(conn, jobs, {"acme": EMPLOYER}) == (1, 0)
    assert workday.store_results(conn, jobs, {"acme": EMPLOYER}) == (0, 1)


# -- _process_one / scrape_employers -----------------------------------------

def test_process_one_reports_search_error(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("bad payload")

    monkeypatch.setattr(workday, "search_employer", boom)

    result = workday._process_one("acme", {"acme": EMPLOYER}, "analyst", True, [], [], [])

    assert result == {
        "employer": "Acme Corp", "query": "analyst", "found": 0, "new": 0, "existing": 0, "error": "bad payload",
    }


def test_process_one_returns_zero_counts_when_nothing_found(monkeypatch) -> None:
    monkeypatch.setattr(workday, "search_employer", lambda *_a, **_k: [])
    monkeypatch.setattr(workday, "fetch_details", lambda *_a, **_k: pytest.fail("no details expected"))

    result = workday._process_one("acme", {"acme": EMPLOYER}, "analyst", True, [], [], [])

    assert result == {"employer": "Acme Corp", "query": "analyst", "found": 0, "new": 0, "existing": 0}


def test_process_one_stores_jobs_even_if_detail_fetch_crashes(monkeypatch, conn) -> None:
    def fake_search(employer_key, employer, search_text, **kwargs):
        assert kwargs == {"location_filter": False, "accept_locs": ["sg"], "reject_locs": ["ca"]}
        return [_job(1)]

    def crash(*_a, **_k):
        raise RuntimeError("detail layer down")

    monkeypatch.setattr(workday, "search_employer", fake_search)
    monkeypatch.setattr(workday, "fetch_details", crash)
    monkeypatch.setattr(workday, "get_connection", lambda: conn)

    result = workday._process_one("acme", {"acme": EMPLOYER}, "analyst", False, ["sg"], ["ca"], [])

    assert result == {"employer": "Acme Corp", "query": "analyst", "found": 1, "new": 1, "existing": 0}
    row = conn.execute("SELECT url FROM jobs").fetchone()
    assert row["url"].endswith("/Careers/job/Singapore/Analyst-1_R1")


def test_process_one_end_to_end_with_faked_workday_api(monkeypatch, conn) -> None:
    monkeypatch.setattr(
        workday, "workday_search",
        lambda *_a, **_k: {"total": 2, "jobPostings": [_posting(1), _posting(2, "Toronto, Canada")]},
    )
    monkeypatch.setattr(
        workday, "workday_detail",
        lambda _employer, path: {"jobPostingInfo": {"jobDescription": "<p>Role</p>", "externalUrl": f"https://acme.test{path}"}},
    )
    monkeypatch.setattr(workday, "get_connection", lambda: conn)

    result = workday._process_one("acme", {"acme": EMPLOYER}, "analyst", True, ["singapore"], ["canada"], [])

    assert result["found"] == 1 and result["new"] == 1
    assert conn.execute("SELECT url FROM jobs").fetchone()["url"] == "https://acme.test/job/Singapore/Analyst-1_R1"


def _fake_process_one(results: dict):
    calls: list[str] = []

    def fake(key, employers, search_text, location_filter, accept_locs, reject_locs, excluded_titles):
        calls.append(key)
        return results[key]

    return fake, calls


def test_scrape_employers_sequential_sums_counts_and_skips_unknown_keys(monkeypatch) -> None:
    results = {
        "a": {"found": 3, "new": 2, "existing": 1},
        "b": {"found": 1, "new": 0, "existing": 1, "error": "boom"},
    }
    fake, calls = _fake_process_one(results)
    monkeypatch.setattr(workday, "_process_one", fake)
    monkeypatch.setattr(workday, "init_db", lambda: None)

    stats = workday.scrape_employers(
        "analyst", {"a": EMPLOYER, "b": EMPLOYER}, employer_keys=["a", "missing", "b"],
    )

    assert stats == {"found": 4, "new": 2, "existing": 2}
    assert calls == ["a", "b"]


def test_scrape_employers_parallel_processes_every_employer(monkeypatch) -> None:
    results = {key: {"found": 2, "new": 1, "existing": 1} for key in "abc"}
    results["c"]["error"] = "boom"
    fake, calls = _fake_process_one(results)
    monkeypatch.setattr(workday, "_process_one", fake)
    monkeypatch.setattr(workday, "init_db", lambda: None)

    stats = workday.scrape_employers("analyst", {key: EMPLOYER for key in "abc"}, workers=3)

    assert stats == {"found": 6, "new": 3, "existing": 3}
    assert sorted(calls) == ["a", "b", "c"]


def test_scrape_employers_with_one_employer_stays_sequential_even_with_workers(monkeypatch) -> None:
    fake, calls = _fake_process_one({"a": {"found": 1, "new": 1, "existing": 0}})
    monkeypatch.setattr(workday, "_process_one", fake)
    monkeypatch.setattr(workday, "init_db", lambda: None)
    monkeypatch.setattr(workday, "ThreadPoolExecutor", lambda *_a, **_k: pytest.fail("pool not expected"))

    assert workday.scrape_employers("analyst", {"a": EMPLOYER}, workers=4)["found"] == 1
    assert calls == ["a"]


# -- run_workday_discovery ---------------------------------------------------

def _patch_discovery(monkeypatch, search_cfg, scrape=None):
    calls: list[dict] = []

    def fake_scrape(**kwargs):
        calls.append(kwargs)
        return {"found": 2, "new": 1, "existing": 1}

    monkeypatch.setattr(workday.config, "load_search_config", lambda: search_cfg)
    monkeypatch.setattr(workday, "scrape_employers", scrape or fake_scrape)
    return calls


def test_run_workday_discovery_without_employers_returns_zero_stats(monkeypatch) -> None:
    monkeypatch.setattr(workday, "load_employers", dict)
    monkeypatch.setattr(workday.config, "load_search_config", lambda: pytest.fail("config must not load"))

    assert workday.run_workday_discovery() == {"found": 0, "new": 0, "existing": 0, "queries": 0}


def test_run_workday_discovery_without_queries_returns_zero_stats(monkeypatch) -> None:
    calls = _patch_discovery(monkeypatch, {"queries": []})

    result = workday.run_workday_discovery(employers={"acme": EMPLOYER})

    assert result == {"found": 0, "new": 0, "existing": 0, "queries": 0}
    assert calls == []


def test_run_workday_discovery_uses_tier_filter_and_passes_search_policy(monkeypatch) -> None:
    proxies: list[str] = []
    monkeypatch.setattr(workday, "setup_proxy", proxies.append)
    calls = _patch_discovery(monkeypatch, {
        "queries": [
            {"query": "data analyst", "tier": 1},
            {"query": "ml engineer", "tier": 2},
            {"query": "recruiter", "tier": 3},
            {"query": "untiered"},
        ],
        "location": {"accept_patterns": ["singapore"], "reject_patterns": ["canada"]},
        "exclude_titles": ["Senior Staff"],
        "proxy": "proxy.test:8080",
        "workday_location_filter": False,
    })

    result = workday.run_workday_discovery(employers={"acme": EMPLOYER}, workers=3)

    assert [c["search_text"] for c in calls] == ["data analyst", "ml engineer"]
    assert calls[0]["employers"] == {"acme": EMPLOYER}
    assert calls[0]["location_filter"] is False
    assert calls[0]["accept_locs"] == ["singapore"] and calls[0]["reject_locs"] == ["canada"]
    assert calls[0]["excluded_titles"] == ["senior staff"]
    assert calls[0]["workers"] == 3
    assert proxies == ["proxy.test:8080"]
    assert result == {"found": 4, "new": 2, "existing": 2, "queries": 2}


def test_run_workday_discovery_honours_custom_max_tier(monkeypatch) -> None:
    calls = _patch_discovery(monkeypatch, {
        "workday_max_tier": 1,
        "queries": [{"query": "a", "tier": 1}, {"query": "b", "tier": 2}],
    })

    workday.run_workday_discovery(employers={"acme": EMPLOYER})

    assert [c["search_text"] for c in calls] == ["a"]


def test_run_workday_discovery_falls_back_to_all_queries_when_no_tier_matches(monkeypatch) -> None:
    calls = _patch_discovery(monkeypatch, {"queries": [{"query": "a", "tier": 5}, {"query": "b", "tier": 6}]})

    result = workday.run_workday_discovery(employers={"acme": EMPLOYER})

    assert [c["search_text"] for c in calls] == ["a", "b"]
    assert result["queries"] == 2


def test_run_workday_discovery_loads_registry_when_not_given(monkeypatch) -> None:
    monkeypatch.setattr(workday, "load_employers", lambda: {"acme": EMPLOYER})
    calls = _patch_discovery(monkeypatch, {"queries": [{"query": "a", "tier": 1}]})

    workday.run_workday_discovery()

    assert calls[0]["employers"] == {"acme": EMPLOYER}
    assert calls[0]["location_filter"] is True


def test_run_workday_discovery_treats_empty_searches_yaml_as_no_queries(monkeypatch, tmp_path) -> None:
    # An empty searches.yaml makes yaml.safe_load return None.
    searches = tmp_path / "searches.yaml"
    searches.write_text("", encoding="utf-8")
    monkeypatch.setattr(workday.config, "SEARCH_CONFIG_PATH", searches)
    monkeypatch.setattr(workday, "scrape_employers", lambda **_k: pytest.fail("no scrape expected"))

    result = workday.run_workday_discovery(employers={"acme": EMPLOYER})

    assert result == {"found": 0, "new": 0, "existing": 0, "queries": 0}


def test_location_filter_helpers_load_config_when_not_given(monkeypatch) -> None:
    monkeypatch.setattr(workday.config, "load_search_config", lambda: {
        "location": {"accept_patterns": ["Singapore"], "reject_patterns": ["Canada"]},
    })

    accept, reject = workday._load_location_filter()

    assert (accept, reject) == (["Singapore"], ["Canada"])
    assert workday._location_ok("Singapore", accept, reject)
    assert not workday._location_ok("Toronto, Canada", accept, reject)
