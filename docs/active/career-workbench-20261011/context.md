# Integration context

Base: 357e805 (owner remote fork/main). Branch: codex/career-workbench-20261011. The primary checkout remains at 48bec07 with unrelated active WIP; it must not be reset or broadly copied over. Shared files were compared against current remote: CLI, database, LLM and dashboard deltas are career-owned; packaging retains the extra documentation already merged upstream.

Primary agent owns all Git mutations and live validation. Read-only reviewers cover (1) diagnosis/evidence/learning and (2) local host/worker/frontend. No reviewer owns the operational database or browser.

Validation owner: primary agent. Working directory: this isolated worktree. Interpreter: existing sibling .venv, with PYTHONPATH explicitly bound to this worktree/src. Test databases and ports are temporary fixtures; operational data and the user's running local service are untouched. Logs: sibling data/career-integration-20261011/targeted.log and core.log, outside publication scope. Success requires pytest exit 0; failures are diagnosed before retries. Reviewers may read completed output but do not monitor or duplicate these runs.

The user-selected Edge conversation is already open and has been read. Its next work concerns agent semantic observation, bounded goal execution, recovery, reusable experience, latency, application consistency and controlled same-model evaluation. No message sent yet; first complete the career delivery.

Review corrections: descendant task reset events invalidate older inherited assessments without blocking newer reviews; UI selection reads are fenced and published atomically, including delayed background results and dialogs. Core reviewer has accepted the assessment fix. Host review is verifying final frontend races with deferred responses.

Validation to date: initial targeted suite 395 passed / 1 skipped; assessment/service/HTTP suite 35 passed, then final assessment suite 20 passed. Full core suite: 3886 passed / 4 skipped / 100 deselected, with one environment-sensitive existing launcher assertion failing because the explicit PYTHONPATH adds an allowed MCP environment variable. That assertion passed after removing PYTHONPATH; pytest's own configured pythonpath still resolves this worktree. No product change was needed. Ruff and release archive audit passed. Final frontend checks and final-commit CI remain required.

Final frontend validation: 20 deferred-response Node cases passed through the Python core-test bridge; JavaScript syntax passed. Cases include switching targets, history, bootstrap refresh, saved selection ownership, late dialogs, progress writes, old background snapshots and completed results deferred while busy. Final distributions were rebuilt and audited after these changes. Remote CI is the remaining integration gate. Retain this isolated checkout after delivery as the verified version until unrelated work in the shared source is reconciled; do not reset the shared source to make it match.
