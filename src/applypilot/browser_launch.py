"""Headless Chromium for page reading and PDF rendering."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_MISSING_EXECUTABLE = "Executable doesn't exist"


def launch_chromium(playwright, **options):
    """Launch Playwright's bundled Chromium, or the installed Edge/Chrome if it is missing.

    The bundled browser must be downloaded separately for each Playwright release
    (``playwright install``); without it every enrichment, extraction and PDF run
    failed at launch even though the system browser used for applications was
    present. Any other launch error is raised unchanged.
    """
    from playwright.sync_api import Error as PlaywrightError

    try:
        return playwright.chromium.launch(**options)
    except PlaywrightError as exc:
        if _MISSING_EXECUTABLE not in str(exc) or "executable_path" in options:
            raise
        from applypilot import config

        try:
            executable = config.get_chrome_path()
        except FileNotFoundError:
            raise exc from None
        log.warning(
            "Playwright's bundled Chromium is not installed; using %s instead "
            "(run `python -m playwright install chromium` to restore it).",
            executable,
        )
        return playwright.chromium.launch(executable_path=executable, **options)
