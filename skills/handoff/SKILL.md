---
name: handoff
description: Create a compact, high-signal handoff for continuing in another chat or coding agent. Use when the user says "handoff", wants to switch tools, or is nearing usage limits and needs the next agent to resume work mid-stream without redoing discovery.
---

## Principles

Preserve momentum — the next agent must know exactly what to do first. Prefer facts over narrative. Separate confirmed facts from assumptions. Record failed attempts so the next agent does not repeat them. Never include secrets or credentials.

## Output

Write or update `HANDOFF.md` in the project root unless the user requests a different file. If it already exists, update in place — keep still-relevant context, remove stale items.

## Modes

Choose the lightest mode that preserves continuity.

**Quick** — use when near a hard usage limit. Target: 10–25 lines. Include only: objective, current status, next action, blockers, files touched, critical warnings.

**Full** — use for complex or multi-step tasks with significant history. Target: 25–100 lines. Use all sections from the template below.

## What To Capture

Always:
- the user's actual goal
- the full task list to achieve this goal
- what is done, in progress, and remaining
- the single best next action
- blockers and dependencies
- files changed or examined
- validation run and validation still needed

When relevant:
- branch name
- failing tests or known errors
- environment quirks
- commands that succeeded or failed
- design constraints from the user
- links to local docs or plans
- orchestrator session details

Never:
- chatty narrative or repeated background
- speculative details that do not affect next steps
- secrets or credential values

## Template

Use this structure. For Quick Mode, omit sections that do not apply.

```md
# HANDOFF

## Objective
- What the user wants, in one or two sentences.

## Task List
- The full list of tasks to achieve the objective
- Extract from the plan if one was provided, otherwise from your own planning
- Be brief, but include all relevant context not captured elsewhere

## Current Status
- What is complete.
- What is in progress.
- What is not started.

## Decisions Made
- Confirmed decisions the next agent should not re-litigate unless new evidence appears.

## Failed or Rejected Approaches
- Attempts that did not work and why.

## Active Blockers
- Missing information, failing checks, unresolved bugs, or external dependencies.

## Files That Matter
- Paths to files changed, created, or heavily inspected, with a brief reason each matters.

## Orchestrator State (only if called as part of an orchestrator session)
- Active run dir, list of workers, model ratings so far, next planned workers

## Validation
- Tests, lint, builds, or manual checks already run, with results.
- Checks still needed.

## Next Action
- The exact first thing the next agent should do.

## Resume Prompt
Continue this task using `HANDOFF.md` as the source of truth. Do not redo discovery. First perform the `Next Action`, then continue remaining work and update `HANDOFF.md` if the plan changes.
```

## Next Action Standard

The `Next Action` must be specific enough that the next agent can start immediately.

Good:
- `Update src/auth/session.ts to guard null refresh tokens, then rerun the auth test suite.`
- `Open package.json and align the Vite version with the plugin peer dependency before retrying install.`

Weak:
- `Continue working on it.`
- `Fix the bug.`

## Quality Checks

Before finishing, verify:
- the todo list is clear
- the next action is concrete and specific
- blockers are explicit
- failed attempts are recorded if any occurred
- the handoff contains no secrets
- the handoff can be scanned in under two minutes

## Writing Style

- Short bullets. Prefer file paths and concrete nouns.
- Label uncertain items as `Assumption:` or `Unverified:`.
- Each bullet should be independently useful.

## Optional Enhancements

Add only when they provide real value:
- `Branch:` line if git state matters
- `Commands:` section for critical reproducible commands
- `Risks:` section for sensitive refactors or migrations
- `Open Questions:` if user input is needed
- Note the repo or working directory per item if the task spans multiple repos or tools

## Example

```md
# HANDOFF

## Objective
- Continue migrating the app from legacy auth middleware to token-based session validation without breaking admin routes.

## Task List
- [x] Create new token validator and wire for standard user routes.
- [x] Begin updating error handling across auth middleware.
- [ ] Audit all admin routes in `src/routes/admin.ts` for legacy middleware dependencies.
- [ ] Update admin routes to use the new token validator behind the existing fallback.
- [ ] Remove or isolate the legacy middleware helper once no routes depend on it directly.
- [ ] Run full integration test suite to confirm no regressions.
- [ ] Clean up partial error handling updates and verify coverage.

## Current Status
- New token validator exists and is wired for standard user routes.
- Admin routes still reference legacy middleware.
- Error handling was partially updated but not fully tested.

## Decisions Made
- Keep the existing session cookie format for backward compatibility.
- Do not change route shapes in this pass.
- Preserve current audit logging behavior.

## Failed or Rejected Approaches
- Replacing all middleware in one pass caused admin authorization regressions.
- Removing the legacy helper entirely was deferred — two internal routes still import it.

## Active Blockers
- Need to inspect admin route coverage before removing fallback logic.
- Full integration test run has not been completed.

## Files That Matter
- `src/auth/token-validator.ts`: new validation path.
- `src/middleware/auth.ts`: route wiring in progress.
- `src/routes/admin.ts`: still using legacy path.
- `tests/auth/integration.test.ts`: primary regression coverage.

## Orchestrator State
- Run dir: `.ai-orchestrator/current/`
- Workers: `01-claude-refactor-auth` (completed), `02-codex-add-tests` (in-progress)
- Model ratings so far: codex 8/10 targeted edits, claude 7/10 review
- Next planned workers: `03-copilot-git-ops` — pending 02 completion

## Validation
- Targeted auth unit tests passed.
- Admin integration tests not yet run.
- Manual review suggests fallback code may still be required.

## Next Action
- Update `src/routes/admin.ts` to use the new validator behind the existing fallback, then run `tests/auth/integration.test.ts`.

## Resume Prompt
Continue this migration using `HANDOFF.md` as the source of truth. Do not redo discovery. First update `src/routes/admin.ts` to use the new validator behind the existing fallback, then run `tests/auth/integration.test.ts`.
```
