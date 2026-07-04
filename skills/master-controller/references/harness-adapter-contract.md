# Harness Adapter Contract

MC core must not hardcode one AI harness. Each adapter describes how to start, supervise, and stop a tmux-backed orchestrator session in a target repo.

## Adapter Responsibilities

An adapter provides:

- `name`: stable harness identifier such as `codex`.
- `preflight`: command or function that checks local availability without starting a run.
- `build_start_command`: returns the shell command used inside tmux, including MC environment variables for the run state, plan path, slice id, and slice artifact directory.
- `send_prompt`: injects the rendered orchestrator prompt into the tmux session.
- `capture`: writes transcript or pane output to the slice artifact directory.
- `detect_activity`: reports whether the session is still active or idle as `{"running": bool, "active": bool, "capture": string}`.
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
- The harness receives fixed MC environment variables for the slice: `MC_SLICE_ARTIFACT_DIR`, `MC_RUN_JSON_PATH`, `MC_PLAN_PATH`, `MC_SLICE_ID`, `MC_RESULT_SCHEMA_PATH`, `MC_WORKER_JOBS_PATH`, `MC_WORKER_ARTIFACT_ROOT`, `AI_ORCHESTRATOR_ARTIFACT_ROOT`, `MC_SLICE_TMP_DIR`, `TMPDIR`, `MC_TOOL_HOME_ROOT`, and `COPILOT_HOME`.
- MC records activity checks as JSON lines with `checked_at`, `running`, and `active` fields.
- MC must preserve live pane output while polling and must also attempt a final capture before and after stop.
- MC must close the session after completion or timeout.

## Harness Profiles

MC keeps one capability profile per tool, not one profile per role combination. The launch command is composed from:

- the selected orchestrator harness, for example `codex` or `claude`;
- runtime requirements, such as worker tools being used;
- run policy, such as `commit_required=true`.

This keeps tool-specific instructions together while avoiding many partially tested combinations. For example, the Codex profile adds sandbox network access only when worker tools are requested, and adds scoped git-directory access only when commits are required.

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
