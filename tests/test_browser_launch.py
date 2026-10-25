"""Headless launches fall back to the installed browser only when Playwright's is missing."""

from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError

from applypilot import browser_launch, config


class _Chromium:
    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = []

    def launch(self, **options):
        self.calls.append(options)
        if self.failures:
            raise self.failures.pop(0)
        return "browser"


class _Playwright:
    def __init__(self, *failures):
        self.chromium = _Chromium(failures)


_MISSING = PlaywrightError("BrowserType.launch: Executable doesn't exist at C:/ms-playwright/chrome.exe")


def test_bundled_chromium_is_used_when_present() -> None:
    playwright = _Playwright()
    assert browser_launch.launch_chromium(playwright, headless=True) == "browser"
    assert playwright.chromium.calls == [{"headless": True}]


def test_missing_bundled_chromium_falls_back_to_the_system_browser(monkeypatch) -> None:
    monkeypatch.setattr(config, "get_chrome_path", lambda: "C:/Edge/msedge.exe")
    playwright = _Playwright(_MISSING)

    assert browser_launch.launch_chromium(playwright, headless=True, proxy={"server": "x"}) == "browser"
    assert playwright.chromium.calls[1] == {
        "executable_path": "C:/Edge/msedge.exe", "headless": True, "proxy": {"server": "x"},
    }


def test_other_launch_errors_and_missing_system_browser_are_raised(monkeypatch) -> None:
    other = PlaywrightError("Target page, context or browser has been closed")
    with pytest.raises(PlaywrightError, match="closed"):
        browser_launch.launch_chromium(_Playwright(other))

    def no_browser():
        raise FileNotFoundError("no browser")

    monkeypatch.setattr(config, "get_chrome_path", no_browser)
    with pytest.raises(PlaywrightError, match="Executable doesn't exist"):
        browser_launch.launch_chromium(_Playwright(_MISSING))


def test_explicit_executable_is_not_replaced(monkeypatch) -> None:
    monkeypatch.setattr(config, "get_chrome_path", lambda: pytest.fail("must not look up a browser"))
    with pytest.raises(PlaywrightError):
        browser_launch.launch_chromium(_Playwright(_MISSING), executable_path="C:/custom.exe")
