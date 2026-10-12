# P1-B0 controlled Host continuity evidence

Date: 2026-10-12. Baseline: PR21 `6f2a116c36846dd8a8d17e1ec7d427089011ba63`.
Scope: opt-in preparation continuity through the production Host/queue and the
existing attended SQLite transaction owner. This is controlled integration
evidence, not a live Codex browser, installed-product or real ATS acceptance claim.

## Implementation boundary

`scripts/visual-bridge-host.mjs` accepts `continuity: true` and exposes
`host.review(request_id)`. `scripts/attended-host-journal.mjs` transports actual
Host review/completion evidence and consumes one exact-request permit. The
ordinary Host path remains default. There is no new planner, automatic pump,
browser controller or parallel recovery state machine.

`src/applypilot/apply/attended_host.py` implements `host-attach`, `host-review`,
`host-complete`, and `host-reconcile` through the existing
`attended_application.execute` / `attended-application` CLI entry. It reuses
`recover`, `bind-consistency`, source resolution, Supervisor assessment, Episode
barriers, lease/material checks and the submission ledger. One outstanding
request lives in the existing attempt payload; no new table is introduced.
The source operator sequence is documented in
[the bridge guide](../../visual-worker-bridge.md#opt-in-same-attempt-preparation-continuity).

The owner commits the pending request and source bindings before publishing a
permit. Publication reacquires the owner lock and revalidates current authority,
so reconciliation or a submit latch in the commit/delivery window cannot revive
a stale permit. The Host atomically consumes it once, compares a fresh form to
the reviewed form, executes the existing bounded operation, and journals the
original feedback plus independent current inspection. C/D commits on the same
attempt before the original bridge worker receives its response. Re-delivery of
a committed result after restart does not replay input.

Missing completion is recorded explicitly as owner transport evidence, not a
fabricated successful Host response. It conservatively latches unknown effects,
even if a process stopped before it could publish a committed permit. Historical
successful field readbacks remain historical; current values still pass D checks.
Frozen fact edits require a new authorized attempt. `needs_fact` continuation
here demonstrates binding an omitted field to an already frozen confirmed fact.

## Actual acceptance path

`tests/fixtures/apply/attended-host-lab.mjs` starts an isolated headless Chromium
with a synthetic application form. It fulfills only that exact fixture URL and
denies other network requests; service workers and downloads are disabled. It
exposes a controlled facade for the Playwright/CDP capabilities used by the
production `createInAppBrowserHost` and `browserAdapter`. The fixture controller
injects parser/stop/coverage failures and reads an independent page oracle.

`tests/test_attended_host.py` uses the real Python `request_visual_operation`
queue, actual Host output, actual SQLite admission/attempts, and synthetic
authorization/material/profile data. Main acceptance cases do not construct
success-only checkpoints. A separate Python process tests owner restart; it
explicitly prepends this worktree's `src` and asserts the imported owner's
`__file__`, preventing a source venv's editable installation from shadowing it.

| Scenario | Verified result |
| --- | --- |
| Partial batch plus parser overwrite | Actual first field effect retained as historical progress; current email mismatch blocks consistency; newly reviewed email/title correction completes under the same attempt, without a submit gate. |
| `needs_fact` | Incomplete record makes no input; an unbound new field is refused; explicit bindings to frozen confirmed facts allow the next reviewed subgoal. |
| Lost acknowledgment and owner process restart | C/D commit survives a deliberately failed publication; a new Python process republishes the original response with exactly one input event. |
| Lost original completion | Effect uncertainty remains durable; no new permit or automatic replay. |
| Host stopped before input | Unused permit revoked; attempt parked conservatively. |
| Host disconnected after actual input | Unknown effect retained through stopped-Host transport and restarted owner; no replay permission. |
| Page changed after permit | Host rejects before application input using an independent fresh observation. |
| Actual iframe coverage loss | Owner refuses preparation permission; no writes. |
| Durable submit latch | Fresh Host review cannot grant input; receipt-only reconciliation remains authoritative. The latch is an explicit adversarial SQLite injection. |

`tests/test_attended_host_contract.py` adds negative identity/source/request-byte
cases, single outstanding-request enforcement, direct-recover bypass rejection,
material-change revocation, claimed-checkpoint/Episode barriers, existing CLI
dispatch, and two separate-SQLite-connection publication-race cases. Its crafted
envelopes are adversarial contract fixtures, separate from browser acceptance.

## Validation environment

Final source checks: 9 controlled Chromium cases passed; 174 non-browser Python
owner/C/D/bridge cases passed; 48 Node Host cases passed. Ruff passed for all
`src` plus the two new Python test modules; `git diff --check` passed. These are
regression counts, not application completion rates or empirical model trials.

Run Python tests from this source root with a disposable synthetic
`APPLYPILOT_DIR`, task-owned `TEMP`/`TMP`, and a fresh `--basetemp` directory. Set
`APPLYPILOT_TEST_PYTHON` to a Python environment whose Playwright version matches
the installed browser cache. On this MSI host, the existing source venv has
Python 3.11.14 / Playwright 1.62.0 and cached Chromium revision 1234; system
Python's Playwright expected absent revision 1223. No installation was performed.
This machine required the approved elevated test context for Chromium child
processes and synthetic authorization file identity; checks were not weakened.

```text
python -m pytest -q --tb=short --basetemp <fresh-temp>
  tests/test_attended_host.py tests/test_attended_host_contract.py
  tests/test_attended_recovery.py tests/test_attended_recovery_consistency.py
  tests/test_attended_application.py tests/test_application_consistency.py
  tests/test_visual_bridge.py
node --test scripts/visual-bridge-host.test.mjs
python -m ruff check src tests/test_attended_host.py tests/test_attended_host_contract.py
git diff --check
```

## Remaining verification

- The facade is an isolated local test capability surface, not an actual Codex
  `cua`/Browser session. Current Codex capability compatibility, source-installed
  operational invocation and packaged/installed startup remain unverified.
- No model worker/MCP protocol process, paid LLM, actual employer interaction,
  submission, user installation update, personal resume or credential access was
  part of browser acceptance. The production queue request function is exercised
  directly. No model success-rate, latency or cost improvement is claimed.
- No automatic migration to a stopped/replaced Host or browser survives restart.
  The demonstrated restart is the Python owner while the original Host is alive;
  stopped Host cases remain parked.
- Bounded observed text/select/record preparation is supported. Upload acceptance,
  asynchronous real ATS parsing, broader widgets, iframe/closed-shadow coverage,
  actual final-review projections and final submission are outside this delivery.
  A `consistent` result verifies only the bound fields, never the whole application.
- File transport trusts the attending operator and local directory permissions;
  request hashes do not create a security boundary against a hostile local writer.
  Original page evidence stays private outside source control. Release packaging
  and cleanup/retention policy need their existing operational review.

Connection points for integration: new actions on the existing attended CLI/API;
the optional Host constructor argument plus `review`; existing shared attempt,
recovery and consistency payloads. No E scoring, procedure promotion, generic
metrics or performance-attribution interface changes are included in this lane.
