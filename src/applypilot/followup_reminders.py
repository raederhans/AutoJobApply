"""Local reminder dates over existing progress, stored outside application state."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from applypilot.progress_report import _instant, _rows, _text, collect_report
from applypilot.storage.read_only import open_read_only

STORE_NAME = "followup-reminders.json"
BUCKETS = ("overdue", "today", "future", "undated")
_TERMINAL = {"rejected", "withdrawn", "accepted", "declined", "conflict"}


def _id(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _day(value: str) -> str:
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("Reminder dates must use YYYY-MM-DD") from exc
    return value


def _today(on: str | None) -> str:
    return _day(on) if on is not None else datetime.now(UTC).date().isoformat()


def _load(workspace: Path) -> dict:
    path = workspace / STORE_NAME
    if not path.exists():
        return {"version": 1, "items": {}, "overrides": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError
        for name in ("items", "overrides"):
            if not isinstance(state.get(name), dict):
                raise TypeError
            for key, item in state[name].items():
                prefix = "reminder" if name == "items" else "action"
                if not re.fullmatch(prefix + r"-[0-9a-f]{16}", key) or not isinstance(item, dict):
                    raise ValueError
                if item.get("due_date") is not None:
                    _day(item["due_date"])
                if name == "items":
                    if not re.fullmatch(r"(?:url|registry|record)-[0-9a-f]{16}", item.get("target_key", "")):
                        raise ValueError
                    if not re.fullmatch(r"application-[0-9a-f]{16}", item.get("record_id", "")):
                        raise ValueError
                    if not item.get("due_date") or not isinstance(item.get("next_step"), str):
                        raise ValueError
                for field in ("created_at", "updated_at", "dismissed_at"):
                    if item.get(field) is not None and not _instant(item[field]):
                        raise ValueError
        return state
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("Local reminder file is invalid; preserve it and repair it before updating") from exc


@contextmanager
def _edit(workspace: Path):
    """Serialize short local edits; atomic replacement preserves an earlier good file."""
    lock = workspace / (STORE_NAME + ".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("Another local reminder edit is active; retry after it completes") from exc
    os.close(descriptor)
    temporary = None
    try:
        state = _load(workspace)
        yield state
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=workspace,
                                         prefix=".followup-reminders-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, workspace / STORE_NAME)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def _snapshot(workspace: Path) -> tuple[dict, dict, list[dict]]:
    path = workspace / "applypilot.db"
    if not path.is_file():
        return {}, {}, []
    conn = open_read_only(path, timeout=10, snapshot=True)
    try:
        targets = {row["record_id"]: row for row in collect_report(conn)["rows"]}
        registries = _rows(conn, "followup_applications", ("application_id", "job_url"))
        by_url = defaultdict(list)
        keys = defaultdict(set)
        for application in registries:
            rid = _id("application-", application["application_id"])
            keys[rid].add(_id("registry-", application["application_id"]))
            if application.get("job_url"):
                by_url[application["job_url"]].append(application["application_id"])
                keys[rid].add(_id("url-", application["job_url"]))
        for job in _rows(conn, "jobs", ("url",)):
            url = job["url"]
            identities = by_url.get(url, [])
            rid = _id("application-", identities[0] if len(identities) == 1 else url)
            keys[rid].add(_id("url-", url))
        key_targets = defaultdict(set)
        for rid, target in targets.items():
            keys[rid].add(_id("record-", rid))
            for key in keys[rid]:
                key_targets[key].add(rid)
            targets[rid] = {**target, "target_keys": keys[rid]}
        actions = _rows(conn, "followup_actions", ("action_id", "job_url", "due_at", "completed_at"))
        # Reminder-specific reads exclude action summaries and human response payloads.
        return targets, dict(key_targets), actions
    finally:
        conn.close()


def _target(key: str, targets: dict, key_targets: dict) -> dict:
    matches = key_targets.get(key, set())
    return targets.get(next(iter(matches)), {}) if len(matches) == 1 else {}


def _next_step(target: dict, supplied: str = "") -> str:
    if target.get("application_status") == "submission_uncertain":
        return "Review the existing submission receipt before deciding any further action."
    if target.get("recruiting_stage") in _TERMINAL:
        return "Review this closed or conflicting recruiting record and decide whether to keep the local task."
    label = _text(supplied)
    if label and label not in {"[credential omitted]", "[URL omitted]"}:
        return label
    return "Review the saved application progress, then dismiss or postpone this local reminder."


def _item(reminder_id: str, data: dict, target: dict, today: str, source: str) -> dict:
    due = data.get("due_date") or ""
    bucket = "undated" if not due else "overdue" if due < today else "today" if due == today else "future"
    return {
        "reminder_id": reminder_id, "source": source,
        "record_id": target.get("record_id") or data.get("record_id") or "",
        "target_available": bool(target), "title": target.get("title") or "Unavailable local record",
        "company": target.get("company", ""), "application_status": target.get("application_status", ""),
        "recruiting_stage": target.get("recruiting_stage", ""), "due_date": due, "bucket": bucket,
        "next_step": _next_step(target, data.get("next_step", "")),
    }


def _active(state: dict, targets: dict, key_targets: dict, actions: list[dict], today: str) -> list[dict]:
    items = []
    for reminder_id, data in state["items"].items():
        if not data.get("dismissed_at"):
            items.append(_item(reminder_id, data, _target(data["target_key"], targets, key_targets), today, "local_reminder"))
    for action in actions:
        if action.get("completed_at"):
            continue
        reminder_id = _id("action-", str(action["action_id"]))
        override = state["overrides"].get(reminder_id, {})
        if override.get("dismissed_at"):
            continue
        due = _instant(action.get("due_at"))[:10]
        target = _target(_id("url-", str(action.get("job_url") or "")), targets, key_targets)
        data = {"due_date": override.get("due_date", due)}
        items.append(_item(reminder_id, data, target, today, "existing_action"))
    return sorted(items, key=lambda item: (item["due_date"], item["reminder_id"]))


def list_reminders(workspace: Path | str, *, on: str | None = None, bucket: str = "all", limit: int = 50) -> dict:
    """Group active reminders by UTC calendar day. A read creates no files."""
    if bucket not in {"all", *BUCKETS} or type(limit) is not int or limit < 1:
        raise ValueError("Use a known reminder bucket and a positive limit")
    root = Path(workspace).resolve()
    today = _today(on)
    state = _load(root)
    targets, key_targets, actions = _snapshot(root)
    items = _active(state, targets, key_targets, actions, today)
    groups = {name: [item for item in items if item["bucket"] == name] for name in BUCKETS}
    scheduled = {item["record_id"] for item in items if item["target_available"]}
    candidates = [{key: row[key] for key in ("record_id", "title", "company", "application_status", "recruiting_stage")}
                  for rid, row in sorted(targets.items()) if rid not in scheduled]
    return {
        "date": today, "timezone": "UTC", "counts": {name: len(rows) for name, rows in groups.items()},
        "groups": {name: rows[:limit] for name, rows in groups.items() if bucket in {"all", name}},
        "unscheduled_count": len(candidates), "unscheduled": candidates[:limit],
        "target_unavailable_count": sum(not item["target_available"] for item in items),
        "next_step": "Use set-reminder with a record_id, due date and brief next step; use snooze-reminder or dismiss-reminder for a listed reminder_id.",
    }


def set_reminder(workspace: Path | str, *, record_id: str, due_date: str, next_step: str) -> dict:
    """Create or reset one explicit local reminder for an existing progress record."""
    root = Path(workspace).resolve()
    due = _day(due_date)
    label = _text(next_step)
    if not label or label in {"[credential omitted]", "[URL omitted]"}:
        raise ValueError("Provide a short next step without credentials or a bare URL")
    targets, key_targets, _ = _snapshot(root)
    target = targets.get(record_id)
    if target is None:
        raise ValueError("Unknown record_id; select an existing record from reminders or the progress export")
    unique = [key for key in target["target_keys"] if len(key_targets[key]) == 1]
    key = min(unique, key=lambda k: ({"url": 0, "registry": 1, "record": 2}[k.split("-", 1)[0]], k))
    now = datetime.now(UTC).isoformat()
    with _edit(root) as state:
        matches = [(rid, data) for rid, data in state["items"].items()
                   if _target(data["target_key"], targets, key_targets).get("record_id") == record_id]
        if len(matches) > 1:
            raise ValueError("Multiple local reminders match this record; use a specific reminder_id")
        reminder_id, previous = matches[0] if matches else (_id("reminder-", key), {})
        state["items"][reminder_id] = {
            "target_key": key, "record_id": record_id, "due_date": due, "next_step": label,
            "created_at": previous.get("created_at", now), "updated_at": now, "dismissed_at": None,
        }
    return {"message": "Reminder saved locally.", **_item(reminder_id, state["items"][reminder_id], target, _today(None), "local_reminder")}


def _change(workspace: Path | str, reminder_id: str, *, due_date: str | None = None, dismiss: bool = False) -> dict:
    root = Path(workspace).resolve()
    due = _day(due_date) if due_date is not None else None
    today = _today(None)
    targets, key_targets, actions = _snapshot(root)
    with _edit(root) as state:
        active = {item["reminder_id"]: item for item in _active(state, targets, key_targets, actions, today)}
        item = active.get(reminder_id)
        if item is None:
            raise ValueError("Unknown or inactive reminder_id; list active local reminders first")
        if due and item["due_date"] and due < item["due_date"]:
            raise ValueError("A postponed date cannot precede the current reminder date; use set-reminder to schedule a separate date")
        collection = state["items"] if item["source"] == "local_reminder" else state["overrides"]
        data = collection.setdefault(reminder_id, {})
        data["updated_at"] = datetime.now(UTC).isoformat()
        if dismiss:
            data["dismissed_at"] = data["updated_at"]
        if due:
            data["due_date"] = due
    return {"message": "Reminder hidden locally." if dismiss else "Reminder postponed locally.",
            "reminder_id": reminder_id, "source": item["source"], "due_date": due or item["due_date"],
            "dismissed": dismiss}


def snooze_reminder(workspace: Path | str, *, reminder_id: str, due_date: str) -> dict:
    return _change(workspace, reminder_id, due_date=due_date)


def dismiss_reminder(workspace: Path | str, *, reminder_id: str) -> dict:
    return _change(workspace, reminder_id, dismiss=True)
