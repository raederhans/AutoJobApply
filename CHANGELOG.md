# Changelog

All notable changes to Job Apply Pilot will be documented in this file.

## [Unreleased]

### Added

- Project subagents for agent harnesses: `.codex/agents/` defines `applypilot-job-scout`
  (lead collection on a light model), `applypilot-fit-reviewer` (second look at a
  borderline score) and `applypilot-materials-reviewer` (evidence check of a draft), with
  Claude Code mirrors in `.claude/agents/`. They hold no import, form, submission or
  credential authority; AGENTS.md records the delegation boundary.
- `status --json` and `doctor --json` for machine-readable pipeline counts and setup
  checks; `doctor --json` includes the model chain each CLI task resolves to.
- `followup` for local recruiting feedback: reviewed JSON event import, per-job
  timelines, pending-event resolution, actions with timezone-aware deadlines,
  statistics, legacy history import and `.ics` export. It consumes operator-supplied
  files and does not scan or send email.
- Application progress tracked once per recruiting identity, separating current state
  from historical stage reach; the dashboard shows counts, conversions, actual paths
  and timelines, and missing rounds, dates and receipts stay unknown.
- `followup export-progress` for private CSV/Markdown progress snapshots with inclusive
  UTC date and status filters, and `followup export-html` for self-contained offline
  HTML snapshots of progress and reminders. The workbench previews and downloads the
  same reports.
- Local follow-up reminders (`followup reminders`, `set-reminder`, `snooze-reminder`,
  `dismiss-reminder`) grouped by UTC day. They are stored in a separate local file and
  never change application status or the original action deadline; reminder groups
  download as dated `.ics` snapshots, and none is offered when no dated event remains.
- `compare-jobs` for side-by-side HTML and Markdown comparison of 2-6 saved jobs, with
  `--list`, `--query` and repeatable `--job-id`. It never scores jobs or infers salary or
  deadlines; the workbench compares and downloads 2-4 selected progress records.
- `career` workbench for career-gap work: source-backed catalog and evidence import,
  per-criterion diagnosis, versioned learning routes, task submissions and reviews,
  and a loopback local UI (`career serve` / `career open`). Completing a task never
  changes application status or skill level.
- Workbench application inbox for paused applications: save a human answer, then
  explicitly request continuation; browse and search history, view source-timed
  progress, and link progress records to their exact inbox entries.
- Workbench interview tracking tied to the exact job and round: preparation, to-dos,
  results and rescheduling, plus interview search, deadline filters and overdue
  visibility.
- `interview prepare` for source-bound interview packs, local by default, with an
  optional `--use-llm` question pass that is validated and falls back to the local
  pack; `json-resume export`, `import` and `check` for JSON Resume exchange; and
  `quality run` / `export-promptfoo` for offline evidence checks.
- `writing` for standalone ATS open-question answers and cover letters grounded in
  registered evidence, with `context-check`, `questions`, `plan`, `answer`, `cover`,
  `evaluate` and `benchmark`. Drafts grant no browser-fill or submission authority.
- `radar discover-careers` for official careers-page entry discovery, `radar lifecycle`
  for posting open/closed evidence and advisory repost hints, and `radar budget` for
  evidence-based search budgets; `radar explore` and `radar collect` accept `--budget`,
  and `radar collect` also accepts `--due-only`.
- `browser-prepare plan` / `execute` adapting reviewed Stagehand `observe()` results
  into the existing Host queue without the Stagehand SDK, and `browser-host-path` to
  locate the shipped Host modules of an installation.
- Supervised browser Host additions: observed record semantics, bounded
  `prepare_subgoal`, guarded attended recovery with source-bound consistency checks,
  observed file selection with `required_source`, an explicit Playwright
  `observationMode`, and operation slots shared by concurrent `execute` calls.
- Bounded harness measurement, offline paired experiment planning and isolated
  interactive evaluation with validated, opt-in, read-only procedural advice.
- User guides and fictional examples for the commands above, included in source
  release archives.

### Changed

- LLM calls are routed by task tier: extraction uses the `fast` tier, scoring, resume
  judging and interview packs `standard`, and resume tailoring, cover letters, answers,
  writing and career suggestions `quality`. `LLM_MODEL_<TASK>`, `LLM_MODEL_<TIER>` and
  `LLM_MODEL` override in that order and accept comma-separated fallback chains. When
  the provider reports a model unknown, retired or out of quota after retries, the
  next model in the chain serves the rest of the process and is the model recorded on
  generated artifacts. With no override, DeepSeek extraction now uses `deepseek-flash`
  and Gemini extraction `gemini-3.5-flash-lite`; other tasks keep their previous model.
  `applypilot doctor` lists the resolved chain for every task.
- The database now applies eight ordered migrations instead of one (posting lifecycle,
  follow-up, career, career learning, workbench and workbench publication tables, and
  detail-fetch retry state).
  Existing workspaces upgrade in place on first open; an earlier release fails closed
  on the newer schema version and will not open an upgraded workspace.
- `.env.example` no longer lists the unused `PROXY` variable. JobSpy and Workday
  proxies use the top-level `proxy` setting in `searches.yaml`.
- JD resumes are evidence-led and reusable: supported product and applied-research
  titles route by duties, supplemental fact sources are bound to generated editions and
  rechecked on reuse and promotion, and page fill and short tails are editorial
  warnings rather than hard failures.
- README gains bilingual workflow demos and a getting-started guide.
- Removed unused internal helpers and a superseded browser cleanup path; no command
  was removed.
- CI installs core development and job-board extras together and reuses its
  installed build backend for release builds. Source-to-wheel builds, archive
  privacy audits, compatibility tiers and clean-install smoke checks remain enabled.
- `applypilot init` asks explicitly whether to search remote jobs only (default no,
  unless the location is `Remote`); a radius of 0 no longer turns a named city into a
  remote-only search. The generated `searches.yaml` is written with YAML quoting, and
  the AI step updates only its own keys in an existing `.env`, keeping unrelated lines
  and commenting out a competing provider key instead of discarding the file.
- Development-only experiments and benchmarks (harness evaluation and measurement,
  dry-run, session-aging, runtime-cell diagnostics, the P4 no-submit worker cohort and
  unwired fast paths) moved from
  `applypilot.apply` to a separate `applypilot_lab` source package that is not part of
  the installed wheel. No user command changed.
- Launcher failure classification, ATS application context and Agent turn journaling
  live in their own modules, and shared runtime and career contracts moved to a
  dependency-free `applypilot.contracts` package so storage no longer imports the
  apply or career layers. Old import paths remain as re-exports.
- The test suite runs against a temporary workspace and never writes to
  `~/.applypilot`, and dumps thread stacks when one test phase stalls for 120 seconds.
- Every `manual_review_required` outcome, whether the Agent reports a missing fact or
  the host stops for review, now goes to the human review queue instead of being parked
  as an unclassified failure for automated diagnosis. It grants no retry, resume or
  submission authority.
- `applypilot init` no longer asks for or stores a job-site password; it points to
  `applypilot browser-session` with a persistent browser profile instead. A plaintext
  password left in `profile.json` by an older setup is not carried into the rewritten
  profile; nothing read it, and the credential relay's encrypted store is unaffected.
  The wizard also asks for the Indeed country site,
  defaulting from the target location, and writes `country` to `searches.yaml`;
  discovery warns when Indeed falls back to the USA site.
- Detail enrichment retries transient failures (HTTP 408/429, 5xx other than 501/505,
  navigation timeouts and connection resets) for up to three attempts with 1h and 6h
  backoff before marking them permanent. A rate limit pauses the whole site, including
  jobs discovered later, until the limited job is due again.
- Runtime output directories use short fixed-length digests instead of readable names
  (full identities stay in the runtime namespace record). On Windows, a workspace path
  that leaves no room for an Agent working directory fails with
  `failed:workspace_path_too_long` before a browser is leased.

### Fixed

- A closed or otherwise failed posting that the browser agent reports during observation
  (or, in a preview, during preparation) is classified by its typed failure, for example
  `expired`, instead of `failed:conflicting_agent_results`.
- Fetches of official employer and ATS pages (`radar advance`, official boards, Workday)
  trust the Mozilla CA bundle in addition to the operating-system store, so a site whose
  newer root is missing from the local Windows store no longer fails TLS verification.
- Detail enrichment, smart extraction and resume PDF rendering fall back to the installed
  Edge/Chrome when Playwright's bundled Chromium has not been downloaded for the current
  Playwright release, instead of failing at launch.
- Detail enrichment reads the description block of LinkedIn's public job pages
  deterministically; these pages previously fell through to the LLM tier, which often
  returned nothing.
- An explicit `--workspace` given after configuration was already loaded in the same
  process rebinds every workspace path, including copies held by other modules.
- Read-only report, plan and export commands open the database through one helper that
  refuses writes; the `attended-plan` command now closes its connection.
- The Gemini default model is now `gemini-3.8-flash`; the previous default,
  `gemini-2.0-flash`, was shut down by Google on 2026-06-01. `doctor` and `init`
  share the same defaults. An explicit `LLM_MODEL` still wins.
- Workday discovery reads `employers.yaml` from the active workspace when present,
  replacing the shipped registry, so a custom employer list survives reinstalls.
- `apply --agent-backend claude` runs again. Since 0.5 the subprocess launch check
  rejected the empty `--tools ""` argument the Claude CLI needs, so every Claude turn
  failed before the CLI started.
- Claude turns whose structured report and final `RESULT` agree are no longer recorded
  as `failed:conflicting_agent_results` (the result line was read twice from the
  stream). Claude now receives the visual bridge's form observation, and a configured
  reasoning effort is passed to Claude with `--effort`.
- The career workbench server reads the remaining request body before answering a
  rejected or invalid POST, so clients receive the JSON error instead of a reset
  connection (`WinError 10053/10054` on Windows).
- Concurrent first initialization of a new database no longer fails with
  "database is locked" while switching to WAL.
- Discovery and enrichment: a missing JobSpy remote flag is no longer stored as remote;
  JSON-LD postings with a list-valued `@type` or inside `@graph` are extracted by detail
  enrichment and smart extraction; one failing job no longer aborts its enrichment batch,
  and streaming enrichment stops instead of re-selecting jobs that make no progress; an
  official-source transport that raises `TypeError` is no longer retried without
  headers; empty `sites.yaml`, `employers.yaml` or `searches.yaml` files count as empty.
- Streaming `run` reports discover as partial when a sub-scraper fails, matching
  sequential mode, instead of reporting success.

- Attended Host recovery and resume are bound to durable identity and grant no replay
  authority; delayed batch verification is invalidated at the Host refresh boundary;
  Host request and permit envelopes are bound before input; browser deadlines apply at
  every input boundary.
- Dynamic tool schemas are validated on input and output, including nullable type
  unions, and JSON booleans are no longer treated as numbers.
- Installed packages ship and resolve the Browser Host modules, and the runtime-cell
  source identity is computed the same way from an installed package; Windows listener
  attestation works with localized `netstat` output.
- Task cancellation recovers correctly and specialist subprocess cleanup is bounded.
  A saved workbench answer is consumed once by its original owner and kept out of logs.
- Workbench filters and stale-session recovery, the linked inbox filter query, unknown
  versus zero inbox counts, and calendar downloads with no exportable events.
- Harness evaluation keeps trials with malformed handoff classifications, rejects
  harmful handoffs, does not invent wait time for blocked handoffs, and binds evidence
  to runtime sources.

## [0.7.0] - 2026-09-22

### Added

- Attended `browser-batch` preparation for CLI workers bound to separate in-app
  browser tabs: default concurrency 2, configurable 1-4, per-hostname admission,
  available-memory reserve, exclusive worker leases, isolated failures and progress.
- IAB `fill_batch` for up to four observed text/native-select fields, with
  per-field verification and explicit partial progress when a form changes.
- Read-only `attended-plan` projection of existing attempts, materials, blockers
  and exact admitted receipts, without another executor or submission authority.
- Public real ATS test results and usage guidance. Three Lever rounds exercised
  1/2/4 workers and independently verified 32 contact-field values; these are
  preparation samples, not full applications or a long-run capacity guarantee.

### Fixed

- Batch workers launch from the installed package instead of relying on a source
  checkout's scripts directory. Clean-install smoke checks cover the new entries.
- POSIX batch cancellation forwards termination so workers clean up their isolated
  CLI/MCP descendants; spawn registration defers signals until ownership is recorded.

### Changed

- Extracted browser command handlers from the CLI module. App Server and the older
  external runtime/recipe experiments remain disabled; submissions retain the
  existing attended review, reservation, gate and receipt path.

## [0.6.0] - 2026-09-22

### Added

- Ordered, atomic top-level database migrations with explicit version checks,
  legacy upgrades, newer-schema fail-closed behavior, and rollback coverage.
- Shared transaction ownership and savepoint helpers across job, runtime,
  semantic-write, task-journal, and submission-ledger storage paths.
- Explicit runtime authority boundaries for preflight, application facts, job
  acquisition/results, agent processes, submission authority, and worker cells.
- Privacy-safe browser-tool duration samples and per-tool aggregates for
  diagnosing interaction latency without persisting field values or page data.

### Changed

- Renamed the public product identity from **CapyPilot** to **Job Apply Pilot**
  for clearer search visibility and immediate product recognition. Technical
  identifiers and runtime contracts remain unchanged.
- Replaced the mascot-led identity with a direct job-card, application-route,
  and verified-receipt system across the README and read-only dashboard.
- Rewrote the bilingual project overview around concrete capabilities, the
  Discover → Decide → Prepare → Verify workflow, and the product-contract
  differences from the original Pickle-Pixel/ApplyPilot project.
- Moved retired candidate facts into explicit user-owned lineage policy and
  shortened CI feedback while retaining Python 3.11/3.12/3.13, Chromium, and
  Windows clean-install coverage.
- Reduced redundant Lever browser round trips by grouping independent native
  selects behind one shared verification readback, while retaining individual
  handling for custom, conditional, multi-select, rerendered, or failed fields.

### Fixed

- Expired browser scope leases are reaped before exact-scope release, avoiding
  stale ownership that could block later application attempts.
- Preserved submission, receipt, duplicate, stale-attempt, and stale-page
  fail-closed behavior across the extracted runtime modules.

## [0.5.2] - 2026-09-21

### Added

- Immutable resume content, render, generation, and validation histories, with
  guarded curation, rollback, and exact input bindings for scoring and routing.
- A supervised attended-application path that binds browser observations,
  reviewed materials, authorization, single-submit intent, and receipt outcomes
  to the same durable application attempt.
- Evidence-backed two-week company prioritization with bounded penalties,
  recovery, progress resets, and explicit coverage requirements. Eligibility,
  fit scores, and submission gates remain unchanged.
- Bounded, value-free attended-host queue/action/observation diagnostics through
  `host.metrics()`, with explicit unavailable measurements and inspection coverage.
- Node contract tests and offline Chromium form checks in the existing Python
  core, browser, and Windows CI tiers. See
  [attended observation performance](docs/attended-observation-performance.md).

### Changed

- Resume routing now prefers healthy, evidence-dense reusable variants, preserves
  current-first experience ordering, and uses source-grounded targeted repairs
  instead of repeatedly regenerating complete documents.
- Attended form preparation now handles complex controls, open Shadow DOM,
  upload deltas, bounded form pagination, and selected-display verification while
  retaining fresh identity and submission-safety checks.
- Reuse one immediate, validated form readback in attended in-app-browser prepare
  replies while retaining current visible DOM. Explicit observations, uploads,
  screenshots, structural changes and submit-phase observations stay on the full
  path. No CAPTCHA, external-browser or production-concurrency policy changes.

### Added

- Bounded, value-free attended-host queue/action/observation diagnostics through
  `host.metrics()`, with explicit unavailable measurements and inspection coverage.
- Node contract tests and offline Chromium form checks in the existing Python
  core, browser and Windows CI tiers. See
  [attended observation performance](docs/attended-observation-performance.md).

### Fixed

- Job-description, selected-source, profile-fact, and supplemental-evidence
  changes now invalidate stale unsubmitted scoring and preparation projections
  without rewriting submitted or uncertain application history.
- Do not report an earlier control's persistence as current after a full refresh
  observes a changed value; explicit refreshes do not carry old control results.

## [0.5.1] - 2026-09-06

### Fixed

- Install Chromium in the tagged-release runner before executing browser tests.
  The v0.5.0 tag failed verification because the runner lacked Chromium; no
  v0.5.0 release artifacts were published. v0.5.1 includes the brand changes below.

## [0.5.0] - 2026-09-06

### Changed

- Renamed the public product identity to **CapyPilot** while retaining the
  `applypilot-local` distribution, `applypilot` package and CLI,
  `APPLYPILOT_*` environment variables, local storage identifiers, and
  repository URLs for compatibility.

### Added

- CapyPilot visual identity and bilingual product documentation.
- Expanded employer discovery with independent employer review before board
  leads enter the verified opportunity pipeline.
- Qualified resume reuse and safeguards against stale profile facts.
- Browser runtime and receipt reconciliation improvements that preserve
  application progress across verification and feedback failures.

### Fixed

- Manual `apply --mark-applied` and `--mark-failed` updates now accept either
  the canonical job URL or one uniquely matching application URL, and fail
  clearly instead of reporting success when no database row was updated.

## [0.4.0] - 2026-08-26

### Changed

- Established the independent **ApplyPilot Local** product identity while
  retaining the `applypilot` import package and CLI for compatibility.
- Reframed the product around local-first storage, explicit authorization,
  source provenance, validated resume reuse, and receipt-backed application
  status.
- Moved broad third-party job-board discovery behind the optional
  `jobboards` extra so the core product no longer inherits JobSpy's pinned
  NumPy/Pandas dependency chain.
- Added a single capability preflight before JobSpy expands its search matrix,
  with explicit Python 3.11-3.12 compatibility guidance.
- Replaced per-row Pandas Series construction in JobSpy ingestion and location
  filtering with record/list operations for substantially lower batch overhead.
- Restricted source distributions to an explicit release file set so local
  research notes, experiments, receipts, and runtime data cannot be packaged.
- Enabled automatic CI for pushes and pull requests, with lint, full tests,
  clean package builds, and dependency caching.
- Added a non-interactive, file-based onboarding path and browser-free
  dashboard generation for deterministic clean-workspace checks.
- Added a Windows release smoke job covering wheel install, initialization,
  setup diagnosis, and packaged dashboard generation.
- Gated tagged publishing on version parity before creating a GitHub Release
  with wheel, source archive, verified bundle, and checksums; PyPI publishing
  remains an explicit repository opt-in until its trusted publisher is ready.

### Added

- Fork provenance and AGPL network-deployment guidance in `NOTICE.md`.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-02-17

### Added
- **Parallel workers for discovery/enrichment** - `applypilot run --workers N` enables
  ThreadPoolExecutor-based parallelism for Workday scraping, smart extract, and detail
  enrichment. Default is sequential (1); power users can scale up.
- **Apply utility modes** - `--gen` (generate prompt for manual debugging), `--mark-applied`,
  `--mark-failed`, `--reset-failed` flags on `applypilot apply`
- **Dry-run mode** - `applypilot apply --dry-run` fills forms without clicking Submit
- **5 new tracking columns** - `agent_id`, `last_attempted_at`, `apply_duration_ms`,
  `apply_task_id`, `verification_confidence` for better apply-stage observability
- **Manual ATS detection** - `manual_ats` list in `config/sites.yaml` skips sites with
  unsolvable CAPTCHAs (e.g. TCS iBegin)
- **Qwen3 `/no_think` optimization** - automatically saves tokens when using Qwen models
- **`config.DEFAULTS`** - centralized dict for magic numbers (`min_score`, `max_apply_attempts`,
  `poll_interval`, `apply_timeout`, `viewport`)

### Fixed
- **Config YAML not found after install** - moved `config/` into the package at
  `src/applypilot/config/` so YAML files (employers, sites, searches) ship with `pip install`
- **Search config format mismatch** - wizard wrote `searches:` key but discovery code
  expected `queries:` with tier support. Aligned wizard output and example config
- **JobSpy install isolation** - removed python-jobspy from package dependencies due to
  broken numpy==1.26.3 exact pin in jobspy metadata. Installed separately with `--no-deps`
- **Scoring batch limit** - default limit of 50 silently left jobs unscored across runs.
  Changed to no limit (scores all pending jobs in one pass)
- **Missing logging output** - added `logging.basicConfig(INFO)` so per-job progress for
  scoring, tailoring, and cover letters is visible during pipeline runs

### Changed
- **Blocked sites externalized** - moved from hardcoded sets in launcher.py to
  `config/sites.yaml` under `blocked:` key
- **Site base URLs externalized** - moved from hardcoded dict in detail.py to
  `config/sites.yaml` under `base_urls:` key
- **SSO domains externalized** - moved from hardcoded list in prompt.py to
  `config/sites.yaml` under `blocked_sso:` key
- **Prompt improvements** - screening context uses `target_role` from profile,
  salary section includes `currency_conversion_note` and dynamic hourly rate examples
- **`acquire_job()` fixed** - writes `agent_id` and `last_attempted_at` to proper columns
  instead of misusing `apply_error`
- **`profile.example.json`** - added `currency_conversion_note` and `target_role` fields

## [0.1.0] - 2026-02-17

### Added
- 6-stage pipeline: discover, enrich, score, tailor, cover letter, apply
- Multi-source job discovery: Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs
- Workday employer portal support (46 preconfigured employers)
- Direct career site scraping (28 preconfigured sites)
- 3-tier job description extraction cascade (JSON-LD, CSS selectors, AI fallback)
- AI-powered job scoring (1-10 fit scale with rationale)
- Resume tailoring with factual preservation (no fabrication)
- Cover letter generation per job
- Autonomous browser-based application submission via Playwright
- Interactive setup wizard (`applypilot init`)
- Cross-platform Chrome/Chromium detection (Windows, macOS, Linux)
- Multi-provider LLM support (Gemini, OpenAI, local models via OpenAI-compatible endpoints)
- Pipeline stats and HTML results dashboard
- YAML-based configuration for employers, career sites, and search queries
- Job deduplication across sources
- Configurable score threshold filtering
- Safety limits for maximum applications per run
- Detailed application results logging
