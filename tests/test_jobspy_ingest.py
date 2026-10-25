"""Offline tests for the JobSpy ingestion path (no processes spawned, no network)."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from applypilot.discovery import jobspy


@pytest.fixture
def conn(tmp_path: Path):
    connection = jobspy.init_db(tmp_path / "jobs.db")
    yield connection
    connection.close()


def _row(**overrides) -> dict:
    row = {
        "job_url": "https://example.test/job/1",
        "title": "Data Analyst",
        "company": "Example",
        "location": "Singapore",
        "description": "short",
        "site": "linkedin",
    }
    row.update(overrides)
    return row


def _stored(conn, url: str):
    return conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()


# -- parse_proxy -------------------------------------------------------------

def test_parse_proxy_accepts_four_and_two_part_formats() -> None:
    full = jobspy.parse_proxy("proxy.test:8080:alice:s3cret")
    assert full["jobspy"] == "alice:s3cret@proxy.test:8080"
    assert full["playwright"] == {
        "server": "http://proxy.test:8080",
        "username": "alice",
        "password": "s3cret",
    }

    bare = jobspy.parse_proxy("proxy.test:8080")
    assert bare["jobspy"] == "proxy.test:8080"
    assert bare["user"] is None
    assert bare["playwright"] == {"server": "http://proxy.test:8080"}


@pytest.mark.parametrize("value", ["proxy.test", "a:b:c", "a:b:c:d:e"])
def test_parse_proxy_rejects_unrecognised_formats(value: str) -> None:
    with pytest.raises(ValueError, match="Proxy format not recognized"):
        jobspy.parse_proxy(value)


# -- _scrape_with_retry ------------------------------------------------------

def test_scrape_with_retry_retries_transient_errors_with_linear_backoff(monkeypatch) -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    def flaky(_kwargs, _timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("HTTP 429 too many requests")
        return "ok"

    monkeypatch.setattr(jobspy, "_scrape_once_with_timeout", flaky)
    monkeypatch.setattr(jobspy.time, "sleep", sleeps.append)

    assert jobspy._scrape_with_retry({}, max_retries=2, backoff=5.0) == "ok"
    assert len(attempts) == 3
    assert sleeps == [5.0, 10.0]


def test_scrape_with_retry_does_not_retry_permanent_errors(monkeypatch) -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    def broken(_kwargs, _timeout):
        attempts.append(1)
        raise ValueError("bad search term")

    monkeypatch.setattr(jobspy, "_scrape_once_with_timeout", broken)
    monkeypatch.setattr(jobspy.time, "sleep", sleeps.append)

    with pytest.raises(ValueError, match="bad search term"):
        jobspy._scrape_with_retry({}, max_retries=2)
    assert len(attempts) == 1
    assert sleeps == []


def test_scrape_with_retry_raises_after_transient_retries_are_exhausted(monkeypatch) -> None:
    attempts: list[int] = []

    def always_timeout(_kwargs, _timeout):
        attempts.append(1)
        raise TimeoutError("JobSpy query exceeded 150s wall-clock timeout")

    monkeypatch.setattr(jobspy, "_scrape_once_with_timeout", always_timeout)
    monkeypatch.setattr(jobspy.time, "sleep", lambda _seconds: None)

    with pytest.raises(TimeoutError):
        jobspy._scrape_with_retry({}, max_retries=2)
    assert len(attempts) == 3


# -- _scrape_once_with_timeout (child process replaced by a fake) ------------

class _FakeProcess:
    """Stands in for a spawn Process; optionally runs the target in-process."""

    def __init__(self, target, args, *, run_target=False, alive_after_join=0, exitcode=0):
        self._target = target
        self._args = args
        self._run_target = run_target
        self._alive_polls = alive_after_join
        self.exitcode = exitcode
        self.started = False
        self.terminated = False
        self.killed = False
        self.joins: list[float | None] = []

    def start(self):
        self.started = True
        if self._run_target:
            self._target(*self._args)

    def join(self, timeout=None):
        self.joins.append(timeout)

    def is_alive(self):
        if self.killed:
            return False
        if self.terminated:
            # Still alive until killed when the test asks for a stubborn child.
            return self._alive_polls > 1
        return self._alive_polls > 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def _install_fake_context(monkeypatch, **process_options) -> list[_FakeProcess]:
    created: list[_FakeProcess] = []

    class FakeContext:
        def Process(self, target, args, daemon):
            assert daemon is True
            process = _FakeProcess(target, args, **process_options)
            created.append(process)
            return process

    def fake_get_context(method):
        assert method == "spawn"
        return FakeContext()

    monkeypatch.setattr(jobspy.multiprocessing, "get_context", fake_get_context)
    return created


class _FakeJobboards:
    def __init__(self, scrape_jobs):
        self.scrape_jobs = scrape_jobs


def test_scrape_once_with_timeout_returns_child_dataframe(monkeypatch) -> None:
    seen = {}

    def scrape_jobs(**kwargs):
        seen.update(kwargs)
        return pd.DataFrame([_row()])

    monkeypatch.setattr(jobspy, "require_jobboards", lambda: _FakeJobboards(scrape_jobs))
    created = _install_fake_context(monkeypatch, run_target=True)

    df = jobspy._scrape_once_with_timeout({"search_term": "analyst"}, 12)

    assert seen == {"search_term": "analyst"}
    assert list(df["title"]) == ["Data Analyst"]
    assert created[0].started
    assert created[0].joins == [12]


def test_scrape_once_with_timeout_reports_child_exception(monkeypatch) -> None:
    def scrape_jobs(**_kwargs):
        raise ConnectionError("connection reset by peer")

    monkeypatch.setattr(jobspy, "require_jobboards", lambda: _FakeJobboards(scrape_jobs))
    _install_fake_context(monkeypatch, run_target=True)

    with pytest.raises(RuntimeError, match="ConnectionError: connection reset by peer"):
        jobspy._scrape_once_with_timeout({}, 5)


def test_scrape_once_with_timeout_reports_child_exit_without_result(monkeypatch) -> None:
    _install_fake_context(monkeypatch, run_target=False, exitcode=-9)

    with pytest.raises(RuntimeError, match="exited with code -9 without a result"):
        jobspy._scrape_once_with_timeout({}, 5)


def test_scrape_once_with_timeout_terminates_then_kills_a_stuck_child(monkeypatch) -> None:
    created = _install_fake_context(monkeypatch, alive_after_join=5)

    with pytest.raises(TimeoutError, match="exceeded 3s"):
        jobspy._scrape_once_with_timeout({}, 3)

    process = created[0]
    assert process.terminated and process.killed
    assert process.joins == [3, 5, 5]


def test_scrape_once_with_timeout_does_not_kill_a_child_that_obeys_terminate(monkeypatch) -> None:
    created = _install_fake_context(monkeypatch, alive_after_join=1)

    with pytest.raises(TimeoutError):
        jobspy._scrape_once_with_timeout({}, 3)

    process = created[0]
    assert process.terminated and not process.killed
    assert process.joins == [3, 5]


# -- _clean_jobspy_value / row normalisation ---------------------------------

def test_clean_jobspy_value_treats_missing_markers_as_absent_and_keeps_unhashable_values() -> None:
    assert jobspy._clean_jobspy_value(None) is None
    assert jobspy._clean_jobspy_value(float("nan")) is None
    assert jobspy._clean_jobspy_value(pd.NA) is None
    assert jobspy._clean_jobspy_value("value") == "value"
    # pd.isna returns an array for list input; the ambiguous truth value must not leak out.
    assert jobspy._clean_jobspy_value([1, 2]) == [1, 2]


def test_location_helpers_apply_config_filters() -> None:
    accept, reject = jobspy._load_location_config(
        {"location": {"accept_patterns": ["Singapore"], "reject_patterns": ["Canada"]}}
    )
    assert (accept, reject) == (["Singapore"], ["Canada"])
    assert jobspy._location_ok("Singapore", accept, reject)
    assert not jobspy._location_ok("Toronto, Canada", accept, reject)
    assert not jobspy._location_ok("Paris", accept, reject)
    assert jobspy._location_ok(None, accept, reject)


# -- store_jobspy_results ----------------------------------------------------

def test_store_jobspy_results_builds_salary_from_amounts_interval_and_currency(conn) -> None:
    frame = pd.DataFrame([
        _row(job_url="https://example.test/range", min_amount=80000.0, max_amount=120000.0,
             interval="yearly", currency="$"),
        _row(job_url="https://example.test/min-only", min_amount=50000.0, max_amount=float("nan"),
             interval="monthly", currency="S$"),
        _row(job_url="https://example.test/no-interval", min_amount=30.0, max_amount=40.0,
             interval=float("nan"), currency=float("nan")),
        _row(job_url="https://example.test/no-pay", min_amount=float("nan"), max_amount=float("nan"),
             interval="yearly", currency="$"),
    ])

    assert jobspy.store_jobspy_results(conn, frame, "linkedin") == (4, 0)

    assert _stored(conn, "https://example.test/range")["salary"] == "$80,000-$120,000/yearly"
    assert _stored(conn, "https://example.test/min-only")["salary"] == "S$50,000/monthly"
    assert _stored(conn, "https://example.test/no-interval")["salary"] == "30-40"
    assert _stored(conn, "https://example.test/no-pay")["salary"] is None


def test_store_jobspy_results_promotes_long_descriptions_only(conn) -> None:
    long_text = "Build forecasting pipelines. " * 12
    frame = pd.DataFrame([
        _row(job_url="https://example.test/long", description=long_text),
        _row(job_url="https://example.test/short", description="Short blurb"),
        _row(job_url="https://example.test/missing", description=float("nan")),
    ])

    jobspy.store_jobspy_results(conn, frame, "linkedin")

    long_row = _stored(conn, "https://example.test/long")
    assert long_row["full_description"] == long_text
    assert long_row["detail_scraped_at"]
    short_row = _stored(conn, "https://example.test/short")
    assert short_row["description"] == "Short blurb"
    assert short_row["full_description"] is None
    assert short_row["detail_scraped_at"] is None
    missing_row = _stored(conn, "https://example.test/missing")
    assert missing_row["description"] is None
    assert missing_row["detail_scraped_at"] is None


def test_store_jobspy_results_skips_missing_urls_and_excluded_titles(conn, caplog) -> None:
    frame = pd.DataFrame([
        _row(job_url=float("nan"), title="No URL"),
        _row(job_url="https://example.test/keep", title="Data Analyst"),
        _row(job_url="https://example.test/drop", title="Senior Staff  Engineer"),
    ])

    with caplog.at_level(logging.INFO, logger=jobspy.log.name):
        result = jobspy.store_jobspy_results(conn, frame, "linkedin", excluded_titles=["senior staff"])

    assert result == (1, 0)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert _stored(conn, "https://example.test/drop") is None
    assert "Filtered 1 jobs by excluded title" in caplog.text


def test_store_jobspy_results_groups_by_site_and_reports_duplicates(conn) -> None:
    frame = pd.DataFrame([
        _row(job_url="https://example.test/a", site="linkedin"),
        _row(job_url="https://example.test/b", site="indeed"),
        _row(job_url="https://example.test/c", site=float("nan")),
    ])

    assert jobspy.store_jobspy_results(conn, frame, "fallback-query") == (3, 0)
    assert _stored(conn, "https://example.test/a")["source_site"] == "linkedin"
    assert _stored(conn, "https://example.test/b")["source_site"] == "indeed"
    # A NaN site stringifies to "nan"; the row is still stored under that label.
    assert _stored(conn, "https://example.test/c")["source_site"] == "nan"

    assert jobspy.store_jobspy_results(conn, frame, "fallback-query") == (0, 3)


def test_store_jobspy_results_uses_source_label_when_site_column_is_absent(conn) -> None:
    row = _row(job_url="https://example.test/no-site-column")
    row.pop("site")

    jobspy.store_jobspy_results(conn, pd.DataFrame([row]), "data analyst")

    assert _stored(conn, "https://example.test/no-site-column")["source_site"] == "data analyst"


def test_store_jobspy_results_keeps_direct_apply_url_only_when_present(conn) -> None:
    frame = pd.DataFrame([
        _row(job_url="https://example.test/direct", job_url_direct="https://apply.example.test/1"),
        _row(job_url="https://example.test/none", job_url_direct=float("nan")),
    ])

    jobspy.store_jobspy_results(conn, frame, "linkedin")

    assert _stored(conn, "https://example.test/direct")["application_url"] == "https://apply.example.test/1"
    assert _stored(conn, "https://example.test/none")["application_url"] is None


class _Records:
    """Minimal DataFrame stand-in so raw numpy scalars reach the store code."""

    def __init__(self, records):
        self._records = records

    def to_dict(self, orient):
        assert orient == "records"
        return self._records


def test_store_jobspy_results_marks_true_remote_flags(conn) -> None:
    frame = pd.DataFrame([
        _row(job_url="https://example.test/remote", location="Singapore", is_remote=True),
        _row(job_url="https://example.test/remote-no-loc", location=float("nan"), is_remote=True),
        _row(job_url="https://example.test/onsite", location="Singapore", is_remote=False),
    ])

    jobspy.store_jobspy_results(conn, frame, "linkedin")

    assert _stored(conn, "https://example.test/remote")["location"] == "Singapore (Remote)"
    assert _stored(conn, "https://example.test/remote-no-loc")["location"] == "Remote"
    assert _stored(conn, "https://example.test/onsite")["location"] == "Singapore"


def test_store_jobspy_results_nan_or_missing_is_remote_is_not_remote(conn) -> None:
    # A mixed column keeps float('nan') for rows where JobSpy had no flag; NaN is truthy in Python.
    frame = pd.DataFrame([
        _row(job_url="https://example.test/nan", location="Singapore", is_remote=float("nan")),
        _row(job_url="https://example.test/true", location="Singapore", is_remote=True),
        _row(job_url="https://example.test/none", location="Singapore", is_remote=None),
    ])
    missing_column = pd.DataFrame([_row(job_url="https://example.test/no-column", location="Singapore")])

    jobspy.store_jobspy_results(conn, frame, "linkedin")
    jobspy.store_jobspy_results(conn, missing_column, "linkedin")

    assert _stored(conn, "https://example.test/nan")["location"] == "Singapore"
    assert _stored(conn, "https://example.test/none")["location"] == "Singapore"
    assert _stored(conn, "https://example.test/no-column")["location"] == "Singapore"
    assert _stored(conn, "https://example.test/true")["location"] == "Singapore (Remote)"


def test_store_jobspy_results_honours_numpy_bool_remote_flags(conn) -> None:
    records = [
        _row(job_url="https://example.test/np-true", location="Singapore", is_remote=np.True_),
        _row(job_url="https://example.test/np-false", location="Singapore", is_remote=np.False_),
        _row(job_url="https://example.test/pd-na", location="Singapore", is_remote=pd.NA),
    ]

    assert jobspy.store_jobspy_results(conn, _Records(records), "linkedin") == (3, 0)

    assert _stored(conn, "https://example.test/np-true")["location"] == "Singapore (Remote)"
    assert _stored(conn, "https://example.test/np-false")["location"] == "Singapore"
    assert _stored(conn, "https://example.test/pd-na")["location"] == "Singapore"


# -- search_jobs -------------------------------------------------------------

def test_search_jobs_builds_kwargs_stores_rows_and_reports_counts(monkeypatch, conn) -> None:
    captured = {}

    def fake_retry(kwargs, max_retries, timeout_seconds):
        captured.update(kwargs=kwargs, max_retries=max_retries, timeout_seconds=timeout_seconds)
        return pd.DataFrame([_row(), _row(job_url="https://example.test/job/2", site="indeed")])

    monkeypatch.setattr(jobspy, "_scrape_with_retry", fake_retry)
    monkeypatch.setattr(jobspy, "init_db", lambda: conn)

    result = jobspy.search_jobs(
        "data analyst", "Singapore",
        sites=["linkedin", "indeed"], remote_only=True, results_per_site=7, hours_old=24,
        proxy="proxy.test:8080:alice:s3cret", country_indeed="Singapore",
    )

    assert result == {"total": 2, "new": 2, "existing": 0}
    kwargs = captured["kwargs"]
    assert kwargs["site_name"] == ["linkedin", "indeed"]
    assert kwargs["results_wanted"] == 7
    assert kwargs["hours_old"] == 24
    assert kwargs["country_indeed"] == "Singapore"
    assert kwargs["is_remote"] is True
    assert kwargs["proxies"] == ["alice:s3cret@proxy.test:8080"]
    assert kwargs["linkedin_fetch_description"] is True
    assert captured["max_retries"] == 2
    assert captured["timeout_seconds"] == 150
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_search_jobs_omits_optional_kwargs_by_default(monkeypatch, conn) -> None:
    captured = {}

    def fake_retry(kwargs, **_options):
        captured.update(kwargs)
        return pd.DataFrame([_row()])

    monkeypatch.setattr(jobspy, "_scrape_with_retry", fake_retry)
    monkeypatch.setattr(jobspy, "init_db", lambda: conn)

    jobspy.search_jobs("data analyst", "Singapore", sites=["indeed"])

    assert "is_remote" not in captured
    assert "proxies" not in captured
    assert "linkedin_fetch_description" not in captured
    assert captured["country_indeed"] == "usa"


def test_search_jobs_returns_error_when_retries_are_exhausted(monkeypatch) -> None:
    def fail(_kwargs, **_options):
        raise TimeoutError("JobSpy query exceeded 150s wall-clock timeout")

    monkeypatch.setattr(jobspy, "_scrape_with_retry", fail)

    result = jobspy.search_jobs("data analyst", "Singapore")

    assert result["total"] == 0 and result["new"] == 0 and result["existing"] == 0
    assert "wall-clock timeout" in result["error"]


def test_search_jobs_returns_early_on_empty_result_without_touching_the_database(monkeypatch) -> None:
    monkeypatch.setattr(jobspy, "_scrape_with_retry", lambda _kwargs, **_options: pd.DataFrame())

    def forbidden():
        raise AssertionError("init_db must not run for an empty result")

    monkeypatch.setattr(jobspy, "init_db", forbidden)

    assert jobspy.search_jobs("data analyst", "Singapore") == {"total": 0, "new": 0, "existing": 0}


def test_search_jobs_rejects_bad_proxy_before_scraping(monkeypatch) -> None:
    monkeypatch.setattr(
        jobspy, "_scrape_with_retry",
        lambda *_a, **_k: pytest.fail("scrape must not start with an invalid proxy"),
    )

    with pytest.raises(ValueError, match="Proxy format not recognized"):
        jobspy.search_jobs("data analyst", "Singapore", proxy="bad-proxy")


# -- _run_one_search ---------------------------------------------------------

def _search(**overrides) -> dict:
    search = {"query": "data analyst", "location": "Singapore", "remote": False, "tier": 1}
    search.update(overrides)
    return search


def _run_one(search, sites, *, proxy_config=None, defaults=None, accept=(), reject=(),
             glassdoor_map=None, excluded=()):
    return jobspy._run_one_search(
        search, list(sites), 25, 48, proxy_config, defaults or {}, 1,
        list(accept), list(reject), glassdoor_map or {}, list(excluded),
    )


def test_run_one_search_builds_per_site_kwargs(monkeypatch, conn) -> None:
    calls: list[tuple[dict, dict]] = []

    def fake_retry(kwargs, **options):
        calls.append((kwargs, options))
        return pd.DataFrame()

    monkeypatch.setattr(jobspy, "_scrape_with_retry", fake_retry)
    monkeypatch.setattr(jobspy, "get_connection", lambda: conn)
    proxy = jobspy.parse_proxy("proxy.test:8080")

    result = _run_one(
        _search(location="Singapore, Singapore", remote=True),
        ["indeed", "linkedin", "glassdoor"],
        proxy_config=proxy,
        defaults={"country_indeed": "Singapore", "query_timeout_seconds": 11},
        glassdoor_map={"Singapore, Singapore": "Singapore"},
    )

    by_site = {kwargs["site_name"][0]: kwargs for kwargs, _ in calls}
    assert by_site["indeed"]["country_indeed"] == "Singapore"
    assert "linkedin_fetch_description" not in by_site["indeed"]
    assert by_site["linkedin"]["linkedin_fetch_description"] is True
    assert "country_indeed" not in by_site["linkedin"]
    assert by_site["glassdoor"]["location"] == "Singapore"
    assert by_site["indeed"]["location"] == "Singapore, Singapore"
    for kwargs, options in calls:
        assert kwargs["is_remote"] is True
        assert kwargs["proxies"] == ["proxy.test:8080"]
        assert kwargs["results_wanted"] == 25
        assert kwargs["hours_old"] == 48
        assert options == {"max_retries": 1, "timeout_seconds": 11.0}
    assert result["total"] == 0 and result["errors"] == 0
    assert result["label"].endswith("(remote) [tier 1]")
    assert {site: item["status"] for site, item in result["sites"].items()} == {
        "indeed": "empty", "linkedin": "empty", "glassdoor": "empty",
    }


def test_run_one_search_glassdoor_falls_back_to_first_location_segment(monkeypatch, conn) -> None:
    seen = {}

    def fake_retry(kwargs, **_options):
        seen[kwargs["site_name"][0]] = kwargs["location"]
        return pd.DataFrame()

    monkeypatch.setattr(jobspy, "_scrape_with_retry", fake_retry)
    monkeypatch.setattr(jobspy, "get_connection", lambda: conn)

    _run_one(_search(location="Singapore, Singapore"), ["glassdoor", "indeed"])

    assert seen == {"glassdoor": "Singapore", "indeed": "Singapore, Singapore"}


def test_run_one_search_reports_all_sites_failed_without_storing(monkeypatch, conn) -> None:
    def fail(_kwargs, **_options):
        raise RuntimeError("blocked")

    monkeypatch.setattr(jobspy, "_scrape_with_retry", fail)
    monkeypatch.setattr(jobspy, "get_connection", lambda: pytest.fail("no DB access expected"))

    result = _run_one(_search(), ["indeed", "linkedin"])

    assert result["new"] == 0 and result["total"] == 0
    assert result["errors"] == 2
    assert all(item["status"] == "error" and "RuntimeError: blocked" in item["error"]
               for item in result["sites"].values())


def test_run_one_search_keeps_results_of_working_sites_when_one_site_fails(monkeypatch, conn) -> None:
    def fake_retry(kwargs, **_options):
        if kwargs["site_name"] == ["indeed"]:
            raise RuntimeError("indeed blocked")
        return pd.DataFrame([_row(job_url="https://example.test/linkedin-1")])

    monkeypatch.setattr(jobspy, "_scrape_with_retry", fake_retry)
    monkeypatch.setattr(jobspy, "get_connection", lambda: conn)

    result = _run_one(_search(), ["indeed", "linkedin"])

    assert (result["new"], result["existing"], result["errors"], result["total"]) == (1, 0, 1, 1)
    assert result["sites"]["indeed"]["status"] == "error"
    assert result["sites"]["linkedin"]["status"] == "partial"
    assert _stored(conn, "https://example.test/linkedin-1") is not None


def test_run_one_search_all_sites_empty_returns_without_storing(monkeypatch) -> None:
    monkeypatch.setattr(jobspy, "_scrape_with_retry", lambda _kwargs, **_options: pd.DataFrame())
    monkeypatch.setattr(jobspy, "get_connection", lambda: pytest.fail("no DB access expected"))

    result = _run_one(_search(), ["indeed", "linkedin"])

    assert (result["new"], result["existing"], result["errors"], result["total"]) == (0, 0, 0, 0)


def test_run_one_search_filters_locations_and_excluded_titles_before_storing(monkeypatch, conn) -> None:
    frame = pd.DataFrame([
        _row(job_url="https://example.test/sg", location="Singapore"),
        _row(job_url="https://example.test/ca", location="Toronto, Canada"),
        _row(job_url="https://example.test/unknown", location=float("nan")),
        _row(job_url="https://example.test/excluded", location="Singapore", title="Senior Staff Engineer"),
    ])
    monkeypatch.setattr(jobspy, "_scrape_with_retry", lambda _kwargs, **_options: frame)
    monkeypatch.setattr(jobspy, "get_connection", lambda: conn)

    result = _run_one(
        _search(), ["linkedin"],
        accept=["singapore"], reject=["canada"], excluded=["senior staff"],
    )

    assert result["total"] == 4
    assert result["filtered"] == 1
    assert result["new"] == 2  # sg + unknown location; the excluded title is dropped at store time
    stored = {row["url"] for row in conn.execute("SELECT url FROM jobs")}
    assert stored == {"https://example.test/sg", "https://example.test/unknown"}


def test_run_one_search_concatenates_multiple_site_frames(monkeypatch, conn) -> None:
    def fake_retry(kwargs, **_options):
        site = kwargs["site_name"][0]
        return pd.DataFrame([_row(job_url=f"https://example.test/{site}", site=site)])

    monkeypatch.setattr(jobspy, "_scrape_with_retry", fake_retry)
    monkeypatch.setattr(jobspy, "get_connection", lambda: conn)

    result = _run_one(_search(), ["indeed", "linkedin"])

    assert (result["new"], result["total"]) == (2, 2)


# -- _full_crawl -------------------------------------------------------------

def test_full_crawl_expands_matrix_filters_tiers_and_locations_and_aggregates(monkeypatch, conn) -> None:
    searches: list[dict] = []
    extra: list[tuple] = []

    def fake_run_one(search, sites, results_per_site, hours_old, proxy_config, defaults, max_retries,
                     accept_locs, reject_locs, glassdoor_map, excluded_titles):
        searches.append(search)
        extra.append((sites, results_per_site, hours_old, proxy_config, defaults, max_retries,
                      accept_locs, reject_locs, glassdoor_map, excluded_titles))
        return {"new": 2, "existing": 1, "errors": 1}

    monkeypatch.setattr(jobspy, "_run_one_search", fake_run_one)
    monkeypatch.setattr(jobspy, "init_db", lambda: conn)
    monkeypatch.setattr(jobspy, "get_connection", lambda: conn)
    jobspy.store_jobs(conn, [{"url": "https://example.test/pre-existing", "title": "T"}], "x", "y")

    cfg = {
        "queries": [
            {"query": "data analyst", "tier": 1},
            {"query": "ml engineer", "tier": 2},
            {"query": "untiered"},
        ],
        "locations": [
            {"location": "Singapore", "label": "sg"},
            {"location": "Remote", "label": "remote", "remote": True},
        ],
        "defaults": {"max_retries": "4", "country_indeed": "Singapore"},
        "glassdoor_location_map": {"Singapore": "Singapore"},
        "location": {"accept_patterns": ["singapore"], "reject_patterns": ["canada"]},
        "exclude_titles": ["Senior Staff"],
    }

    result = jobspy._full_crawl(
        cfg, tiers=[1, 2], locations=["sg", "remote"], sites=["indeed"],
        results_per_site=10, hours_old=24, proxy="proxy.test:8080", max_retries=2,
    )

    assert [(s["query"], s["location"], s["remote"], s["tier"]) for s in searches] == [
        ("data analyst", "Singapore", False, 1),
        ("data analyst", "Remote", True, 1),
        ("ml engineer", "Singapore", False, 2),
        ("ml engineer", "Remote", True, 2),
    ]
    sites, per_site, hours, proxy_config, defaults, retries, accept, reject, gd_map, excluded = extra[0]
    assert sites == ["indeed"] and per_site == 10 and hours == 24
    assert proxy_config["jobspy"] == "proxy.test:8080"
    assert defaults["country_indeed"] == "Singapore"
    assert retries == 4  # defaults.max_retries wins over the argument
    assert (accept, reject, gd_map, excluded) == (
        ["singapore"], ["canada"], {"Singapore": "Singapore"}, ["senior staff"],
    )
    assert result == {"new": 8, "existing": 4, "errors": 4, "db_total": 1, "queries": 4}


def test_full_crawl_location_label_filter_and_default_sites(monkeypatch, conn) -> None:
    seen: list[tuple[str, list[str]]] = []

    def fake_run_one(search, sites, *_rest):
        seen.append((search["location"], sites))
        return {"new": 0, "existing": 0, "errors": 0}

    monkeypatch.setattr(jobspy, "_run_one_search", fake_run_one)
    monkeypatch.setattr(jobspy, "init_db", lambda: conn)
    monkeypatch.setattr(jobspy, "get_connection", lambda: conn)

    result = jobspy._full_crawl(
        {
            "queries": [{"query": "data analyst", "tier": 1}],
            "locations": [{"location": "Singapore", "label": "sg"}, {"location": "Paris", "label": "fr"}],
        },
        locations=["fr"],
    )

    assert seen == [("Paris", ["indeed", "linkedin", "zip_recruiter"])]
    assert result["queries"] == 1 and result["db_total"] == 0


def test_full_crawl_with_no_matching_searches_runs_nothing(monkeypatch, conn) -> None:
    monkeypatch.setattr(jobspy, "_run_one_search", lambda *_a, **_k: pytest.fail("no search expected"))
    monkeypatch.setattr(jobspy, "init_db", lambda: conn)
    monkeypatch.setattr(jobspy, "get_connection", lambda: conn)

    result = jobspy._full_crawl({"queries": [{"query": "x", "tier": 3}], "locations": [{"location": "SG"}]}, tiers=[1])

    assert result == {"new": 0, "existing": 0, "errors": 0, "db_total": 0, "queries": 0}


# -- run_discovery -----------------------------------------------------------

def test_run_discovery_with_empty_config_returns_zero_stats_without_crawling(monkeypatch) -> None:
    monkeypatch.setattr(jobspy, "require_jobboards", lambda: pytest.fail("dependency check must not run"))
    monkeypatch.setattr(jobspy, "_full_crawl", lambda **_k: pytest.fail("crawl must not run"))
    expected = {"new": 0, "existing": 0, "errors": 0, "db_total": 0, "queries": 0}

    assert jobspy.run_discovery({}) == expected

    monkeypatch.setattr(jobspy.config, "load_search_config", dict)
    assert jobspy.run_discovery() == expected


def test_run_discovery_prefers_boards_over_legacy_sites_and_passes_filters(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(jobspy, "require_jobboards", lambda: object())
    monkeypatch.setattr(jobspy, "_full_crawl", lambda **kwargs: captured.update(kwargs) or {"new": 0})

    jobspy.run_discovery({
        "queries": [{"query": "q"}],
        "boards": ["linkedin"],
        "sites": ["indeed"],
        "country": "Singapore",
        "defaults": {"country_indeed": "usa"},
        "tiers": [1],
        "location_labels": ["sg"],
        "proxy": "proxy.test:8080",
    })

    assert captured["sites"] == ["linkedin"]
    assert captured["tiers"] == [1]
    assert captured["locations"] == ["sg"]
    assert captured["proxy"] == "proxy.test:8080"
    # An explicit country_indeed default is not overwritten by the top-level country.
    assert captured["search_cfg"]["defaults"]["country_indeed"] == "usa"
    assert captured["results_per_site"] == 100 and captured["hours_old"] == 72


def test_run_discovery_uses_top_level_country_for_indeed_when_defaults_omit_it(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(jobspy, "require_jobboards", lambda: object())
    monkeypatch.setattr(jobspy, "_full_crawl", lambda **kwargs: captured.update(kwargs) or {"new": 0})

    jobspy.run_discovery({"queries": [{"query": "q"}], "country": "Singapore"})

    assert captured["search_cfg"]["defaults"]["country_indeed"] == "Singapore"
    assert captured["sites"] is None


# -- search_job_board argument validation ------------------------------------

@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"site": "glassdoor"}, "site must be"),
        ({"site": "indeed", "results_per_site": 0}, "results_per_site"),
        ({"site": "indeed", "timeout_seconds": 0}, "timeout_seconds"),
        ({"site": "indeed", "job_type": "volunteer"}, "unsupported job_type"),
    ],
)
def test_search_job_board_rejects_invalid_arguments_before_scraping(monkeypatch, kwargs, message) -> None:
    monkeypatch.setattr(
        jobspy, "_scrape_once_with_timeout",
        lambda *_a, **_k: pytest.fail("scrape must not start for invalid arguments"),
    )

    with pytest.raises(ValueError, match=message):
        jobspy.search_job_board("analyst", **kwargs)


def test_run_discovery_warns_when_indeed_falls_back_to_the_usa_site(monkeypatch, caplog) -> None:
    monkeypatch.setattr(jobspy, "require_jobboards", lambda: object())
    monkeypatch.setattr(jobspy, "_full_crawl", lambda **_k: {"new": 0})

    with caplog.at_level(logging.WARNING, logger=jobspy.log.name):
        jobspy.run_discovery({"queries": [{"query": "q"}]})

    messages = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert any("searches.yaml" in message and "country" in message and "usa" in message for message in messages)


@pytest.mark.parametrize(
    "cfg",
    [
        {"queries": [{"query": "q"}], "country": "Singapore"},
        {"queries": [{"query": "q"}], "defaults": {"country_indeed": "singapore"}},
        {"queries": [{"query": "q"}], "boards": ["linkedin"]},
    ],
)
def test_run_discovery_does_not_warn_when_country_is_set_or_indeed_is_unused(monkeypatch, caplog, cfg) -> None:
    monkeypatch.setattr(jobspy, "require_jobboards", lambda: object())
    monkeypatch.setattr(jobspy, "_full_crawl", lambda **_k: {"new": 0})

    with caplog.at_level(logging.WARNING, logger=jobspy.log.name):
        jobspy.run_discovery(cfg)

    assert not [record for record in caplog.records if record.levelno == logging.WARNING]
