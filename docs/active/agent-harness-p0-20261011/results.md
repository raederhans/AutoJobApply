# P0-A implementation and validation

Base commit: `15273c56be8b5a0ed77944de9ac11e69fb4c7b3e`.
Scope: batch dependency checks and group-slot timing. Career M1-M4 is preserved.

## Actual changes

- `scripts/browser-form-state.mjs`: an optional trusted host pre-input validator
  runs on the same fresh `observeForm` result. Worker argument shapes are not
  expanded. Existing direct four-argument control calls keep their behavior.
- `scripts/browser-field-batch.mjs`: check requiredness and its evidence source,
  question text/instructions/record section/constraints/completeness/language,
  other-field validation/value visibility and coverage. Check both before input
  and after readback. Reuse existing identity/structure/value and error handling.
  Return bounded dependency keys/counts, without copying question/validation text.
  Use a Map for cross-field value lookup rather than repeated linear searches.
- `scripts/browser-host-group.mjs`: record group-slot waiting separately from
  host execution. `group.metrics()` exposes bounded aggregate counters, active/
  waiting/scheduled counts and span totals/maxima. Existing execute return shapes,
  two-slot sharing, same-job reservation and manual review remain unchanged.
  A timing failure or backward clock does not change execution.
- Add two Node regression suites and a pytest wrapper in the existing Node-enabled
  browser CI tier. No CI thresholds or pre-existing assertions are weakened.

## Local red/green evidence

The four focused baseline source/test copies were checked by Git blob SHA before
editing. This was not a complete local checkout. Node version: 22.16.0;
Python: 3.13.5. The repository browser CI specifies Node 24 / Python 3.12.

| Check | Result |
| --- | --- |
| Four existing field-batch tests, original main code | 4 passed |
| New dependency regressions, original main code | 3 passed / 18 failed (expected red) |
| Existing + dependency regressions after patch | 25 passed |
| New group-timing regressions | 9 passed |
| Combined distinct Node tests | 34 passed / 0 failed / 0 skipped |
| Python wrapper | 2 passed; it reruns the new Node suites, not two additional behavioral cases |

The critical negative case fills B, invalidates A without changing A's value,
and observes that baseline reports `verified`. The patch returns `parked`.
For drift between one readback and the next preflight, the patch stops before
writing B and preserves the newly observed state. Correcting the active field's
own validation is allowed. A stable two-field batch still has four form reads:
no extra full-page observation is inserted.

Timing tests use a controlled clock and deferred host calls, not sleeps as a
performance benchmark. In the test, wall time is 40 ms, summed host service is
65 ms, and summed slot wait is 10 ms. This checks boundaries under overlap;
it is NOT a measured speedup. Existing host queue/action/observation telemetry
is retained. Do not add group spans to those nested/overlapping measures.

Run on a full checkout:

```sh
node --test scripts/browser-field-batch.test.mjs scripts/browser-batch-dependencies.test.mjs scripts/browser-host-group-timing.test.mjs
python -m pytest -q tests/test_agent_harness_batch_p0.py
```

## Evidence limits and remaining work

The local tests execute the production batch/control adapters against deterministic
browser-state doubles. They do not run the actual DOM parser, a live Codex/Claude
model, authenticated IAB, employer submissions or the entire Python suite.
Remote CI status must be read from the PR checks for the exact head commit;
this checked-in local report does not claim those checks have passed.

The guard validates observed checkpoints. It is not an atomic transaction over
a live asynchronous web page. In particular, the later host full-refresh check
(E8 in `plan.md`), complete record interpretation, school-selection subgoals,
Supervisor/Episode integration, procedural/failure memory and same-model
interactive holdout evaluation remain separate work. Ordinary-field batches
remain one to four fields, excluding uploads, declarations and Submit.

Legacy `batch_result.completed` remains the number of returned step reports,
including a step whose readback failed. It is NOT a verified-field count and
must not be used as readiness proof. Inspect status and per-step evidence.

No facts, CV text, candidate values, credentials or operational databases were
added. SubmissionGate, submit-intent/receipt reconciliation, uncertain-effect
stopping, browser ownership and authority boundaries were not changed.
