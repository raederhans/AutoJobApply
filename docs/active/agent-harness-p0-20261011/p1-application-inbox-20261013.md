# Local application inbox

The career workbench now exposes the existing durable application exception
queue. Open `applypilot --workspace <workspace> career open`, choose
**申请待处理**, save a confirmed answer, then explicitly request continuation.
This works without selecting or creating a career target.

The added HTTP surface is `GET /api/career/v1/application-inbox` plus
`POST /api/career/v1/application-inbox/{id}/respond` and `/resume`. Both writes
reuse the workbench's loopback, Origin and session-token checks. The server
constructs the operator envelope from its own durable records; the client
supplies only a request key, displayed revision and, for respond, answer text.

Answers are immutable local JSON artifacts with SHA-256 references in the
existing human-response table. The HTTP idempotency record stores an answer
digest rather than another copy of the answer. Saving and requesting continuation
are separate actions. Resume delegates to `OperatorRuntime` without injecting
an executor, preserving its request/checkpoint/attempt/lease checks and durable
`requested` lifecycle. It never launches a new application, browser or model.
The existing operator service owns its transaction; the HTTP layer does not
wrap it in an outer write transaction. A stable command identity covers the
queue-commit / HTTP-cache gap without granting another execution.

The UI distinguishes awaiting response, response saved, continuation requested
and blocked. Expired or changed bindings require the original owner to inspect
current state. Other exception kinds remain informative, without an action
button. List reads use SELECT only and never initialize missing control tables.
Unknown counts are null / “—”; truncated counts explicitly cover only the
latest 100 open items. Drafts stay in page memory and are not replaced by the
career background-task polling loop.

## Short functional evidence

- Three real loopback HTTP / SQLite cases passed in 1.67 s: answer then one
  resume request with idempotent retries; expiry and stale-click rejection;
  read-only missing-schema behavior with unchanged database bytes.
- One local headless Chromium walkthrough used a fictional job and the real
  career page, HTTP server and SQLite. It saved an answer, showed the ready
  action, requested continuation and displayed the waiting status. There were
  no page errors. Durable evidence contained one response, one `requested`
  command, the original single runtime turn, an open human request and an
  `in_progress` attempt with `submit_started=0`.
- Screenshots and request/durable evidence are in the task-local
  `delivery-20261013/inbox-browser-demo/` directory (`01-pending.png` through
  `04-requested.png`, `evidence.json`). The task-owned
  `demo_application_inbox.py --output <new-task-directory> --serve` starts a
  fresh fictional workspace for manual demonstration; it does not touch an
  existing user workspace.
- Ruff and JS syntax checks passed. The earlier sandbox's WinError 10013
  blocked localhost access; the same short tests passed in the approved local
  execution context. No new long suite or further audit was added for this UI.

This proves a usable local answer/request workflow, not completion of the
original application. A live original dispatcher must consume the request and
perform fresh checks. Actual Codex Browser remains unavailable on this MSI
environment; this demonstration used local Playwright Chromium. No real ATS,
personal data, paid model call, user-install update or remote publication was
used.
