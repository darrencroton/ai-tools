---
name: implementation-plan
description: Create a narrow, auditable implementation plan with frozen acceptance slices. Use only when the user explicitly asks for this skill, an implementation plan, or a plan-first workflow before coding.
---

# Implementation Plan

Use this skill to produce the plan-first artifact for a later implementation chat. Do not implement code while using this skill unless the user explicitly changes the task.

## Purpose

Create a plan that makes each agent loop narrow, boring, and auditable. The output should be good enough that a new chat can implement one slice without needing the original discussion.

## Workflow

1. Inspect the codebase enough to understand the requested change and the relevant conventions.
2. Define the smallest useful acceptance slice. If the request has multiple concerns, split it into ordered slices.
3. For each slice, freeze the contract before proposing implementation detail.
4. Identify risky surfaces: auth, billing, permissions, persistence, database schema, migrations, shared types, API contracts, routing, global state, concurrency, generated files, public CLI flags, or release/deployment config.
5. If a slice touches a risky surface, mark it as requiring explicit approval or split it until the risk is isolated.
6. Define validation before coding: tests to add/update, targeted checks to run, and behaviours that must not regress.
7. End with a copyable implementation prompt for the next chat.

## Planning Receipt

Use this shape for every implementation slice:

```md
## Slice <N>: <short name>

### Intended Change
- ...

### Acceptance Criteria
- Inputs:
- Outputs:
- User-visible behaviour:
- Behaviour that must not change:

### Authorized Surface
- Files allowed to change:
- Functions/classes/components allowed to change:
- Tests allowed or expected to change:

### Explicit Non-Goals
- ...

### Risk Flags
- Risky surfaces touched:
- Approval needed before implementation:

### Validation Plan
- Tests to add/update:
- Commands to run:
- Manual checks:

### Rollback Path
- ...
```

## Output Rules

- Keep plans specific to files, symbols, and observable behaviour.
- Prefer one slice that can be completed and reviewed independently over a broad multi-concern pass.
- Do not list files as authorized just because they might be convenient; only authorize files the implementation is expected to touch.
- If discovery shows the planned surface is too broad, recommend a smaller first slice.
- If the repository state is unclear or dirty in relevant files, call that out before finalising the plan.
- Include a final `Next Chat Prompt` that tells the next agent to use `scoped-implementation` and paste or reference the exact slice receipt.

## Next Chat Prompt Format

```md
Use the scoped-implementation skill. Implement only Slice <N> from this plan:

<paste the complete slice receipt>

Before coding, confirm the authorized surface. After coding, report the authorization gate before the quality summary.
```
