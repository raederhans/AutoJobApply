"""Bounded retry policy for transient detail-page fetch failures.

A transient failure (rate limit, 5xx, navigation timeout) leaves ``detail_scraped_at``
NULL and records ``detail_attempts`` plus ``detail_retry_after``; only the last allowed
attempt is marked as a permanent failure. Timestamps are stored in one fixed UTC format
so SQLite can compare them against ``strftime('now')`` without bound parameters, which
lets every pending-work query share the same predicate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

MAX_DETAIL_ATTEMPTS = 3
# Wait applied after the 1st and 2nd failed attempt; the 3rd failure is final.
DETAIL_RETRY_BACKOFFS = (timedelta(hours=1), timedelta(hours=6))

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S+00:00"

_NOW_SQL = f"strftime('{_TIMESTAMP_FORMAT}', 'now')"
RATE_LIMIT_ERROR = "HTTP 429"

# Rows still waiting for their first or next attempt, excluding those in backoff.
DETAIL_DUE_SQL = (
    "detail_scraped_at IS NULL AND "
    f"(detail_retry_after IS NULL OR detail_retry_after <= {_NOW_SQL})"
)

# Sites cooling down after a rate limit: a 429 row whose cooldown has not passed, whether
# it waits for another attempt or exhausted its attempts (the final 429 keeps the cooldown
# time). The subquery is uncorrelated, so SQLite evaluates it once per statement instead of
# once per candidate row (no index covers these columns).
_COOLING_SITES_SQL = (
    "SELECT site FROM jobs WHERE site IS NOT NULL "
    f"AND detail_error = '{RATE_LIMIT_ERROR}' AND detail_retry_after > {_NOW_SQL}"
)

# Due rows whose site is not cooling down. A 429 row waiting for its retry keeps the whole
# site out, including jobs discovered after it, so a streaming run does not spend attempts
# on a throttled site.
DETAIL_PENDING_SQL = (
    f"{DETAIL_DUE_SQL} AND (site IS NULL OR site NOT IN ({_COOLING_SITES_SQL}))"
)


def retry_after(attempts: int, now: datetime | None = None) -> str | None:
    """Return when the next attempt is allowed, or None once attempts are exhausted."""
    if attempts >= MAX_DETAIL_ATTEMPTS:
        return None
    base = now or datetime.now(UTC)
    return (base + DETAIL_RETRY_BACKOFFS[attempts - 1]).strftime(_TIMESTAMP_FORMAT)
