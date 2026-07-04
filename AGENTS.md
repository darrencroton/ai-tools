# Global Agent Instructions

These instructions apply to all projects across all AI coding assistants (Claude Code, Codex CLI, etc.).

---

## Code Quality

- Work to the highest professional coding standards at all times
- Write documentation as you go
- For full test suite runs (>1 min): delegate to a subagent, have it capture output and return a pass/fail summary, then act on that report in the main context — do not fill the main context with raw test output
- Check exit codes after every execution and report failures immediately
- Never simplify failing tests — they indicate real problems that must be fixed

---

## Delegation & Model Selection

- For every non-trivial coding task, explicitly consider whether a subagent should be used for implementation support, independent analysis, testing, or review
- Choose the least expensive and lowest effort model that is likely to handle the delegated work reliably
- Escalate to a stronger model and/or higher effort level when the task is complex, architectural, scientific, security-sensitive, performance-sensitive, cross-cutting, or when the current agent may not be strong enough for the required reasoning
- Delegation does not transfer responsibility: the main agent remains accountable for the final implementation, must review subagent output critically, and must verify changes before reporting success
- Do not spawn subagents for trivial edits, quick factual checks, or tasks where delegation overhead exceeds the benefit
- When running long or noisy commands through a subagent, have it capture output and return a concise pass/fail summary with key evidence, not raw logs

---

## Git Workflow

- Never use `--no-verify` or bypass hooks
- Never amend a previous commit — always create a new one
- Always ask the user before committing; never commit without explicit approval
- Commit to the branch currently being worked on — do not auto-create a new branch (including off `main`) unless explicitly told to
- Commit messages must be meaningful, list every changed file with reasons, grouped logically

---

## File Organisation

- Never delete files — archive to `archive/` subdirectories in the project root (create `archive/` if needed; add to `.gitignore` if working in a repo)
- When asked to create a Markdown report write to the `docs/` directory (default) or Obsidian (if told) → use `obsidian-inbox/` directory

---

## Writing & Markdown

- When writing Markdown do NOT hard-wrap or truncate prose — modern Markdown readers soft-wrap automatically, and manual wrapping renders poorly and is hard to read/edit.
