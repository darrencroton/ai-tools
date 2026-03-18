# Global Agent Instructions

These instructions apply to all projects across all AI coding assistants (Claude Code, Codex CLI, etc.).

---

## Code Quality

- Work to the highest professional coding standards at all times
- Write documentation as you go
- If available, use subagents to run full test suites when required
- Check exit codes after every execution and report failures immediately
- Never simplify failing tests — they indicate real problems that must be fixed

---

## Git Workflow

- Never use `--no-verify` or bypass hooks
- Never amend a previous commit — always create a new one
- Always ask the user before committing; never commit without explicit approval
- Commit messages must be meaningful, list every changed file with reasons, grouped logically

---

## File Organisation

- Never delete files — archive to `archive/` subdirectories in project root (create `archive/` if needed; add to `.gitignore` if working in a repo)
- Write reports to the `docs/` directory (default) or Obsidian (if asked) → use `obsidian-inbox/` directory
