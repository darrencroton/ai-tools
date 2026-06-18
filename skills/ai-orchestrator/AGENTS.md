# AI Orchestrator Repo Guide

## Purpose

This repo defines the `ai-orchestrator` skill. It teaches an AI coding agent how to delegate work to external AI CLIs while keeping one orchestrator responsible for planning, monitoring, verification, and final synthesis.

## File Roles

- `README.md`: human-facing overview and short maintenance notes
- `SKILL.md`: source of truth for generic orchestration workflow, role selection, monitoring cadence, and helper usage
- `references/templates.md`: prompt shapes only
- `references/claude.md`, `references/codex.md`, `references/copilot.md`: model-specific CLI references; keep the same structure across senior-worker model files and only change the model-specific details
- `scripts/worker_jobs.py`: tracked worker launcher plus `status`, `activity`, `cancel`, and `extract`
- `ai-reminder`: separate tmux/session reminder helper for long-running Claude/Codex sessions

Do not mix these purposes. Keep model-specific CLI flags and monitoring details out of `SKILL.md`. Keep prompt-shape guidance out of the model reference files.

## Working Rules

- Use `scripts/worker_jobs.py` for worker launches and artifact tracking
- Use `worker_jobs.py activity` as the health check, `cancel` to stop workers cleanly, and `extract` to read the clean final answer
- Session-backed tools must be monitored indirectly from lightweight signals; do not require the orchestrator to read full session logs to decide whether a worker is healthy
- Worker labels use `<nn>-<tool>-<subtask-slug>[-rN]`
- If an edit prompt follows a planning prompt, carry exact targets into the edit prompt: `path:line`, function names, and snippets where useful
- Keep worker outputs compact and scanner-friendly with `SECTION:` and `RESULT:` conventions from `references/templates.md`

## When Changing Model Support

- Update the model table in `SKILL.md`
- Add, remove, or revise `references/<model>.md`
- Update `scripts/worker_jobs.py` if the model needs custom session matching, health checks, or extraction fallback
- Update `README.md` and this file if structure or maintenance expectations changed
- Update `references/templates.md` only if prompt shape or output contract changes

## Verification

- Run `python3 -m py_compile scripts/worker_jobs.py` after helper changes
- Replay a relevant artifact or run a small smoke test when changing `activity`, `cancel`, or `extract`
- There are no formal automated tests in this repo
