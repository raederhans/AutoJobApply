# Attended in-app browser workers

## Current migration entry

Discovery, preparation and explicitly authorized submission can run on an actual in-app browser tab, with no
CDP port or shared-session assertion. In the supported Browser session, use:

```js
const host = await createInAppBrowserHost({ directory, tab, phase: 'discovery' });
```

The host derives its target from the returned tab, rejects a second owner for
that tab, and exposes URL/title/tab identity with every observation. Launch
`applypilot --workspace <workspace> browser-work --bridge-dir <directory>
--task-file <goal.txt> --phase discovery` (or `prepare`) with the source-tree
Python environment / workspace wrapper. `scripts/run_browser_worker.py` is also
available as a standalone runner for the same API.

When the current IAB exposes Playwright APIs but no `dom_cua` or `cua`, attach a
preparation host explicitly with the supported observation mode:

```js
const host = await createInAppBrowserHost({
  directory, tab, phase: 'prepare', observationMode: 'playwright'
});
```

This mode requires `tab.playwright.domSnapshot`, `evaluate`, and `locator`.
Before attachment, read the current browser's capabilities, tab, and CDP
documentation if `observeForm` will use its supported `capabilities.get('cdp')`
live-value observation. Capability availability does not authorize an undocumented
CDP call or a different browser controller.
It returns the actual Playwright page snapshot and existing structured form
observation, without inventing node IDs. `fill_batch` and existing observed form
control operations keep their current control validation, blur and readback.
`upload_artifact` also supports an observed file `field_key` and a host-provided
artifact reference through the documented chooser capability. Screenshots,
node/coordinate actions, navigation and typing/key primitives through this bridge
are rejected before input; the attending operator handles those steps with the
current browser's documented APIs. After direct operator
actions, call `host.invalidate()` and obtain a fresh observation. Default
`observationMode: 'dom_cua'` retains the existing browser path. This mode does not
replace the final pre-submit snapshot or grant submission authority.

Structured fields report `required_source` as `native`, `aria`, `visible_label`,
or `not_asserted`. Visible trailing asterisks are admitted only from a visible
label in the nearest `.form-group` with exactly one control and one label whose
nonempty `for` matches that control. A file input may additionally have a
`.custom-file-label` inside its own `.custom-file` container when the semantic
label is the group's unique direct-child label; a companion's nonempty `for`
must match the file input. Ambiguous or unrelated labels do not assert
required state. This metadata guides review; it does not prove completeness or
authorize filling declarations. The operator still reviews all visible mandatory
questions independently before submission.
The standalone runner also accepts `--phase submit`, but only for a matching
submit host whose top-level `submission_authorized` is the boolean `true`.
The task text cannot grant that authority or upgrade a prepare host. Discovery
and prepare remain the ordinary starting phases. Authorization is scoped to the
bound application, not permission for a worker to submit other jobs.
The goal is ordinary task prose. The worker uses the configured Codex model
and only the page-operation MCP, without shell, standalone Playwright, native
desktop input or web search. It can observe DOM or screenshots, click, scroll,
type, press keys and navigate to an exact link from its current observation
in the same tab. Tool discovery remains available when MCP tools are deferred.

This is an **attended operational entry**, not just a fixture smoke. The current
Codex task still services `peek/execute` through the supported Browser runtime.
It is not connected to the unattended `apply` submission pipeline and does not
change that pipeline's default browser. A successful child exit means its turn
finished, not that a job was applied to. Read its result, including auth_required,
unknown action outcomes or missing evidence. A total worker timeout terminates
the worker and its child processes and returns 124.

`host.inspect()` independently re-reads the same tab after preparation. Its
result includes the actual page identity and the host's explicit submission
authorization (false outside submit phase). It is a visible-page review, **not** a replacement for
the existing pre-submit audit (uploads, answer provenance, controls, gate and
receipt reconciliation). The explicit submit phase enables attended execution;
it does not connect the worker to the unattended pipeline or prove that its
gates and ledger have been migrated. Confirm those checks with the host before
submitting, and verify the matching receipt after submitting once.

## Authentication, uploads and batch continuation

Workers prefer guest/direct application when available. They may explore an
existing Google SSO option for the user's identified account and application
sign-in consent. Ambiguous accounts and unrelated permission grants need host
handling. The availability of a signed-in Edge or Chrome profile does not prove
that the IAB tab has the same session; do not copy cookies or password stores.

Email OTP retrieval for the current employer and secure password filling are
host handoffs under the user's authorization. Passwords and OTPs must never be
included in ordinary `type_text`, worker goal text, queue payloads or logs.
The host must verify a supported secure capability before claiming automated
login. Otherwise return `auth_required` and preserve this application. After a
handoff, re-observe the same tab before continuing.

For attachments, pass trusted absolute paths in `artifacts: {resume: absolutePath}`
when creating the host. The worker sees only the available `artifact_ids` and
calls `upload_artifact` with `artifact_id` and an observed upload control's
`field_key` from `form_state` (or a host-reviewed legacy `node_id`, never both).
Playwright observation mode uses `field_key`: the host re-observes the same page
and verifies its URL, control identity, file type, availability and unique locator
before clicking. Workers cannot supply paths, selectors or action buttons through
this route. For a clipped 1x1 file input in a uniquely labelled file group, the
observation can include `upload_trigger`: a unique visible sibling `type=button`
whose text matches the input's bound label. Submit, Apply, Confirm and consent
buttons are excluded. The host rechecks this binding and clicks that observed
trigger once, without first clicking the clipped input or retrying.
The legacy `node_id` route retains its existing behavior and requires
host review of the observed upload node.
The host starts a caught `waitForEvent('filechooser')` before clicking, then
calls `chooser.setFiles([hostArtifactPath])` with exactly one nonempty file selection.
An empty array is not a supported clear/cancel operation.
`upload_result.status: file_selection_done` reports
selection only; `webpage_acceptance: unverified` requires checking the accepted
filename/status on the page. Pre-input validation failures reject the request;
an uncertain chooser/click/setFiles failure stops the host with `outcome_unknown`.
Failure replies expose `host_state` and `handoff_required`. An active host's
pre-input rejection has `reobserve_before_retry: true`; a stopped host has
`reobserve_before_retry: false` and requires coordinator handling. This applies
to runtime and readback errors as well as timeouts. Do not switch controllers,
upload controls or resume-text modes while an upload outcome is unresolved.
Do not repeatedly click Upload merely because a native picker is outside the
page screenshot. If the supported chooser fails, preserve progress for host
handling. Selecting a file is not proof that an ATS accepted the attachment.

Use supplied authoritative materials for ordinary questions. A missing required
fact produces `needs_fact` for that job; the coordinator continues other jobs
and collects questions after the batch. Do not fabricate answers. Assessments,
security challenges, sensitive identity/financial material and unsupported legal
declarations likewise preserve the affected job for host handling. An uncertain
submission remains `submission_uncertain` and must not be replayed.

Worker authorization checks and prompt contracts have narrow unit coverage;
this does not establish live SSO, secure password filling, ATS acceptance or a
completed worker submission. Those require separate runtime evidence.

### Follow-up application observations, 2026-09-06

An attending IAB session subsequently completed four real applications across
Workday, Phenom and SuccessFactors. Email OTP verification and Google account
linking succeeded; Google sign-in also worked for a deferred draft. These are
attending-session results, not proof of unattended worker authentication.
The earlier blocked check below is historical.

Treat upload-before-entry as a useful choice when parsing is offered, not a
mandatory sequence. One observed form cleared unsaved answers after upload;
this does not establish a general ATS behaviour or its cause. Inspect the
settled state, preserve correct values, and repair actual changes only. Roche
listed accepted attachments on final review despite earlier delayed feedback.
A transient alert alone should not trigger a second upload or whole-form refill.
Use current controls to recover from focus changes and verify actual selected
options. No employer-specific reset rule or fixed action-count test is needed.

### Feedback choices and completion boundaries

Choose observations to answer the remaining question. DOM/accessibility is
useful for exact accessible names, required markers and selected values; a
screenshot can resolve a native attachment display or ambiguous layout. A page
URL, settled status or matching receipt establishes outcomes that a successful
click cannot establish. These sources complement each other; there is no fixed
sequence or requirement to collect every source after every action. In this
bridge, an action already returns a fresh observation, which can ground the next
action without another `observe` call.

In the AI Agent batch, an attachment filename was visible in a screenshot even
though DOM text and a file-property read did not expose it. That absent readout
was inconclusive, not evidence to upload again. A button's accessible name also
differed from its short visible text, and required labels included an asterisk.
Inspect current controls when a locator misses, and check actual checkbox or
selection state before choosing another supported interaction.

Return once the requested facts or a decisive blocker are established. Further
scrolling or screenshots should answer a remaining question. The Infineon
inspection reached a guest form but expired at its configured 180-second total
deadline. The retained final bridge request was a screenshot scroll with a
successful response; retained evidence does not isolate model time, host service
latency or redundant observation as the cause. The prompt now makes feedback
reuse and completion boundaries explicit; the timeout is unchanged, and improved
live completion time remains unverified. Employer login, Singpass and required
identity information remain external handoffs.

When a JD is already known but its current platform cannot accept an application,
consider the employer careers site, its official application entry, or another
recruiting platform carrying the same opening. This is a flexible search option,
not a required platform list or sequence. A worker restricted to its bound tab
hands broader searches to the host/coordinator. Before progressing, match the
company, title, location and available requisition ID, and consult the application
ledger across platforms. Reconcile any uncertain prior submission before another
attempt anywhere; changing platforms does not remove that uncertainty. Using a
normal official alternative does not bypass the original site's login or CAPTCHA,
and that site's barrier need not stop legitimate application paths elsewhere.

The 2026-09-06 small-platform test also informs CAPTCHA handling: passive badges
and background frames are not themselves blocking challenges. After an authorized
submit, observe the site's own verification outcome. An explicit rejection should
retain the error and prepared page for host handling; an ambiguous outcome needs
receipt reconciliation before another attempt. Manual clearance requires a fresh
observation, including whether the form already submitted. The worker does not
solve challenges or manipulate tokens. A confirmed rejected route can be handed
to the coordinator for a same-job official alternative without halting the batch.

Phone formatting is a soft reminder in both visual-worker and CLI prompts.
Inspect the rendered prefix/flag and number: some controls separate the country
code, while others infer it from a full international number. The Cynapse widget
interpreted a bare national number as another country's number, then displayed
Singapore correctly with the full +65 number. Consider visual feedback when DOM
values are inconclusive, and recheck after parsing or country changes when useful.
This guidance adds no hard gate, fixed observation sequence or extra required field.

### Upload migration check, 2026-09-06

The actual IAB adapter selected the existing 93,208-byte resume PDF through
the documented chooser API on `scripts/fixtures/upload-smoke.html`. An actual
CLI worker then independently used two MCP operations (observe, upload_artifact)
and read the matching filename and size. No desktop input or network submission
was used. Python bridge/worker checks: 36 passed; JavaScript host checks: 7 passed.

Roche 202607-119061 was reopened, but its application flow required fresh email
verification. MSD R400206 required Create Account/Sign In after Apply Manually
in both IAB and the connected Edge profile; the inspected MSD sign-in page had
email/password and no Google option. The IAB `browserAuth` request capability
was unavailable, and Edge advertised no browserAuth capability. No new
application was submitted. Password export, cookie copying and raw secret
entry are not implemented workarounds. Existing Edge connectivity does not
establish Google SSO or an authenticated employer session.

These changes live in the migration source tree. The unattended apply driver
and installed source copy have not been switched by this validation.

For user login/takeover, `host.pause('auth_required')` invalidates prior
observations and cancels unclaimed pending operations promptly. Preserve the
tab and original target. After the user is done, `host.resume()` observes that
same tab before making the host available; it does not navigate or replay old
input. The supervisor can return to the already recorded job URL in the same
tab if the login flow did not return there, then invalidate/reobserve before
continuing. Close a completed host with `host.close()`. No desktop polling
daemon or automatic per-click execution loop is installed.

## Roles and tools

- Coordinator: candidate queue and status; no click-by-click workflow planning.
- Discovery agent: optional recency, list, Posts and people exploration. It owns
  a discovery tab and returns leads with visible evidence.
- Application agent: one job and one application tab. DOM and visual controls
  are alternatives within this role, not separate agents or website-specific
  worker types. Material preparation can run independently without page input.
- Host review/submission/receipt: retain the current application gates and
  ledger. A different browser with the same URL is never audit evidence.
- Operator handoff: actual login/security/desktop exceptions; native Computer
  Use is not a dependency of ordinary web discovery or preparation.

Prefer guest or direct routes when offered. Search can continue while signed
out if the page permits it; only a real authentication barrier needs login.
24-hour and optional 8-hour searches are preferences, not eligibility gates.
Treat requested URL filters as unverified, and distinguish reposted from first
publication for reporting only. Reposts remain eligible under recency preferences; only exact prior submissions are excluded as duplicates. Ordinary exploration choices do not require new approval steps.

## Migration checks, 2026-09-06

- Actual discovery worker: opened Infineon's Internship - AI Solutions &
  Analytics from LinkedIn preferences in its original IAB tab. It distinguished
  the list's Posted wording from Reposted in the details and identified the
  company application entry without clicking Apply. Host inspection read back
  the same tab and job. This used the operational worker, not the fixture runner.
- Actual prepare worker: read Indeed's Workato Intern, Data Engineering (2942)
  and reported auth_required from the explicit account requirement. It left the
  original tab untouched. Login completion/return remains untested without a
  user login; this is not evidence that authentication is automated.
- Local guest fixture: the main `browser-work` CLI ran a worker which selected
  guest, entered Migration Test, and read B-202 and the entered value back. Host
  inspection and pause/resume retained that same tab. No final submit existed.
- Two real tool-contract mismatches were fixed: actions may request their result
  observation format with `mode`, and browser text entry may specify an observed
  text-input node. The latter focuses and types in that input; action controls
  are not accepted as text-entry targets. Focused-input typing remains supported.
  The targeted-input fix was also exercised in the real local Browser runtime.
- Unit checks cover IAB/CDP identity separation, stale observations, pause
  cancellation, interrupted inputs, observed-link navigation, targeted typing,
  worker phase/tool isolation and process timeout cleanup. Earlier application
  navigation and SuccessFactors identity cases also received a narrow rerun.

These checks qualify the attended discovery/prepare entry and same-page visible
review. They do not qualify full unattended submission, file uploads, a migrated
SubmissionGate, receipts, or end-to-end throughput.

The final guest rerun also used per-invocation worker context slimming:
`skills.max_context_tokens=1`, `features.plugins=false`, `features.apps=false`,
`features.multi_agent=false`, and `agents.enabled=false`. The attending Codex
host still applies the Browser skill. Global Codex configuration is untouched.
On this same local goal, page calls fell from five (including one rejected
targeted-input attempt) to three without errors. Reported total input tokens,
including cached input, fell from 228,128 to 65,731. Both context slimming and
fewer turns contributed; this is not an isolated benchmark or a throughput
claim. The final worker and independent host observation both confirmed B-202
and Migration Test. Combined narrow checks: 13 worker, 10 transport, 2 legacy
wiring, 4 host, 10 search, 32 earlier navigation/SuccessFactors cases passed.

## Legacy CDP visual attachment

This is an opt-in, prepare-only bridge from an isolated worker to an attending
Codex task's supported Browser or Computer Use tools. It is not an unattended
desktop daemon. The attending task reads the applicable Browser/Computer Use
skills, selects one returned tab/window, and services one request at a time.
The worker receives the actual observation and continues its own turn.

## Attach and operate

After supported Browser setup and reading its documentation, import
`scripts/visual-bridge-host.mjs` in that same supported JavaScript session.
Call `createVisualHost({directory, adapter: browserAdapter(tab), target})` with
a fresh queue directory and an already selected tab. For Computer Use use
`computerAdapter(sky, returnedWindow)` only after its required setup and target
selection. Never invoke this module as a standalone desktop automation process.

Attachment now first performs a real observation; a failed observation does not
advertise an active host or invite a worker to wait for an unusable controller.

The target records `application_url` and `cdp_port`. These are binding metadata,
not connection instructions. For the real application launcher, the attending
task must first verify that this is the worker's actual browser page/session,
then set `worker_session_verified: true` in the target. An unrelated Codex App
tab is not a worker CDP page just because its URL matches. Do not transfer
cookies, claim a shared session without evidence, or set this flag for the
standalone local fixture. Shared-session handoff into the application pipeline
has not yet been live-qualified.

Set `APPLYPILOT_VISUAL_BRIDGE_DIR` for the attended application launch. Only a
fresh host bound to the same CDP port and starting application URL is exposed
in prepare. Other phases and ordinary unattended launches keep their existing
tools. The tool is `applypilot_visual.visual_operation`; Codex may require
tool discovery before it is callable.

Use `host.peek()` to inspect a pending request without acting, then inspect the
current visible observation before `host.execute(request_id)`. This performs
one operation and returns a fresh observation to the waiting worker. Requests
support DOM/accessibility observations, screenshots, click, scroll, text and
navigation keys; arbitrary code and target selection are not accepted. Browser
navigation is restricted to exact links in the current observation. These
primitives do not themselves grant authorization for any
external action. The attending task applies the tool's policies and the user's
scope before executing a request. This legacy prepare attachment excludes final submission,
credential entry, security challenges and assessments from visual handoff.

Inputs require the latest `observation_id`. Use `host.invalidate()` after
another controller/user changes the page, then re-observe before further input.
Never issue Playwright and visual inputs concurrently. The bridge serializes
its own requests; cross-tool coordination remains the attending task's job.
`host.peek()` refreshes the heartbeat; `host.close()` marks the host stopped.
There is deliberately no unattended polling pump. Runtime/adapter failures
stop the host, invalidate observations and require deliberate fresh attachment.

Unclaimed requests expire and are cancelled atomically. Claimed requests with
no response produce `outcome_unknown` and block new requests while unresolved.
Inspect the page and reconcile that request before starting a fresh session;
do not delete a live claim or replay its input. Default request wait is 45 s,
optionally up to 120 s for attended smoke tests.

## Narrow smoke test, 2026-09-06

Serve `scripts/fixtures` on loopback port 8766 and attach the selected Browser
tab to `visual-worker-smoke.html`. Run `scripts/smoke_visual_worker.py` with
`--bridge-dir` from the source environment while the attending task services
requests. The script only accepts this local fixture. It takes the configured
Codex model; `--codex-executable` can select a verified installed executable.

Observed results:

- An actual isolated Codex worker called the MCP four times: observe, choose
  Last 8 hours, open Product intern, and enter Apply as guest. It read B-202
  before proceeding and confirmed `Guest application entry: B-202` afterwards.
- A separate child-process probe received one real PNG observation. A subsequent
  coordinate scroll moved the list down to Research intern, verified visually.
- Transport lifecycle checks: 7 passed. Worker configuration/session binding:
  2 passed. Search-window tests: 10 passed. No full regression run.
- The PATH CLI could not run the configured gpt-6-astra model. The already
  installed Codex App executable ran the smoke. No global installation changed.
- Windows Computer Use listed windows, but its runtime then stopped because it
  could not confidently determine the browser URL for policy enforcement.
  Desktop input stopped there. Its adapter is implemented but not live-qualified.

This proves supervised Browser control and result return on a local fixture.
It does not prove real LinkedIn/Indeed filtering, authentication, cross-browser
session sharing, native Computer Use input, or unattended application throughput.
Do not promote the visual bridge to the default driver based on this smoke.

## Follow-up: executable selection and screenshot control

The Windows worker resolver now compares verified stable versions from the
installed App bundle and PATH/npm candidates, selecting the newest. It resolves
this machine to 0.153.4 without a smoke-only executable override. An explicit
`APPLYPILOT_CODEX_EXECUTABLE` takes precedence and fails clearly if missing.
The global npm installation and PATH are unchanged. Four focused resolver tests
passed; the actual resolved executable returned `codex-cli 0.153.4`.

`smoke_visual_worker.py --visual` starts with a screenshot and asks the worker
to choose Last 24 hours by coordinates before receiving DOM data. The actual
worker clicked (488, 188) from that image, observed Last 24 hours, then used DOM
node clicks to open Product intern / B-202 and confirm its guest entry. This
demonstrates screenshot control and structured reading within one bound tab.

For a native Computer Use control, the supported Edge extension successfully
opened and read https://example.com/. The returned native window title was
Example Domain, but native `get_window_state` still stopped on the same URL
policy check. The issue is therefore not confined to an empty/new tab. No native
input or protection bypass was attempted after that stop. The in-app Browser
visual API works independently; this does not repair native Windows Computer Use.
The two focused host tests cover stale observation/interruption and rejecting
an unobservable target before advertising readiness.

## Attended application ledger

The IAB adapter additionally reports `form_state`, `changed_fields`, and
`post_upload_changes`. Bounded `fill_control`, `select_control`, and `set_checked`
operations require a fresh observed field key and recheck identity before input.
Native date/month inputs advance through keyboard segments until focus leaves
the input (bounded to four Tabs), so blur validation is actually exercised.
`control_result.persisted` is an immediate readback, not a guarantee against a
later asynchronous parser or validator. Observe again after visible loading
settles; upload baselines use the fresh form read immediately before that upload
and remain available across consecutive read-only observations for delayed parsing.
The next input attempt (including another upload) or an observed departure from
the page retires the old baseline permanently. Later corrections are therefore
not attributed to that upload, and returning to the original URL does not revive
old deltas. `changed_fields` still describes changes between observations; an
empty `post_upload_changes` does not prove parsing is finished or the form is ready.
Changed values are untrusted observations requiring comparison with candidate facts.
Visible top-document and open Shadow DOM controls are covered. Linked option
lists resolve through ancestor open roots with unique matches, including slotted
labels. `iframe_count` advertises the remaining inspection boundary. Protected credential/identity/consent controls
are excluded from these generic operations. Supported IAB DOM snapshots supply
live input values where the read-only DOM scope omits them; unavailable values
are reported as unknown rather than empty. Anonymous controls in flattened/slotted
shadow trees may still require independent semantic or screenshot readback.
React Select's `selected_display` preserves its visible choice separately from
the cleared search input; exact string persistence alone is not a semantic
country/phone verification.

### Complex controls and structural diagnostics

`open_control {field_key}` opens an observed single combobox.
`search_control {field_key, value}` types a query only into an observed editable
native INPUT with the combobox role. Both return `persisted: null`: opening a
menu or typing a query is not selection. If suggestions arrive asynchronously,
observe again, then use `select_control {field_key, value}` with one exact,
unique, currently observed option. Missing or ambiguous suggestions do not
authorize a guessed choice. A search-input value alone is not selection proof.

Native `<select multiple>` accepts `select_control {field_key, values}` with a
nonempty array of exact observed options. It replaces the selection and checks
the entire `selected_values` set using live DOM snapshot evidence. Empty-array
clearing is rejected before input because the current IAB selection API does
not support it. Custom multi-select add/remove semantics remain unsupported.
`set_checked {field_key, checked: true}` also supports native radio controls
when their complete group is observed. Group identity includes DOM root, form
owner and name; readback checks that the selected member is checked and its
peers are clear. Direct radio deselection is rejected.

`structure_changes` reports bounded added/removed/changed/reordered field keys
and option changes; `post_upload_structure_changes` uses the current upload
baseline. A changed row or option list requires a new observation before input.
Transient DOM node identity helps reject reused selectors after row replacement;
it is not persisted as a reusable recipe. These operations do not expand the
routine scalar-only `fill_batch` subset or frame/closed-shadow coverage.

Recipe shadow telemetry adds value-free `diagnostic_codes` and capped
`diagnostic_counts` alongside the existing outcome/reason. Codes describe actual
observed limitations such as multi-select, truncated options, repeated semantic
fields, unsupported controls, frame scope or unavailable cache candidates.
They contain no labels, values, selectors or raw exceptions. A cache miss does
not claim a cause that the snapshot cannot prove. Native multiple controls are
excluded from routine recipe persistence; diagnostics grant no write authority.

The existing protected-word matcher is conservative: for example, `acceptable`
can match its `accept` guard and exclude an ordinary preference control. This
known false positive remains unchanged; the host must inspect such omissions
rather than treating its observed field list as complete.

Before authorization, an attending operator may record an observed optional or
absent cover requirement without creating a preview status or an attempt:

```powershell
../run.ps1 mark-cover-not-required --url <exact-job-or-application-url> --verified-by codex_root --bridge-dir <active-host-directory> --observation-file <inspection.json>
```

Save the fresh `host.inspect()` result with `source: "attending_host"`,
timezone-aware `observed_at`, nonempty `evidence_refs`, and `all_form_checked: true`.
For the reviewed visible-marker convention, add:

```json
{"cover_letter":{"status":"optional","operator_attested":true,"field_keys":["the observed cover field key"],"basis":"visible_required_marker_convention","evidence_text":"Cover Letter:"}}
```

The evidence text must occur in the actual page snapshot. The cover controls
must be unmarked, while at least two distinct observed peers have visible
required markers. Explicit optional wording may instead use
`basis: "explicit_optional_text"`. Absent cover evidence uses `status: "absent"`
and empty `field_keys`, with no cover controls or cover text in the complete
observation. This trusted operator boundary requires active matching IAB
host/session/tab/URL and supported whole-form coverage, including no unreviewed
frames. It does not authenticate operator claims. Observations expire after
five minutes; host freshness is checked separately. Changes to job identity,
ownership, or readiness during review reject the update. Existing preview use
without these two flags is unchanged.

`applypilot attended-application --db PATH --request-file request.json` connects an
attending Codex operator's browser observations to the existing application ledger.
It performs no browser action. It is a trusted operator ingestion seam, not an
attestation service: the host must independently inspect the current tab; child
worker prose and missing observations must not be translated into passing flags.
Existing submission admission, authorization manifest, frozen materials, live
snapshot validator, duplicate revalidation, SubmissionGate, rate/capacity policy,
and exact-bound receipt reconciliation are reused.

Duplicate revalidation covers evidence already recorded in the application ledger;
an empty receipt table or a failed preparation attempt does not prove that the job
was never submitted through another channel. Reconcile known employer receipts
and recruiter outcomes, including relevant archived or deleted application mail,
before retrying an ambiguous job. Match employer/ATS URL aliases as well as exact
job URLs. Keep the historical submission separate from its later hiring outcome.

Material freezing includes the resume version system's experience, project
references, resume facts and skill boundaries, in addition to identity and
education. Only categorized hashes are stored. Changing any of these facts
invalidates active preparation and requires a fresh audit, even when an existing
resume may still qualify for library reuse. Attempts frozen under an older fact
binding must also be prepared again.

Every request includes `action`, exact `job_url`, `host_session_id`, and `tab_id`.
After `begin`, also include the returned `attempt_id`. Host identity values come
from the current host session and target tab, never a stale browser handle.

1. `begin`: add `manifest_path` pointing to an existing authorized exact-job
   manifest. Returns the leased attempt and frozen material binding. In the same
   transaction it acquires the existing jobs `in_progress` / `agent_id` /
   `apply_task_id` ownership fields, so native queue workers cannot take the job.
   Retrying begin with the same job, host/tab, and manifest recovers the existing
   attempt through resume; it does not create a second attempt.
2. `checkpoint`: add `source: "attending_host"`, timezone-aware `observed_at`,
   nonempty `evidence_refs`, and an independently observed `snapshot`. Only its
   digest and evidence references are stored; raw field values are not persisted.
3. `resume`: validates identity/material bytes and renews a still-active lease.
   A material change invalidates preparation; finalize that attempt and begin with
   current materials/authorization. An expired lease is not silently revived.
4. `claim`: add the same freshly observed `snapshot`. Existing audit and admission
   must pass; returns `gate_id` and `checkpoint_digest`. Same-attempt claim replay
   is idempotent. A claimed checkpoint is immutable.
5. `submit-intent`: add that snapshot, `checkpoint_digest`, and fresh host
   observation metadata. Only after this command succeeds may the attending host
   perform the one authorized external submit action. Lost output is resolved by
   `resume`, never by replaying the click. The durable latch intentionally favors
   possible incomplete submission over duplicate submission after a crash.
6. `receipt`: add fresh observation metadata and `receipt` containing the existing
   receipt envelope (`source`, `receipt_id`, `company_name`, `job_title`, and
   decisive `confirmation_text` or portal status). Exact attempt/gate/job binding
   is injected and conflicting supplied binding is rejected. Submitted attempts
   enter the existing uncertain gate/batch state before receipt admission; only
   decisive admitted evidence closes them as applied. A rejected receipt rolls back
   that tentative transition, leaving all prior ledger states intact. Use `fail`
   when a completed submit action has no decisive receipt and must be recorded as
   uncertain. Reconciliation also works
   after a lease expires or an uncertain failure is finalized.
7. `fail`: add `reason`. Before intent this terminates preparation; after intent
   it preserves `submission_uncertain`. `resume` after intent permits only receipt
   reconciliation, never another browser submission.

Snapshot fields required for claim/intent are the existing validator's `url`,
`required_unfilled`, `sensitive_required_unknown`, `file_fields`, `form_fields`,
`text_fields`, `select_fields`, `full_name_values`, `email_values`,
`captcha_visible`, `verification_visible`, `assessment_visible`,
`resume_field_present`, `resume_uploaded`, and `submit_control_count`. Include
other observed validator fields when applicable. Upload state must come from
accepted attachment evidence; absence of a visible file input is not upload proof.
All-page completeness and custom widget interpretation remain the attending
operator's responsibility. Unsupported or unobserved fields must not be filled
with empty arrays/false just to pass. Snapshot/evidence observations expire after
five minutes. The CLI requires the fresh snapshot to match the prepared digest;
changes before claim require a new checkpoint. Changes after claim require stopping
that attempt. Browser/session migration is deliberately not implicit.

The command does not grant authorization, lower eligibility/material requirements,
handle credentials, or replace the native browser tool's safety boundaries. Request
files may contain personal values: use controlled local temporary files and retain
only necessary evidence references. Do not put passwords or verification codes in
request files, snapshots, evidence references, or failure reasons.

### Earlier-step resume evidence

An attended multi-step form can use `upload-checkpoint` before its final review
checkpoint. It accepts the common exact job/attempt/host/tab binding plus fresh
`source`, `observed_at`, `evidence_refs`, and this `upload` object:

```json
{
  "attempt_id": "the current attempt ID",
  "page_url": "the actually observed upload page URL",
  "field_label": "Resume",
  "visible_filename": "resume.pdf",
  "sha256": "the frozen resume digest",
  "size": 90362,
  "acceptance_marker": "attachment_card",
  "accepted_attachment_text": "resume.pdf Remove attachment"
}
```

`acceptance_marker` must describe an observed `attachment_card` or
`uploaded_file_list`. A file-picker selection, bare success flag, or missing field
is not acceptance evidence. The filename must match the bound PDF, bytes must
match the frozen resume, and the observed page must remain in the same application
flow (including embedded query job identifiers). Compact proof retains references,
filename, material binding, and an accepted-text digest; raw attachment text is not
stored. This is still trusted attending-host observation, not independent server
verification of uploaded bytes.

A valid proof is scoped to the original leased attempt and host/tab. It is usable
on a later final page only when that page has no resume field. The seam constructs
only the existing validator's small resume-upload proof from its accepted state;
request dictionaries cannot inject arbitrary agent observations. Material changes
invalidate it. An observed present-but-empty resume control removes the old proof.
Capturing upload proof invalidates any prior final-page checkpoint; capture the
final page again. Upload evidence cannot be changed after gate claim. Reusing an
old upload object under another attempt is rejected.
# Concurrent preparation

For the integrated CLI + IAB queue, bounded routine field batches and read-only
application state view, see [Attended runtime batch](attended-runtime-batch.md).
Default preparation concurrency is two; the attending Codex task still reviews
and services requests, and submission retains the existing single authority.
