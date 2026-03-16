# Bug Hunt Template

Use for defect isolation, regressions, incident analysis, or root-cause analysis.

## Required Additional Sections

- `Symptom`
- `Impact`
- `Reproduction Status`
- `Timeline`
- `Suspected Root Cause`
- `Confirmed Root Cause`
- `Fix Options`
- `Mitigations`
- `Follow-Up Actions`

## Completion Gate

Do not finish until the report contains:

- the user-visible or system-visible symptom
- the impact or current risk
- whether reproduction is confirmed, partial, or not yet achieved
- root-cause confidence
- concrete follow-up actions

## Template

```md
## Objective

## Scope

## Context

## Symptom

## Impact

## Reproduction Status

## Timeline

## Method

## Evidence

## Findings

## Suspected Root Cause

## Confirmed Root Cause

## Fix Options

## Mitigations

## Risks / Unknowns

## Follow-Up Actions

## Recommended Next Actions

## Completion Status
```

## Notes

- Keep the distinction between `Suspected` and `Confirmed`.
- State when the root cause is still an inference.
- If no reproduction exists, say what evidence still supports the current hypothesis.
- `Timeline` may be stated as `Not applicable` for code defects that are not production incidents.
