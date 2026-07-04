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

- For every non-trivial coding task, explicitly consider whether a subagent would improve quality, speed, testing, or independent review
- Match the delegated model to the task: use the least expensive reliable option for routine work, and escalate to a stronger model or higher effort for complex, architectural, scientific, security-sensitive, performance-sensitive, or cross-cutting work
- The main agent remains accountable: review subagent output critically, verify changes, and report only the validated result
- Do not delegate trivial edits, quick factual checks, or work where coordination overhead outweighs the benefit

---

## Communication

- During multi-step or long-running work, provide concise progress updates: one sentence at each main step describing what you are doing, what you learned, or what you will do next
- Keep updates sparse and useful; do not narrate trivial actions or repeat obvious status, but do not stay silent through meaningful phases of work

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
