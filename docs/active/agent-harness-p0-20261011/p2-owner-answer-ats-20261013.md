# Owner answer to local ATS recovery

The application inbox could durably save an answer and request the original
worker, but the launcher discarded the answer reference when entering its
operator-resume scope. Its prompt explicitly prohibited inferring content from
that opaque reference. A saved availability answer therefore could not reach
the new preparation turn.

The original worker now reads career-workbench answers from the exact workspace
content-addressed artifact after OperatorRuntime admits the request, attempt,
checkpoint and browser lease. The reader checks the expected directory and
filename, rejects symlink/junction resolution changes, bounds the bytes, checks
SHA-256, and verifies schema and request identity. Other response-store providers
remain opaque. Missing or changed workbench artifacts fail closed.

The answer enters only the current thread's temporary owner scope and the fresh
child prompt as a JSON string explicitly marked as user data for the pending
question. The scope removes its answer in finally. Runtime requests,
checkpoints, command envelopes and inbox timelines remain reference-only. This
does not update profile facts, weaken risk/provenance checks, grant a new attempt
or authorize Submit.

## Local functional observation

`tests/test_owner_ats_integration.py::test_saved_answer_reaches_same_page_owner_after_inbox_refresh`
uses fictional records, the real local inbox HTTP API, a second SQLite
connection, the real dispatcher/runtime/scope and a controlled Playwright page.
It saves an answer twice, queues resume twice, rechecks employer/job/page identity,
fills one local availability field using the new JSON answer prompt, records a
unique completed child and newer checkpoint, and verifies a terminal replay
does not rerun the owner. The page survives refresh through synthetic
sessionStorage. Submit is disabled; submission count and submit_started are zero.

The browser portion uses a deterministic local agent and the existing synthetic
durable child fixture. A separate focused launcher test proves the real run_job
prompt reaches its local fake subprocess, with no answer in control rows or
launcher log files. Neither test invokes a real model, an employer ATS, a paid
API or the actual Codex Browser.

## Verification on 2026-10-13

14 focused checks passed in 11.79 seconds: the vertical local flow; actual
launcher prompt seam; expired attempt, stale runtime owner and advanced
checkpoint rejection; changed bytes, outside paths and request/schema mismatch;
lost-owner expiry; unknown-effect no-replay; the existing preparation-only and
Submit-phase scope checks; and the legacy reference-only handoff contract.
Ruff on the three changed Python files and git diff --check passed.
The focused launcher privacy check was strengthened to make the fake child echo
the synthetic answer: it passed again (one test, 1.07 seconds), proving the
existing agent-message redaction keeps that echoed value out of runtime logs.
This check alone did not cover tool inputs; the independent review found a
separate reproducible privacy gap described below.

### Independent review repair

The reviewer demonstrated that assistant/tool_use events with ref and text but
without element copied a short private answer into two logs. A long answer could
leak a truncated prefix while passing the former whole-string assertion.
The action-description sink now records only the tool name for all such events;
it never includes text, element, URLs, paths or field values. The exact input is
still provided separately to the existing supervisor, so this does not alter
owner/lease/continuation or effect guards.

Four narrow regression cases inject private synthetic short text, long text,
element and URL inputs into the same local fake launcher. They check whole
answers and distinctive prefixes against runtime logs and captured production
worker-action descriptions (the input to last_action, dashboard events and
logger output). These four cases plus the prompt/agent-message privacy check
passed: 5 tests in 2.68 seconds. Ruff and git diff --check passed again. The
review/probe evidence remains in the sibling reviews directory; the repair's
log is owner-ats/tool-log-privacy-check.log. No full suite, browser or model was
run for this narrow repair.

Evidence is in the parent workspace's
`interview-and-recovery-20261013/owner-ats/`: before.png, after.png,
focused-tests.log, privacy-check.log, tool-log-privacy-check.log, run-owner-ats.py
and outcome.json. The initial ordinary sandbox
attempt failed resolving the permitted synthetic Temp path (WinError 3); the
authorized escalated local run succeeded. The first product run identified two
fixture mistakes (default inbox hides resolved history; checkpoints are
append-only), both corrected without weakening guards.

## Ownership and remaining boundary

The owner-ATS agent is the sole owner of both local servers, browser and test
process. Servers use port 0 and close in finally; the browser is closed and the
HTTP server thread joined. Database and browser artifacts use the isolated
`LocalAppData/Temp/ap-owner-ats-20261013` root.

A new database reader/inbox refresh can recover the persisted request; a dead
page-owning process cannot inherit the old lease. Failed owner heartbeat expires
the request. Unknown effect leaves the command started and blocks replay.

Availability is a medium-risk, time-sensitive fact. This change delivers the
explicit response to preparation, but does not invent a profile fact or bypass
its provenance/expiry requirements. Completing a real audited preparation for a
new structured answer source needs a separately designed scoped fact admission
contract. Production model interpretation, real process restart/liveness and
actual Codex Browser behavior were not exercised here.
