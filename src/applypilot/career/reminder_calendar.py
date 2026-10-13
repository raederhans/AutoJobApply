"""A file snapshot of dated local reminders; never a calendar account operation."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from uuid import uuid4


def _text(value: str) -> str:
    # RFC 5545 TEXT: a newline is data, never another content line/property.
    value = "".join(char for char in value if ord(char) >= 32 or char in "\t\r\n")
    return (value.replace("\x7f", "").replace("\\", "\\\\").replace("\r\n", "\n")
            .replace("\r", "\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,"))


def _fold(line: str) -> str:
    parts, current, size = [], "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > 75:
            parts.append(current)
            current, size = " ", 1
        current += char
        size += width
    parts.append(current)
    return "\r\n".join(parts)


def render_calendar(report: dict) -> tuple[bytes, dict[str, int]]:
    """Render the already filtered public reminder projection as all-day events.

    Each export has fresh event UIDs: it is a snapshot, not a synchronization
    feed. Missing/invalid dates and unresolved targets are counted, not invented.
    """
    counts = {"events": 0, "omitted_undated": 0, "omitted_unavailable": 0}
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = []
    for items in report["groups"].values():
        for item in items:
            if item["target_available"] is not True:
                counts["omitted_unavailable"] += 1
                continue
            due = item["due_date"]
            try:
                if not isinstance(due, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", due) is None:
                    raise ValueError
                day = date.fromisoformat(due)
            except ValueError:
                counts["omitted_undated"] += 1
                continue
            summary = f"跟进：{item['title']} · {item['company']}"
            description = (f"下一步：{item['next_step']}\n"
                           "本地提醒快照，沿用清单中的 UTC 日历日期。导入后以全天事件显示。\n"
                           "此文件不随工作台编辑自动更新；请在处理前核对当前岗位记录。")
            lines.extend([
                "BEGIN:VEVENT", f"UID:{uuid4()}@applypilot.local", f"DTSTAMP:{stamp}",
                # A DATE without DTEND/DURATION is one whole day, including 9999-12-31.
                f"DTSTART;VALUE=DATE:{day.isoformat().replace('-', '')}",
                "SUMMARY:" + _text(summary), "DESCRIPTION:" + _text(description),
                "CLASS:PRIVATE", "TRANSP:TRANSPARENT", "END:VEVENT",
            ])
            counts["events"] += 1
    if counts["events"] == 0:
        # An RFC 5545 object needs at least one component. Keep the omission
        # report but do not manufacture an empty, unimportable calendar file.
        return b"", counts
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ApplyPilot//Local Reminder Snapshot//ZH",
        "CALSCALE:GREGORIAN",
        f"X-APPLYPILOT-EXPORTED-COUNT:{counts['events']}",
        f"X-APPLYPILOT-OMITTED-UNDATED:{counts['omitted_undated']}",
        f"X-APPLYPILOT-OMITTED-UNAVAILABLE:{counts['omitted_unavailable']}", *lines, "END:VCALENDAR",
    ]
    return ("\r\n".join(_fold(line) for line in lines) + "\r\n").encode("utf-8"), counts
