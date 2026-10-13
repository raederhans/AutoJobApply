# Installed runtime boundaries (2026-10-13)

Baseline: `a88fc7c14df6b7b9329a831a3d49d977b66c06b5`. This follow-up addresses
the independent installed-wheel findings without enabling production Runtime
Cells or changing the user's installed package.

## Changes

- Wheels explicitly include the seven production Browser Host modules and their
  relative import closure. Release auditing rejects a wheel missing any member;
  tests check the mapping against actual imports. No fixture or test JS enters
  this closure.
- `applypilot browser-host-path [--json]` returns the imported package's Host
  module URI, anchored to packaged assets or its own source checkout. It does
  not search cwd, initialize a workspace, start a browser, or attach authority.
  Existing Browser runtime module-loading restrictions still apply.
- Both launcher composition points bind Runtime Cell identity to the loaded
  Python package. `source_manifest_identity` accepts the repository root or
  package directory. Its v1 canonical namespace remains `src/applypilot/...`;
  the JSON encoding, Python file set, hashing and digest format are unchanged.
  Identical source and wheel Python bytes therefore have the same identity.
  A code change still invalidates prior source-bound receipts. Empty or missing
  packages cannot produce an identity.
- Shadow/canary work in an installed wheel and retain the one-Cell
  `NOT_ADMITTED` result. `APP_SERVER_PRODUCTION_CELL_ADMITTED` stays false.
  Locating packaged JS does not expand the Python-only identity's scope or
  certify the Host artifact or actual Codex Browser session.
- An explicit `APPLYPILOT_DIR` no longer eagerly resolves `Path.home()`, allowing
  isolated worker environments without home variables. The default is unchanged
  when that variable is absent.
- The existing release smoke now checks installed Host assets and real launcher
  Runtime Cell capacity before its synthetic workspace/dashboard checks.

## Evidence and limits

Targeted source regressions cover missing/incomplete packages, non-ASCII URI
paths, cwd isolation, CLI workspace side effects, source/wheel identity
equivalence, code changes, empty package rejection and hard-gated capacity.
Initial targeted source batches passed 40 and 25 cases respectively (the
identity tests overlap; these counts are not added). Ruff and diff checks pass.
An audited sdist-to-wheel build and a fresh offline task-owned venv passed 12
installed checks: exact installed module provenance, console entry points,
seven-module Node import closure, three Python MCP stdio initialize/tools-list
exchanges and one-Cell shadow/canary capacity. All 230 Python files and seven
Host files matched source and wheel bytes. Home variables were absent; no user
workspace was created. Existing dependency files were read without executing
the dependency environment's editable `.pth`. Python 3.11's locale decoding of
the probe's Unicode `.pth` path required a test-fixture encoding correction.

Initial artifact SHA256:
`3ed49db1ca8a3758ca6472de275f9b835943180bd22ff41631f681f9618c48f9`.
Evidence: `task-3/delivery-20261013/installed-stage1-utf8/installed-evidence.json`;
later combined artifacts are identified separately by the delivery manifest.
Sandbox sdist extraction and venv setup failures were retained; approved local
execution completed those steps without a dependency download or user install.

The actual Codex Browser read-only `cua.getState()` entry failed during kernel
startup: MXC launcher could not locate its Windows platform directory
(`HRESULT 0x80070003`). No page action ran. Actual Codex Browser execution, real
ATS, production model workers and paid-model performance remain outside this
proof; controlled Chromium results cannot replace that missing runtime check.
