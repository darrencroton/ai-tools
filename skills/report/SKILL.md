---
name: report
description: Create structured engineering reports with consistent scope, evidence, findings, risks, and next actions. Use when the user has asked you to produce a plan, proposal, investigation report, codebase exploration summary, execution-trace write-up, bug hunt or root-cause analysis, comparison, assessment, final report, or concise status update instead of free-form prose.
---

# Report

Use this skill to turn analytical or planning work into a structured report with a clear finish line. Do not write a free-form essay when this skill applies.

## Workflow

Follow this sequence:

1. Classify the request into the lightest report mode that fits.
2. Read [references/common-report-contract.md](references/common-report-contract.md).
3. Read the matching mode template:
   - [references/template-plan.md](references/template-plan.md)
   - [references/template-investigation.md](references/template-investigation.md)
   - [references/template-bug-hunt.md](references/template-bug-hunt.md)
   - [references/template-comparison.md](references/template-comparison.md)
   - [references/template-final-report.md](references/template-final-report.md)
   - [references/template-quick-update.md](references/template-quick-update.md)
4. Gather the evidence required for that mode before writing the report body.
5. Write the report in the template's section order.
6. End only after the completion gate for that mode is satisfied.

## Mode Selection

Choose exactly one primary mode unless the user explicitly asks for a hybrid report.

If the task spans two modes, choose the mode that matches the primary deliverable and explicitly import the essential sections from the secondary mode. Example: an investigation that ends in a recommended implementation can stay in `investigation` mode while adding the necessary planning sections.

- `plan`: new features, implementation proposals, design choices, phased rollout plans
- `investigation`: codebase exploration, behaviour tracing, execution path analysis, system understanding
- `bug-hunt`: defect isolation, regressions, incident analysis, root-cause analysis
- `comparison`: compare implementations, options, branches, libraries, architectures, or observed behaviours
- `final-report`: close-out summary after work is complete or when the user asks for an assessment
- `quick-update`: short status report where a full report would be unnecessarily heavy

If the request is "find out how this works," default to `investigation`.
If the request is "what should we build," default to `plan`.
If the request is "why is this broken," default to `bug-hunt`.
If the request is "which option is better," default to `comparison`.
If the request is "summarize what was done," default to `final-report`.

## Evidence Standard

Base the report on concrete evidence, not impression.

- Reference the files inspected.
- Name commands, traces, tests, logs, or documents used.
- Separate confirmed facts from assumptions.
- State what was not checked when that affects confidence.
- Keep conclusions proportional to the evidence available.
- When synthesizing outputs from multiple workers or contributors, define the judgment criteria before filling interpretation-heavy sections such as `Interpretation`, `Current Understanding`, `Recommended Approach`, or `Decision`.

## Output Rules

- Prefer headings and short bullets over long prose blocks.
- Keep the template's section order stable.
- Use explicit labels such as `Confirmed`, `Assumption`, `Unverified`, and `Blocked` when helpful.
- Make scope boundaries explicit.
- Make next actions concrete and ordered.
- Include a completion statement tied to the requested scope.

Write reports to `docs/` by default only when the user asks for a document or file output. Otherwise, use the report structure directly in the response.

## Completion Gates

Do not conclude early. A report is complete only when its required sections and gate checks are satisfied.

- `plan`: include goals, non-goals, constraints, recommended approach, implementation phases, and validation plan
- `investigation`: include the question, evidence trail, current understanding, and remaining unknowns
- `bug-hunt`: include symptom, impact, reproduction status, root-cause confidence, and next actions
- `comparison`: define comparison criteria before recommending an option
- `final-report`: include work completed, validation performed, and residual risks
- `quick-update`: include current state, what changed, next step, and blockers or "none"

If a required section has no content, state that explicitly instead of omitting it.
