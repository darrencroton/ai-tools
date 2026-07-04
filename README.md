# AI Tools

A collection of skills for AI-assisted software development that stays narrow, auditable, and under your control. Each run implements one small slice, proves it stayed inside the agreed boundaries, and asks before committing. The agent moves fast inside the lane — it just doesn't get to redraw it.

## What you can do with this

Use these skills when you want AI to help you implement features and fix bugs without losing track of what changed and why.

**Stay in the loop as work progresses.** Plan the work upfront, then run one slice at a time with checkpoints between them. The agent pauses before risky changes, surfaces drift and review findings, and asks for your approval before committing. Good when the work touches sensitive surfaces or you want a clear record of each decision.

**Hand it over and come back when it's done.** Give the agent a complete plan and let it run all remaining slices on its own — implementing, auditing scope, reviewing quality, and committing each slice that passes all gates. It stops if it hits a slice you've flagged for human approval or a problem it can't resolve within the agreed contract. Good when the plan is well-isolated and the cost of an error is low.

Both paths use the same skill chain. They differ only in who holds the gates and when handoff happens.

## Skills

This README is the maintained human-facing skill index. Each skill's own `SKILL.md` remains the source of truth for trigger conditions, detailed workflow, and output format.

| Skill | What it does |
|-------|-------------|
| [`implementation-plan`](skills/implementation-plan/) | Breaks a request into auditable slices, with optional batches when stronger implementers can safely combine related slices. Each slice gets acceptance criteria, an authorized surface, validation, risk flags, and a copyable prompt for the next chat. |
| [`scoped-implementation`](skills/scoped-implementation/) | Implements one frozen slice without expanding scope. Restates the authorized surface before coding, stays inside approved files, and prepares a receipt for drift audit. |
| [`drift-audit`](skills/drift-audit/) | Answers one question: was the implementation authorized? Compares actual changes against the frozen contract before any quality review. |
| [`code-review`](skills/code-review/) | Performs a senior-level review after drift audit passes. Covers correctness, edge cases, tests, error handling, maintainability, and domain-specific risks. |
| [`ai-orchestrator`](skills/ai-orchestrator/) | Manages delegation to external AI tools when independence, parallel work, or context economy helps. Owns planning, verification, and final responsibility. |
| [`code-simplifier`](skills/code-simplifier/) | Refines working code for clarity and maintainability without changing behaviour. A separate cleanup pass, not part of the default implementation flow. |
| [`handoff`](skills/handoff/) | Writes a compact handoff file when continuing in another chat. Captures current status, what's left, blockers, and the single best next action. |
| [`commit`](skills/commit/) | Stages and commits specific files by name, never skips hooks, and writes a message that lists changed files with reasons. Only called after explicit approval. |
| [`report`](skills/report/) | Produces structured engineering reports for investigations, bug hunts, comparisons, status updates, and final summaries. |
| [`summarise-paper`](skills/summarise-paper/) | Summarises a science paper from a local PDF or URL into a structured markdown document with accuracy and quote-fidelity checks. |

## Workflow

The default flow for feature or bug work:

1. **Plan** — call `implementation-plan`. Define slices, freeze contracts, flag risky surfaces.
2. **Implement** — new chat, call `scoped-implementation` with the slice receipt. One slice per chat.
3. **Audit scope** — call `drift-audit`. Was what happened authorized?
4. **Review quality** — call `code-review` after drift audit passes.
5. **Simplify** (optional) — call `code-simplifier` if you want a cleanup pass over working code.
6. **Hand off** (if needed) — call `handoff` before ending a session that isn't finished.
7. **Commit** — call `commit` only after you approve.

Use `ai-orchestrator` when delegation improves quality, speed, or context management, such as independent review, plan critique, codebase mapping, or long-running validation.

Use explicit skill calls. Do not rely on the model to guess which workflow applies.

### Running A Plan

The plan outputs a `Next Chat Prompt`. Paste it into a fresh session. Choose which version fits your situation:

**Mode A — Stay in the loop.** You approve before risky slices and before each commit. One slice, a few tightly-coupled slices, or a named batch per chat, then a handoff to the next session:

```md
Plan file: <path>
Slices or batch this session: <e.g. Slice 2, Slices 2–3, or Batch A>

Read the full plan file first. If a selected slice or batch receipt is incomplete or the plan state is unclear, stop and tell me before coding.

Work on the current feature branch for this plan; if none exists, create one and tell me the name.

Use ai-orchestrator as the controlling skill. Keep the implementation local; delegate per that skill's guidance when independence or context economy helps — primarily hostile drift-audit, independent code-review, and long-running tests.

For each selected slice or batch, in plan order:
1. Restate the frozen contract (authorized surface + non-goals) from the plan.
2. If any included slice's Risk Flags mark approval-needed, stop and get my approval before coding.
3. Apply scoped-implementation against the selected contract.
4. Apply drift-audit. Report the authorization gate result before any quality review.
5. If the gate passes, apply code-review. If it fails, fix the drift and re-audit.
6. Surface drift and review findings to me, fix them, then re-run the relevant gate. If consecutive reviews return only minor findings and have clearly converged record residuals in the slice summary and proceed.
7. Ask me before committing. On my approval, commit the selected slice or batch with the commit skill.

After the selected slice(s) or batch are committed, use handoff to record state and the next slice or batch to resume from. Do not continue past the selected scope.

Confirm before starting: plan file read, selected slice(s) or batch, branch, and the first slice.
```

**Mode B — Step away.** The agent runs all remaining slices, gates each one, and comes back with a summary. It stops on any approval-gated slice or unresolvable problem:

```md
Plan file: <path>
Scope: all remaining slices, in plan order.

Read the full plan file first. If the plan is incomplete or its state is unclear, stop and report instead of improvising.

Act as the orchestrator per the ai-orchestrator skill. You own the full run — implement, gate, recover, and make the accept/reject call. Delegate to other models for independence and context economy per that skill: at minimum a hostile drift-audit and an independent code-review per slice, plus long-running tests.

Setup: create a new branch for this run, switch to it, and report the name.

For each slice or approved batch, in plan order:
1. Restate the frozen contract (authorized surface + non-goals).
2. If any included slice's Risk Flags mark approval-needed, STOP the run and report — do not self-approve a slice the plan gated for a human.
3. Apply scoped-implementation against the selected contract.
4. Apply drift-audit (delegate a hostile audit). Record the authorization gate result.
5. If the gate fails, fix the drift inside the contract and re-audit. If it can't be fixed inside the contract, STOP and report.
6. On a passing gate, apply code-review (delegate for independence). Fix findings, then re-run the relevant gate. If consecutive reviews return only minor findings and have clearly converged record residuals in the slice summary and proceed.
7. When the slice passes validation, drift-audit, and code-review, commit it with the commit skill. This prompt is explicit approval to commit each slice that has cleared all three gates — and only those.

Stop the run early on: an approval-gated slice, a blocker, an unapproved scope change, a gate/validation failure unfixable inside the contract, or context pressure. On any stop, write a handoff with current state and the next slice or batch to resume.

When all slices are complete, write a final summary: slices committed, gate results per slice, and anything left for me to assess.

Confirm before starting: plan file read, branch name, the ordered slice list you'll execute, and the first slice.
```

## Setup

- `AGENTS.md`: global instructions used across AI coding assistants
- `setup.sh`: links shared AI coding configuration files into local tool directories
- `tools.conf`: tool registration used by the setup script
- `skills/`: shared skill library; each skill documents itself in its own `SKILL.md`
- Generated files and local artefacts are excluded via `.gitignore`
