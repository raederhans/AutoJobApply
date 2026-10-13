# P0-C/D: attended recovery assessment and bound application consistency

## Baseline and delivery boundary

Continue draft PR #21 from `8287c982aca24553cc3f5e94182c21ec216833e3`.
Latest main read at the start is `64d2ba6a76457abe8648e8b5cf34665d206be6a3`.
Career M1-M4, the canary fix and P0-A/B1/B2/B3 remain unchanged.

C adds guarded recovery assessment to the existing attended-application command;
D adds explicit source bindings checked by its existing checkpoint/claim/intent
path. Neither action drives a browser. The attending Host still owns observation,
request review and subsequent input. This is not an unattended recovery daemon,
a new application executor, an automatic final-review page parser, or a universal
semantic proof that every application is correct.

## Evidence and minimal production changes

Source inspection found that ordinary `resume` checked current ownership, lease
and frozen materials but returned without a fresh page inspection. It also used
the attended JSON submit flag without first joining the durable attempt's flag.
A stale projection must not clear a true durable submission latch. These are
source-audited gaps; no unperformed baseline runtime comparison is claimed.
The current B3 Host already reports waiting, missing facts, verified/partial
progress and unknown outcomes. C consumes these actual structured envelopes
instead of treating model prose, silence or every empty option list as failure.

Existing material/provenance checks remain in force, but there was no attended
mapping from a specific observed record/question to a current profile source,
reviewed writing revision or frozen attachment carried through final review.
D supplies that mapping without accepting caller-provided expected values.

| Location | Actual change |
| --- | --- |
| `src/applypilot/apply/attended_application.py` | Add `recover` and `bind-consistency` to the existing transactional command. Join irreversible submission evidence before resuming; require fresh Host form/session/epoch/page checks for recovery/binding; invalidate the old prepared checkpoint after recovery; enforce bound consistency at checkpoint, claim and submit-intent. |
| `src/applypilot/apply/attended_recovery.py` | Validate original Host responses and fresh `inspect` envelopes; distinguish pre-input no-effect, observation ambiguity, waiting, missing facts and unknown effects. Project a bounded, value-free completion history through the existing `ApplicationSupervisorLoop`. Read existing Episode barriers without creating a false CDP lease or mutating another runtime's Episode. |
| `src/applypilot/apply/application_consistency.py` | Resolve explicit references through current profile paths, the existing fact resolver, existing reviewed writing artifacts and frozen materials. Check question/record identity, value/selection, review projections and attachment purpose/revision. Store source references and digests, not expected plaintext values. |
| `tests/test_application_consistency.py` | Deterministic source/field/review/attachment checks plus existing fact and writing-artifact integration. |
| `tests/test_attended_recovery.py` | Host identity/outcome validation, existing Supervisor behavior, waiting bounds, historic progress and read-only Episode barriers. |
| `tests/test_attended_recovery_consistency.py` | Real SQLite attended-ledger transitions, fresh bindings, process reconnection, mandatory bound audits and monotone submit/unknown-effect behavior. |

No SubmissionGate implementation, manifest admission, applicant fact authority,
material freeze implementation, model configuration, browser controller, concurrency
limit or receipt-reconciliation rule is replaced. The attended seam now respects
more of the existing durable submission evidence rather than weakening it.

## C: how to use the existing entry

Run requests through the existing source-installed command:

```powershell
applypilot --workspace <workspace> attended-application --db <jobs.db> --request-file <reviewed-request.json>
```

Use the same exact `job_url`, `attempt_id`, `host_session_id` and `tab_id` as the
existing attempt. A `recover` request additionally contains:

- `source: "attending_host"`, fresh timezone-aware `observed_at`, and nonempty
  `evidence_refs`, using the existing observation contract;
- `host_token_epoch` from the current Host binding;
- `feedback`: the original `host.execute()` response, not a worker's summary;
- `inspection`: a newly obtained `host.inspect()` result from that original tab.

A stopped Host's `outcome_unknown` can be recorded without an impossible new
inspection. It latches uncertainty, removes the old ready checkpoint and blocks
new checkpoint/claim transitions. A later alleged success cannot clear it. This
requires manual effect reconciliation, not another click or silent new session.

A true durable attempt submit flag, uncertain/applying job status or unresolved
gate state wins over stale attended JSON. After submission only receipt
reconciliation is available; recovery cannot renew the attempt with a false
submit flag. Claimed checkpoints cannot be reopened by recovery. Changed
materials, expired leases, another job/query identity, a different Host/tab/epoch,
or an unresolved existing Episode also prevent continuation.

For pre-submit feedback, C records up to 16 distinct Host completion summaries.
Reading one response repeatedly is not another action. Missing facts stay distinct
from ambiguous observation and from normal waiting. Three distinct waiting
completions without usable fresh options park for review; arrived options can
produce a reviewed next step, never automatic replay. Fresh ambiguity/coverage
loss overrides an old waiting claim. Historical verified field references remain
explicitly historical, not a claim that current applicant facts still match.

The existing Supervisor is reused as a pure diagnostic projection over those
completions, not impersonated as the original live model turn. No `steer`, model
call or interrupt executor is invoked here. Existing Episode state is consulted
read-only; an IAB attempt without a real Episode is not given invented CDP lease
metadata. Legacy `resume` remains a lease/state operation and explicitly returns
`fresh_host_inspection_before_input`, with no browser authority. A successful
recovery assessment also never grants browser or submission authority.

## D: explicit source mapping, then mandatory reuse

Before claiming a gate, use `action: "bind-consistency"` with the same fresh
Host inspection/observation metadata and `host_token_epoch`. `bindings` contains
one to 128 mappings like:

```json
{"field_key":"<current observed key>","source":{"kind":"profile","path":["personal","email"]}}
```

Other supported references are:

```json
{"kind":"profile","path":["education",0,"institution"]}
{"kind":"fact","fact_ref":"<existing current fact>","scope":"global:*"}
{"kind":"writing","path":"<absolute local path to artifact.json>","revision_id":"<existing reviewed revision>"}
{"kind":"material","material_kind":"resume"}
```

Profile paths are limited to ordinary contact and existing education/employment/
project sources. Current fact references use the existing exact-scope, expiry and
supersession resolver; high-sensitivity facts stay outside this generic checker.
No career-learning assessment, arbitrary expected value or webpage claim becomes
a fact. Typed project/education/employment sources require matching observed
record purposes; unknown or conflicting record semantics are rejected rather
than inferred from keyword similarity to a candidate's history.

Writing references retain the existing exact job/revision/text/context binding,
reviewed-draft status, no-authority flags and review contract. Current deterministic
writing checks are rerun. Application answers must match the observed question,
instructions and constraints. This verifies version/content consistency and the
existing review record, not newly proven entailment of arbitrary prose. It does
not promote generated writing into the current applicant fact registry.

Once bound, every later checkpoint, claim and submit-intent must include a fresh
`snapshot.consistency` projection. Omission cannot be a passing check. The
projection contains `form_state` and, when relevant, `review_entries` and
`attachments`. Editable values and simultaneous final-review values must both
match. A disappeared field requires explicit Host review evidence, not an empty
list interpreted as success. Bound checks cannot be dropped within the attempt.

Final-review entry shape:

```json
{"claim_id":"<contract claim>","value":"<actually observed text>","question_digest":"<bound question>","record_kind":"<observed matching record kind>"}
```

Accepted-attachment entry shape:

```json
{"claim_id":"<contract claim>","purpose":"resume","material_sha256":"<frozen material SHA>","size":123,"acceptance_marker":"attachment_card"}
```

These are trusted attending-Host projections, not authenticated server evidence.
The Host must observe the review/accepted attachment and retain the cited evidence;
copying a desired value or digest is not independent verification. Existing upload
checkpoints, file identity and pre-submit checks remain required. A file-picker
selection is not attachment acceptance. This slice does not introduce automatic
review-page extraction or prove the remote server's uploaded bytes.

A D report is `consistent` only for its explicit bindings and never claims whole
application completeness, semantic entailment, or submission permission. The
report and persistent contract exclude plaintext candidate values; artifact paths
and source references remain private workspace metadata, not shared experience.
Local request files/Host observations still contain personal data under the
existing private-workspace policy. Unbound legacy attempts retain their original
audits and are not falsely advertised as having passed these new D checks.

## Actual local validation and CI boundary

The current container could not resolve GitHub for a clone. Focused sources were
retrieved with the connected GitHub API. The original full attended source was
verified against blob `9b3f1a1e77fae0344efdd6bc55ff9e4fad2c12e2` before editing.
No full local checkout is claimed.

Executed locally on Node 22 / Python 3.13:

```text
PYTHONPATH=src python -m pytest -q tests/test_application_consistency.py tests/test_attended_recovery.py -k 'not supervisor and not writing and not fact_'
60 passed, 21 deselected
```

The source-independent subset checks real pure logic and SQLite Episode lookup;
it does not run the full repository's Supervisor/fact/writing dependencies.
Those 21 selected-out cases and the separately authored attended-ledger integration
suite require the full-repository CI. The earlier 41- and 56-case local runs overlap
this subset and must not be added to it. Python compilation and focused diff
checks passed. No local full-suite, Ruff, Windows, packaging, browser, real model
or employer test result is claimed for C/D.

New-head CI status and its exact merge preview will be recorded in the PR
conversation. The green B2/B3 run is historical and cannot verify these changes.
All new inputs are fictional controlled fixtures; no employer application is sent,
no personal resume or runtime database is committed, and no earlier test or CI
threshold is relaxed.

## Remaining scope

The supported C/D entry points above are implemented. Automatic repair, cross-session
browser migration, implicit final-review extraction, unknown record semantics,
new languages/aliases, and unbound whole-application semantic contradictions are
not claimed solved. P0-E and P1 still need same-model interactive ablations,
cold/warm unseen-employer holdouts, production recovery rate and cost/latency
measurement. No release, local installation update or merge to main occurs here.
