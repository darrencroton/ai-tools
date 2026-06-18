# Claude Code Reference

## Roles It Can Fill

- **Orchestrator**: Yes
- **Senior worker**: Yes
- **Junior worker**: Not preferred

## Best Used For

- Complex edits and refactors
- Long-running coding tasks
- Plan review and deep debugging

## Avoid Using It For

- Low-value tactical chores when a junior worker is available
- Launching Claude Code workers from inside a Claude Code orchestrator session

## Config Discovery

Read `~/.claude/settings.json` for relevant user defaults if present. If no model is configured there, omit `--model` and let Claude Code use its default. Never hardcode model names.

## Core Commands

Launch all Claude worker runs via [../scripts/worker_jobs.py](../scripts/worker_jobs.py). The commands below are the worker command payloads to pass after `worker_jobs.py start --label <label> --`.

```bash
# Edit task worker command
claude -p "PROMPT" --permission-mode acceptEdits --output-format text --add-dir <dir>

# Read-only review / plan review worker command
claude -p "PROMPT" --permission-mode plan --output-format text --add-dir <dir>

# Resume most recent session in the current directory
claude --continue
```

## Helper Use

Use [../scripts/worker_jobs.py](../scripts/worker_jobs.py) for per-run directories, status tracking, and extraction. Let it own stdout/stderr capture and omit extra shell redirections from the worker command. Worker labels must use `<nn>-<tool>-<subtask-slug>[-rN]`, for example `01-claude-review-plan`.

Check health with:

```bash
python3 <skill-dir>/scripts/worker_jobs.py activity --run-dir "$run_dir" --label <label>
```

If `healthy=yes`, keep waiting on cadence. Use `cancel` to stop a worker cleanly:

```bash
python3 <skill-dir>/scripts/worker_jobs.py cancel --run-dir "$run_dir" --label <label>
```

Use `worker_jobs.py extract` when you want the clean final answer. Use `worker_jobs.py extract --json` when you need the extracted text plus its source artifact. If Claude exits `0` with empty stdout, extraction falls back to the matched Claude session automatically.

## Notes

- Do not launch Claude Code as a worker from inside a Claude Code orchestrator session; nested Claude sessions are blocked.
- For senior multi-file edit or review tasks, wait for the role-appropriate window, then run `worker_jobs.py activity`. An advancing session timestamp, recent assistant activity, or `healthy=yes` means keep waiting.
- If extraction is still empty or malformed after completion, inspect the matching stderr file, retry once with a tighter prompt if appropriate, then fall back.
- While workers run, keep the orchestrator on orchestration work only; do not duplicate the delegated investigation locally.

## Key Flags

| Flag | Notes |
|---|---|
| `-p / --print` | Non-interactive prompt string |
| `--model` | Any valid model string; omit to use the CLI default |
| `--permission-mode` | Use `acceptEdits` for edit tasks, `plan` for read-only review |
| `--output-format` | `text`, `json`, `stream-json` |
| `--add-dir` | Additional directory to permit tool access to |
| `--continue` | Resume the most recent session in the current directory |
| `--resume` | Resume by session ID or picker |

## Permission Guidance

- **Edit tasks**: `--permission-mode acceptEdits`
- **Read-only review**: `--permission-mode plan`
- **Unrestricted execution**: only if the user explicitly requests it

## Resume

```bash
claude --continue
```

Offer that exact command if continuation is useful.
