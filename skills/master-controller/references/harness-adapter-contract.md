# Harness Adapter Contract

MC core must not hardcode one AI harness. Each adapter describes how to start, observe, supervise, and stop a tmux-backed orchestrator session in a target repo.

## Implemented Adapter Responsibilities

The current `TmuxHarnessAdapter` provides these concrete methods:

- `name`: stable harness identifier such as `codex`.
- `preflight`: command or function that checks local availability without starting a run.
- `build_shell_command`: returns the shell command used inside tmux, including MC environment variables for the run state, plan path, slice id, and slice artifact directory.
- `start`: starts a fresh tmux session in the target repo with the built shell command.
- `send_prompt`: injects the rendered orchestrator prompt into the tmux session.
- `send_literal`: send short model-supervised operational text literally to the current tmux session and submit it without shell evaluation.
- `capture`: writes transcript or pane output to the slice artifact directory.
- `detect_activity`: reports whether the session is still active or idle as `{"running": bool, "active": bool, "capture": string}`.
- `detect_hard_prompt`: reports whether the visible pane appears to contain a trust, approval, credential, permission, or external-side-effect prompt that must block unattended send, wait, pause, retry, or resume actions.
- `request_stop`: asks the harness to stop gracefully.
- `force_stop`: terminates the tmux session after a terminal stop/finalize decision or failed graceful stop.
- `session_exists` and `sessions_with_prefix`: support liveness checks and stale-session cleanup.

Structured result detection is owned by MC's runner and command layer by checking `orchestrator-result.json` in the current slice artifact directory, not by a separate adapter method.

## Primitive-Level Responsibilities

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
- The harness receives fixed MC environment variables for the slice: `MC_SLICE_ARTIFACT_DIR`, `MC_RUN_JSON_PATH`, `MC_PLAN_PATH`, `MC_SLICE_ID`, `MC_RESULT_SCHEMA_PATH`, `MC_WORKER_JOBS_PATH`, `MC_WORKER_ARTIFACT_ROOT`, `AI_ORCHESTRATOR_ARTIFACT_ROOT`, `MC_SLICE_TMP_DIR`, `TMPDIR`, and `MC_TOOL_HOME_ROOT`. Tool home redirects are worker-only: `COPILOT_HOME` / `CODEX_HOME` are set only when that tool is a required worker and not the orchestrator harness itself, so an orchestrator always keeps its real config and session state.
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

Profile composition also owns supported orchestrator model and effort overrides. For example, a Claude run that requests a specific model must be composed by MC as `claude --permission-mode auto --model <model> --session-id <generated-id>` so model selection does not bypass transcript capture or other profile-managed launch requirements. A Codex run that requests model and effort is composed from the same profile table as `-m <model>` plus `-c model_reasoning_effort="<effort>"`.

Worker model and effort selection is a prompt-level contract, not a second worker-launch subsystem inside MC. MC renders per-tool worker command guidance from the same profile table, then requires the orchestrator to preserve worker evidence. This keeps `ai-orchestrator` as the single worker-launch abstraction while MC verifies artifacts and stops if the configured model or effort is unsupported.

Copilot and OpenCode are mechanically validated MC orchestrator harnesses: both accept the same tmux paste-buffer-plus-double-Enter prompt injection as Codex/Claude, both were directly observed reaching a stable ready state before first send (Copilot via a stable-pane heuristic like Claude's; OpenCode via a literal `"Ask anything..."` composer placeholder), and Copilot's directory-trust dialog text was confirmed to match an existing generic `TRUST_PROMPT_MARKERS` entry verbatim. Their `HARNESS_PROFILES` entries list `roles: ["orchestrator", "senior-worker", "junior-worker"]` on that basis.

This is a mechanical-readiness fact, not a suitability judgment. Whether a given harness should actually be used as orchestrator, senior worker, or junior worker for a specific task is a per-run decision for the operator or the orchestrating model, made from the functional role definitions in `ai-orchestrator`'s `SKILL.md` plus the configured model's demonstrated capability — not a restriction this contract or `HARNESS_PROFILES["roles"]` should hardcode. A `roles` entry means "MC can safely automate this harness unattended in this role class"; it does not mean "this harness is a good choice" for every task in that class.

Two residual coverage gaps apply to Copilot and OpenCode alike, in addition to whatever gaps already existed for Codex/Claude: (1) only the directory-trust prompt was directly triggered and confirmed for each; other hard-stop prompt classes (credential, permission-denial, external side effect) rely on the same generic keyword markers used for every harness, untested against these two CLIs' actual prompt wording. (2) Extended real-world runs (long multi-step slices, concurrent workers sharing a tool's local state/session store) have not been exercised — validation here covered readiness detection and one short end-to-end slice per harness.

## Failure Semantics

MC should record stable failure reasons in run state and artifacts instead of exposing opaque process errors when possible. Some reasons come from adapter operations, while others come from MC primitives that compose adapter evidence:

- `missing-harness`
- `missing-tmux`
- `start-failed`
- `prompt-injection-failed`
- `hard-stop-prompt`
- `pause-budget-exhausted`
- `capture-failed`
- `result-missing`
- `stop-failed`
- `timeout`

`timeout` is terminal for deterministic batch execution when the result file never appears before the configured deadline. In model-supervised operation, a bounded `wait` timeout is an observation result, not an acceptance or failure verdict by itself; the MC model must then choose a safe next primitive such as another wait, `pause-until`, `send`, `finalize-slice`, or `stop-with-evidence`.

MC records terminal failures in run state and stops rather than retrying indefinitely. Model-supervised waits and pauses are not acceptance states; they preserve evidence and return control for a later observe, send, finalize, or stop decision. Acceptance still requires `orchestrator-result.json` plus deterministic validation, authorization, drift audit, code review, commit, and clean-worktree gates.
