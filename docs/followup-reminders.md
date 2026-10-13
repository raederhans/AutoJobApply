# Local followup reminders

In the local career workbench, choose **求职进度** and use a record's **设置提醒**
button. Enter a UTC date and a short next step, then save explicitly. The same
page's local reminder list groups overdue, today, future and undated entries;
each entry supports postponing its date or hiding it from that list. A missing
or ambiguous source record stays visible for review instead of being guessed.

The list initially displays at most 100 entries per group and can show up to 500;
counts still cover the full groups, and the page identifies any remaining hidden
entries. The HTTP editor uses the same local JSON store as the CLI. It does not
mark an application contacted or change an existing action's completion/deadline
in SQLite. For existing actions, an overridden date affects only this reminder
list; other progress and calendar views retain the original source date.

The page sends each edit once. If the connection drops, refresh the list to check
the saved state before another edit. A damaged reminder file is preserved and
reported. The editor requires the local workbench session and matching origin.

Inspect existing local actions together with manually scheduled reminders:

```powershell
applypilot --workspace C:\local-data followup reminders
applypilot --workspace C:\local-data followup reminders --on 2026-10-13 --bucket today
applypilot --workspace C:\local-data followup reminders --bucket overdue --limit 20
```

The list groups reminders by **UTC calendar date**: earlier dates are `overdue`,
the same date is `today`, and later dates are `future`. All times within the same
UTC day are in the same group. Invalid legacy action dates appear under `undated`
so they can be corrected. Counts cover all entries; `--limit` bounds each group
and the unscheduled-record list. `--on` is a view date, not a historical snapshot.

Entries show role, company, application/recruiting status, date, a clear next
step and a safe `reminder_id`. Unscheduled records also provide the `record_id`
used by [progress export](job-progress-export.md), so a URL is not required.

Use an ID from the actual list; the following IDs are fictional placeholders:

```powershell
applypilot --workspace C:\local-data followup set-reminder --record-id application-0123456789abcdef --due-date 2026-10-13 --next-step "Prepare three project examples for the interview"
applypilot --workspace C:\local-data followup snooze-reminder --reminder-id reminder-0123456789abcdef --due-date 2026-10-16
applypilot --workspace C:\local-data followup dismiss-reminder --reminder-id reminder-0123456789abcdef
```

`set-reminder` sets or resets one local reminder per existing record, including
an imported application without a job URL. Setting a dismissed reminder again
reactivates that local reminder. The next step is a short task, bounded to 240
characters; omit application answers, credentials and document content. Embedded
URLs are omitted and credential-bearing text is rejected rather than stored.
For an uncertain submission the list directs you to review the existing receipt;
closed/conflicting recruiting records direct you to review whether the task is
still relevant. These status checks do not perform any action.

`snooze-reminder` accepts the same date or a later date. It can also postpone the
local reminder for an existing `followup_actions` entry: those IDs start with
`action-`. Each existing action has a separate override, even when multiple
actions refer to the same application. Completed database actions stay excluded.

`dismiss-reminder` hides only that reminder from this local list. It does not
mark the existing action completed or change an application to contacted.
Existing `due`, `timeline`, progress exports and calendar exports continue using
the original action deadlines/completion records. Use the existing
`complete-action` command separately when you actually complete its task.

Reminder dates and local overrides live in `followup-reminders.json` in the
selected workspace. The database is read-only even during set/postpone/dismiss.
No profile or application material is loaded. Reads create no files or schema;
writes create only this reminder file and a temporary edit lock. There is no
scheduler, email/WhatsApp delivery, browser activity or application submission.

Only hashed target references are stored; URLs and raw action IDs are not copied.
A unique exact target fingerprint preserves association if a discovered job
later gets a registered application identity. A deleted or ambiguously linked
target remains visible with `target_available: false`; no company/title matching
is attempted. Existing action summaries, raw errors and human responses are
omitted; their next steps are fixed review guidance.

Updates use an exclusive short lock and atomic file replacement. A malformed
reminder file is preserved and reported instead of being overwritten. If a
process is interrupted and leaves `followup-reminders.json.lock`, verify that
no reminder edit is running before removing that stale lock manually. These
reminders are a local planning checklist, not scheduled notifications.

The [fictional checklist example](examples/reminder-checklist.json) was produced
through the real CLI using a synthetic database. It shows one overdue reminder,
one due today and one future reminder; setting and postponing them left the
database SHA256 unchanged.
