# P0-B2/B3: observed semantics and bounded preparation subgoals

## Baseline and scope

Continue draft PR #21 at `d6b83bbba2bf70eedadec7019f1b3ee2548a6c06`.
Latest main read before implementation was `64d2ba6a76457abe8648e8b5cf34665d206be6a3`.
Career M1-M4 (PR #19), the PR #20 canary fix and P0-A/B1 are retained.
This delivers B2 diagnostics and two deliberately bounded B3 prepare operations.
It does not complete P0-C/D/E or the P1 experience and live-model evaluation work.

## Failure evidence and minimal changes

The existing observer already carried question, group, option and coverage data.
B2 adds interpretation of that evidence rather than a second page scraper.
The new B2 tests on the original observer produced **1 pass / 11 failures**.
Most missing-diagnostic failures demonstrate absent capability, not eleven
independent production incidents. Two concrete live-DOM defects were reproduced:

1. Replacing a semantic record container while retaining its child controls did
   not change the old field identity. The observer now includes up to eight
   explicit ancestor record scopes and their current CDP backend-node identity.
   Same-child/new-record changes invalidate old control/subgoal observations.
2. CDP's empty string index for an OPTION attribute was decoded as `undefined`.
   An empty `value` then fell back to the visible placeholder label during matching,
   so an ordinary native school select's value became unavailable. Decode absent
   string-table content as the empty attribute string; retain exact option checks.
   The initial B3 run was 21 pass / 2 native-select failures before this correction.

Production changes:

| Location | Change |
| --- | --- |
| `scripts/browser-form-state.mjs` | Reuse the existing DOM traversal and CDP snapshot for explicit record paths, combobox expansion and live owner identity. Feed existing scoped file-group labels into question provenance. Correct empty attribute decoding. Invoke the existing trusted dependency callback at the custom option's second pre-click observation too. |
| `scripts/browser-form-semantics.mjs` | Pure transient annotation: identified/ambiguous/not-covered fields, record scope, not-expanded/ambiguous/unobserved options, and advisory resume/cover-letter purpose. Keep frame, closed-shadow and all-step coverage unknown. No values, writes, learned facts or persistent experience. |
| `scripts/browser-preparation-subgoals.mjs` | School selection and requested fields of one existing record, implemented through `operateObservedControl`, `validateFieldBatch` and `fieldBatchChanges`. Bound action/wait budgets, verify committed selections and field values, retain correct partial progress, stop on drift. |
| `scripts/visual-bridge-host.mjs` | Expose the new operation only on an enabled IAB prepare host, under the existing reviewed request queue. Pass the request deadline as trusted context. Recheck the result against the existing final form refresh, revoking stale verification without repair/replay. |
| `src/applypilot/apply/visual_bridge.py` | Strict subgoal shape, current observation and prepare/browser boundary checks before queue publication. Reuse existing leases, claim/response binding, unknown-effect stopping and durable file writes unchanged. |
| `src/applypilot/apply/visual_bridge_mcp.py` | Make the operation discoverable in the existing worker-facing tool, with explicit authoritative-fact and scoped-result instructions. No new MCP server or independent controller. |

The new record path uses only explicit fieldsets, ARIA groups and sections with
owned labels/headings. It does not infer record ownership from coordinates,
filled applicant values, a descendant record's heading or arbitrary CSS classes.
Unresolved/truncated record paths remain ambiguous. Attachment-purpose detection
is conservative label interpretation, not permission to upload an artifact.
The existing fact/material and final submission validators remain authoritative.

## Available operation and bounds

`visual_operation` now accepts `operation: prepare_subgoal` with the latest
`observation_id`, DOM feedback, and one of these argument shapes:

```json
{"kind":"school_selection","record_key":"<observed record key>","field_key":"<observed school field key>","value":"Example University","query":"Example"}
```

```json
{"kind":"record_fields","record_key":"<observed record key>","steps":[{"operation":"fill_control","field_key":"<observed field key>","value":"Example Corp"}]}
```

Keys come from the current host observation; values must come from existing
reviewed applicant facts. A webpage, inferred semantic label or worker-provided
flag cannot grant fact, artifact or submission authority. The host still reviews
`peek()` requests and calls `execute()`; there is no automatic approval or pump.

School selection uses at most one open, one query entry and one exact-option
selection. It may perform up to two short read-only waits for asynchronous
options. Duplicate targets park; missing asynchronous candidates return `waiting`.
A typed search query or a successful click is not selection proof: use the
committed display/unique ARIA selection or native selected option. A correct
existing selection is retained without another input.

`record_fields` accepts one to four requested text/native-select/date/month
fields in one currently observed, identity-bound record. It reuses the existing
native date blur behavior without expanding ordinary `fill_batch`. Required
observed record fields lacking supplied facts return `needs_fact` before input.
Already correct supplied values are retained. Cross-record requests are rejected.
Verification covers the requested fields, not all possible fields in that record
or the factual correctness/completeness of the whole application.

A changed country requirement, parser overwrite, inserted field, record purpose
or identity, validation, options or coverage invalidates dependencies before the
next input. Expected changes to the active school's own menu are allowed only
after its own bounded interaction/wait; unrelated fields and final refresh have
no such exemption. Generic post-input runtime failures propagate to the existing
Host `outcome_unknown` stop, never an automatic replay.

This slice does NOT add record creation/deletion, arbitrary date widgets, custom
multi-select, frame/closed-shadow inspection, navigation, uploads, declarations,
login, submission or automatic recovery across sessions. It does not remove the
existing whole-form pre-submit review. `verified` is immediate and scoped;
`whole_application_verified` and `grants_submission_authority` remain false.
A source installation and fresh Host/MCP process are needed to use the new code;
no release version, PyPI package, existing local installation or running user
browser was changed by pushing this PR.

## Actual validation

Focused source files were read through the GitHub connector and checked against
Git blob hashes before editing. The container could not clone GitHub (DNS).
A full repository checkout, local Ruff, Windows or packaging run is not claimed.
Local environment: Node 22.16.0, Python 3.13.5, installed Python Playwright and
`/usr/bin/chromium`. The shared fixture contains only fictional data and rejects
submission. Browser requests are blocked; the fixture is loaded with `setContent`
in memory. An initial localhost navigation was blocked by environment policy and
was not bypassed or counted as an application regression.

Commands and results:

- `node --test scripts/browser-semantic-preparation.test.mjs`: **38 passed, zero
  failed/skipped**. Set `APPLYPILOT_TEST_CHROMIUM` only when using a known local
  executable; CI uses its existing Playwright-installed Chromium.
- `python -m pytest -q tests/test_preparation_subgoals.py`: **29 passed**. This is
  28 Python transport/schema cases plus one wrapper rerunning the same 38 Node
  tests. Do not add the wrapper as independent behavioral evidence.
- `node --experimental-test-module-mocks --test scripts/browser-host-refresh-dependencies.test.mjs`:
  **46 prior B1 tests passed**. Those older tests remain mocked boundary tests.
- Node syntax, Python compilation and focused `git diff --check` passed.
- New B2 cases against the original observer: **1 passed / 11 failed**. The new
  B3 operation did not exist in that baseline; no invented baseline B3 success
  rate or model-quality comparison is reported.

The new 38 tests execute the actual observer callback, live CDP snapshot,
Playwright controls and Host adapter against real Chromium DOM. Terminal checks
independently inspect field values, selection display, peer records and event
counts. The Host tests also exercise the real filesystem queue and stop behavior
after an actual input followed by browser closure. Test code drives the choices;
there are no model calls. The DOM-CUA branch uses an empty text stub while the
form observation/actions are real Chromium. It is not an authenticated Codex IAB,
native Windows desktop or employer ATS validation. The Python queue test and the
real-browser Host tests are separate tests, not one full Python-to-browser run.

One controlled matched school task used the same primitive executor and exact
final choice. Three reviewed primitive requests became **one** subgoal request;
form reads, including Host attachment and initial/final observations, were **12 vs 11**.
This is request/read accounting on a synthetic page, not measured model latency,
cost savings, autonomous completion rate or the P0-E skill/harness experiment.
No additional observer call is introduced solely to attach B2 diagnostics.

Remote CI must be checked for this new PR head and its merge preview, separately
from the previously green B1 run. Resulting CI status is recorded in the PR
conversation without rewriting implementation history as though CI were local.

## Remaining acceptance

B2/B3 are implemented for the explicit supported scope above. Broader implicit
records, multilingual semantic ambiguity, richer school aliases, record creation,
and selective region-only observation still need separate evidence and admission.
P0-C will connect existing Supervisor/Episode recovery owners to current IAB
signals. P0-D will verify cross-material/domain consistency. P0-E/P1 must measure
same-model harness value and cold/warm unseen-employer/page-family holdouts.
Candidate memory and career-learning reviews are not promoted to applicant facts.
SubmissionGate, frozen materials, submit-intent/receipt reconciliation, uncertain
submission no-retry and sensitive-data boundaries are unchanged.
