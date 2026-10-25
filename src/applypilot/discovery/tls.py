"""TLS trust for read-only fetches of employer and ATS pages."""

from __future__ import annotations

import ssl

import certifi


def public_web_tls_context() -> ssl.SSLContext:
    """Verify against both the Mozilla CA bundle and the operating-system store.

    Python sees only the roots already installed in the Windows store, while Windows
    itself downloads newer roots on demand. The Mozilla bundle covers those (observed
    on 2026-10-25: jobs.tuvsud.com chains to D-TRUST BR Root CA 2 2023, which the
    local store lacked although the browser trusted it). Certificate and hostname
    verification stay on; only the set of trust anchors grows.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_default_certs()
    return context
