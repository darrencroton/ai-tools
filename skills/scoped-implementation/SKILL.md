---
name: scoped-implementation
description: Implement one frozen acceptance slice and audit actual changes against the authorized surface. Use only when the user explicitly asks for this skill or provides an implementation-plan slice to execute.
---

# Scoped Implementation

Use this skill when a plan already exists and the job is to implement one narrow slice without redrawing the lane.

## Preconditions

Before coding, identify the frozen contract:

- intended slice
- acceptance criteria
- allowed files/functions/components
- tests allowed or expected to change
- explicit non-goals
- risky surfaces and approval status
- validation plan
- rollback path

If the contract is missing or too vague, stop after drafting a candidate contract and ask the user to approve it. Do not implement a non-trivial change without an auditable slice.

## Workflow

1. **Confirm contract** - restate the authorized surface and non-goals briefly.
2. **Check starting state** - inspect `git status` and relevant files. Do not overwrite unrelated user changes.
3. **Implement only the slice** - keep edits inside the authorized files/functions. Do not perform opportunistic cleanup.
4. **Validate** - run the targeted checks from the contract. Add or update tests when the contract requires it.
5. **Authorization gate** - compare actual changes with the frozen contract before judging code quality.
6. **Quality gate** - review the implementation for correctness, tests, maintainability, and regressions.
7. **Report receipt** - finish with the implementation receipt below.

## Delegation

Keep small implementation slices local when delegation would add more prompt/context overhead than value.

Use `ai-orchestrator` only when it is also explicitly requested or already active for the task. When using it:

- delegate codebase mapping, long-running tests, implementation of a well-bounded slice, or hostile drift audit when that improves quality or saves meaningful time
- never let a worker expand the slice, approve drift, or own the final verdict
- give edit workers the frozen contract and exact authorized surface
- give audit workers only the frozen contract, diff, and relevant tests

## Authorization Gate

Before normal code review, answer:

- Intended slice:
- Allowed files/functions:
- Actual files/functions changed:
- Behaviour added:
- Behaviour removed:
- Tests added/updated:
- Explicit non-goals preserved:
- Drift found:
- Drift disposition: none / fixed / needs user approval

If drift exists, resolve it before the quality gate unless the user explicitly approves the expanded scope.

## Implementation Receipt

End with this shape:

```md
## Implementation Receipt

### Intended Slice
- ...

### Authorized Surface
- ...

### Actual Changed Surface
- ...

### Behaviour Added
- ...

### Behaviour Removed
- ...

### Tests Added / Updated
- ...

### Validation Run
- ...

### Authorization Gate
- PASS / FAIL / PASS WITH APPROVED DRIFT
- Notes:

### Quality Gate
- PASS / FAIL / PASS WITH RISKS
- Notes:

### Rollback Path
- ...
```
