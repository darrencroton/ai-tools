---
name: scoped-implementation
description: Implement one frozen acceptance slice from an implementation-plan without expanding scope. Use only when the user explicitly asks for this skill or provides an implementation-plan slice to execute. After this skill, the user should explicitly call drift-audit for authorization review.
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
5. **Prepare drift audit input** - collect the frozen contract, changed files, diff summary, and validation results for the user's next explicit call to the drift-audit skill.
6. **Report receipt** - finish with the implementation receipt below. Do not run the drift-audit skill as part of this skill unless the user explicitly calls both skills in the same request.

## Delegation

Keep small implementation slices local when delegation would add more prompt/context overhead than value.

Use `ai-orchestrator` only when it is also explicitly requested or already active for the task. When using it:

- delegate codebase mapping, long-running tests, or implementation of a well-bounded slice when that improves quality or saves meaningful time
- delegate the drift-audit skill only when the user explicitly called the drift-audit skill or explicitly asked to combine implementation and drift audit
- never let a worker expand the slice, approve drift, or own the final verdict
- give edit workers the frozen contract and exact authorized surface
- give workers applying the drift-audit skill only the frozen contract, diff, and relevant tests

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

### Tests Added / Updated
- ...

### Validation Run
- ...

### Drift Audit Input
- Frozen contract:
- Diff / changed files:
- Relevant tests:

### Recommended Next Step
- Explicitly call the drift-audit skill before the code-review skill.

### Rollback Path
- ...
```
