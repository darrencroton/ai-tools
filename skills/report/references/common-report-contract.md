# Common Report Contract

Use this contract for every report mode unless the mode template explicitly marks a section as optional.

## Required Sections

1. `Objective`
   - State the question, task, or decision the report addresses.
2. `Scope`
   - State what is included.
   - State what is excluded.
3. `Context`
   - Provide only the background needed to interpret the report.
4. `Method`
   - State how the investigation, evaluation, or planning was performed.
   - For solo work, list the files, commands, tests, traces, logs, or documents checked.
   - For multi-contributor work, name the contributors used, their role, and what each was asked to check.
5. `Evidence`
   - List files, commands, traces, logs, tests, docs, or observations used.
6. `Findings`
   - Record the discovered facts.
7. `Interpretation`
   - Explain what the findings mean for the task at hand.
8. `Risks / Unknowns`
   - List what remains uncertain, unverified, or blocked.
9. `Recommended Next Actions`
   - Give concrete next steps in priority order.
10. `Completion Status`
   - Explain why this report is complete for its stated scope.

## Evidence Rules

- Prefer primary evidence over recollection.
- Include file paths where relevant.
- For codebase investigations and code-derived claims, include `path:line` citations for every material claim.
- Name commands or checks that produced important results.
- Distinguish `Confirmed` from `Assumption` and `Unverified`.
- Do not hide missing evidence; state the gap directly.

## Writing Rules

- Use headings, not essay-style narrative.
- Prefer short bullets under each section.
- Keep claims scoped to the evidence available.
- Avoid mixing facts and recommendations in the same bullet.
- When the report makes recommendations or evaluative judgments, define the applicable judgment criteria before the interpretation-heavy section instead of reasoning ad hoc.
- Make the report reusable by another human or AI assistant without rereading the full chat.

## Output Skeleton

```md
## Objective

## Scope

## Context

## Method

## Evidence

## Findings

## Interpretation

## Risks / Unknowns

## Recommended Next Actions

## Completion Status
```
