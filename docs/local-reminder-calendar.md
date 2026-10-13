# Download a local reminder calendar

In the career workbench, open **求职进度 → 本地跟进提醒**, select a download
group, then choose **下载日历 (.ics)**. The file includes every dated reminder
with an available source in that group, independently of the progress table's
filters and the reminder list's display limit. Import the file into your chosen
calendar yourself; downloading does not connect to a calendar account.

Events use the current reminder's date, including local postponements, as an
all-day date. Group selection uses today's UTC calendar date. There is no
invented clock time or timezone conversion for an all-day event. The original
SQLite action deadline and older calendar exports retain their own source date.
Hidden local reminders and completed source actions are absent from this view.

The download result gives the number of exported events and skipped entries.
Missing/invalid dates are counted separately from unavailable source records;
an unavailable source takes precedence so the same entry is not counted twice.
Those entries stay visible in the workbench for review. An empty or entirely
undated group produces an empty calendar with truthful omission counts.

Each download is a new snapshot with fresh event identifiers. Changes in the
workbench do not update files already downloaded or calendars already imported.
Repeated imports can create duplicate events; inspect or replace the old import
according to your calendar application's controls. Calendar account import,
update behavior and notification preferences have not been exercised here.

The browser endpoint is
`GET /api/career/v1/job-progress/reminders/download?format=ics&bucket=all`.
It returns `followup-reminders.ics`, `text/calendar; charset=utf-8`, with no-store
headers and integer event/omission counts for the download message. It reuses
the existing complete public reminder projection and performs no writes. URLs,
source IDs, application answers, private action summaries and credentials are
not added. Event text contains only the projection's public role/company labels
and current local next step. The file defines no alarms, attendees or organizer.

The serializer follows [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545), sections
3.1, 3.3.11 and 3.6.1: CRLF lines, UTF-8 folding within 75 octets, escaped TEXT,
and one-day DATE events. A DATE event without an end or duration also avoids
inventing an end beyond the maximum supported date. Newlines in labels/tasks
remain text rather than creating another property. The fixed omission counters
are local extension properties; the UI exposes their values before any import.
