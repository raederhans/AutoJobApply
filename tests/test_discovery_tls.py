"""Official-page fetches trust the Mozilla bundle as well as the system store."""

from __future__ import annotations

import ssl

import certifi

from applypilot.discovery import advance, official, workday
from applypilot.discovery.tls import public_web_tls_context


def test_context_verifies_and_includes_the_mozilla_bundle() -> None:
    context = public_web_tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True

    bundled = ssl.create_default_context(cafile=certifi.where())
    bundled_subjects = {cert["subject"] for cert in bundled.get_ca_certs()}
    loaded_subjects = {cert["subject"] for cert in context.get_ca_certs()}
    assert bundled_subjects <= loaded_subjects


def test_official_fetchers_use_the_shared_context(monkeypatch) -> None:
    seen = []

    def fake_urlopen(request, timeout=None, **kwargs):
        seen.append(kwargs.get("context"))
        raise OSError("stop before network")

    monkeypatch.setattr(official.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(workday.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(workday, "_opener", None)
    for call in (
        lambda: official._default_transport("https://boards.example.test/jobs"),
        lambda: workday._urlopen("https://example.wd1.myworkdayjobs.com/"),
    ):
        try:
            call()
        except OSError:
            pass
    assert len(seen) == 2 and all(isinstance(item, ssl.SSLContext) for item in seen)

    connection = advance._PinnedHTTPSConnection("jobs.example.test", "93.184.216.34", 443, 5)
    loaded = {cert["subject"] for cert in connection._context.get_ca_certs()}
    assert {cert["subject"] for cert in public_web_tls_context().get_ca_certs()} == loaded
