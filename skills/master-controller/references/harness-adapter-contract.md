# Harness Adapter Contract

MC core must not hardcode one AI harness. Each adapter describes how to start, supervise, and stop a tmux-backed orchestrator session in a target repo.

## Adapter Responsibilities

An adapter provides:

- `name`: stable harness identifier such as `codex`.
- `preflight`: command or function that checks local availability without starting a run.
- `build_start_command`: returns the shell command used inside tmux.
- `send_prompt`: injects the rendered orchestrator prompt into the tmux session.
- `capture`: writes transcript or pane output to the slice artifact directory.
- `detect_activity`: reports whether the session is still active or idle.
- `detect_completion`: checks for explicit completion markers or structured result creation.
- `request_stop`: asks the harness to stop gracefully.
- `force_stop`: terminates the tmux session after timeout or failed graceful stop.

## Required Artifacts

For each slice, the adapter must allow MC to capture:

- `prompt.md`
- `transcript.txt` or `pane-capture.txt`
- `git-status-before.txt`
- `git-status-after.txt`
- `git-diff.patch`
- `orchestrator-result.json`

## Tmux Requirements

- Every slice starts in a fresh tmux session.
- Session names must include the run id and slice id.
- The working directory must be the target repo/worktree.
- MC must be able to capture pane output before and after stop.
- MC must close the session after completion or timeout.

## Failure Semantics

Adapters return structured failure reasons instead of raising opaque process errors when possible:

- `missing-harness`
- `missing-tmux`
- `start-failed`
- `prompt-injection-failed`
- `timeout`
- `capture-failed`
- `result-missing`
- `stop-failed`

MC records the failure in run state and stops rather than retrying indefinitely.
