# P0-B1: delayed Host refresh invalidation

## Current baseline and continuation

Latest main inspected: `64d2ba6a76457abe8648e8b5cf34665d206be6a3`.
It contains career M1-M4 / PR #19 (`15273c56`) and the Canary cleanup test fix
from PR #20. Existing draft PR #21 / P0-A (`aa47ee2`) is continued, not replaced.
Its exact-head CI run `38124888235` completed successfully before this batch.
The updated branch includes main's Canary test blob
`0ebd401fbcb7013b2c6263bbb8a76e980d5928a4`; no main-branch write or PR merge is performed.

This report supplements `plan.md` and closes its E8 delayed-refresh seam only.
It does not mark all of P0-B, or all seven capability directions, complete.

## Reproduction and minimum production change

The old `browserAdapter.observe` checked structure and the values/options of
completed batch fields, but did not reuse the batch's requirements, record-purpose,
validation, coverage and other-field dependency comparison. After the batch's
last immediate readback, a final full refresh could therefore attach `verified`
to a form whose selected value was unchanged but whose validation was rejected,
whose school question changed record purpose, or whose non-batch field was
silently overwritten by parsing.

- `scripts/browser-field-batch.mjs`: expose `fieldBatchChanges`, factoring the
  existing pre-input and post-readback comparison without expanding its policy.
  No new observer, cache, controller, dependency schema or authority is introduced.
- `scripts/visual-bridge-host.mjs`: use that same comparison on the already-read
  final form, with no active-field exemption. Retain the existing exact completed
  field checks. On drift, preserve completed steps and park the result with
  bounded structure/dependency diagnostics. Observation never repairs or retries.
- `scripts/browser-host-refresh-dependencies.test.mjs`: Host-boundary regressions
  for both DOM-CUA and Playwright observation modes, plus real filesystem queue
  and unknown-effect stop cases.
- `tests/test_agent_harness_batch_p0.py`: include the new suite in the existing
  Node-enabled browser CI tier. Only this suite enables Node module mocks.

No single-control persistence rule, SubmissionGate, submit-intent latch, fact
resolver, material binding, model configuration, concurrency limit or approval
policy is changed. Existing partial-parked results are never promoted by a later
settled page. The receipt-only boundary after an uncertain submission is untouched.

## Validation actually performed in this continuation

The container cannot clone/download GitHub. Focused files were retrieved through
the connected API and copied with exact Git blob verification before editing:

| Production source at PR #21 baseline | Verified Git blob |
| --- | --- |
| `scripts/visual-bridge-host.mjs` | `981a4c4ef6ecd42671936534d745097bc78ad67c` |
| `scripts/browser-field-batch.mjs` | `7d2f7619604b5331923f750209e6b2c4aeb3f514` |
| `scripts/browser-observation-feedback.mjs` (unchanged) | `da57376e9b87d58f3088ed329a7fbd4496f267c9` |

Against those original production modules, the new suite produced **14 passed /
32 failed** out of 46 tests. Failures assert false retained batch verification,
not a model benchmark or proof that SubmissionGate admitted an application.
After the two production changes: **46 passed / 0 failed / 0 skipped**.
The pytest wrapper also passed for this one suite; that reruns the same 46 cases
and is not additional behavioral coverage. Node syntax checks passed.

The new suite explicitly mocks `browser-form-state.mjs`, supplying deterministic
observations and writes. It runs the real Host, batch and feedback modules;
queue cases use real temporary files. It does NOT validate DOM extraction,
real Playwright/IAB actions, Windows desktop control or model decision quality.
The local module-resolution placeholder is not a repository change. No private
candidate material or real employer submission was used. The full repository
suite and platform checks require the new exact-head CI; the earlier green run
must not be presented as verification of these new changes.

Stable two-field adapter case: **6 form reads before and after the change**:
one initial read, two preflight/readback pairs and the existing final full read.
This proves no extra form observation at this seam, not a measured speedup.
Transient diagnostic arrays are capped at 40 keys; a 68-field test verifies
counts/truncation without copying question or validation text into diagnostics.
An already parked one-step partial batch remains parked; a lost final read after
two writes produces `outcome_unknown`, a stopped Host, and no replay.

## Revised P0/P1 delivery status and next acceptance

Use the full evidence register and locations in `plan.md`; this table updates
status and narrows the next executable slices.

| Direction | Current batch status | Next minimum slice / acceptance |
| --- | --- | --- |
| 1. Page semantics | Existing question/group/coverage data reused in P0-A/B1; delayed semantic drift now invalidates the batch. | P0-B2: explicit ambiguity/not-expanded/not-covered diagnostics using existing descriptors. Actual local DOM tests for repeated record dates, nested groups, collapsed/ambiguous school choices and attachment purpose. Do not claim new frame coverage. |
| 2. Bounded execution | P0-A guards preflight/readback; B1 closes final Host refresh. Existing ordinary 1-4-field scope stays unchanged. | P0-B3: one school-selection subgoal and one repeated-record subgoal with independently checked selected record/value; invalidate affected state after parsing/country/row changes. No guessed options. |
| 3. Recovery | Existing Supervisor/Episode/Host owners retained; B1 tests partial parking, stale observation rejection and unknown-effect stop at the Host seam. | P0-C: map actual IAB events to existing failure categories and trace the execution path. Fault-inject normal waiting, no effect, incomplete observation, missing fact and restart; revalidate identity/material/submit state before continuation. |
| 4. Experience | Existing value-free candidate/validated/invalidated store is unchanged. | P1-A: validate applicability and negative conditions before procedural reuse; freeze memory for unseen-employer/page-family holdouts, with cold/warm cohorts. No applicant values or free-form private transcripts. |
| 5. Speed | P0-A's group-slot attribution retained; B1 adds zero form reads. | P1-C: connect existing model/host/loading/recovery spans without summing nested or parallel time. Missing timing/cost stays unavailable. Measure matched task gains before model/worker changes. |
| 6. Domain consistency | B1 catches delayed record-purpose and parser-overwrite changes, but does not establish factual consistency of a whole application. | P0-D: existing fact/material evidence versus repeated employment/project/education records and final review. Controlled contradictions must be detected without inventing or rewriting applicant facts. |
| 7. Harness value | New tests establish a narrowly controlled Host regression; they are not the requested skill/harness ablation. | P0-E: same model/tools/safety policy, isolated equivalent initial state, independent terminal assertions; include failures, false-ready, recovery and user-intervention burden. Do not substitute scripted outputs for interactive runs. |

P0-B2/B3, P0-C/D/E and the P1 batches remain pending. Their tests should target
production observations/actions on a controlled DOM before any live model claim.
No recruitment outcome, throughput or end-to-end readiness improvement is claimed
from this Host-boundary suite alone.
