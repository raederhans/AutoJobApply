# Replay identity correction — 2026-10-13

Baseline: `a88fc7c14df6b7b9329a831a3d49d977b66c06b5`.

The installed-package acceptance probes reproduced two identity errors: a
completed operator resume could consume another application's human request,
and recovery history keyed only by command ID could return a different
attempt's verified result. Neither probe repeated its external-effect callback.
This patch corrects result attribution and human-request consumption.

## Operator resume

Before returning any terminal resume replay, `OperatorRuntime` reads the original
exception, exact request, and durable response. The exception and request must
match the command's run/attempt/actor/turn; the exception request ID must match
the supplied ID. The response type, timestamp, command input reference, and
response digest must also match. Identity rejection happens before consumption
and before any owner call.

The check accepts the original resolved exception and consumed request. It also
allows consumption of the original open request after a crash between recording
a verified command and consuming the request. It does not require the latest
checkpoint to remain the parent checkpoint: the successful child already
advanced that checkpoint. Initial resume admission retains the existing parent,
lease, job, pre-submit, and checkpoint checks.

## Recovery command history

Every historical result must match incoming command/run/attempt/actor/turn before
terminal return, verification, timeout handling, or execution. A unique durable
`started` record must prove the entire incoming `RecoveryCommand` envelope.

New started records store `command_digest_version="1"` and `command_sha256` in
their existing details JSON. SHA-256 covers every dataclass field, including
payload, command/action/effect scope, failure category, next action, retry
budget, policy, lineage, schema, and authority flags. The preimage is the UTF-8
prefix `applypilot:recovery-command-envelope-v1\0` followed by the existing
persistable JSON projection with sorted keys, compact separators, and Unicode
preserved. Mapping key order is insignificant; value and type changes remain
significant. No schema or storage migration is required.

Errors distinguish `recovery command lineage collision`, `recovery command
envelope collision`, and `recovery replay lacks durable command envelope`.
Rejected history remains unchanged; neither handler nor verifier runs. A
competing started claim is re-read through the same validation before returning
its persisted result. Exact replay preserves existing at-most-once ownership,
executed-only verification, and stale-started termination behavior.

### Legacy records

Historical records without the versioned full-envelope digest fail closed, even
if lineage matches or a terminal verified result exists. Incoming parameters
cannot retrospectively sign old history, and the executor does not append a new
started record, overwrite old results, or repeat an uncertain effect. Such
records remain available for audit. An operator must reconcile the original
effect and obtain any necessary new authorization before choosing a new action;
changing the command ID alone is not evidence that repeating the effect is safe.

## Verification

All inputs were synthetic SQLite fixtures and local mock callbacks. Modules were
loaded from this worktree by inserting its resolved `src` at `sys.path[0]` and
asserting both `operator_runtime.__file__` and `recovery_execution.__file__` were
under that directory. Tests used the existing local venv, not an installed-wheel
substitution. APPLYPILOT_DIR, TEMP, TMP, and pytest basetemp were isolated under
`C:\Users\raede\AppData\Local\Temp\replay-identity-20261013`.

- `test_operator_runtime.py` + `test_recovery_execution.py`: **88 passed**.
- `test_operator_commands.py` + `test_durable_agent_runtime.py` +
  `test_durable_agent_runtime_adversarial.py`: **37 passed**.
- Ruff check on the four modified Python files: **passed**.
- `git -c core.bare=false diff --check`: **passed**.

Coverage includes foreign human requests with no response; exception and request
lineage drift; missing/changed response evidence; checkpoint advancement;
verified-to-consumed crash recovery; recovery lineage and full-envelope drift
at started/executed/verified/failed boundaries; mixed historical lineage;
legacy/orphan history rejection; exact executed-only verification; canonical
payload ordering; and a competing started claim. Rejection tests assert no new
SQLite row changes and no repeated owner/handler/verifier calls.

The original acceptance probes were also run against the corrected source:

- `patched-operator-replay.json`: both normal controls succeed, owner calls = 1,
  unrelated request remains open, wrong request raises `OperatorCommandError`,
  `invariant_failed=false`.
- `patched-recovery-identity.json`: wrong attempt/turn raises lineage collision,
  no foreign result is returned, handler calls = `["attempt-1"]`,
  `invariant_failed=false`.

These JSON files are retained in the task's isolated temporary directory above.
There were no real applications, browser actions, emails, credentials, remote
Git actions, paid API calls, or installation changes. No blocker remains for
this bounded correction; wheel packaging and real browser acceptance belong to
the coordinating task.
