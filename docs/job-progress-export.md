# Local job progress export

The local career workbench also exposes this projection under **求职进度**.
Start it for the intended workspace (`applypilot --workspace C:\local-data career serve`)
and select that stage. A career learning target is not required. Search role,
company or location, choose inclusive UTC dates and any execution/recruiting
statuses, then apply the filters. The table has 100 records per page; both download
buttons include **all matching records**, using the applied filters rather than
unsaved form edits. Downloads go to the browser's normal download location.

The three summary numbers count matching records, records with a recorded review
clue, and records associated with an open action. An action can be associated with
more than one record; the last number is not a unique-action total. A historic
failure clue does not authorize a retry. Missing dates display as `未记录`, and
unavailable source tables produce unavailable counts rather than measured zeros.

The HTTP preview and download routes reuse this module's read-only projection.
They do not write report files into the workspace or change application state.
Short acceptance covers literal keyword filtering before pagination, all-match
CSV/Markdown download, UTC/status filters, unknown dates, formula escaping and
unchanged database bytes (`tests/test_job_progress_browser.py`).

Export the existing workspace database to a spreadsheet CSV and a Markdown
summary. The command reads a SQLite snapshot in read-only mode; it does not
refresh progress, initialize or migrate a database, load a profile or resume,
contact a provider, share files, or submit applications.

Always select the data workspace explicitly before the command. From a deployed
checkout using its workspace wrapper:

```powershell
..\run.ps1 followup export-progress --output-dir C:\local-reports\progress-20261013
```

The installed CLI also supports an explicit workspace:

```powershell
applypilot --workspace C:\local-data followup export-progress --output-dir C:\local-reports\progress-20261013
applypilot --workspace C:\local-data followup export-progress --output-dir C:\local-reports\needs-review --since 2026-10-01 --until 2026-10-13 --status failed --status submission_uncertain
applypilot --workspace C:\local-data followup export-progress --output-dir C:\local-reports\submitted-october --date-field submitted_at --since 2026-10-01 --until 2026-10-13
applypilot --workspace C:\local-data followup export-progress --output-dir C:\local-reports\interviews --status interview_1
```

Each call creates `job-progress.csv` (UTF-8 with BOM for Excel) and
`job-progress.md` in the output directory. Existing report files are never
overwritten; choose another output directory to take a new snapshot. The JSON
response contains the exported count, pending count, filters and local filenames.

Date bounds are inclusive **UTC calendar dates**. The default `updated_at` is
derived from the latest available job, attempt, recruiting event or action
timestamp, and the CSV includes `updated_at_source`. It is an observation date,
not proof that an application changed state. `--date-field submitted_at` and
`discovered_at` select other date fields. Missing or invalid dates remain blank;
records with no selected date are excluded when a date bound is supplied. Legacy
timestamps without an offset are interpreted as UTC. This differs from
`followup stats`, whose submission cohorts use Singapore dates.
If an existing recruiting identity has an unknown submission date, it stays
unknown even when the job has a recorded `applied_at` observation timestamp.
For jobs without an identity, `submitted_at` is the existing `jobs.applied_at`
record value; the export does not upgrade it to a verified submission date.

Repeat `--status` to select any listed application status or recruiting stage.
The filter matches either column; for example, `failed` selects application
execution failures and `interview_1` selects first-round recruiting progress.
Application status and recruiting stage remain separate: an execution failure is
not a recruiting rejection. Unknown application status text is exported as
`unknown`. No status filter includes every row, including discovered jobs which
have not been applied to.

Rows combine `jobs` with already registered followup applications only when one
identity has the exact same URL. Standalone imported applications are included;
the exporter does not match by company/title, normalize URLs, or create identities.
Verified, user-confirmed, reported, uncertain and unverified submission bases
stay distinct. This export does not independently verify submission.

The CSV includes record ID, role, company, location, application status,
recruiting stage, submission basis, submission/discovery/update dates, update
source, todo reason, earliest open-action due date and open-action count.
Record IDs are hashes so raw URL-based identities are never exported.

Todo reasons use the existing fixed failure taxonomy, plus
`submission_confirmation_missing`, `retry_blocked_requires_review`,
`application_readiness_requires_review` and `followup_action_pending` labels.
Full errors and retry explanations can contain answers or credentials, so those
texts are never copied. Action summaries, human prompts/responses, resume and
cover-letter paths/content, evidence payloads, message IDs and all URL fields are
omitted. Embedded URLs/credential assignments in the short public role/company
labels are redacted. Spreadsheet formula prefixes are escaped and Markdown cells
are escaped.

Failure categories reflect the recorded reason and may describe a historical
failure; they do not establish that a retry is appropriate. An explicit open
action remains pending until its existing record is completed, including when
its due date has passed.

The small [example CSV](examples/job-progress.csv) and
[example Markdown summary](examples/job-progress.md) use fictional companies and
roles. They were generated from a synthetic database; no real job applications
were read for these examples.

The report module is independently callable by local Python code:

```python
from applypilot.progress_report import export_progress

result = export_progress(
    r"C:\local-data\applypilot.db",
    r"C:\local-reports\review-snapshot",
    since="2026-10-01",
    until="2026-10-13",
    statuses=["failed", "submission_uncertain"],
)
```
