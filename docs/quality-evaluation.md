# Offline quality contracts

The `quality` CLI checks recorded outputs against local, reviewed fixtures. It
does not call a model or read a profile, mailbox, browser or database. The score
is the fraction of applicable deterministic checks that pass. It is not a
measurement of model capability, prose quality or real-world factual truth.

```powershell
applypilot quality run --file scripts/evals/synthetic-pass.json
applypilot quality run --file scripts/evals/synthetic-fail.json
applypilot quality export-promptfoo --file scripts/evals/synthetic-pass.json --output-dir C:\demo\new-eval
```

`run` prints per-case `fixture`, `schema`, `citations`, `support_levels`,
`unsupported_claims` and `submission_evidence` results. Inapplicable checks are
`null`. Any failed case returns exit code 1; malformed fixture input returns 2.
The deliberate negative examples in `synthetic-fail.json` should exit 1.

Each suite has `schema_version: 1` and a nonempty `cases` array. Every case has
`schema_version: 1`, a unique `id`, `kind`, `jd`, `sources` and the recorded
`output`. The provided JSON fixtures are runnable examples containing fictional
data only.

For `kind: evidence`, the fixture's `requirements` specify an ID, verbatim JD
text, an adjudicated `expected_support` label (`direct`, `transferable` or
`gap`), allowed `source_ids`, and reviewed `allowed_statements`. Each output
`evidence_map` entry must contain exactly `requirement_id`, `support_level`,
`source_id`, `source_quote` and `statement`. Every requirement must occur exactly
once; all entries are checked. Quotes must be verbatim in their named source,
support labels must match the fixture oracle, and complete statements must be
among the reviewed assertions for that requirement. Numeric-claim extraction is
shared with the production resume validator and rejects added quantities absent
from the quoted evidence. Gap entries use empty source IDs and quotes.

Verbatim matching only establishes citation provenance. The reviewed fixture
oracle determines which complete assertions and support labels are permitted;
an unrelated verbatim quote cannot upgrade transferable evidence to direct
experience. Arbitrary paraphrases and extra narrative fields are rejected.
This intentionally narrow output contract cannot assess free-form resume prose,
the correctness of the fixture author's judgment, or semantic equivalence of
new wording.

For `kind: receipt`, the fixture includes an exact `job_url`. Sources are typed
`receipt`, `submit_attempt` or `note`. Receipt sources carry `status` and a
`receipt_id`. Output contains exactly `job_url`, `status` and `evidence_refs`.
`submitted` requires a referenced, confirmed receipt for that exact job;
a click, uncertain receipt, unrelated job or invented evidence ID fails.
`submission_uncertain`, `failed` and `ready` can honestly report no receipt.
These are synthetic evidence gates, not ATS receipt verification.

Promptfoo export creates a **new** directory and refuses an existing directory,
including an empty one. It writes `promptfooconfig.yaml` and `assertion.py`.
The config uses the official [Echo provider](https://www.promptfoo.dev/docs/providers/echo/)
to replay the recorded JSON, with zero model calls. Its external
[Python assertion](https://www.promptfoo.dev/docs/configuration/expected-outputs/python/)
imports the same `get_assert(output, context)` function used by local checks.
The fixture is supplied in `context['vars']['case']`; the function returns
`pass`, `score` and `reason`. A malformed context fails closed.

Use an already-installed Promptfoo CLI and the environment containing ApplyPilot:

```powershell
$env:PROMPTFOO_PYTHON = (Resolve-Path .venv\Scripts\python.exe).Path
promptfoo eval --config C:\demo\new-eval\promptfooconfig.yaml
```

The default replay export measures the gates against saved outputs. To evaluate
fresh model outputs, reuse the Python assertion with separately authorized
providers and reviewed fixtures; that is outside the offline CLI workflow.
Python callers can use `evaluate_case(case)` for the recorded value or
`evaluate_case(case, output)` for an override, plus `run_suite(payload)`,
`get_assert(output, context)` and
`export_promptfoo(payload, output_dir)`.
