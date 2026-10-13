"""Small browser contract over local reminder files, never application writes."""

from __future__ import annotations

import re

from applypilot import followup_reminders


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ValueError as exc:
        message = str(exc)
        reasons = (
            ("Local reminder file is invalid", "reminder_store_invalid"),
            ("Another local reminder edit is active", "reminder_busy"),
            ("Unknown record_id", "reminder_record_changed"),
            ("Unknown or inactive reminder_id", "reminder_record_changed"),
            ("Multiple local reminders", "reminder_ambiguous"),
            ("A postponed date cannot precede", "reminder_date_before_current"),
            ("Provide a short next step", "reminder_step_invalid"),
            ("Reminder dates must use", "reminder_date_invalid"),
        )
        code = next((code for prefix, code in reasons if message.startswith(prefix)), "reminder_input_invalid")
        raise ValueError(code) from exc


def list_items(workspace, *, limit=100):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("reminder_input_invalid")
    return {**_call(followup_reminders.list_reminders, workspace, limit=limit), "limit": limit}


def mutate(workspace, operation, data, *, reminder_id=None):
    fields = {"set": {"record_id", "due_date", "next_step"}, "snooze": {"due_date"}, "dismiss": set()}
    if operation not in fields or set(data) != fields[operation]:
        raise ValueError("reminder_input_invalid")
    if any(not isinstance(value, str) for value in data.values()):
        raise ValueError("reminder_input_invalid")
    if "due_date" in data and len(data["due_date"]) != 10:
        raise ValueError("reminder_date_invalid")
    if operation == "set":
        if not re.fullmatch(r"application-[0-9a-f]{16}", data["record_id"]):
            raise ValueError("reminder_record_changed")
        if not 1 <= len(data["next_step"].strip()) <= 200:
            raise ValueError("reminder_step_invalid")
        return _call(followup_reminders.set_reminder, workspace, **data)
    if not isinstance(reminder_id, str) or not re.fullmatch(r"(?:action|reminder)-[0-9a-f]{16}", reminder_id):
        raise ValueError("reminder_record_changed")
    function = followup_reminders.snooze_reminder if operation == "snooze" else followup_reminders.dismiss_reminder
    return _call(function, workspace, reminder_id=reminder_id, **data)
