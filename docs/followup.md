# Local application followup

`followup` keeps recruiting feedback, interview rounds and next actions in the
workspace database. Its observations are separate from `jobs.apply_status`,
submission receipts and company-priority history. It consumes JSON supplied by
the operator; it does not scan or send email.

Always select the workspace before the command:

```powershell
applypilot --workspace C:\demo\workspace followup import --file C:\demo\events.json
applypilot --workspace C:\demo\workspace followup pending
applypilot --workspace C:\demo\workspace followup resolve --event-id <id-from-pending> --url https://example.test/jobs/42
applypilot --workspace C:\demo\workspace followup timeline --url https://example.test/jobs/42
applypilot --workspace C:\demo\workspace followup add-action --url https://example.test/jobs/42 --summary "Prepare examples" --due-at 2026-10-12T09:00:00+08:00
applypilot --workspace C:\demo\workspace followup due --at 2026-10-12T09:00:00+08:00
applypilot --workspace C:\demo\workspace followup complete-action --action-id <id-from-timeline>
applypilot --workspace C:\demo\workspace followup export-ics --file C:\demo\followup.ics
```

The import file is an array of reviewed observations:

```json
[
  {
    "provider": "outlook",
    "message_id": "example-message-42",
    "occurred_at": "2026-10-09T10:00:00+08:00",
    "event_type": "interview_invited",
    "job_url": "https://example.test/jobs/42",
    "company": "Example Company",
    "title": "Analyst Intern",
    "evidence_ref": "local-reviewed-mail:example-message-42",
    "summary": "First interview invitation",
    "scheduled_at": "2026-10-13T14:00:00+08:00",
    "round": 1
  }
]
```

Required fields are `provider`, `message_id`, `occurred_at`, `event_type`,
`summary`, and `evidence_ref` for recruiting observations. Supported types are
`recruiter_feedback`, `rejected`, `assessment`, `interview_invited`, `interview`,
`interview_completed`, `offer`, `withdrawn`, and `manual_note`. A `manual_note`
may omit evidence and is presented as a note, not a confirmed recruiting result.
Evidence references record operator-supplied provenance; the importer does not
independently verify their contents. `company`, `title` and `job_url` are optional
candidates. `scheduled_at` is optional for interviews; `round` is an optional
positive integer. All dates require an explicit UTC offset. Dates are stored in UTC.

Only an exact existing `jobs.url` is automatically associated. Missing or unknown
URLs stay pending even if a company/title candidate looks unique. `resolve`
records the operator's explicit choice without replacing the original source
URL or observation. An already matched event cannot be reassigned.

The case-insensitive provider plus case-sensitive message ID is the immutable
import identity. Identical re-imports are ignored; any conflicting observation
rejects the entire batch, preserving all earlier data. An operator must choose a
different message ID for a distinct observation. Invalid fields also reject the
entire batch. Action completion is idempotent.

Calendar export includes matched dated interviews and open actions, optionally
filtered by `--url`. Entries have stable UIDs, UTC dates, escaped text, UTF-8
line folding and CRLF line endings. Pending interviews are excluded until resolved.
This first version exports point-in-time calendar entries without duration,
email delivery, or an editable web interface.

Dashboard integration uses
`applypilot.followup.followup_summary(conn, now=None, limit=20)`. It returns
`pending_count`, `open_action_count`, `due_action_count`, `due_actions`,
`upcoming_interviews` and `recent_events`. `timeline(conn, job_url)`,
`pending_events(conn)` and `due_actions(conn, at=None)` are also read-only.
A missing connection or missing followup tables returns empty data and creates
no schema. Writes lazily create only the two followup tables after the existing
database initializer has accepted the workspace schema.
