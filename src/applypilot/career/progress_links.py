"""Resolve public progress handles to exact existing application queue records."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from applypilot import progress_report


def _handle(identity):
    # This is the documented report handle, not a company/title similarity key.
    return "application-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _read(conn, table, fields):
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    return conn.execute(f'SELECT {",".join(fields)} FROM "{table}"').fetchall() if set(fields) <= columns else []


def _source(conn, record_id):
    if not isinstance(record_id, str) or not re.fullmatch(r"application-[0-9a-f]{16}", record_id):
        raise ValueError("progress_record_changed")
    matches = [row for row in progress_report.collect_report(conn)["rows"] if row["record_id"] == record_id]
    if len(matches) != 1:
        raise ValueError("progress_record_changed")
    by_url, urls = defaultdict(list), set()
    for identity, url in _read(conn, "followup_applications", ("application_id", "job_url")):
        if isinstance(url, str) and url:
            by_url[url].append(identity)
            if _handle(identity) == record_id:
                urls.add(url)
    for (url,) in _read(conn, "jobs", ("url",)):
        if not isinstance(url, str) or not url:
            continue
        identities = by_url.get(url, [])
        identity = identities[0] if len(identities) == 1 else url
        if _handle(identity) == record_id:
            urls.add(url)
    if len(urls) != 1:
        raise ValueError("progress_source_unavailable")
    return matches[0], next(iter(urls))


def related_inbox(conn, inbox, record_id, **filters):
    owned = not conn.in_transaction
    if owned:
        conn.execute("BEGIN")
    try:
        record, url = _source(conn, record_id)
        result = inbox.list_items(conn, job_url=url, **filters)
        result["record_filter"] = {field: record[field] for field in ("record_id", "title", "company")}
        return result
    finally:
        if owned:
            conn.rollback()
