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
- a copyable next-chat prompt

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

## Notes

- Generated files, local artefacts, and selected third-party or separately managed skills are excluded via `.gitignore`
