# P1 JS Host request and permit contract review

Date: 2026-10-13. Source baseline: `a88fc7c14df6b7b9329a831a3d49d977b66c06b5`.
Scope: `visual-bridge-host.mjs`, `attended-host-journal.mjs`, their direct browser
dependencies, and related Node tests. Python owner, schemas, packaging, CLI,
runtime-cell identity and shared existing documentation were not edited.

## Confirmed defects and corrections

The ordinary Host accepted unsupported request schemas, a request whose embedded
ID differed from its queued filename, absent/string deadlines, and non-object
arguments. The previous deadline comparison admitted `undefined` and coerced
strings. A synthetic counting adapter demonstrated calls reaching `act`; this
is an adversarial transport test, not evidence that a real ATS accepted input.
The Host now rejects these envelopes before claiming their pending file, and
requires canonical lowercase UUID filenames. Normal Python workers already
publish schema 1, canonical IDs, numeric deadlines and argument objects.

The continuity Host review previously accepted a request ID different from the
filename selected by the operator. Review now binds that filename and rejects
unsupported request schema. Permit admission previously ignored permit schema,
tab/job identity and nonnumeric expiration. Missing expiration passed its
comparison. These fields are now checked before claim and again before permit
consumption, against the current owner and original Host binding.

A separately expiring permit could lapse while the fresh independent observation
was awaiting the browser. A deterministic clock test demonstrated one `act`
invocation after that owner deadline, while the worker deadline remained valid.
The consumed permit deadline is now checked again after observation; expiration
produces a journaled failed result with no input. This is a crafted transport
case: the ordinary Python worker limits requests to 120 seconds, while the owner
currently uses `min(request deadline, now + 300)`, so ordinary generated permits
usually share the worker deadline, which the Host already rechecks. The fix
preserves the stricter permit's own semantics without changing either deadline.

No additional JS module or runtime dependency was introduced. One existing
Host test helper was updated to include the schema 1 field emitted by the actual
Python queue; its delayed-readback assertions and failure guards are unchanged.

## Reproduction and verification

Before the production changes, the initial new contract suite reported 14 failed
and 2 passed tests (Node counts include parent/subtests). Failures confirmed the
request, review and permit defects above; the valid single-use path and null
permit expiration rejection already passed. These used actual queue files and
production Host/journal with synthetic adapter observations and owner permits.
The permits are crafted fixtures, not SQLite owner acceptance evidence.

After the changes:

- 219 Node tests passed across Host/journal, form state, batch/dependencies,
  complex controls, observation feedback, refresh dependencies and Host groups,
  using `node --experimental-test-module-mocks --test <selected files>`.
- The final journal-only suite passed 21 tests, including an additional canonical
  filename case. It also checks valid single execution with worker response
  withheld, immutable completion on repeated execution, changed request bytes,
  revoked permits, and unknown effects after an input/readback disconnect.
- All 9 existing `tests/test_attended_host.py` controlled Chromium + real SQLite
  owner scenarios passed in 34.09 seconds on this changed source tree. This
  includes partial/parser progress, needs-fact bindings, lost acknowledgment with
  owner restart, missing completion, stopped/disconnected Host, changed page,
  iframe coverage loss and submit latch.
- `git diff --check` passed.

Environment: Windows, Node 22.23.0, the existing full source venv
`C:\Users\raede\Desktop\简历\applypilot-local\source\.venv\Scripts\python.exe`.
The Python command explicitly inserted this worktree's `src`, asserted the owner
module belonged to it, and set `APPLYPILOT_TEST_PYTHON` to the same venv. Passing
verification used synthetic data, `TEMP`/`TMP`, `APPLYPILOT_DIR` and pytest basetemp isolated under
`task-3/js-contract-test-temp` or `task-3/js-contract-evidence-20261013`.
The controlled Chromium run used the approved elevated context for local child
processes/file identity; no installation or guard change was made.

The default sandbox-redirection temp directory initially rejected atomic rename
with EPERM. Setting task-owned `TEMP`/`TMP` resolved that environment issue. An
overbroad Node test invocation that included browser suites without their runtime
environment was interrupted and is not counted as a pass; the final bounded
Node invocation excluded those browser suites and the unrelated career workbench.
Synthetic Node output is retained outside Git in `node-core.log` and
`node-journal-final.log` in the evidence directory above. The Chromium result was
captured in the execution transcript.

## Lifecycle and recovery boundary

The existing tab-owner guard and directory `.host-owner` marker prevent ordinary
duplicate attachment. A failed initial observation publishes no active Host;
the directory remains reserved and requires a fresh session directory. Stopping
the Host retains its journal and forbids execution/review/peek; it does not
automatically attach a replacement tab or migrate permits. These existing
fail-closed lifecycle semantics were preserved.

Permit consumption still precedes any possible browser effect. The worker does
not receive a continuity response until the SQLite owner commits its C/D result.
A normal repeated execute cannot find the claimed request back in `pending`;
the original completion remains unchanged. Missing completion, stopped Host and
input uncertainty retain the existing reconciliation barriers, exercised by the
9 integration cases. Cleanup remains the trusted operator's private-directory
retention policy; this patch does not erase recovery evidence or implement a
background replay/cleanup pump.

The transport assumes trusted local directory writers. Digests and UUID checks
do not authenticate a hostile writer or authorize reconstructing/replaying old
pending files and permits. No power-loss/fsync durability claim is made.

## Remaining evidence limits

The 9 cases use the existing isolated `controlledChromiumfacade`, with only the
fixture URL fulfilled and other requests denied. They are not an actual Codex
`cua`/Browser runtime, installed-product startup or real ATS claim. This lane
did not invoke Codex browser tools, import an unsupported browser runtime, use
desktop browser fallback, run a model/MCP worker, access personal materials or
credentials, pay for LLM calls, install user software, or submit an application.
Actual supported-runtime compatibility and installed/packaged operational entry
remain separate verification work. No new blocker was found for this bounded
JS source fix; Python lease/reconciliation and packaging changes belong to their
parallel owners.
