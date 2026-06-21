# AI Tools

Shared AI agent configuration files and hand-written skills.

## Contents

- `AGENTS.md`: global instructions used across AI coding assistants
- `setup.sh`: setup script for linking shared AI coding configuration files into local tool directories
- `tools.conf`: tool registration used by the setup script
- `skills/`: reusable skills for AI coding

## Agentic Coding Workflow

The point of this workflow is not to make agents slower or more ceremonial. The point is to make each run narrow enough that speed is useful and the result can be audited.

### Core Rule

An agent loop should not mean "keep expanding the feature until it feels done." It should mean: freeze one acceptance slice, implement only that slice, prove the work stayed inside the lane, then review quality.

Use explicit skill calls. Do not rely on the model to guess which workflow applies.

### 1. Plan First

Call [`implementation-plan`](skills/implementation-plan/) when you want the planning chat.

The plan should define one or more small slices. Each slice needs:

- intended change
- acceptance criteria
- authorized files/functions/components
- tests to add, update, or run
- explicit non-goals
- risky surfaces, such as auth, billing, schema, shared types, routing, API contracts, global state, or deployment config
- rollback path
- a copyable next-chat prompt (see [Running A Plan: Two Modes](#running-a-plan-two-modes))

If a slice touches a risky surface, split it smaller or require explicit approval.

### 2. Implement In A New Chat

Call [`scoped-implementation`](skills/scoped-implementation/) and paste the exact slice receipt from the plan.

The implementing agent should:

- restate the authorized surface before coding
- inspect current git state
- change only the approved files/functions
- avoid opportunistic cleanup
- run the planned validation
- prepare the frozen contract, diff summary, changed files, and validation results for [`drift-audit`](skills/drift-audit/)

The required receipt is:

- intended slice
- authorized surface
- actual changed surface
- behaviour added
- behaviour removed
- tests added or updated
- validation run
- drift audit input
- rollback path

### 3. Use The Orchestrator When It Helps

Call [`ai-orchestrator`](skills/ai-orchestrator/) when the work benefits from delegation, independent review, long-running checks, or another model's perspective.

The orchestrator is responsible for the workflow, not for delegating everything. It should keep small slices local when delegation adds overhead. It should delegate when doing so improves quality, speed, independence, or context management.

Good delegation targets:

- codebase mapping before a plan
- plan critique
- implementing a well-bounded slice
- long-running tests
- hostile drift audit
- standalone quality review

The orchestrator must name required skills in worker prompts. Delegates should not infer skills from context. If a required skill is unavailable to a worker, the worker should report that and follow the explicit prompt contract.

### 4. Audit Drift Before Quality

The first review question is not "is this good code?" It is "was this authorized?"

Call [`drift-audit`](skills/drift-audit/) after implementation and before [`code-review`](skills/code-review/).

Use the authorization gate to compare:

- intended slice vs actual changes
- allowed files/functions vs changed files/functions
- expected tests vs actual tests
- behaviour added
- behaviour removed
- non-goals preserved
- new coupling

Only after [`drift-audit`](skills/drift-audit/) passes should normal code review start.

For higher-risk work, have the orchestrator run a hostile drift audit with a second agent. Give that worker only the frozen contract, the diff, and relevant tests. Its job is to find extra behaviour, removed edge cases, hidden rewrites, missing tests, and new coupling.

### 5. Review Quality

Call [`code-review`](skills/code-review/) after [`drift-audit`](skills/drift-audit/) passes, or for standalone reviews.

When a frozen contract exists, [`code-review`](skills/code-review/) should consume the [`drift-audit`](skills/drift-audit/) result, then review correctness, edge cases, tests, error handling, maintainability, and any domain-specific risks.

Passing tests are useful evidence, not proof. A missing test is a real finding when the change is risky enough that a regression could ship unnoticed.

### 6. Simplify Separately

Call [`code-simplifier`](skills/code-simplifier/) only when you explicitly want a simplification/refactor pass over working code.

This is not part of the default implementation workflow. Use it when behaviour already works and you want a leaner, smarter, more maintainable way to do the same thing. The simplifier can be ambitious, but it must preserve public contracts, accepted edge cases, data shapes, and product behaviour.

### 7. Preserve State

Call [`handoff`](skills/handoff/) when continuing in another chat or agent.

A useful handoff should include:

- objective
- task list
- current status
- frozen contract, if one exists
- files that matter
- validation run and still needed
- authorization gate status
- next action

### 8. Commit Only After Approval

Call [`commit`](skills/commit/) only after explicit user approval.

The commit should stage specific files by name, never bypass hooks, never amend, and include a meaningful message that lists changed files with reasons.

### Practical Default

For normal feature or bug work:

1. [`implementation-plan`](skills/implementation-plan/) in the planning chat.
2. New chat with [`scoped-implementation`](skills/scoped-implementation/) for one slice.
3. Use [`ai-orchestrator`](skills/ai-orchestrator/) only if delegation or independent audit is worth it.
4. Run [`drift-audit`](skills/drift-audit/).
5. Run [`code-review`](skills/code-review/).
6. Use [`handoff`](skills/handoff/) if the work continues elsewhere.
7. Use [`commit`](skills/commit/) only when approved.

Keep the loop boring, narrow, and auditable. The agent can move fast inside the lane, but it does not get to redraw the lane while working.

### Running A Plan: Two Modes

The plan is created in one chat; implementation happens when you return and paste the plan's `Next Chat Prompt` into a fresh session. There are two ways to run that session. The skill chain is identical in both (`scoped-implementation` → `drift-audit` → `code-review` → `commit`, with `ai-orchestrator` for delegation and `handoff` at boundaries). They differ only in who holds the gates and when handoff happens.

Both modes keep two non-negotiables: a slice whose Risk Flags mark approval-needed pauses (Mode A) or stops the run (Mode B), and each slice reports its authorization-gate result before quality review.

**Mode A — Assisted run.** Use when slices are risky, touch flagged surfaces, or you want a checkpoint between them. You stay in the loop, approve before risky slices, review findings, and approve each commit. One slice (or a few tightly-coupled slices) per chat, then a handoff to the next session. Set the plan file path and which slice(s) this session covers:

```md
Plan file: <path>
Slices this session: <e.g. Slice 2 — or Slices 2–3 only if tightly coupled>

Read the full plan file first. If a selected slice receipt is incomplete or the plan state is unclear, stop and tell me before coding.

Work on the current feature branch for this plan; if none exists, create one and tell me the name.

Use ai-orchestrator as the controlling skill. Keep the implementation local; delegate per that skill's guidance when independence or context economy helps — primarily hostile drift-audit, independent code-review, and long-running tests.

For each selected slice, in plan order:
1. Restate the frozen contract (authorized surface + non-goals) from the plan.
2. If the slice's Risk Flags mark approval-needed, stop and get my approval before coding.
3. Apply scoped-implementation against the slice contract.
4. Apply drift-audit. Report the authorization gate result before any quality review.
5. If the gate passes, apply code-review. If it fails, fix the drift and re-audit.
6. Surface drift and review findings to me, fix them, then re-run the relevant gate.
7. Ask me before committing. On my approval, commit that slice with the commit skill.

After the selected slice(s) are committed, use handoff to record state and the next slice to resume from. Do not continue past the selected slice(s).

Confirm before starting: plan file read, selected slice(s), branch, and the first slice.
```

**Mode B — Autonomous full-loop driver.** Use when the plan is well-isolated and you want to step away. The orchestrator runs all remaining slices, delegates hostile drift-audit and independent review per slice, recovers from findings itself, and commits each slice that clears all gates. With a fresh branch per run and a commit per gated slice, the only real downside is wasted time. You assess at the end:

```md
Plan file: <path>
Scope: all remaining slices, in plan order.

Read the full plan file first. If the plan is incomplete or its state is unclear, stop and report instead of improvising.

Act as the orchestrator per the ai-orchestrator skill. You own the full run — implement, gate, recover, and make the accept/reject call. Delegate to other models for independence and context economy per that skill: at minimum a hostile drift-audit and an independent code-review per slice, plus long-running tests.

Setup: create a new branch for this run, switch to it, and report the name.

For each slice, in plan order:
1. Restate the frozen contract (authorized surface + non-goals).
2. If the slice's Risk Flags mark approval-needed, STOP the run and report — do not self-approve a slice the plan gated for a human.
3. Apply scoped-implementation against the slice contract.
4. Apply drift-audit (delegate a hostile audit). Record the authorization gate result.
5. If the gate fails, fix the drift inside the contract and re-audit. If it can't be fixed inside the contract, STOP and report.
6. On a passing gate, apply code-review (delegate for independence). Fix findings, then re-run the relevant gate.
7. When the slice passes validation, drift-audit, and code-review, commit it with the commit skill. This prompt is explicit approval to commit each slice that has cleared all three gates — and only those.

Stop the run early on: an approval-gated slice, a blocker, an unapproved scope change, a gate/validation failure unfixable inside the contract, or context pressure. On any stop, write a handoff with current state and the next slice to resume.

When all slices are complete, write a final summary: slices committed, gate results per slice, and anything left for me to assess.

Confirm before starting: plan file read, branch name, the ordered slice list you'll execute, and the first slice.
```

## Notes

- Generated files, local artefacts, and selected third-party or separately managed skills are excluded via `.gitignore`
