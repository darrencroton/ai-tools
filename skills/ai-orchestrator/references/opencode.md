# OpenCode CLI Reference

## Roles It Can Fill

- **Orchestrator**: Yes
- **Senior worker**: Yes
- **Junior worker**: Yes

Role fit is entirely a function of the configured model, not a fixed property of this CLI. OpenCode is typically configured against local/self-hosted models (see Config Discovery below) which vary widely in capability — a strong local model can carry senior-worker or orchestrator work; a small quantized model should stay in junior-worker or narrowly-scoped work. If a subscription/hosted model is configured instead, evaluate it the same way you would Claude Code or Codex CLI.

## Best Used For

- Local/self-hosted iteration at zero marginal API cost
- Long-running or exploratory tasks where a capable local model is configured
- Offline or low-connectivity work

## Avoid Using It For

- Correctness-critical judgement calls when only a small/weak local model is configured
- Tasks that clearly need frontier-model reasoning, unless a comparably strong model is selected for this run

## Config Discovery

Read `~/.config/opencode/opencode.json` for configured providers and models. Model strings use the form `provider/model`, e.g. `macstudio/qwen/qwen3.6-27b-q8` (local models here are served via `~/.llm/llama-server/` and reached over Tailscale). List what's actually available with:

```bash
opencode models
```

Omit `-m` to use the configured default (`model` key in `opencode.json`). Never hardcode model names — read them from config or ask the user. Use `--variant <level>` for effort/reasoning-level when the user or supervising workflow specifies one; it only has an effect on models that support provider-specific reasoning-effort control (check `"reasoning": true` in the model's config entry as a starting signal, but treat unsupported variants as a no-op rather than an error).

## Core Commands

Launch all OpenCode worker runs via [../scripts/worker_jobs.py](../scripts/worker_jobs.py). The commands below are the worker command payloads to pass after `worker_jobs.py start --label <label> --`.

```bash
# Edit task worker command
opencode run "PROMPT" [-m <provider/model>] [--variant <effort>] --agent build --auto --dir <dir>

# Read-only review / plan review worker command
opencode run "PROMPT" [-m <provider/model>] [--variant <effort>] --agent plan --auto --dir <dir>

# Resume most recent session
opencode run "PROMPT" --continue --dir <dir>
```

Confirmed by direct testing: `opencode run` prompt text is a positional argument (not a flag), and its default text output puts only the model's final answer on stdout — tool-call and status chatter goes to stderr. This matches Claude/Copilot's plain output shape, so no `--format json` or session-log fallback is needed for extraction.

## Helper Use

Use [../scripts/worker_jobs.py](../scripts/worker_jobs.py) for per-run directories, status tracking, and extraction. Let it own stdout/stderr capture and omit extra shell redirections from the worker command. Worker labels must use `<nn>-<tool>-<subtask-slug>[-rN]`, for example `01-opencode-summarize-config`.

Check health with:

```bash
python3 <skill-dir>/scripts/worker_jobs.py activity --run-dir "$run_dir" --label <label>
```

OpenCode has no dedicated session-log integration in the helper (it is not `claude` or `codex`), so `activity` reports recent helper-managed file activity, the same fallback Copilot uses. If `healthy=yes`, keep waiting on cadence. Use `cancel` to stop a worker cleanly:

```bash
python3 <skill-dir>/scripts/worker_jobs.py cancel --run-dir "$run_dir" --label <label>
```

Use `worker_jobs.py extract` when you want the clean final answer — it reads stdout directly for OpenCode.

## Notes

- Local models can be slow on first response (cold context/model load) and their latency varies a lot by machine and model size; do not treat a quiet 30–60s window alone as evidence of a hang.
- Quality and tool-use reliability vary by configured model; note in your synthesis which model actually did the work.
- `opencode models` output must match what you pass to `-m`; do not guess a model string.
- While workers run, keep the orchestrator on orchestration work only; do not duplicate the delegated investigation locally.

## Key Flags

| Flag | Notes |
|---|---|
| (positional) | Prompt text passed directly as an argument to `opencode run`, not via a flag |
| `-m / --model` | `provider/model` string from `opencode.json` / `opencode models`; omit to use the configured default |
| `--variant` | Reasoning-effort level when explicitly requested and supported by the underlying model |
| `--agent` | `build` (default, edit-capable) or `plan` (read-only) |
| `--auto` | Auto-approve permissions not explicitly denied; required for non-interactive/unattended runs |
| `--dir` | Working directory for the run |
| `-c / --continue` | Continue the most recent session |
| `-s / --session` | Resume a specific session id |
| `--format` | `default` (plain text, used here) or `json` (JSONL event stream) |

## Permission Guidance

- **Edit tasks**: `--agent build --auto`
- **Read-only review**: `--agent plan --auto`. Confirmed by direct testing: without `--auto`, a headless `opencode run --agent plan` call hangs indefinitely waiting for a tool-execution approval that no one is present to give — there is no TTY to approve it. `--agent plan` already keeps the agent read-only regardless of `--auto`; `--auto` only bypasses the approval prompt, it does not grant write access. Do not skip launching a worker, or substitute an orchestrator's own direct checks for a required worker run, based on an assumption that `--auto` is unsafe or unsupported for read-only tasks — test the documented command before concluding it can't be used.
- **Unrestricted execution**: only if the user explicitly requests it

## Resume

```bash
opencode run "PROMPT" --continue --dir <dir>
```

Offer that exact command if continuation is useful.
