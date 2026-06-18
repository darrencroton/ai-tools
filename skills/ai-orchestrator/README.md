# AI Orchestrator

A skill for AI coding assistants (Claude Code, Codex CLI, GitHub Copilot CLI) that turns the current assistant into an **orchestrator** — selectively routing coding and analysis work to external AI CLI tools while retaining ownership of planning, authorization, quality, final synthesis, and final delivery.

## Purpose

The orchestrator delegates selectively when a worker will improve quality, speed, independence, or context management. It keeps work local when the slice is small, prompt construction would cost more than the task, delegation would weaken correctness, or the orchestrator needs tight control of the acceptance boundary.

Workers produce inputs, evidence, drafts, and implementation. The orchestrator remains the finisher and must retain the final user-facing deliverable, authorization decisions, accept-or-reject decisions, and correctness-critical judgment.

This skill is standalone. It can run by itself with only the files in this repository. When installed alongside [`ai-tools`](https://github.com/darrencroton/ai-tools), it can also coordinate companion skills such as [`implementation-plan`](../implementation-plan/), [`scoped-implementation`](../scoped-implementation/), [`drift-audit`](../drift-audit/), [`code-review`](../code-review/), [`code-simplifier`](../code-simplifier/), [`handoff`](../handoff/), and [`commit`](../commit/).

## Supported Tools

| Tool | Role | Best for |
|---|---|---|
| **Claude Code** (`claude`) | Orchestrator, Senior worker | Complex edits, refactors, deep debugging, plan review |
| **Codex CLI** (`codex`) | Orchestrator, Senior worker | Complex edits, refactors, deep debugging, plan review |
| **GitHub Copilot CLI** (`copilot`) | Junior worker | Surgical edits, git/GitHub ops, low-stakes research, codebase mapping |

## Structure

```
SKILL.md                  # Main skill definition, roles, workflow, model table
ai-reminder               # tmux reminder helper for Codex/Claude sessions
scripts/
  worker_jobs.py          # tracked worker launcher/status/activity/cancel/extract helper
references/
  claude.md               # Claude Code CLI reference and commands
  codex.md                # Codex CLI reference and commands
  copilot.md              # GitHub Copilot CLI reference and commands
  templates.md            # Delegation prompt templates by role and task type
```

## Usage

This skill is loaded by an AI coding assistant that supports skill files (e.g. Claude Code). Once loaded, the assistant acts as orchestrator and uses the templates and model references to delegate work.

Operating conventions:
- Start with a short execution checklist and keep it updated through the run
- Decide whether delegation is worth the overhead; do not delegate by default
- Name required skills explicitly in the checklist and in each worker prompt, or write `none`
- Use self-contained worker prompts with absolute paths when practical
- Include the frozen contract for implementation work: intended slice, allowed files/functions, expected tests, explicit non-goals, risky surfaces, and validation plan
- For analysis tasks, ask workers to return `SECTION:` markers plus `path:line` evidence
- Use `scripts/worker_jobs.py` for worker launches; artifacts are written to `.ai-orchestrator/runs/` in the project by default (override with `AI_ORCHESTRATOR_ARTIFACT_ROOT`)
- Use `--run-dir current` to reference the latest run without knowing the timestamped path
- Use `worker_jobs.py activity` as the worker health check; for session-backed tools it reads lightweight session signals, otherwise it uses helper-managed file activity
- Use `worker_jobs.py cancel` to stop workers cleanly and preserve final status
- Use `worker_jobs.py extract` to read each worker's clean final output rather than raw wrapper output; inspect raw stdout or stderr only for failures, malformed extraction, or debugging
- Use `worker_jobs.py extract --json` when you need the extracted text plus its source artifact for debugging
- Use worker labels in lowercase kebab-case: `<nn>-<tool>-<subtask-slug>[-rN]` so files sort cleanly within each run directory
- While workers run, stay in the orchestrator role: monitor status, manage the checklist, and prepare synthesis or follow-up review prompts rather than duplicating the delegated investigation
- Run or request authorization drift audit before quality review when a frozen implementation contract exists

## Explicit Skill Coordination

The orchestrator does not assume workers will infer skills from context. Every worker prompt includes `REQUIRED SKILLS`.

When a required skill is available to a worker, the worker should read that skill before acting. When it is not available, the worker should report `skill unavailable: <name>` and continue with the explicit task contract in the prompt.

Common companion skills when this repository is used with `ai-tools`:

| Skill | How the orchestrator uses it |
|---|---|
| [`implementation-plan`](../implementation-plan/) | Plan-first workflow; freeze slices, authorized surfaces, validation plans, and rollback paths |
| [`scoped-implementation`](../scoped-implementation/) | Implementation workflow; execute one frozen slice and prepare drift-audit input |
| [`drift-audit`](../drift-audit/) | Authorization gate after implementation and before quality review |
| [`code-review`](../code-review/) | Quality gate after drift audit, or standalone code review |
| [`code-simplifier`](../code-simplifier/) | Explicit separate simplification/refactor pass for working code |
| [`handoff`](../handoff/) | Preserve task state, frozen contract, validation, and next action |
| [`commit`](../commit/) | Approved git commits with explicit staging and complete commit messages |

The companion skills are helpful but not mandatory. The orchestrator prompts carry the essential contract so workers can still complete or audit the task when those skills are unavailable.

Trigger conditions:
- The user wants to delegate a task to an external AI agent
- The user mentions `claude`, `codex`, or `copilot` explicitly
- The user asks to "use another model"
- The user wants to spread work across multiple models

## Optional Helper

`ai-reminder` is a small companion script for long-running Codex or Claude sessions. The skill itself works without it, but on long coding tasks an orchestrator can drift and stop delegating as consistently as the workflow intends. Running `ai-reminder` alongside the session provides a periodic nudge back toward the current task, plan, and delegation discipline.

NOTE: The orchestrator must be running inside a tmux pane for `ai-reminder` to work.

Typical usage:
- `ai-reminder start --tool codex`
- `ai-reminder start --tool claude --interval 120`

Ensure the script is executable before first use: `chmod +x ai-reminder`.

If you use it regularly, add a shell alias so it can be launched from whatever project you are currently working in. Run `ai-reminder --help` for the full command set and option details.

## Roles

| Role | Purpose | Typical tasks | Hard limits |
|---|---|---|---|
| **Orchestrator** | Human-facing owner and finisher | Planning, delegation, context packaging, verification, testing, final synthesis, final answer/report/recommendation | Only the assistant directly handling the user; must retain the final user-facing deliverable, the acceptance decision, and correctness-critical judgment |
| **Senior worker** | Deep technical work | Multi-file edits, refactors, complex logic, plan review, implementation drafts, evidence gathering | No re-delegation; outputs are inputs or drafts for orchestrator review, not the final deliverable |
| **Junior worker** | Tactical work | Surgical edits, approved git ops, low-stakes research, codebase mapping, support-text drafts | Escalate when scope or importance grows; outputs are inputs or drafts for orchestrator review, not the final deliverable |

## Adding (Removing) a Model

1. Add a new row to the model table in `SKILL.md` (or remove the relevant row)
2. Add `references/<model>.md` following the structure of the existing model files (or remove the relevant file)
3. Update `scripts/worker_jobs.py` if the model needs custom activity, extraction, or session matching behavior
4. Update `README.md` and `AGENTS.md` if the supported structure or maintenance expectations changed
5. Only update `references/templates.md` if the new model requires a new role, prompt shape, or output-extraction pattern
