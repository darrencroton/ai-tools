# AI Tools

A collection of skills for AI-assisted software development that stays narrow, auditable, and under your control. Each run implements one small slice, proves it stayed inside the agreed boundaries, and asks before committing. The agent moves fast inside the lane — it just doesn't get to redraw it.

## What you can do with this

Use these skills when you want AI to help you implement features and fix bugs without losing track of what changed and why.

**Stay in the loop as work progresses.** Plan the work upfront, then run one slice at a time with checkpoints between them. The agent pauses before risky changes, surfaces drift and review findings, and asks for your approval before committing. Good when the work touches sensitive surfaces or you want a clear record of each decision.

**Hand it over and come back when it's done.** Give the agent a complete plan and let it run all remaining slices on its own — implementing, auditing scope, reviewing quality, and committing each slice that passes all gates. It stops if it hits a slice you've flagged for human approval or a problem it can't resolve within the agreed contract. Good when the plan is well-isolated and the cost of an error is low.

**Supervise the run with MC.** Use `master-controller` when you want the gatekeeper outside the slice orchestrator. In model-supervised mode, the MC model stays in the loop while deterministic tools start slices, observe tmux state, preserve artifacts, and enforce gates. In deterministic batch mode, MC runs fail-closed without live model judgment. Both modes accept work only from local evidence: authorized files, validation, drift audit, code review, commit ancestry, and clean git state.

All paths use the same underlying skill chain. They differ in who holds the gates, how much state is captured mechanically, and when handoff happens.

## Skills

This README is the maintained human-facing skill index. Each skill's own `SKILL.md` remains the source of truth for trigger conditions, detailed workflow, and output format.

| Skill | What it does |
|-------|-------------|
| [`implementation-plan`](skills/implementation-plan/) | Breaks a request into auditable slices, with optional batches when stronger implementers can safely combine related slices. Each slice gets acceptance criteria, an authorized surface, validation, risk flags, and a copyable prompt for the next chat. |
| [`master-controller`](skills/master-controller/) | Supervises execution of an existing implementation plan one slice at a time. Creates durable run state, checks slice eligibility, launches tmux-backed harness sessions, captures artifacts, and verifies gates without becoming a planner. |
| [`scoped-implementation`](skills/scoped-implementation/) | Implements one frozen slice without expanding scope. Restates the authorized surface before coding, stays inside approved files, and prepares a receipt for drift audit. |
| [`drift-audit`](skills/drift-audit/) | Answers one question: was the implementation authorized? Compares actual changes against the frozen contract before any quality review. |
| [`code-review`](skills/code-review/) | Performs a senior-level review after drift audit passes. Covers correctness, edge cases, tests, error handling, maintainability, and domain-specific risks. |
| [`ai-orchestrator`](skills/ai-orchestrator/) | Manages delegation to external AI tools when independence, parallel work, or context economy helps. Owns planning, verification, and final responsibility. |
| [`code-simplifier`](skills/code-simplifier/) | Refines working code for clarity and maintainability without changing behaviour. A separate cleanup pass, not part of the default implementation flow. |
| [`handoff`](skills/handoff/) | Writes a compact handoff file when continuing in another chat. Captures current status, what's left, blockers, and the single best next action. |
| [`commit`](skills/commit/) | Stages and commits specific files by name, never skips hooks, and writes a message that lists changed files with reasons. Only called after explicit approval. |
| [`report`](skills/report/) | Produces concise evidence-backed written synthesis when explicitly requested. Optional; not part of the default implementation gate chain. |
| [`summarise-paper`](skills/summarise-paper/) | Summarises a science paper from a local PDF or URL into a structured markdown document with accuracy and quote-fidelity checks. |

## Workflow

The default flow for feature or bug work:

1. **Plan** — call the `implementation-plan` skill. Define slices, freeze contracts, flag risky surfaces.
2. **Implement** — new chat, call the `scoped-implementation` skill with the slice receipt. One slice per chat.
3. **Audit scope** — call the `drift-audit` skill. Was what happened authorized?
4. **Review quality** — call the `code-review` skill after drift audit passes.
5. **Simplify** (optional) — call the `code-simplifier` skill if you want a cleanup pass over working code.
6. **Hand off** (if needed) — call the `handoff` skill before ending a session that isn't finished.
7. **Commit** — call the `commit` skill only after you approve.

Use `ai-orchestrator` when delegation improves quality, speed, or context management, such as independent review, plan critique, codebase mapping, or long-running validation. Use `master-controller` when you want the same plan execution guarded by durable state and external gate verification.

Use explicit skill calls. Do not rely on the model to guess which workflow applies.

### Running A Plan

The plan outputs a `Next Chat Prompt`. Paste it into a fresh session. Choose which version fits your situation:

**Mode A — Stay in the loop.** You approve before risky slices and before each commit. One slice, a few tightly-coupled slices, or a named batch per chat, then a handoff to the next session.

**Mode B — Step away.** The agent runs all remaining slices, gates each one, and comes back with a summary. It stops on any approval-gated slice or unresolvable problem.

The copyable Mode A and Mode B launcher templates live in [`skills/implementation-plan/SKILL.md`](skills/implementation-plan/SKILL.md) (section "Next Chat Prompt Format") — that file is the single source for them; every generated plan already ends with the right one filled in.

**Mode C — Run through MC.** Use `master-controller` when the plan is complete and you want the Mode A slice-by-slice workflow managed by an outside controller instead of by repeated human prompts. MC keeps durable state, starts each eligible slice in a fresh harness session, verifies the gates from outside that session, commits only slices that pass validation, drift audit, and code review, and stops for you on approval-gated work or anything outside policy.

Mode C has two variants:

- **Mode C1 — Model-supervised MC.** Use this when nuanced operational supervision matters. The MC model remains active, reads pane/log/json evidence, decides whether a live interruption is recoverable, and invokes deterministic MC tools for state transitions. Rolling 5-hour usage windows or temporary service interruptions can be bounded pauses when the pane evidence is clear and the harness session is still resumable. Weekly, monthly, account, billing, credential, trust, permission, dependency/license, remote-side-effect, destructive-action, or ambiguous conditions are user stops.
- **Mode C2 — Deterministic batch MC.** Use this for simple unattended runs where fail-closed behavior is enough. MC can run `run --scope remaining`, but it should stop rather than interpret unclear operational text. This remains the compatibility path for straightforward plans.

Mode C1 launcher:

```md
Plan file: <path>
Target repo: <path>
Scope: <next slice, or all remaining slices>
Harness: codex unless I specify otherwise. (claude, copilot, and opencode are also validated MC orchestrator harnesses — name one explicitly to use it.)
Worker tools: <omit unless the plan requires external workers, e.g. copilot or opencode>

Use master-controller as the supervising skill for this run in model-supervised Mode C.

Read the full plan file first. If the plan is incomplete, ambiguous, not an implementation-plan output, or needs material editing, stop and report instead of improvising.

Use the current feature branch unless I explicitly name another branch. Confirm the target repo, plan file, branch, scope, harness, and worker tools before starting runtime execution.

Initialize or reuse the MC run for this repo and plan, then run preflight and dry-run the next slice. Use MC profiles for normal local execution; do not ask me to hand-compose Codex or Claude sandbox flags.

For each eligible slice, keep the MC model in the loop. Start the slice through MC, observe pane/log/json/git evidence on a calm cadence, and let deterministic MC commands preserve artifacts and enforce state transitions. If a structured result appears, finalize the slice through MC gates before advancing.

Use the model-supervised primitive loop rather than `run --scope remaining`: `start-slice`, then repeated `observe` or bounded `wait`, then exactly one of `pause-until`, `send`, `finalize-slice`, or `stop-with-evidence` based on the evidence. After `finalize-slice` passes, dry-run or start the next eligible slice. Keep all acceptance decisions inside MC's deterministic gates; pane text can justify operational wait/resume/stop decisions only.

Recover only bounded operational interruptions that are clearly transient and do not expand the slice contract. A rolling 5-hour usage window with a parseable reset can be paused until reset plus buffer when the harness process is alive; after re-observing for hard-stop prompts, send a continuation prompt such as `You were interrupted. Review what you were doing then continue.` If the process exited before writing a result, restart only from a clean authorized state; otherwise stop with evidence.

Stop and report for any approval-gated slice, missing evidence, validation failure, drift, review failure, unauthorized file change, dirty post-commit state, branch or plan mismatch, weekly/monthly/account/billing cap, credential/trust/permission prompt, requested external side effect, destructive action, dependency/license change, harness failure, ambiguous operational state, or blocker outside the frozen contract. Do not self-approve human-gated work.

When the requested scope stops or completes, summarize the MC run: slices attempted, slices committed, gate result for each slice, operational stop or recovery evidence if any, artifact location, current git status, and the next action needed from me.
```

Mode C2 deterministic batch launcher:

```md
Plan file: <path>
Target repo: <path>
Scope: <next slice, or all remaining slices>
Harness: codex unless I specify otherwise. (claude, copilot, and opencode are also validated MC orchestrator harnesses — name one explicitly to use it.)
Worker tools: <omit unless the plan requires external workers, e.g. copilot or opencode>

Use master-controller as the supervising skill for this run.

Read the full plan file first. If the plan is incomplete, ambiguous, not an implementation-plan output, or needs material editing, stop and report instead of improvising.

Use the current feature branch unless I explicitly name another branch. Confirm the target repo, plan file, branch, scope, harness, and worker tools before starting runtime execution.

Initialize or reuse the MC run for this repo and plan, then run preflight, dry-run the next slice, and run the requested scope. Use MC profiles for normal local execution; do not ask me to hand-compose Codex or Claude sandbox flags.

For each eligible slice, let MC launch a fresh orchestrator session, enforce the frozen authorized surface, require validation, drift-audit, code-review, and commit evidence, and advance only when all gates pass.

Stop and report for any approval-gated slice, missing evidence, validation failure, drift, review failure, unauthorized file change, dirty post-commit state, harness failure, branch or plan mismatch, requested external side effect, or blocker outside the frozen contract. Do not self-approve human-gated work.

When the requested scope stops or completes, summarize the MC run: slices attempted, slices committed, gate result for each slice, stop reason if any, artifact location, current git status, and the next action needed from me.
```

Mode C is the right family when you want the work to keep moving without manually reprompting each slice, but still want the safety boundary held outside the implementing agent. Use C1 when operational judgment matters; use C2 when a conservative batch run is enough. MC is not a planner; create or repair the plan first with `implementation-plan`, then hand the complete plan to MC.

Mode C operational notes:

- MC runs atomic slices only; plan batches (Batch A/B groupings) apply to Modes A and B, not Mode C. MC's scope selector is the next eligible slice or all remaining slices, in plan order.
- MC's blocking commands outlive assistant tool-call limits: run `run --scope remaining` in the background and poll `status`, and in C1 use repeated bounded `wait` calls rather than one long wait (see `master-controller/SKILL.md` → "Long-Running Command Discipline").
- For C1 on subscription harnesses, run the MC model on a different provider than the orchestrator harness so one usage window cannot stall the supervisor and the supervised session together.
- When MC stops on an approval-gated slice and you approve it, record the approval with `mc.py approve --slice "<Slice N>" --reason "<why>"` and rerun — do not edit the plan's approval flag (that breaks the frozen digest). If a plan must genuinely be revised mid-run, re-`init` with `--assume-complete` naming the already-committed slices so the new run resumes in the right place.

## Setup

- `AGENTS.md`: global instructions used across AI coding assistants
- `setup.sh`: links shared AI coding configuration files into local tool directories
- `tools.conf`: tool registration used by the setup script
- `skills/`: shared skill library; each skill documents itself in its own `SKILL.md`
- Generated files and local artefacts are excluded via `.gitignore`
