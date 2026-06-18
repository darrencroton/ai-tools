# Delegation Prompt Templates

Use these templates to define the work by role. Fill in all fields. Remove placeholder text. Include only task-relevant context, but include enough that the worker does not need unstated background.

Templates define prompt shape only. To run a template, choose a model from [SKILL.md](../SKILL.md), then use that model's reference file for the actual CLI invocation.
Always pass `--add-dir` with the tightest directory scope that covers the task — not the repo root — to prevent workers reading outside their intended scope.
Use scanner-safe `SECTION: NAME` markers in prompts. The helper extractor matches `SECTION:` header lines by pattern, so minor Markdown formatting differences in model output do not matter. For analysis or investigation, require `path:line` citations for every material claim. Keep outputs concise; prefer bullets and short tables over long prose dumps.
For correctness-critical investigations, explicitly name the required evidence scope in the prompt: the files, directories, docs, configs, schemas, or artifacts the worker must check before concluding.
Every worker prompt must include `REQUIRED SKILLS`. Use `none` when no skill is required. When skills are listed, the worker must read each named `SKILL.md` completely if available, report `skill unavailable: <name>` for missing skills, and still follow the explicit task contract in the prompt.

## Role Ranges

- **Senior worker**: multi-file changes, refactors, complex logic, plan review, deep debugging
- **Junior worker**: surgical edits, approved git/GitHub operations, low-stakes web research, codebase mapping, non-critical summarising

## Output Patterns

**Confirmation tasks** (make a change, run an operation):
Use a literal `RESULT:` contract:
- Start line 1 with `RESULT:`
- Return only the `RESULT:` line or lines
- Do not include preamble, progress updates, or trailing notes
- If blocked, write `RESULT: blocked - <reason>`

**Information tasks** (find, analyse, research):
Use structured section markers in the prompt (e.g. `SECTION: FINDINGS`, `SECTION: RISKS`).
When reliable captured structure matters, put the hard format contract inside `RETURN:` next to the requested sections:
- Start with the first literal `SECTION:` line on line 1
- Return only the requested sections, in the order shown
- Do not include preamble, progress updates, or trailing notes outside those sections
- Write `- none` for empty sections instead of omitting them

## Worker Mode

This line is pre-baked at the top of every template below:

```
WORKER MODE: Delegated worker only — no ai-orchestrator skill, no re-delegation, complete locally, report blockers.
```

---

## SENIOR WORKER — Read-Only Investigation

```
WORKER MODE: Delegated worker only — no ai-orchestrator skill, no re-delegation, complete locally, report blockers.

TASK: <Specific investigation question>

REQUIRED SKILLS:
  - <skill-name or none>

FILES:
  - </absolute/path/to/file1.ext>
  - </absolute/path/to/file2.ext>

REQUIRED COVERAGE:
  - <Files, directories, docs, configs, or artifacts that must be checked before concluding>

FOCUS:
  - <Question or claim to verify>
  - <Question or claim to verify>

CONTEXT:
<Minimal task-specific background. Include only details the worker cannot infer from the code.>

CONSTRAINTS:
  - Read-only analysis
  - Cite `path:line` for every material claim
  - Keep the output compact; include only material findings
  - If any required coverage item was not checked, say so explicitly
  - If more files are needed, name them in SECTION: OPEN_QUESTIONS instead of guessing

RETURN:
SECTION: FINDINGS
<Numbered or bullet list with `path:line` citations>

SECTION: EVIDENCE
<Claim -> `path:line` support>

SECTION: RISKS
<Bullets>

SECTION: OPEN_QUESTIONS
<Bullets>

When done, print RESULT: followed by 1-2 sentences on the main conclusion and any blocker.
```

---

## SENIOR WORKER — Complex Edit

```
WORKER MODE: Delegated worker only — no ai-orchestrator skill, no re-delegation, complete locally, report blockers.

TASK: <imperative verb + specific description>

REQUIRED SKILLS:
  - <usually scoped-implementation, or none>

FROZEN CONTRACT:
  - Intended slice: <one narrow acceptance slice>
  - Allowed files/functions: <exact authorized surface>
  - Tests expected: <tests to add/update/run>
  - Explicit non-goals: <behaviour and files that must not change>
  - Risky surfaces approved: <none or explicit approval state>

FILES:
  - </absolute/path/to/file1.ext>
  - </absolute/path/to/file2.ext>

REQUIRED COVERAGE:
  - <Files, configs, conventions, or artifacts that must be checked before editing>

CONTEXT:
<Paste relevant code or describe current state. Include function signatures,
data structures, or key logic that bears on the task.>

TARGETS:
- <Exact function names or `path:line` anchors to edit>
- <Current snippet to replace when that will save discovery time>

CONSTRAINTS:
  - Stay inside FROZEN CONTRACT; do not expand scope
  - <What must not change>
  - <Coding style / conventions>

EXPECTED OUTPUT: <What done looks like>

RETURN:
RESULT: <1-2 sentences of what was done>
```

---

## SENIOR WORKER — Plan Review

```
WORKER MODE: Delegated worker only — no ai-orchestrator skill, no re-delegation, complete locally, report blockers.

REVIEW THIS PLAN AND FIND PROBLEMS:
---
<Paste the orchestrator's full plan, approach, or proposed code>
---

REQUIRED SKILLS:
  - <usually implementation-plan for plan critique, or none>

CODEBASE CONTEXT:
<Relevant absolute file paths, function names, data structures, or constraints.>

RETURN:
SECTION: CONCERNS
<Numbered list of concerns, risks, missed edge cases, or logic errors. Cite `path:line` when referencing code.>

SECTION: BETTER_APPROACHES
<Bullets>

SECTION: OPEN_QUESTIONS
<Bullets>

When done, print RESULT: followed by 1 sentence on the number and severity of issues found.
```

---

## SENIOR WORKER — Hostile Drift Audit

```
WORKER MODE: Delegated worker only — no ai-orchestrator skill, no re-delegation, complete locally, report blockers.

TASK: Audit this completed implementation for authorization drift before normal code quality review.

REQUIRED SKILLS:
  - drift-audit

FROZEN CONTRACT:
<Paste intended slice, acceptance criteria, allowed files/functions, expected tests, explicit non-goals, risky surfaces, and rollback path.>

DIFF:
<Paste git diff or provide exact patch/commit reference and files to inspect.>

RELEVANT TESTS:
  - <Test files or commands relevant to the slice>

CONSTRAINTS:
  - Read-only audit
  - Do not perform general code review except where it proves drift
  - Focus on extra behaviour, removed behaviour, hidden rewrites, missing tests, and new coupling
  - Cite `path:line` for every material claim when inspecting files

RETURN:
SECTION: AUTHORIZATION_FINDINGS
<Numbered list. Say `- none` if no drift found.>

SECTION: BEHAVIOR_ADDED
<Bullets>

SECTION: BEHAVIOR_REMOVED
<Bullets>

SECTION: MISSING_TESTS
<Bullets>

SECTION: NEW_COUPLING
<Bullets>

SECTION: VERDICT
<PASS / FAIL / PASS_WITH_RISKS and one sentence>

When done, print RESULT: followed by 1 sentence on whether the implementation stayed inside the contract.
```

---

## JUNIOR WORKER — Surgical Edit

```
WORKER MODE: Delegated worker only — no ai-orchestrator skill, no re-delegation, complete locally, report blockers.

TASK: <Single specific change — one sentence, imperative>

REQUIRED SKILLS:
  - <usually scoped-implementation, or none>

FROZEN CONTRACT:
  - Intended slice: <one narrow acceptance slice>
  - Allowed file/function: <exact authorized target>
  - Explicit non-goals: <behaviour and files that must not change>

FILE: </absolute/path/to/file.ext>

LOCATION: <Function name, class name, or line range>

CURRENT CODE:
<Paste exact current code>

CHANGE TO:
<Paste exact replacement or describe precisely>

CONSTRAINTS:
  - Do not touch any other files
  - Do not refactor surrounding code
  - Stop and report blocked if the requested change requires expanding the contract

VERIFY: <Test command to run after change, if applicable>

RETURN:
RESULT: <1 sentence of what was changed>
```

---

## JUNIOR WORKER — Git / GitHub Operation

```
WORKER MODE: Delegated worker only — no ai-orchestrator skill, no re-delegation, complete locally, report blockers.

TASK: <Specific git or GitHub operation>

REQUIRED SKILLS:
  - commit

REPO: <Local path or GitHub owner/repo>

CONTEXT:
<What was done, what this commit/PR represents, relevant branch names,
issue numbers, or anything needed to write a good message.>

USER APPROVAL: <explicitly approved / not approved>

REQUIREMENTS:
  - <Commit message format, PR title, labels, etc.>
  - <Constraints — e.g. do not push to main>

APPROVAL GATE:
If approval is not explicit, stop after preparing the message/body/command summary.
Do not commit, push, merge, open a PR, or modify remote state.

RETURN:
RESULT: <1-2 sentences of what was done>
```

---

## JUNIOR WORKER — Low-Stakes Web Research

```
WORKER MODE: Delegated worker only — no ai-orchestrator skill, no re-delegation, complete locally, report blockers.

RESEARCH: <Specific question or topic>

REQUIRED SKILLS:
  - <openai-docs for OpenAI product/API questions, summarise-paper for science papers, or none>

FIND:
  - <Exactly what is needed>
  - <Any secondary questions>

SOURCE PREFERENCE: <official docs / recent articles / GitHub issues / papers>

SECTION: FINDINGS
<Bullet list only. Include source URLs and dates where relevant. Call out uncertainty.>

IMPORTANT:
  - Low-stakes task only
  - Do not edit any files
  - Do not make correctness-critical decisions
  - If the task becomes important or high-risk, stop and say so
```

---

## JUNIOR WORKER — Low-Stakes Codebase Mapping

```
WORKER MODE: Delegated worker only — no ai-orchestrator skill, no re-delegation, complete locally, report blockers.

TASK: <Low-stakes codebase-mapping question>

REQUIRED SKILLS:
  - <skill-name or none>

SCOPE:
  - <Directories, files, or symbols to inspect>
  - <What to ignore, if relevant>

RETURN:
SECTION: FINDINGS
<Bullet list only. Include file paths, symbols, and `path:line` citations where relevant. Call out uncertainty.>

IMPORTANT:
  - Do not edit any files
  - Do not make correctness-critical decisions
  - If the task becomes important or high-risk, stop and say so
```

---

## Escalation Rules

| Condition | Action |
|---|---|
| Junior worker task touches > 1 file | Escalate to a senior worker |
| Junior worker task requires understanding > ~100 lines | Escalate to a senior worker |
| Junior worker research or mapping becomes correctness-critical | Keep it with the orchestrator or escalate to a senior worker |
| Task asks to verify a workplan or prove parity against code | Use a senior read-only investigation |
| Git/GitHub action lacks explicit user approval | Do not execute the state-changing action; draft only |
| Any tool given an ambiguous prompt | Clarify with user before delegating |
