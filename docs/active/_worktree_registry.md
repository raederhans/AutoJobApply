# Worktree Registry

| Worktree / path | Task | Base branch / commit | Current branch / HEAD | Goal | State | Hotspots | Tests | Overlap | Order | Next action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `C:/Users/raede/.codex/worktrees/applypilot-architecture-convergence-20260922` | Architecture convergence 1–5 | `main` / `6e23d06` | `codex/architecture-convergence-20260922` / pending commits | Launcher, submit authority, bounded worker split, transactions, schema migrations | ready-for-integration | `launcher.py`, `worker_orchestration.py`, storage/database | All repository CI-equivalent tiers, build, and installed-wheel smoke passed | Dirty main checkout is protected; no path ownership shared with another active writer | 1 | Commit, open PR, merge, and fast-forward main |

States: `in-progress`, `blocked`, `ready-for-review`, `ready-for-integration`, `integrated`, `abandoned`.

## Career workbench delivery (2026-10-11)

- Worktree: sibling `career-integration-20261011`; branch `codex/career-workbench-20261011`, based on owner `fork/main` at `357e805`.
- Scope: M1–M4 catalog, evidence, diagnosis, learning/review and local workbench; private runtime materials excluded.
- State: integrated remotely; PR #19 merged at `15273c5`, followed by the canary test correction PR #20 at `64d2ba6`. Remote main and ancestry rechecked on 2026-10-12. This does not update the original source checkout or installed application.
- Ownership: primary agent owns Git and integration. The original checkout at `48bec07` contains unrelated WIP and is preserved.
- Retention: keep the isolated verified checkout after merging until the original checkout's unrelated work is reconciled. Record final remote delivery facts in the local registry without resetting or broadly copying that checkout.

## PR #21 local integration lanes (2026-10-12)

All three lanes start from verified draft PR #21 head `6f2a116c36846dd8a8d17e1ec7d427089011ba63`, seven commits ahead of remote `main` at `64d2ba6`. The integration owner alone manages worktree creation and later local cherry-picks. Thirty-three original checkouts (the source plus 32 worktrees), their HEADs and status inventories were preserved.

| Task directory / worktree | Branch | Owned scope |
| --- | --- | --- |
| `task-3/AutoJobApply-integration` | `codex/pr21-integration-20261012` | Handoff evaluation burden, delivery truth, capability boundaries and acceptance checklist |
| `task-4/AutoJobApply-host-recovery` | `codex/pr21-host-recovery-20261012` | Explicit Host to existing C/D/SQLite recovery and reviewed continuation |
| `task-5/AutoJobApply-timing` | `codex/pr21-timing-20261012` | Independent timing/cost attribution module and empirical-run budget preparation |
| `task-3/AutoJobApply-combined` | `codex/pr21-combined-20261012` | Local cherry-picks of all three lanes, shared evaluator resolution and combined acceptance |

State: all three lanes committed; combined acceptance completed locally in the fourth isolated worktree on the same PR head. All 33 original HEAD/status inventories remained identical. No push, remote merge, release, user-installation change or real application was performed. See [P1 integration closeout](agent-harness-p0-20261011/p1-integration-closeout.md).

## Delivery package

- Summary: extracted preflight and submission authority, isolated Runtime Cell shadow ownership, unified compatible SQLite transactions, and introduced atomic ordered top-level migrations.
- Files: application launcher/worker owners, storage transaction and migration modules, focused regression tests, and this task record.
- Diff from base: net code reduction in launcher/worker with new responsibility-owned modules; additive database migration tests.
- Commit and branch state: reviewed working tree ready for Lore commits.
- Divergence from main: based on merged PR #9 (`6e23d06`); no unrelated main WIP included.
- Overlap and conflict risk: main contains unrelated WIP outside the current planned product hotspots.
- Validation evidence: Ruff; 2790 non-browser; 48 compatibility; 32 Windows; 65 browser; build and installed-wheel smoke; two independent PASS reviews after database fixes.
- Unverified risks: full worker lifecycle/entry/recovery/telemetry decomposition is explicitly deferred; no production database was migrated.
- Recommended integration method: protected-main PR followed by fast-forward synchronization of the dirty main checkout.
