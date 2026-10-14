# Context

## Authorized publication continuation (2026-10-14)

The user authorized normal push, draft PR, final-SHA CI verification, ready and
merge after PR22. Root owns all Git and live checks. The original two feature
trees remain clean and retained. New release tree is
`task-3/AutoJobApply-interview-release-20261014`, branch
`codex/interview-release-20261014`, based on verified fork/main
`b7b953689d86a926e8a5331149c9e1b48d6a2619`. Feature commits were replayed as
`aa0615f` and `7c36e77`. PR22 changed only CI/publish YAML, CHANGELOG and its test;
there is no file intersection with the 21 feature paths. MSI shell and supported
network permissions are available. No old MSI repair or cloud cleanup is replayed.

Root's exact live commands, cwd this release tree, existing source-venv Python:

| Process | Command | Resources/log/stop |
| --- | --- | --- |
| Targeted owner/interview/followup | `python -B -X utf8 ../interview-publication-20261014/run_targeted.py` | Explicit fictional APPLYPILOT_DIR and short LocalTemp basetemp; own port-0 servers/browser; targeted.log; exit or assertion failure closes fixtures |
| Interview page story | `python -B -X utf8 scripts/demo_interview_workbench.py --output ../interview-publication-20261014/ui` | New fictional workspace, own port-0 server/browser; ui.log; script finally closes resources |
| Candidate build | `python -B -X utf8 scripts/build_release.py --no-isolation --out-dir ../interview-publication-20261014/artifacts` | New output, no installation; build.log; process exit |

Only root starts/polls/retries these processes. Independent reviewer reads the
frozen source and completed evidence, without a duplicate live run. No user data,
real submission, manual deployment, tag publication or user-install update.

All three runs finished with exit code 0: 52 targeted checks in 17.61s; eight
page-story checks and zero page errors; candidate wheel/sdist/bundle archive
audit passed. Ruff on src and 25 existing Node state checks passed. Independent
review PASS: old/new feature binary patches match exactly and PR22 has no source
overlap. No services/browser/build processes remain running. Root will record
the PR and exact final-SHA CI/main outcome in the external publication evidence
directory, keeping this source record stable during CI. No code changed for
publication beyond the two unchanged feature patches.

## Current truth

Main is `9d46199de6f5773096e68d1b190e9271f262859a`. Original dirty source and the
combined/publication/delivery trees are protected. The four release trees were removed
after approval; this task does not recreate them.

## Decisions and deviations

| Time | Evidence or decision | Impact |
| --- | --- | --- |
| 2026-10-13 | Reuse followup events/actions and interview preparation | No isolated new workbench or duplicated reminder model |
| 2026-10-13 | User requests feature-first, bounded validation | No repeated full suite or framework expansion |
| 2026-10-13 | Root alone owns Git integration and commits | Implementers deliver diffs ready for review |

## Live process ownership

| Process | Owner | Log path | State |
| --- | --- | --- | --- |
| Interview backend HTTP/DB checks | `interview_backend` | `task-3/interview-and-recovery-20261013/interview-backend/` | Completed: 90 checks plus scoped follow-ups; resources closed |
| Web demo and directly affected browser flow | root | `task-3/interview-and-recovery-20261013/interview-ui/run2/` and `interview-ui-run2.log` | Completed: seven lifecycle checks, no page errors; resources closed |
| Owner/mock-ATS recovery flow | `owner_ats_deepening` | `task-3/interview-and-recovery-20261013/owner-ats/` | Completed: 14 focused checks plus one redaction check; all resources closed |
| Final interview browser story | root | `task-3/interview-and-recovery-20261013/interview-ui-run3.log` | Completed: eight checks passed; all resources closed |
| Combined candidate build | root | `task-3/interview-and-recovery-20261013/candidate-build-final.log` | Completed: candidate-release-final wheel/sdist/bundle audits passed; no install |

Root demo command: existing source-venv Python `-B -X utf8 scripts/demo_interview_workbench.py
--output <task-3>/interview-and-recovery-20261013/interview-ui/run2`, cwd
`AutoJobApply-interview-management`; TEMP/TMP point to local short Temp. The script
binds its own checkout before importing product modules and creates a new fictional
workspace, loopback server (port 0) and isolated Chromium context. Success is the complete
story with no page errors; an assertion failure stops the run and records outcome.
Finally closes server/worker/browser. Only root starts, polls or retries this process.

Run1 observed successful invitation association and preparation but the reminder assertion
expected a raw action summary. Existing reminders deliberately exclude that private body.
The fix adds only a safe unique interview link to Web reminder entries, leaving generic
summaries and exports untouched. Run2 repeats the single story to observe that link and
complete the remaining lifecycle/session checks. The before outcome and screenshot remain.

Run2 passed all seven story checks. Its fresh fictional workspace was allocated by
the runtime under CreatorTemp despite the requested shell TEMP/TMP. No personal
workspace was used. The final demo script now explicitly creates its fictional
workspace inside the new output directory, removing dependence on the runtime default.
Existing workbench state checks passed 25/25 after loading the new panel module.

Independent owner review reproduced private answer text in a tool-action log when
`ref` exists and `element` is absent. That report uses a short synthetic answer to
avoid the old full-string assertion's truncation blind spot. The owner agent is
fixing the directly affected description path before local commit.

The owner log fix passed five focused checks (2.68s). Independent recheck found
zero answer matches and returned PASS_WITH_NOTES. Local owner commit is
`c34f72c7bc70625f8e7ec3c9740463e92cae0f5f`; its unchanged patch was integrated as
`27305514773441f411871137292749542842edbf` in the interview tree. Both remain local.
The previously delivered MSI repair `6e1b321` was already integrated into merged
main via `fdb4cee` with the same stable patch-id; it was not applied again.

Interview review reproduced cross-job CLI stage-id collisions and completed-round
action rejection. The backend repair scopes public identities to the exact job or
application, keeps the original CLI stage metadata and permits completed-round
follow-up actions. Unknown-round invitations must retain an event anchor rather
than inventing a round. Final scoped checks and reviewer recheck are in progress.

Final backend repair selection passed 35 checks. Interview independent recheck
passed three checks and returned PASS, with source SHA-256 freeze evidence. Final
run3 browser story passed eight checks with no page errors. Owner reviewer returned
PASS_WITH_NOTES, with real runtime/ATS/model limitations unchanged.

The first candidate build failed on sandbox temporary-directory permissions. Its
log and partial output remain. The same offline build in a fresh final directory
under supported permissions passed wheel/sdist/bundle archive audits. Four changed
wheel members (panel, interview domain, answer reader and launcher) match source
bytes; see candidate-wheel-source-proof.json. No package was installed or published.

## Handoff

`interview_backend` owns followup adapters, `career/server.py` and scoped backend tests.
Root owns frontend HTML/JS/CSS, this task record and registry.
`owner_ats_deepening` owns apply owner/runtime recovery in a separate worktree; shared
career server/frontend/interview files are excluded. Semantic overlap is the existing
inbox/resume contract; report any required shared seam before changing it.

## Next step

Record the verified interview local commit and deliver the demo/report. Both new
worktrees and original dirty checkout remain; no cleanup or remote publication.
