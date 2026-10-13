# P1-C0: measurement contract and empirical pilot preparation

Base: PR #21 `6f2a116c36846dd8a8d17e1ec7d427089011ba63`.
This batch prepares measurement and an experiment, and makes no model-performance
claim. It does not change model routing, worker counts, recovery authority, user
installation, or submission policy. All validation uses fictional fixtures; no
LLM request, credential/profile access, or paid experiment is required.

## Contract

`harness_measurement.py` is independent of model clients and browser launchers.
E wraps its existing `environment` and `decide`, and attaches a versioned
`measurement` object to each trial. Exceptions retain observed call duration and
their original control flow. Model/DOM/error payloads are not copied into telemetry.
The original evaluation fields remain available; the integration lane separately
owns its evaluation schema and manual-intervention fields.

| Component | Boundary/source | Scope and remaining unknowns |
| --- | --- | --- |
| decision | Before/after E's `decide` | Inclusive subprocess startup, transport, client call and envelope parsing; failed calls included. |
| model_client | Existing `provider_ms` around `LLMClient.chat_once` | Client-observed network/provider wait/response work; **not server inference time**. A failed response without this timer stays unknown. |
| model_service | No server-side boundary in the existing client | Unknown for live calls. Reference drivers made no model call (`not_occurred`). |
| encoding | Existing `context_encode_ms` boundary per turn | JSON construction/serialization measured there; not tokenization or complete worker transport encoding. The final span is retained even on serialization failure or a context-limit exit before the turn record. |
| coordination_wait | Explicit coordinator-owned interval required | Unknown in current E. Neither decision residuals nor Host queue age establish pure coordination time. |
| execution_slot_wait | Explicit attempt/turn-bound slot interval required | Unknown in current E, which does not use browser-host-group. Group lifetime aggregates remain separate. |
| browser | Every E `observe`/`act` RPC | Includes the initial observation, failed calls, transport, action/readback and any nested browser waits. Not pure browser execution. |
| page_load | Explicit load boundary required | Unknown. `init` is environment setup, not a substitute for page-load timing. |
| recovery | Explicit recovery-owner interval required | Unknown. Oracle `recovery_triggered` is an outcome, not a duration. |
| environment_setup / oracle / cleanup | Each corresponding E environment call | Separate inclusive diagnostic spans; elapsed trial still includes all of them. |

Every metric has `status`, `value`, `observed_subtotal`, `observed_samples`,
`unknown_samples`, `unit`, and `source`. `measured` means all expected supplied
samples were numeric; `partial` keeps the observed subtotal but sets the complete
value to null; `unknown` has no measured value; `not_occurred` is an explicitly
observed absence, not a default for missing data. A measured zero remains zero.
Malformed/negative/nonfinite/bool counters are unavailable, never silently zero.
Empty legacy windows do not prove a component did not occur.

Wrapped call durations use one Python `perf_counter_ns` clock and local endpoint
differences. Encoding retains E's `monotonic` duration; externally supplied
durations retain unknown clock alignment. Event clock provenance is explicit and
no absolute timestamps are joined. `private_trace.attempt_id` equals E's existing `run_id`; event `turn`
equals the existing zero-based model decision index, or null before the first
decision. Events contain only an allowlisted component/source, numeric duration,
turn and inclusive relationship. Normalized usage and conditional cost estimates
also join by turn, with failed-response entries retained as unknown. Storage is capped at 256 events; dropped events
make component totals incomplete. Raw request IDs, applicant answers, endpoint
URLs, browser bindings and credentials are not needed for these measurements.

`record_external(component, duration_ms, attempt_id=..., turn=..., boundary_id=...)`
is a trusted boundary-owner seam for coordination, slot, load and recovery events.
It rejects another attempt, invalid turns and duplicate boundaries. The boundary
ID is used only for local deduplication and is not serialized. The owner must
measure the interval and bind it to the actual attempt; an aggregate metric
snapshot is not acceptable evidence for a fabricated event. B0 does not need to
change its recovery state machine to use this seam in a later integration.

Durations are **inclusive work per component**, never additive wall-clock shares.
Decision contains model-client work; browser/Host spans can contain load,
observation, slot or recovery waits. Parentage is causal and does not prove exact
temporal containment. Parallel work may exceed elapsed wall time. No component
sum, residual subtraction, exclusive time, percentage-of-elapsed attribution or
critical path is derived. Those require aligned start/end intervals and a union
of overlapping intervals, not a sum of child durations. E's `elapsed_ms` remains
the trial wall-clock result and must not be added to any span.

`legacy_metric_views` reuses existing BridgeMetrics, browser-host-group and
`performance_attribution.normalize_attribution`:

- Bridge percentiles retain their bounded recent-window scope. Queue age uses
  request creation/Host wall timestamps and can overlap group waits; it is not
  pure coordinator time. Percentiles cannot be converted into totals.
- Group slot/execute totals retain their lifetime/parallel-work scope. If an
  unavailable sample exists, the complete total is unknown; the observed subtotal
  is separately labelled. Zero samples plus unavailable samples is not zero time.
- Legacy performance spans keep their nested advisory scope, with private route
  dimensions stripped. Existing aggregated spans cannot be joined to individual
  turns after the fact.

`measurements.json` groups by evaluation schema, driver, split, arm and cohort;
it includes observed/unknown status denominators and omits private trace IDs.
`trials.jsonl` remains the private correlation artifact. Do not publish raw
production trial archives. New measurement/planning source files are included in
E's source manifest hash list. Public totals remain unknown if any trial is
partial, while observed subtotals include the measured work inside partial trials.

## Token and money provenance

The adapter supports compatible prompt/input and completion/output usage names.
Input and output are totals. Cached and cache-write input are subsets of input;
reasoning output is a subset of output. `total` is input + output only. Missing
subsets remain unknown even when totals are known. Conflicting aliases/totals or
impossible subset counts invalidate the affected metric. Context bytes are never
converted into guessed tokens. Reference-driver usage is explicitly fixture
evidence and cannot produce a reported or estimated provider cost.

`estimated_usd` is conditional on caller-supplied text input/output/cache rates
and supported provider counters, and is labelled as an estimate. Missing usage
or cache evidence, any missing response, missing required rates, or explicitly
reported unsupported cache-write/audio billing keeps the estimate unknown.
This preserves the existing three-rate estimator for older compatible usage
responses; omitted cache-write details remain an unknown metric, not an asserted
zero. A changed provider billing contract must be checked before using that
estimate. Reasoning tokens are already in output and are not charged again.
The combined integration additionally rejects unmodeled fields at the usage root
or inside input/output details when estimating cost. Known token totals are still
reported, but a newly introduced category cannot silently inherit a text rate.
This is deliberately conservative: extra provider metadata, including prediction
details not modeled by this adapter, also requires an explicit contract update
before the estimator returns a monetary value.
`reported_usd` stays unknown because the client has no per-attempt billed-cost
source. Organization/day billing totals must not be assigned to a trial.

## Offline plan and paired experiment

The JSON template deliberately leaves model/binding, prices, price source/date,
input-token assumptions, provider output-cap semantics and spending proposal
unknown. Its CLI only reads repository assets and writes a new explicit output
directory; it cannot invoke an LLM or inspect credentials:

```powershell
$env:PYTHONPATH = 'src'
python -m applypilot.apply.harness_experiment_plan --root . --template docs/active/agent-harness-p0-20261011/p1-c0-budget-template.json --output ..\c0-plan
```

The planner reuses `tasks`, `validate_splits`, `schedule` and the existing source
manifest. With one repetition: 7 held-out tasks × 3 arms × 2 cohorts = 42 trials,
plus 7 × 2 = 14 training trials. At 8 turns each, full worst-case coverage needs
448 decisions, including training; maximum declared output is 458,752 tokens.
A 128-decision cap cannot guarantee a complete cohort. Retain all failures and
stop after the configured cap; never drop incomplete cells to claim a gain.

The planner estimates **a conditional budget**, not a hard money ceiling. E
currently enforces decision count, max output request, context bytes and trial
deadline, but not a provider-token input cap or actual dollar-spend cap. An input
token assumption, byte size, timeout or local cancellation cannot prove the final
bill. Timeout requests may still be billed. No amount in this template grants
authorization to make calls. A real spending ceiling needs an execution/provider
enforcement mechanism in addition to explicit user authorization.

For a later authorized pilot:

1. Select one configured endpoint/model and pin temperature, effort, provider
   output-cap semantics and supported usage format. No fallback or mixed driver
   modes. Record the private endpoint binding digest, source/tool/safety hashes,
   browser version, initial-state digest and available model-revision identity.
   Unknown provider revisions stay unknown.
2. Set a reviewed budget using current dated official pricing, explicit input
   assumptions and a spending limit/enforcement decision. Keep secrets in the
   already configured execution environment, out of the template and reports.
   Map template `max_output_tokens` to existing E `--max-tokens`.
3. Train only on the existing `lab-a` / `fieldset` split, then freeze the existing
   ProcedureExperienceStore. All cold/warm arms use the same frozen memory hash;
   holdout cannot write it. E currently has no frozen-memory import option, so
   `--train` is needed for meaningful learned-advice exposure in this CLI.
4. Execute the existing reproducible six-cell-per-task schedule on fresh browser
   contexts and identical fictional facts. Here **warm means procedure advice**,
   not a warm browser/session or a guaranteed provider prompt-cache hit. Provider
   caching is a separate nuisance variable; record cached tokens and randomize
   cell order. Report actual advice hits, including zero-hit warm trials.
5. Keep existing within-cohort arm comparisons. New
   `cold-warm-comparisons.json` adds warm-minus-cold within each arm, rejects
   duplicate/missing/non-equivalent cells and mixed schemas, and preserves
   unknown elapsed/token/cost/intervention pairs. It includes recovery and advice
   denominators. Basic/current arms are negative controls for order/cache effects.
6. Report autonomous and assisted outcomes, false-ready, correct handoff,
   recovery, intervention, elapsed and cost separately. Use task-cluster analysis
   and existing bootstrap evidence where applicable; repeats of one synthetic
   employer are not independent employers. The cold/warm point contrasts do not
   establish statistical significance or operational ATS generalization. E still
   excludes C/D orchestration, uploads, final review and submission.

## Official evidence adopted

- [Python 3.11 clocks](https://docs.python.org/3.11/library/time.html): elapsed
  intervals use a consistent monotonic performance clock with an undefined origin;
  Python clock sharing does not establish compatibility with browser clocks.
- [OpenTelemetry traces](https://opentelemetry.io/docs/concepts/signals/traces/)
  and [tracing API](https://opentelemetry.io/docs/specs/otel/trace/api/): parent/child
  relationships are not an additive accounting model.
- [OpenAI usage types](https://github.com/openai/openai-python/blob/main/src/openai/types/completion_usage.py),
  [Responses usage](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_usage.py),
  [reasoning](https://developers.openai.com/api/docs/guides/reasoning) and
  [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching):
  token totals/subsets and possible billing categories require explicit treatment.
- [Costs API](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs):
  aggregate provider billing is not automatically a per-trial invoice.

## Local validation (2026-10-12)

- System Python 3.12.10, isolated `APPLYPILOT_DIR`, and a fresh task-local
  `--basetemp`: `python -m pytest tests/test_harness_measurement.py
  tests/test_harness_evaluation.py tests/test_performance_attribution.py
  tests/test_production_performance_attribution.py -m 'not browser' -q`:
  **68 passed, 1 deselected** (2.30 s).
- Existing real Chromium reference-driver test
  `tests/test_harness_evaluation.py::test_real_production_controls_train_freeze_and_unseen_dom_holdout`:
  **1 passed** (74.63 s), covering 6 training, 7 holdout and 1 false-ready-control
  trials. Browser page networking is denied by the existing lab. Installed
  Playwright is located through `APPLYPILOT_TEST_PYTHON`; the evaluator itself
  loads from this isolated worktree, as verified by resolved module paths.
- Ruff 0.16.4 checked both new modules, changed E and the new test file: passed.
  `git diff --check`: passed.
- Both offline planner and original E preflight completed with `status=not_run`.
  The planner reported zero model calls and unknown cost. Their final manifests'
  source hashes were checked against this worktree's current source manifest.
  Generated artifacts are outside the repository in `../c0-offline-plan-final`
  and `../c0-e-preflight-final`; no generated trial data or databases are committed.
- Read-only official-source research preceded implementation; an independent
  final static audit identified and then verified fixes for context-limit encoding
  coverage and partial-subtotal aggregation. Separate failure/recovery research
  was unnecessary: these were locally proven accounting defects, with no new
  provider or browser behavior introduced.

Reference tests prove the measurement/fixture contract, not real-model quality,
speed or actual paid cost. Unknown production boundaries remain explicitly listed
above; real empirical execution still needs the configuration and budget decisions
in the pilot checklist.

## Source identity and archive comparison boundary (2026-10-13)

E and the offline planner require a complete source checkout, including the lab
fixture and scripts. Before creating output, each entry checks its own executing
file, loaded Python dependencies and lazy module origins against `--root/src`.
A different checkout, or an installed evaluator claiming a checkout's hashes,
is rejected even when the files currently have identical bytes. Preflight stays
`not_run` with zero model calls and does not import the client or profile config.
The source manifest includes package initializers and the full local browser
import closure, including `browser-observation-feedback.mjs`.

The model subprocess uses the parent's verified source directory explicitly with
isolated Python flags. It verifies the same root and frozen source digest, then
verifies the imported client before `get_client`, including for a configuration
probe. Calling the internal decision/worker entry without that binding fails.
Keep the source tree unchanged throughout an experiment; this is source-path and
file-digest provenance, not an attestation against in-process code patching or
concurrent modification while Python is importing files.

A normal installation remains supported for production commands and packaged
Browser Host assets. This fixture evaluator is not a wheel-only experiment
runner: an installed interpreter may run it by explicitly selecting the desired
checkout's `src` before importing E, with matching `--root`. It never silently
changes a wrongly selected package or root, and does not use cwd as a fallback.

Archive arm comparisons reject complete triples spanning different evaluation
schemas, and include `evaluation_schema` in valid output. Incomplete and duplicate
cells remain errors; same-schema complete triples retain task-cluster pairing.
