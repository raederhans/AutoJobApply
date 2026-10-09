"""Read-only planning from the existing fetch ledger, never a coverage claim."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

EVIDENCE_VERSION = 1
_FAILURES = {"failed", "blocked", "error"}


def _time(value):
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _now(value):
    if value is None:
        return datetime.now(UTC)
    parsed = _time(value)
    if parsed is None:
        raise ValueError("now must be a datetime or ISO timestamp")
    return parsed


def _limit(budget, default, maximum=None):
    if budget is None:
        return default
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0 or (maximum is not None and budget > maximum):
        raise ValueError(f"budget must be an integer between 0 and {maximum}" if maximum is not None else "budget must be a nonnegative integer")
    return budget


def _runs(conn, since=None):
    # A plan must not initialize or migrate a database as a side effect.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='radar_fetch_runs'").fetchone() is None:
        return []
    query = "SELECT * FROM radar_fetch_runs WHERE finished_at IS NOT NULL AND status != 'running'"
    params = ()
    if since is not None:
        query += " AND julianday(finished_at) >= julianday(?)"
        params = (since.isoformat(),)
    cursor = conn.execute(query, params)
    columns = [item[0] for item in cursor.description]
    runs = []
    for values in cursor.fetchall():
        row = dict(zip(columns, values))
        try:
            metadata = json.loads(row.get("metadata_json") or "{}")
        except (ValueError, TypeError):
            metadata = {}
        row["metadata"] = metadata if isinstance(metadata, dict) else {}
        row["finished"] = _time(row["finished_at"])
        if row["finished"] is not None:
            runs.append(row)
    return sorted(runs, key=lambda row: (row["finished"], str(row.get("started_at") or "")), reverse=True)


def _cooldown(runs, now):
    latest = next((run for run in runs if run["status"] != "skipped"), None)
    if latest is None:
        return None
    failed = latest["status"] in _FAILURES or latest["metadata"].get("search_status") in _FAILURES or bool(latest.get("error"))
    until = latest["finished"] + timedelta(minutes=30)
    return until.isoformat() if failed and now < until else None


def _result(selected, deferred, limit):
    return {
        "read_only": True, "budget_limit": limit,
        "selected": selected, "deferred": deferred,
        "coverage": "non_exhaustive", "promotion_attribution": "unavailable",
        "why": None if selected else "zero_budget" if limit == 0 else "no_eligible_sources",
    }


def plan_exploration(conn, queries=None, sites=("linkedin", "indeed"), budget=None,
                     now=None, job_type=None, hours_old=24):
    """Reserve one exploration call, then rank measured new observations/attempt.

    An omitted budget gives automatic discovery four calls; explicit queries keep
    their requested combinations (at most six). Legacy lead_count is not yield.
    """
    from applypilot.discovery.explore import _QUERY_PAIRS, default_exploration_queries

    current = _now(now)
    sites = list(dict.fromkeys(sites))
    if not sites or any(site not in {"linkedin", "indeed"} for site in sites):
        raise ValueError("sites must be linkedin and/or indeed")
    if job_type not in {None, "internship", "fulltime", "parttime", "contract"}:
        raise ValueError("unsupported job_type")
    if not isinstance(hours_old, int) or not 1 <= hours_old <= 720:
        raise ValueError("hours_old must be between 1 and 720")
    explicit = queries is not None
    if explicit:
        queries = list(dict.fromkeys(query.strip() for query in queries if query.strip()))
        if not 1 <= len(queries) <= 3 or any(len(query) > 160 for query in queries):
            raise ValueError("choose 1 to 3 short role queries")
    else:
        today = current.astimezone(timezone(timedelta(hours=8))).date()
        daily = default_exploration_queries(today)
        queries = list(dict.fromkeys([*daily, *(query for pair in _QUERY_PAIRS for query in pair)]))
    limit = _limit(budget, len(queries) * len(sites) if explicit else 4, 6)
    runs = _runs(conn, since=current - timedelta(days=30))
    eligible, deferred = [], []
    for query in queries:
        for site in sites:
            source_id = f"{site}-jobs"
            source_runs = [run for run in runs if run["source_id"] == source_id]
            history = [run for run in source_runs if run["status"] != "skipped"
                       and run["metadata"].get("query") == query
                       and run["metadata"].get("job_type") == job_type
                       and run["metadata"].get("requested_time_filter_hours", 24) == hours_old]
            measured = [run for run in history if run["metadata"].get("budget_evidence_version") == EVIDENCE_VERSION
                        and isinstance(run["metadata"].get("new_observations"), int)
                        and run["metadata"]["new_observations"] >= 0]
            new = sum(run["metadata"]["new_observations"] for run in measured)
            evidence = {
                "attempts": len(history), "measured_attempts": len(measured),
                "new_observations": new if measured else None,
                "new_observations_per_attempt": new / len(measured) if measured else None,
                "last_finished_at": history[0]["finished_at"] if history else None,
                "yield_status": "measured" if measured else "unavailable",
                "history_window_days": 30,
                "cooldown_until": _cooldown(source_runs, current),
                "job_type": job_type, "hours_old": hours_old,
            }
            item = {"site": site, "query": query, "source_id": source_id, "evidence": evidence}
            if evidence["cooldown_until"]:
                deferred.append({**item, "reason": "provider_cooldown"})
            else:
                eligible.append(item)
    if explicit or not any(item["evidence"]["attempts"] for item in eligible):
        ordered = [(item, "explicit_query" if explicit else "cold_start_rotation") for item in eligible]
    else:
        # Stable input order breaks ties, preserving the daily rotation for unseen pairs.
        oldest = min(eligible, key=lambda item: _time(item["evidence"]["last_finished_at"]) or datetime.min.replace(tzinfo=UTC)) if eligible else None
        remaining = [item for item in eligible if item is not oldest]
        remaining.sort(key=lambda item: (
            -(item["evidence"]["new_observations_per_attempt"] or 0),
            _time(item["evidence"]["last_finished_at"]) or datetime.min.replace(tzinfo=UTC),
        ))
        ordered = ([(oldest, "exploration_slot")] if oldest else []) + [(item, "measured_yield" if item["evidence"]["measured_attempts"] else "unmeasured_history") for item in remaining]
    selected = [{**item, "reason": reason} for item, reason in ordered[:limit]]
    deferred.extend({**item, "reason": "budget_exhausted"} for item, _ in ordered[limit:])
    return _result(selected, deferred, limit)


def plan_official_collection(conn, companies, budget=None, due_only=False, now=None, explicit=False):
    """Keep legacy all-active collection; opt-in policy adds cadence/cooldown."""
    current = _now(now)
    companies = list(companies)
    limit = _limit(budget, len(companies))
    policy = budget is not None or due_only
    runs = _runs(conn)
    eligible, deferred = [], []
    for company in companies:
        source_id = f"official:{company.get('id', '')}:{company.get('provider', '')}"
        history = [run for run in runs if run["source_id"] == source_id and run["status"] != "skipped"]
        latest = history[0] if history else None
        successful = next((run for run in history
                           if run["status"] in {"complete", "partial"}
                           and not run.get("error")
                           and run["metadata"].get("search_status") not in _FAILURES), None)
        due_at = successful["finished"] + timedelta(hours=24) if successful else None
        item = {
            "company_id": company.get("id"), "provider": company.get("provider"),
            "source_id": source_id, "company": dict(company),
            "evidence": {
                "last_finished_at": latest["finished_at"] if latest else None,
                "last_successful_at": successful["finished_at"] if successful else None,
                "due_at": due_at.isoformat() if due_at else None,
                "due": due_at is None or current >= due_at,
                "cooldown_until": _cooldown(history, current),
                "new_stored_jobs": latest["new_count"] if latest else None,
            },
        }
        if not company.get("active", False):
            reason = "inactive"
        elif policy and item["evidence"]["cooldown_until"]:
            reason = "provider_cooldown"
        elif due_only and not explicit and not item["evidence"]["due"]:
            reason = "not_due"
        else:
            eligible.append(item)
            continue
        deferred.append({**item, "reason": reason})
    if policy and not explicit:
        eligible.sort(key=lambda item: (
            _time(item["evidence"]["last_finished_at"]) or datetime.min.replace(tzinfo=UTC),
            -(item["evidence"]["new_stored_jobs"] or 0),
        ))
    selected = []
    for index, item in enumerate(eligible):
        if index >= limit:
            deferred.append({**item, "reason": "budget_exhausted"})
            continue
        reason = (
            "explicit_company" if explicit else
            "never_attempted" if policy and item["evidence"]["last_finished_at"] is None else
            "least_recent_attempt" if policy else "active_source"
        )
        selected.append({**item, "reason": reason})
    return _result(selected, deferred, limit)
