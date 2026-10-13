# Plan

## Goal

Complete the existing Web workbench's invitation-to-interview-management workflow,
then validate and improve the existing owner-resume/ATS recovery integration locally.

## Scope

Use existing followup events/actions, interview preparation and inbox/reminder surfaces.
Persist exact-job/round links, preparation, actions, results, rescheduling and cancellation.
Keep application submission truth and continuation authority separate from local management.

## Sources of truth

Verified fork/main: `9d46199de6f5773096e68d1b190e9271f262859a` (PR21 merged).
Existing `AGENTS.md`, `followup.py`, interview CLI/model and production owner guards.

## Stages

- [x] Implement a usable Web interview flow on `codex/interview-management-20261013`.
- [x] Validate/fix a local owner-to-mock-ATS recovery flow on the isolated recovery branch.
- [x] Observe both flows, check directly affected safety/data boundaries, and review.
- [x] Prepare stable local commit batches and demo commands/evidence/known gaps.

## Acceptance criteria

Refresh restores records; duplicate clicks do not duplicate events/actions; stale revision
or expired permission cannot silently write. Reschedule/cancel preserve history and the
intended round. Preparation retains material provenance. Existing inbox/reminders remain usable.

## Non-goals

No deployment, remote merge, real application, recruiter contact, external calendar invite,
paid model call, user-install update or restoration of removed release worktrees.

## Risks and constraints

Use synthetic workspaces only. Protect credentials, owner identity, expiry, stale state,
unknown effects and duplicate-operation boundaries. User prioritizes feature observation
and minimal directly relevant checks; do not repeatedly run the full suite or add a test framework.
