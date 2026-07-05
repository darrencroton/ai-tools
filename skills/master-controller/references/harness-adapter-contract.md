# Harness Adapter Contract

MC core must not hardcode one AI harness. Each adapter describes how to start, observe, supervise, and stop a tmux-backed orchestrator session in a target repo.

## Adapter Responsibilities

An adapter provides:

- `name`: stable harness identifier such as `codex`.
- `preflight`: command or function that checks local availability without starting a run.
- `build_start_command`: returns the shell command used inside tmux, including MC environment variables for the run state, plan path, slice id, and slice artifact directory.
- `send_prompt`: injects the rendered orchestrator prompt into the tmux session.
- `send_literal`: send short model-supervised operational text literally to the current tmux session and submit it without shell evaluation.
- `capture`: writes transcript or pane output to the slice artifact directory.
- `detect_activity`: reports whether the session is still active or idle as `{"running": bool, "active": bool, "capture": string}`.
- `detect_completion`: checks for explicit completion markers or structured result creation.
- `detect_hard_prompt`: reports whether the visible pane appears to contain a trust, approval, credential, permission, or external-side-effect prompt that must block unattended send, wait, pause, retry, or resume actions.
- `request_stop`: asks the harness to stop gracefully.
- `force_stop`: terminates the tmux session after timeout or failed graceful stop.

Model-supervised MC commands may compose these adapter responsibilities into primitives:

- `observe`: capture compact pane/transcript/process/result/git evidence without finalizing gates.
- `send`: send a literal continuation or operational instruction only to the current slice's recorded session, refusing hard prompts.
- `wait`: observe for a bounded duration, appending JSONL observation events, returning early on result, process exit, hard-stop prompt, or max wait.
- `pause-until`: persist pause state, observe until an absolute timestamp plus buffer, and refuse hard-stop conditions.
- `start-slice`: launch a slice and return control after recording `current_slice.before_head`.
- `finalize-slice`: capture final evidence and run deterministic gates using persisted `before_head`.
- `stop-with-evidence`: preserve pane/transcript/git evidence and record a structured stop reason without accepting the slice.

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
- Deterministic batch execution must close the session after completion or terminal timeout.
- Model-supervised execution may keep a live session open through a classified pause or bounded wait. It must close or reap the session only after evidence is captured and the MC model or deterministic gate has chosen stop/finalize/restart.

## Observation And Send Safety

Adapters must support evidence capture without changing harness state. Observation should be compact enough for repeated MC model review and must preserve full pane or transcript artifacts on disk.

Literal sends must:

- target only the current slice's recorded tmux session
- send text literally, not through shell evaluation
- reuse the harness prompt-submission discipline, including settle and robust submit behavior for TUIs that need more than a single Enter
- refuse when a trust, approval, credential, permission, or external-side-effect prompt is visible
- record the sent text, timestamp, reason, and evidence pointer as an operational event

## Harness Profiles

MC keeps one capability profile per tool, not one profile per role combination. The launch command is composed from:

- the selected orchestrator harness, for example `codex` or `claude`;
- runtime requirements, such as worker tools being used;
- run policy, such as `commit_required=true`.

This keeps tool-specific instructions together while avoiding many partially tested combinations. For example, the Codex profile adds sandbox network access only when worker tools are requested, and adds scoped git-directory access only when commits are required.

Profile composition also owns supported model overrides. For example, a Claude run that requests a specific model must be composed by MC as `claude --permission-mode auto --model <model> --session-id <generated-id>` so model selection does not bypass transcript capture or other profile-managed launch requirements.

## Failure Semantics

Adapters return structured failure reasons instead of raising opaque process errors when possible:

- `missing-harness`
- `missing-tmux`
- `start-failed`
- `prompt-injection-failed`
- `timeout`
- `hard-stop-prompt`
- `pause-budget-exhausted`
- `capture-failed`
- `result-missing`
- `stop-failed`

MC records terminal failures in run state and stops rather than retrying indefinitely. Model-supervised waits and pauses are not acceptance states; they preserve evidence and return control for a later observe, send, finalize, or stop decision. Acceptance still requires `orchestrator-result.json` plus deterministic validation, authorization, drift audit, code review, commit, and clean-worktree gates.
