# GitHub Copilot CLI Reference

## Roles It Can Fill

- **Orchestrator**: No
- **Senior worker**: No
- **Junior worker**: Yes

## Best Used For

- Surgical single-file edits
- Approved git/GitHub operations
- Low-stakes web research
- Low-stakes codebase mapping and non-critical summarising

## Avoid Using It For

- Multi-file refactors
- Correctness-critical judgement
- Owning complex plans

## Config Discovery

Read `~/.copilot/config.json` for the user's model (`model` key if present), but do not rely on Copilot's implicit default staying stable across CLI versions or sessions.
Inspect the current model list from the CLI:

```bash
COLUMNS=300 copilot --help | sed -n '/--model <model>/,/--mouse/p'
```

For captured worker runs, prefer the latest available `claude-sonnet-X`. If no Sonnet model is available, prefer the latest plain `gpt-X`. Set `--model` explicitly rather than relying on the default.

## Core Commands

Launch all Copilot worker runs via [../scripts/worker_jobs.py](../scripts/worker_jobs.py). The commands below are the worker command payloads to pass after `worker_jobs.py start --label <label> --`. Prefer `--silent` for captured non-interactive runs.

```bash
# Non-interactive execution worker command
copilot --model <model> -p "PROMPT" --allow-all-tools --autopilot --silent --add-dir <dir>

# Low-stakes web research worker command
copilot --model <model> -p "PROMPT" --allow-all-tools --allow-all-urls --autopilot --silent --add-dir <dir>

# GitHub operations worker command (with MCP tools)
copilot --model <model> -p "PROMPT" --allow-all-tools --add-github-mcp-toolset all --autopilot --silent --add-dir <dir>

# Resume most recent session
copilot --continue --allow-all-tools
```

## Helper Use

Use [../scripts/worker_jobs.py](../scripts/worker_jobs.py) for per-run directories, status tracking, and extraction. Let it own stdout/stderr capture. Worker labels must use `<nn>-<tool>-<subtask-slug>[-rN]`, for example `02-copilot-map-config`.

Check health with:

```bash
python3 <skill-dir>/scripts/worker_jobs.py activity --run-dir "$run_dir" --label <label>
```

For Copilot, `activity` reports recent helper-managed file activity. If `healthy=yes`, keep waiting on cadence. Use `cancel` to stop a worker cleanly:

```bash
python3 <skill-dir>/scripts/worker_jobs.py cancel --run-dir "$run_dir" --label <label>
```

Use `worker_jobs.py extract` when you want the final answer or section filtering. Use `worker_jobs.py extract --json` when you need the extracted text plus its source artifact.

## Notes

- Keep Copilot in junior-worker scope only. If the task expands beyond that scope, stop and reassign it.
- For junior-worker tasks, wait for the role-appropriate window, then run `worker_jobs.py activity`. A recent `last_activity_at` or `healthy=yes` means keep waiting.
- Model choice materially affects captured-output reliability. In this environment, the latest available `claude-sonnet-X` followed strict section contracts more reliably than the tested GPT alternatives.
- `--silent` suppresses CLI wrapper noise, not model-authored preambles or progress chatter.
- For captured runs, prefer a lean `RETURN:` block over a separate `OUTPUT CONTRACT` preamble. Require the first literal `SECTION:` line on line 1, forbid text outside the requested sections, and use `- none` for empty sections.
- If extraction is still empty or malformed after completion, inspect the matching stderr file, retry once with a tighter `RETURN:` block if appropriate, then fall back.
- While workers run, keep the orchestrator on orchestration work only; do not duplicate the delegated investigation locally.

## Key Flags

| Flag | Notes |
|---|---|
| `-p / --prompt` | Non-interactive prompt string |
| `--model` | Any valid model string |
| `--allow-all-tools` | All tools without confirmation; required for non-interactive |
| `--allow-all-urls` | Allow access to all URLs without confirmation |
| `--autopilot` | Enables continuation without user interaction |
| `--silent` | Output only the agent response; prefer for captured non-interactive runs |
| `--allow-tool` / `--deny-tool` | Scoped tool permissions e.g. `shell(git:*)` |
| `--add-dir` | Additional directory to permit access to |
| `--add-github-mcp-toolset` | `all` for full GitHub API; or specific toolset name |
| `--continue` | Resume most recent session |
| `--resume` | Resume by session ID or picker |

## Permission Guidance

- **Surgical edits**: `--allow-all-tools --autopilot`; use `--add-dir` to scope file access
- **Low-stakes web research**: `--allow-all-tools --allow-all-urls --autopilot`
- **Low-stakes codebase mapping / summarising**: `--allow-all-tools --autopilot`
- **GitHub operations**: `--allow-all-tools --add-github-mcp-toolset all --autopilot`
- **State-changing git/GitHub work**: only after explicit user approval
- **Locked-down**: `--allow-tool` + `--deny-tool` for precise control

## Resume

```bash
copilot --continue --allow-all-tools
```
Offer that exact command if continuation is useful.
