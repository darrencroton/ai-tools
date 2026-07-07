---
name: handoff
description: Create a compact, high-signal handoff for continuing in another chat or coding agent. Use when the user says "handoff".
---

## Principles

Preserve momentum — the next agent must know exactly what to do first. Prefer facts over narrative. Separate confirmed facts from assumptions. Record failed attempts so the next agent does not repeat them. Never include secrets or credentials.

## Output

Write or update `HANDOFF.md` in the project root unless the user requests a different file. If it already exists, update in place — keep still-relevant context, remove stale items. Do not commit the handoff unless told or already part of the repo.

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
- frozen contract, authorized surface, and authorization gate status when the task used implementation-plan or scoped-implementation

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

## Plan Workflow Handoff

When all of the following are true, populate `Resume Prompt` using the full next-chat launcher below — not the generic resume prompt:

- an implementation plan file exists and was the source of the current work
- you just finished implementing one or more slices or a named batch from that plan
- you are stopping at a slice or batch boundary so the user can review before the next session begins (i.e. you are **not** running all remaining slices autonomously without user checkpoints)

Fill in the plan file path and set `Slices or batch this session` to the next unstarted slice, slice range, or named batch. The next chat must read **both** the plan file **and** `HANDOFF.md` — the handoff is now part of the run contract and records what has been committed and what remains.

```md
## Resume Prompt
Plan file: <path>
Slices or batch this session: <next slice, slice range, or batch>

Read the full plan file first AND read HANDOFF.md — the handoff is part of the contract for this run and records committed state. If either is unclear or the plan state is ambiguous, stop and tell me before coding.

Work on the current feature branch for this plan; if none exists, create one and tell me the name.

Use ai-orchestrator as the controlling skill. Keep the implementation local; delegate per that skill's guidance when independence or context economy helps — primarily the hostile drift-audit skill, an independent code-review skill pass, and long-running tests.

For each selected slice or batch, in plan order:
1. Restate the frozen contract (authorized surface + non-goals) from the plan.
2. If any included slice's Risk Flags mark approval-needed, stop and get my approval before coding.
3. apply the scoped-implementation skill against the selected contract.
4. apply the drift-audit skill. Report the authorization gate result before any quality review.
5. If the gate passes, apply the code-review skill. If it fails, fix the drift and re-audit.
6. Surface drift and review findings to me, fix them, then re-run the relevant gate. If consecutive reviews return only minor findings and have clearly converged record residuals in the slice summary and proceed.
7. Ask me before committing. On my approval, commit the selected slice or batch with the commit skill.

After the selected slice(s) or batch are committed, use the handoff skill to record state and the next slice or batch to resume from. Do not continue past the selected scope.

Confirm before starting: plan file read, HANDOFF.md read, selected slice(s) or batch, branch, and the first slice.
```

For all other handoffs — including autonomous full-plan runs and non-plan work — omit the Resume Prompt section entirely.

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

## Frozen Contract
- Intended slice, authorized files/functions, tests, non-goals, risky surfaces, and rollback path if a scoped implementation contract exists.

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

## Authorization Gate
- Actual changed surface versus authorized surface.
- Drift status: none, fixed, approved, or unresolved.

## Next Action
- The exact first thing the next agent should do.

## Resume Prompt
*(Only when handing off at a slice or batch boundary with user checkpoints between sessions — use the full launcher format from the Plan Workflow Handoff section above. Omit this section for all other handoffs.)*
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
```
