# Local job comparison

List existing jobs and obtain safe IDs before choosing a comparison:

```powershell
applypilot --workspace C:\local-data compare-jobs --list
applypilot --workspace C:\local-data compare-jobs --list --query analyst --limit 20
applypilot --workspace C:\local-data compare-jobs --job-id job-0123456789abcdef --job-id career-job-0123456789abcdef --output-dir C:\local-reports\comparison
```

The example IDs are placeholders. Copy real `job_id` values from `--list`.
`job-...` identifies an existing pipeline job by a hash of its exact URL;
`career-job-...` identifies the latest public research job by a hash of its exact
source ID. Raw platform IDs and `platform:<id>` / `career:<id>` aliases may also
be used only when they resolve uniquely. Unknown or ambiguous IDs are rejected,
and duplicate aliases for the same job are rejected. No company/title guessing
or URL normalization is used to select comparison columns.

Choose **2–6 distinct jobs**. The command creates `job-comparison.html` and
`job-comparison.md`, with columns in the order you supplied. Existing output
files are never overwritten. Both formats show company, role, location,
application execution status, recorded posting availability, salary text,
explicitly saved deadline text, employment type, level, observation date,
public requirement quotes and a short public-description preview.

Only existing fields are copied. Pipeline salary comes from `jobs.salary`;
research jobs may contain an explicit scalar `salary` value. No salary is
estimated from descriptions, company, location or seniority. Deadlines use only
explicitly saved `deadline`, `application_deadline`, `closing_date`,
`valid_through` or `validThrough` fields if present. Observation dates, posting
closure, authorization expiry and “accepting at least until” conditions are
never converted into a deadline. Missing or unsafe fields are shown as unknown.

Research requirements use up to five already saved requirement text quotes and
their source sections. Pipeline jobs have no standard requirements field, so
that row stays unknown. The separate description row is a whitespace-normalized
preview capped at 240 characters, not an inferred requirements list or a model
summary. Research rows use only the latest `research_jd` snapshot; private
evidence, diagnoses, model attempts, BLOBs and source files are not accessed.
Posting availability and application status remain distinct.

The HTML is self-contained, uses the existing inline report styling and system
fonts, and has no scripts, network resources, external links, forms or actions.
It opens directly in a browser and supports printing; narrow windows can scroll
the comparison horizontally. All HTML/Markdown text is escaped. URLs and
credential assignments are omitted; form/script text is excluded from public
description previews. Applicant answers, resumes, materials, raw errors and
fit scores are outside the query/export allowlist. No score, ranking, LLM call,
provider contact or application action is performed.

Reads use an existing SQLite database in read-only mode without initializing or
migrating it. The list response has a total count and a bounded display; only the
specified IDs enter a comparison. The example
[HTML comparison](examples/job-comparison.html) and
[Markdown comparison](examples/job-comparison.md) were generated from a
fictional fixture. The independent Python entry is:

```python
from applypilot.job_comparison import export_comparison

export_comparison(r"C:\local-data", r"C:\local-reports\comparison", job_ids=[
    "job-0123456789abcdef", "career-job-0123456789abcdef",
])
```
