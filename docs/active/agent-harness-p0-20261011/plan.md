# Application-agent capability plan, 2026-10-11

## Baseline and scope

Baseline: `15273c56be8b5a0ed77944de9ac11e69fb4c7b3e`, the merge of PR #19.
The current main branch and PR metadata were read through the connected GitHub API.
PR #19's 53 changed paths were inspected: career M1-M4 is the new baseline, not
an unimplemented task or a substitute for application-agent work.

The container cannot clone/download GitHub. Focused source files were retrieved
through the connector and copied locally, then verified against Git blob hashes:

| File | Baseline Git blob SHA |
| --- | --- |
| `scripts/browser-form-state.mjs` | `9ccd177a32066cc32888b261fe661bb131da0097` |
| `scripts/browser-field-batch.mjs` | `f929560c0d56aaac0926374873af404ec4952cdb` |
| `scripts/browser-host-group.mjs` | `f1f2d273f7d474209be7fbee746afc640a44f566` |
| `scripts/browser-field-batch.test.mjs` | `232e352a678a33a0b80304fd89cc61543346f894` |

This is a focused audit and implementation, not a full repository clone or a
live model/application benchmark. No production database, private applicant
material, browser credentials or employer submissions are used.

## Evidence register

- **E1, reproduced at baseline:** `browser-field-batch.mjs` compares structural
  and value changes but misses changing requiredness, question semantics,
  validation of other fields, and observation coverage. In the new deterministic
  suite, 18 of 21 tests fail before the fix; the four existing batch tests pass.
  In particular, filling B can invalidate A without changing its value, and the
  baseline batch reports `verified`. This is false batch verification, not proof
  that SubmissionGate would admit an application.
- **E2, reproduced at baseline:** a requirement, record purpose, validation or
  parsed value may change between one readback and the next control preflight.
  `operateObservedControl` refreshes identity/options, but the batch does not get
  to validate the complete dependency state before that next input. Checking only
  after the write stops too late. The new suite checks the actual writes list.
- **E3, source gap:** `browser-host-group.mjs` has a shared two-slot semaphore,
  same-job reservation and per-host metrics from `peek`, but no separate group
  slot-wait/host-service attribution. `docs/attended-runtime-results.md` records
  an earlier live sample with an 862 ms maximum four-field action and 25.855 s
  maximum operator request wait. Those are different boundaries and small-sample
  historical observations, not a current throughput SLA.
- **E3a, existing code additionally checked:**
  `scripts/browser-observation-feedback.mjs::BridgeMetrics` already records
  queue/action/observation/host-service times, payload sizes and readback reuse;
  `canReuseActionReadback` already supports a bounded same-action readback ticket.
  P0-A adds only the distinct group-slot boundary, not another host metrics
  subsystem. Its durations overlap the existing host queue span and must not
  be added to that span.
- **E4, already implemented:** the current observer has `group_key`, `group`,
  `application_question.section_path`, question provenance/completeness,
  selected-display evidence, exact custom-option selection, structural deltas,
  and coverage metadata. Do not rebuild them or call the product screenshot-only.
  `docs/visual-worker-bridge.md` still documents custom multiselect and frame/
  closed-shadow limits. Historical label issues may already have been repaired.
- **E5, already implemented with limited scope:** `recipe_experience.py` stores
  value-free routine structure with candidate/validated/invalidated lifecycle;
  `provider_recipe_fast_path.py` separates cache hits from execution admission.
  This is not demonstrated learning of arbitrary long-tail interactions.
- **E6, already implemented with integration/evidence limits:** Supervisor,
  Episode, recovery admission, handoff, facts/provenance, frozen materials and
  exact-job receipt reconciliation already exist. Their presence is not proof
  that every current IAB path invokes them or that a real stalled task recovers.
- **E7, evaluation boundary:** `runtime_evaluation.py` is recorded/scripted
  conformance replay. `docs/quality-evaluation.md` describes saved-output gates.
  `docs/concurrency-validation-20261009.md` distinguishes real IAB local fixtures
  from model throughput, and its controlled runs bypassed model decisions.
  A same-model, same-tools skill/harness/one-change comparison remains unmeasured.

- **E8, source-audited residual:** in `scripts/visual-bridge-host.mjs`, the
  delayed full-refresh check for `lastBatchResult` compares structural state and
  selected values/options but not the new question/validation dependency set.
  P0-A's change is within the batch and the next pre-input observation; it does
  not yet unify that later host refresh, final review or SubmissionGate checks.
  Reuse the dependency comparison there in the next batch, with a host-level
  delayed-refresh regression. No end-to-end false-ready elimination is claimed.

## P0 batches: concrete changes and acceptance

| Batch / problem | Existing locations to reuse | Minimum change | Acceptance and status |
| --- | --- | --- | --- |
| **P0-A: stale dependencies and false batch verification (directions 1,2,6)**. E1/E2. | `scripts/browser-form-state.mjs::operateObservedControl`, `scripts/browser-field-batch.mjs::operateFieldBatch`; existing `structureChanges`, question/coverage observations. | Add a trusted host-only pre-input validation callback to the existing freshly observed form, and a bounded value-free dependency delta in the batch. Check before input and after readback. Preserve current ordinary-field scope and returned observation. | Implement first. Changed requiredness/question section/constraints/other-field validation/coverage parks; preflight drift causes zero further writes; own validation clearing is allowed; stable two-field batch still uses four existing reads. No extra full-page observation, new controller, new submission authority, or unknown-effect retry. |
| **P0-A: coordination timing (direction 5)**. E3. | `scripts/browser-host-group.mjs`; existing shared slots and `browser-observation-feedback.mjs::BridgeMetrics`; `src/applypilot/apply/performance_attribution.py`. | Record reviewed-request admission-to-slot wait and host execute duration separately, plus bounded aggregate counts/highwater. Read-only metrics, no new execution pump. | Implement first. Deterministic overlapping-call tests prove two-slot cap, queued duplicate rejection, immediate slot reuse, attribution boundaries and timing failure isolation. Do not call host duration pure browser time, or its sum batch wall time. |
| **P0-B: explicit semantic uncertainty and bounded subgoals (directions 1,2)**. E4. | `browser-form-state.mjs::observeForm`, `application_question`, `control_descriptors.py`, `scripts/visual-bridge-host.mjs`, existing open/search/select control operations. | Extend existing descriptors with record scope and explicit identified/ambiguous/not-expanded/not-covered diagnostics. First close E8 in the existing delayed-refresh check using the same dependency comparison, then single school selection and one repeated experience record. Use current exact options and selected-display proof, not saved coordinates. | Next. Real local DOM cases: two identically named dates in distinct records; nested groups; same-name schools with ambiguous options; collapsed menu; missing frame coverage; resume vs cover upload. Ambiguity must not become an authorized guessed mapping. Test final selected record/value, not a click flag. Selectively refresh only where supported; retain full review before submission. |
| **P0-C: recovery distinctions and continuity (direction 3)**. E2/E6. | `application_supervisor_loop.py`, `application_episode.py`, `failure_taxonomy.py`, `recovery_execution.py`, current visual host pause/resume and attended ledger. | Trace the current IAB call path into existing recovery owners. Map observed incompleteness, proven no-effect, bounded normal waiting and `needs_fact` to existing categories; add categories only if no existing equivalent. Retain confirmed steps and failed alternatives. | Next. Fault injection for delayed options, lost focus, parser overwrite, missing fact, restart and lost response after submit intent. Fresh same-job/tab/epoch/material check before continuation. Thinking/tool-running silence alone must not trigger repeated replans. Any submitted/unknown effect remains receipt-only, never replayed. Report recovery rate and extra reads, including failed attempts. |
| **P0-D: application consistency (direction 6)**. E4/E6. | `application_facts.py`, `answer_provenance.py`, `application_questions.py`, `material_readiness.py`, `attended_application.py`, upload checkpoints and current final-review validator. | Bind observed record purpose and question revision to existing fact/material evidence. Add explicit mismatch diagnostics for projects represented as employment, parsed contacts reverting, conflicting date/education/answer/attachment revisions. | Next. Synthetic profile/resume/short-answer/form/review fixtures with agreed ground truth. Detect contradictions without rewriting facts, inferring employment from prose, or promoting career-learning assessments into applicant facts. Reaudit changed dependencies; unchanged validated material remains reusable under existing policy. |
| **P0-E: actual harness-value evaluation (direction 7)**. E7. | Existing fixture runners, `runtime_evaluation.py`, `full_stack_no_submit_benchmark.py`, real control adapters, provenance and timing spans. | Add a separately labelled interactive evaluation lane, not a rebranding of scripted replay. Run base skill, current harness and exactly one change with the same model/settings/tools/necessary safety policy, isolated equivalent initial states and independent final-state assertions. | Next. Store model/tool/config/baseline hashes, task/variant IDs, order and termination reason. Count autonomous/assisted completion, false-ready, substantive errors, recoverable-case recovery, intervention time, total elapsed and available token/cost data, including failed attempts. Missing cost is null, not zero. Repeated paired runs; no employer submissions. Local deterministic tests in P0-A are NOT this benchmark. |

## P1 batches: build only after P0 evidence

| Batch / problem | Existing locations to reuse | Minimum change | Acceptance |
| --- | --- | --- | --- |
| **P1-A: procedural and failure experience (direction 4)**. E5. | `recipe_experience.py`, `recipe_cache.py`, `provider_recipe_shadow.py`, Episode/event log, existing fact store. | Keep progress, applicant facts, structural/procedural templates and failure hypotheses separate. Extend bounded candidate records with non-sensitive applicability, validation evidence and negative conditions. Promotion is evidence-driven; no default capability flag flip and no free-form private transcript store. | Cold/warm paired runs with memory frozen during holdout; split by employer and page family, not only URL. Demonstrate fewer failed alternatives/round trips without higher false-ready. Invalidation on structural/policy mismatch; malicious page text cannot grant trust; never store applicant values, credentials, raw screenshots or CV text as general experience. |
| **P1-B: selective observation and broader bounded execution (directions 1,2,5)**. E4. | Existing form deltas, structure/option fingerprints, field batches, host observations and new timing. | Expand only frequently failing controlled subgoals. Use dependent-field invalidation rather than broad unconditional re-reading, but do not discard necessary final-page proof. | Same initial state and model: fewer observations/coordinator turns with no lost dependency changes. Hold out nesting/layout/control variants. Unknown unsupported scope remains explicit. No claim to closed-shadow/frame coverage without actual tests. |
| **P1-C: full latency attribution and routing (direction 5)**. E3/E7. | `performance_attribution.py`, `agent_output.py`, `browser_worker.py`, browser group metrics, current host metrics. | Distinguish measured model/first-tool, context usage, operator coordination, group queue, host service, page loading and recovery. Join by existing private attempt/turn IDs; expose aggregate value-free summaries. | No double-counting nested/parallel spans. Missing provider timing stays unavailable. A/B/BA shows gains in matched tasks before changing model/effort or concurrency. Do not equate cached input tokens with billable cost or CLI working set with whole-app RAM. |
| **P1-D: domain and cross-step holdout expansion (directions 4,6,7)**. E7. | Existing answer/fact/material/receipt fixtures and P0-E interactive lane. | Add unseen employers, repeated rows, asynchronous parsing and validated manual handoff, then separate longer-run recovery cohorts. | Publish denominator/uncertainty and intervention burden; distinguish supported-scope capability from end-to-end completion. Safe parking does not count as autonomous success on a completable task. |

## Invariants for every batch

1. Applicant facts and material lineage retain their existing owners. Learning
   progress and model guesses are not new fact authority.
2. SubmissionGate, authorized exact-job binding and the irreversible submit-intent
   latch are unchanged. Unknown effects remain stopped for reconciliation.
3. No CAPTCHA bypass, account/cookie transfer, automatic browser request approval,
   standing authorization expansion or real-employer repeated submissions.
4. Keep new diagnostic persistence value-free and bounded. Field IDs in transient
   host replies retain existing private-workspace handling; no personal values
   or raw validation text are copied into general experience or timing metrics.
5. Report separately: implemented code, local tests, CI checks, actual DOM tests,
   live model results and unverified scope. Do not substitute one for another.
