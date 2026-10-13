# Local interview demo

Run from the isolated `AutoJobApply-interview-management` checkout with an existing
Python runtime containing the project dependencies. No installation update is needed.

```powershell
& 'C:\Users\raede\Desktop\简历\applypilot-local\source\.venv\Scripts\python.exe' -B -X utf8 scripts/demo_interview_workbench.py --serve --output '..\interview-and-recovery-20261013\manual-demo-1'
```

The output directory must be new. The command prints the loopback URL and creates
a fictional workspace inside that directory. Open the printed URL and select
面试管理. Ctrl+C closes the local server. It does not read a personal profile, import
email, contact recruiters, deliver calendar invitations or call a model. Normal
`--serve` mode does not require a browser driver; omit `--serve` to run the controlled
Playwright story if that browser runtime is available.

Try the pending invitation's exact-job association, preparation, source quotations,
preparation todo and reminder return link. Then reschedule, record a result or cancel
a round. Refresh restores saved records. A new round uses the existing registered
job and an explicit round number. Local interview results do not change application
submission facts or provide application continuation authority.

The page displays and edits times in the device timezone and preserves the recorded
IANA zone. Ambiguous daylight-saving times are rejected in this UI; the existing CLI
can record an explicit offset. Preparation uses the currently available source-bound
JD and resume and warns when these are not proven to be the actual submitted version.

Evidence: `interview-ui/run3/outcome.json` records eight lifecycle checks, including
duplicate submission, reload persistence and session expiry with draft preservation.
It also proves a completed round accepts a follow-up todo. Its screenshot
`interview-ui/run3/interviews.png` shows completed and cancelled rounds.
Backend evidence is in `interview-backend/verification.md`. These prove a local
workbench flow on fictional data, not an actual Codex Browser or real ATS run.
