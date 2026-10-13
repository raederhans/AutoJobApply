# Task

## Current status

Both features are implemented, observed on fictional data and independently
reviewed. Review findings are fixed. The owner batch is committed and integrated;
the interview batch is verified and ready for its local commit.

## Checklist

- [x] Verify main and create new branches; preserve user dirty checkout and prior deliveries.
- [x] Read repository instructions and existing followup/interview interfaces.
- [x] Persist interview lifecycle and expose authenticated workbench routes.
- [x] Connect Web list/detail/forms, preparation and actions to those routes.
- [x] Validate local owner resume/ATS integration and fix observed gaps.
- [x] Observe refresh, duplicate action, reschedule/cancel and expiry/stale-state behavior.
- [x] Independent review and prepare stable local commits; no remote merge/deployment.

## Validation evidence

| Command or check | Result |
| --- | --- |
| `git ls-remote fork refs/heads/main` | Matches `9d46199` |
| Both new worktree HEAD/status checks | Correct base; initially clean |
| Backend followup/interview/HTTP checks | 90 passed; scoped follow-ups 4 and 3 passed |
| Existing workbench state checks | 25 passed |
| Controlled Chromium interview story | Seven checks passed; no page errors; local synthetic data only |
| Owner mock-ATS checks | 14 passed plus one log check; review exposed a partial-text logging gap |
| Final interview identity/follow-up repairs | 35 focused checks passed; independent recheck 3 passed |
| Final controlled Chromium story | Eight checks passed, including completed-round follow-up; no page errors |
| Tool-input privacy repair | Five focused checks passed; independent short-answer probe matches zero logs |
| Independent review | Interview PASS; owner PASS_WITH_NOTES; no open finding |
| Combined candidate build | Wheel/sdist/bundle audit passed; four changed wheel members match source |

## Open risks and remaining work

No implementation or review blocker remains. No live model, real ATS or actual
Codex Browser was exercised. No user installation was changed. Availability still
requires the existing provenance/expiry controls; this does not add a scoped fact
admission contract. Device-timezone UI rejects ambiguous daylight-saving times.
Actual ATS authorization and user-only operations will be reported separately from local proof.
