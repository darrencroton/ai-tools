# Master Controller Model-Supervised Transition Plan

## Purpose

Transition Master Controller (MC) from a mostly deterministic `mc.py run --scope remaining` supervisor into a model-supervised controller backed by deterministic tools. The end state should let a frontier MC model read the plan, validate setup, start each slice in tmux, observe live harness state, make bounded operational judgments from pane/log/json evidence, recover obvious transient interruptions, stop on ambiguous or risky conditions, and still rely on deterministic gates for authorization, validation, review, and commit acceptance.

This plan is for a fresh implementation team. It captures current behaviour, desired behaviour, architectural boundaries, implementation slices, validation expectations, and the next-chat launcher.

## Current State

Mode C in the root `README.md` currently tells the user-facing model to use `master-controller`, initialize or reuse an MC run, run preflight, dry-run the next slice, then run the requested scope through `mc.py`. In practice, the important command is usually `python3 skills/master-controller/scripts/mc.py run --repo <path> --scope remaining --allow-profile-command`.

Current MC has strong deterministic mechanics:

- It parses implementation-plan markdown and fails closed on incomplete slice contracts.
- It creates durable state under `.ai-mc/runs/<run-id>/run.json`.
- It launches one eligible slice at a time in a fresh tmux-backed harness session.
- It writes per-slice artifacts including prompt, pane captures, activity logs, git status, diff, validation summary, drift audit, code review, worker evidence, transcript artifacts, and `orchestrator-result.json`.
- It verifies changed files against the authorized surface itself.
- It verifies commit ancestry, HEAD advancement, and a clean post-commit worktree itself.
- It treats drift-audit and code-review verdicts as gate evidence, requiring non-empty artifact files.
- It can reconcile a narrow commit-hash evidence defect when local git evidence proves the slice passed.

Current MC is not yet an intelligent operational supervisor during a running slice:

- The user-facing MC model blocks while `mc.py run` runs.
- The Python loop polls tmux for result/exited/timeout but does not ask the model to reason over live screen state.
- Pane text is captured and stored, but operational semantics are not interpreted.
- A subscription limit message, service outage message, or retry-later message is treated like ordinary idle or failure evidence until timeout or harness exit.
- Timeout currently causes stop/kill and a blocked or failed slice, even when the pane text explains a benign recoverable wait.

## Desired End State

The end state is a hybrid controller:

- The MC model is the supervisor and owns operational judgment.
- `mc.py` and support modules provide deterministic tools for exact state transitions, tmux control, artifact capture, and gate verification.
- Acceptance gates remain deterministic and evidence-based; screen text must never be enough to accept code, commits, drift, review, validation, or changed-file authorization.
- Operational triage may use pane/log/transcript text, process state, current time, elapsed time, result-file presence, and git state as evidence.
- Known transient operational interruptions are recovered automatically when recovery is low risk and bounded.
- Unknown, ambiguous, policy-sensitive, or potentially destructive situations stop with evidence for the user.

Examples of expected operational behaviour:

- If Codex or Claude says a 5-hour usage/session limit was reached and resets at a specific time or after a specific duration, MC should compute the wait until reset, add a small buffer, preserve state, wait patiently, then send a continuation prompt such as `You were interrupted. Review what you were doing then continue.`
- If the pane says a weekly or monthly usage limit was reached, MC should stop, report the evidence and reset information if available, and wait for the user.
- If a service is temporarily unavailable and says to try again later, MC may wait and retry within a bounded policy.
- If a credential prompt, trust prompt, destructive-action request, dependency/license change, remote push, release/deploy request, or unclear failure appears, MC should stop and report.

## Architectural Principle

Do not hard-code the MC's full judgment into a large `classify_operational_event` function. That would recreate the current problem in a different form: the deterministic script would become the de facto controller.

Instead, implement a small operational-observation layer that gives the MC model structured evidence and safe actions.

Deterministic code should own:

- Reading and writing `run.json`.
- Starting and stopping tmux sessions.
- Capturing pane text and transcript tails.
- Sending text into tmux.
- Waiting until a bounded relative or absolute time.
- Preserving artifacts while waiting.
- Detecting obvious common signals and extracting useful fields, when possible.
- Enforcing hard deterministic gates after the orchestrator claims completion.

The MC model should own:

- Deciding whether a live situation is recoverable, unclear, or user-blocking.
- Deciding whether to wait, retry, continue, stop, or summarize.
- Applying policy guidance to novel operational messages.
- Choosing a conservative stop when the evidence is insufficient.

## Operational Policy

### Recoverable Without User Approval

MC may recover when all of these are true:

- The issue is operational rather than implementation drift or quality failure.
- The pane/log evidence clearly indicates a transient condition.
- The recovery action is bounded and does not expand the slice contract.
- The action does not require secrets, account changes, trust approval, dependency/license changes, remote side effects, destructive filesystem actions, or human approval.
- MC can preserve artifacts and resume or continue the same slice without accepting incomplete work.

Examples:

- Rolling 5-hour usage limit with a reset time or duration.
- Temporary service unavailable message with a retry-later instruction.
- Network transient where retrying the same prompt/session is safe and bounded.
- Harness idle while the transcript and pane show recent progress.

### Stop For User

MC must stop when any of these are true:

- Weekly, monthly, account, billing, or subscription cap reached.
- Credential, login, MFA, trust, secret, or permission prompt appears.
- The orchestrator asks to perform an unapproved external side effect.
- The worktree becomes dirty outside permitted MC/audit surfaces before the slice has a valid passing result.
- The plan changed after init.
- The branch changed after init.
- The recovery action would require expanding the authorized surface.
- The situation is ambiguous after reasonable observation.
- A deterministic acceptance gate fails and cannot be reconciled by an explicitly allowed MC recovery.

## Proposed Tooling End State

The CLI should support model-supervised Mode C with primitive commands. Names below are proposed; implementation may choose equivalent names if docs and tests are kept consistent.

### `observe`

Return a compact machine-readable snapshot for the active slice.

Expected data:

- run id, status, current slice id/title/attempt
- slice starting commit evidence, including `current_slice.before_head`
- tmux session name
- harness name and model if recorded
- current wall-clock time with timezone and UTC
- elapsed seconds since slice start
- process running/dead
- whether the screen currently appears to contain a trust, approval, credential, permission, or external-side-effect prompt
- pane text tail and full latest capture path
- transcript tail path and summary metadata when available
- `orchestrator-result.json` existence and parse status
- current git status summary
- latest operational event hints, if deterministic detectors found any

The output should be JSON by default, with an optional human format for debugging.

### `send`

Send literal text into the active tmux session and press Enter. This must be separate from full prompt injection so the MC model can send short operational instructions such as continuation prompts.

Safety requirements:

- Only send to the current slice's recorded tmux session.
- Refuse if there is no active current slice.
- Refuse if the run is not in a resumable/running/paused state.
- Refuse if `observe` detects a trust, approval, credential, permission, or external-side-effect prompt on screen.
- Send text literally, not through shell evaluation.
- Reuse the existing prompt-submission discipline from the tmux adapter: paste or send literal text, allow the TUI to settle, and submit robustly enough to avoid the known single-Enter race that leaves pasted text unsent.
- Record the sent text, timestamp, and reason in an append-only operational event artifact; mirror only compact current-state pointers in `run.json`.

### `wait`

Wait for a bounded duration while preserving periodic observations.

Expected behaviour:

- Continue capturing live pane text.
- Append observation records to an operational log.
- Return early if `orchestrator-result.json` appears, the process exits, a deterministic hard-stop signal appears, or a max wait expires.
- Do not finalize gates automatically.
- Use one writer at a time for `run.json`; high-frequency observations should go to append-only JSONL artifacts rather than repeatedly rewriting the main state file.

### `pause-until`

Wait until an absolute timestamp plus optional buffer while preserving observations.

Expected behaviour:

- Persist `paused_until`, `pause_reason`, and evidence excerpt in `run.json`.
- Periodically observe and capture state.
- Return for MC model judgment when the pause expires or an earlier hard-stop signal appears.
- Refuse to pause when the strongest available hint indicates a weekly, monthly, account, billing, credential, trust, approval, unknown-limit, or otherwise hard-stop condition.
- Handle timezone explicitly. Prefer relative durations such as "try again in 3 hours". Accept absolute local reset times only when they include a timezone or are unambiguously in the near future for the controller environment timezone; otherwise classify the time as ambiguous and stop for the user or ask for human judgment.
- Enforce pause budgets such as maximum single pause, consecutive pauses, and cumulative paused seconds per slice/run.

### `start-slice`

Start the next eligible slice but do not block until completion. This replaces the model-supervised use of `run-next` when live supervision is desired.

Expected behaviour:

- Perform the same eligibility, clean-worktree, plan digest, branch, and harness preflight checks as current `execute_slice`.
- Capture and persist the slice starting commit as `current_slice.before_head` before launching the harness. This value is required later by out-of-process finalization so changed-file verification does not have to guess `HEAD^`.
- Render prompt and create artifact directories.
- Launch tmux and send the slice prompt.
- Record `current_slice`.
- Return control to the MC model with session and artifact paths.

### `finalize-slice`

Run deterministic gate verification for a slice that appears complete.

Expected behaviour:

- Capture final pane/transcript/worker/git artifacts.
- Load and verify `orchestrator-result.json`.
- Apply existing gate logic using the persisted `current_slice.before_head`.
- Append the slice entry and update run status.
- Kill or close the tmux session only after artifacts are captured and the slice is finalized.
- If the orchestrator reports `repairable`, return a structured repair decision to the MC model and preserve enough state to restart within the configured repair-attempt cap.

### `stop-with-evidence`

Stop the current run or slice with a structured reason.

Expected behaviour:

- Capture pane, transcript, worker summary, git status, and diff.
- Optionally request graceful stop before force stop.
- Record reason, evidence excerpt, and suggested next action.
- Avoid losing the last live pane text.
- Reap stale or orphaned sessions for the current run when requested by the MC model, while preserving evidence before force stop.

### `run --scope remaining` Compatibility

Keep `run --scope remaining` as a deterministic convenience path for simple unattended runs. It should either:

- remain conservative and stop on unclassified operational interruptions, or
- internally use the same operational primitives with only deterministic recovery policies.

Do not make `run --scope remaining` the only documented Mode C path once model-supervised operation exists. The root `README.md` should distinguish:

- **Mode C1: Model-supervised MC** for intelligent operational handling.
- **Mode C2: Deterministic batch MC** for simple fail-closed runs where the model does not need live judgment.

### Reuse Existing Tooling Where It Fits

The new MC primitives should not reimplement solved mechanics without reason. Before writing new wait, activity, session, or injection logic, inspect and reuse or adapt:

- `skills/ai-orchestrator/scripts/worker_jobs.py` for wait-loop cadence, activity health summaries, session transcript lookup, and extraction patterns.
- `skills/ai-orchestrator/ai-reminder` for safe literal tmux text injection and transcript-activity monitoring patterns.
- Existing `TmuxHarnessAdapter` readiness, trust-prompt detection, capture, and submit-race handling.

Reuse does not mean coupling MC runtime state to ai-orchestrator run directories. It means borrowing tested patterns or factoring shared helpers when that keeps MC leaner and safer.

## Operational Event Hints

Implement lightweight hints, not a comprehensive hard-coded decision engine.

Suggested hint schema:

```json
{
  "kind": "usage_limit",
  "confidence": "high",
  "subtype": "rolling_window",
  "reset_at": "2026-07-05T18:30:00+10:00",
  "retry_after_seconds": null,
  "hard_stop": false,
  "evidence_excerpt": "session limit reached and will reset at 6:30pm",
  "source": "tmux-pane",
  "detected_at": "2026-07-05T15:20:00+10:00"
}
```

Recommended hint kinds:

- `usage_limit`
- `service_unavailable`
- `network_transient`
- `auth_required`
- `trust_prompt`
- `permission_prompt`
- `external_side_effect_request`
- `idle_no_progress`
- `process_exited_without_result`
- `result_ready`

Recommended usage-limit subtypes:

- `rolling_window`
- `weekly_window`
- `monthly_window`
- `account_or_billing`
- `unknown_limit`

The MC model should treat ordinary hints as evidence, not commands. For example, `usage_limit` with `subtype=rolling_window` and a parseable `reset_at` strongly supports `pause-until`, while `usage_limit` with `subtype=weekly_window` supports `stop-with-evidence`.

One exception is required: hard-stop hints are a deterministic floor, not merely advice. `pause-until`, `send` of a continuation prompt, and any automatic retry/resume command must refuse when the strongest available hint is `weekly_window`, `monthly_window`, `account_or_billing`, `unknown_limit`, `auth_required`, `trust_prompt`, `permission_prompt`, or `external_side_effect_request`. The MC model may always stop earlier, but it must not be able to convert those hard stops into an unattended wait.

Recovery policy must also branch on process state:

- If the harness process is alive at a rolling usage limit, MC may pause until reset plus buffer and then send the continuation prompt after re-observing the screen for hard-stop prompts.
- If the harness process has exited at a rolling usage limit before writing `orchestrator-result.json`, MC cannot send into the old session. It must either restart the slice from a clean, authorized state or stop for the user if partial edits, commits, or ambiguous state make restart unsafe.
- If the harness process exited after creating a valid result, MC should finalize the slice rather than resume.

## State Model Changes

Extend `run.json` in a backwards-compatible way.

Suggested top-level additions:

```json
{
  "supervision": {
    "mode": "model-supervised",
    "pause_policy": {
      "rolling_usage_limit": "wait-until-reset-plus-buffer",
      "weekly_usage_limit": "stop-for-user",
      "transient_service_unavailable": "bounded-retry",
      "unknown_operational_event": "stop-for-user"
    },
    "default_resume_prompt": "You were interrupted. Review what you were doing then continue.",
    "default_reset_buffer_seconds": 180,
    "max_single_pause_seconds": 21600,
    "max_consecutive_pauses_per_slice": 2,
    "max_cumulative_pause_seconds_per_run": 43200,
    "max_transient_retries_per_slice": 3
  },
  "operational_events_path": ".ai-mc/runs/<run-id>/operational-events.jsonl"
}
```

Keep high-frequency operational history in append-only JSONL files rather than repeatedly rewriting `run.json`. `run.json` should hold the current compact state and pointers to artifact/event files. Any command that writes `run.json` must use a single-writer strategy, such as an advisory lock around read-modify-write. This is required because model-supervised operation may involve separate `wait`, `observe`, `send`, `pause-until`, `finalize-slice`, and human `stop` invocations against the same run.

Suggested `current_slice` additions:

```json
{
  "current_slice": {
    "slice_id": "Slice 2",
    "title": "Example",
    "artifact_dir": ".ai-mc/runs/<run-id>/slices/slice-002",
    "tmux_session": "mc_<run-id>_slice-002_a1",
    "attempt": 1,
    "started_at": "2026-07-05T15:00:00+10:00",
    "before_head": "<commit HEAD immediately before this slice attempt started>",
    "pause": {
      "paused_until": "2026-07-05T18:33:00+10:00",
      "reason": "rolling usage limit reset",
      "evidence_event_id": "op-0001"
    }
  }
}
```

Persisting `before_head` in `current_slice` is mandatory for model-supervised finalization. In the current blocking runner this value lives in process memory; once `start-slice` and `finalize-slice` are separate commands, guessing it later risks missing unauthorized files from earlier commits in the same slice.

Suggested event fields:

```json
{
  "event_id": "op-0001",
  "slice_id": "Slice 2",
  "attempt": 1,
  "kind": "usage_limit",
  "subtype": "rolling_window",
  "status": "handled",
  "detected_at": "2026-07-05T15:20:00+10:00",
  "evidence_path": ".ai-mc/runs/<run-id>/slices/slice-002/pane-capture-live-latest.txt",
  "evidence_excerpt": "session limit reached and will reset at 6:30pm",
  "decision": "pause-until",
  "decided_by": "mc-model",
  "resume_at": "2026-07-05T18:33:00+10:00",
  "action_taken": "sent continuation prompt",
  "notes": ""
}
```

Allowed run statuses may need additions:

- `paused`
- `resuming`

Alternatively, keep `status=running` and encode pause state under `current_slice.pause`. Prefer explicit statuses because they make summaries clearer.

## Documentation Changes Needed

Update these files during implementation:

- `README.md`: revise Mode C to distinguish model-supervised MC from deterministic batch MC, explain operational recovery policy, and include a launcher prompt that keeps the MC model in the loop.
- `skills/master-controller/SKILL.md`: update workflow, safety rules, default operating path, and command list to describe observation/send/wait/finalize primitives.
- `skills/master-controller/README.md`: document the operational supervisor primitives, examples, state fields, and safe local trials.
- `skills/master-controller/references/harness-adapter-contract.md`: revise timeout/session-closure wording so classified pauses and model-supervised observation are first-class.
- `skills/master-controller/references/run-state-schema.md`: add supervision state and operational event schema.
- `skills/master-controller/references/orchestrator-prompt.md`: add instructions to the slice orchestrator for quota/interruption handling, including writing a blocked result only when it can still act, and otherwise relying on MC to resume.

## Implementation Profiles

- Recommended for frontier/senior implementer: run Batch A first, then Batch B, then Batch C only after manual review of the new supervision contract.
- Recommended for standard implementer: run slices individually.
- Recommended for weaker implementer: run only Slice 1 documentation alignment, then stop for review before runtime code.

## Slice Batches

- Batch A: Slices 1-2 — aligns public contract and state schema before code, so implementation has an agreed target.
- Batch B: Slices 3-4 — adds observe/send/wait primitives and model-supervised start/finalize path after the state/concurrency contract exists; these are coupled through run state and tmux control.
- Batch C: Slices 5-6 — adds operational hints/recovery tests and updates Mode C launcher once primitives are proven.

Do not batch Slice 7 with earlier slices; it is a final cleanup and compatibility review after the new flow works.

## Slice 1: Align MC Supervision Contract

### Intended Change
- Revise documentation so MC is defined as a model-supervised controller backed by deterministic tools.
- Separate deterministic acceptance gates from model-driven operational triage.
- Document rolling usage-limit recovery and weekly usage-limit stop policy.
- Introduce the two Mode C variants: model-supervised MC and deterministic batch MC.

### Acceptance Criteria
- Inputs:
  - Current root `README.md` Mode C text.
  - Current `skills/master-controller/SKILL.md`.
  - Current `skills/master-controller/README.md`.
- Outputs:
  - Updated docs clearly state that the MC model should remain in the loop for nuanced operational supervision.
  - Updated docs preserve deterministic gates for authorization, validation, review, and commit acceptance.
  - Updated docs explicitly say rolling 5-hour windows are recoverable pauses and weekly/monthly/account limits are user stops.
- User-visible behaviour:
  - A user choosing Mode C can tell whether they are asking for model-supervised supervision or deterministic batch execution.
- Behaviour that must not change:
  - MC remains non-planning; implementation-plan still owns plan creation or material plan repair.
  - Approval-gated slices still stop for the user.
  - Gate acceptance still requires local evidence, not natural-language screen text alone.

### Authorized Surface
- Files allowed to change:
  - README.md
  - skills/master-controller/SKILL.md
  - skills/master-controller/README.md
- Functions/classes/components allowed to change:
  - Documentation only.
- Tests allowed or expected to change:
  - None.

### Explicit Non-Goals
- Do not implement new CLI commands in this slice.
- Do not change MC runtime behaviour.
- Do not alter implementation-plan, drift-audit, code-review, or commit skills.

### Risk Flags
- Risky surfaces touched:
  - Public workflow documentation.
  - Human/operator expectations for unattended execution.
- Approval needed before implementation:
  - yes - changes the documented operating model for Mode C.

### Validation Plan
- Tests to add/update:
  - None.
- Commands to run:
  - `git diff --check`
- Manual checks:
  - Read Mode C launcher text and confirm it no longer implies the model should blindly delegate all remaining supervision to `mc.py run --scope remaining`.
  - Confirm the docs distinguish operational recovery from acceptance gates.

### Rollback Path
- Revert the documentation edits in the three authorized files.

## Slice 2: Extend Run State And Adapter Contracts

### Intended Change
- Extend MC reference docs and internal schemas to represent model-supervised state, operational events, pauses, and resumes.
- Update harness adapter contract so observing, sending, waiting, pausing, and finalizing are documented primitives.
- Define backwards-compatible state defaults for existing runs.

### Acceptance Criteria
- Inputs:
  - Current `references/run-state-schema.md`.
  - Current `references/harness-adapter-contract.md`.
  - Current state helpers in `scripts/mc_lib/state.py` and models in `scripts/mc_lib/models.py`.
- Outputs:
  - Documented `supervision` and `operational_events` state sections.
  - Documented append-only operational event log path and single-writer or locked `run.json` update strategy.
  - Documented `current_slice.before_head` so later finalization can verify changed files against the real slice start.
  - Documented optional statuses or current-slice pause fields.
  - Documented pause-budget counters for consecutive and cumulative pauses.
  - Code constants/models support the new state without breaking old run files.
- User-visible behaviour:
  - `status` and `summarize` can display paused/recovering information once later slices use it.
- Behaviour that must not change:
  - Existing run files without supervision fields still load.
  - Existing completed slice entries still count as completed.

### Authorized Surface
- Files allowed to change:
  - skills/master-controller/references/run-state-schema.md
  - skills/master-controller/references/harness-adapter-contract.md
  - skills/master-controller/scripts/mc_lib/constants.py
  - skills/master-controller/scripts/mc_lib/models.py
  - skills/master-controller/scripts/mc_lib/state.py
  - skills/master-controller/scripts/mc_lib/commands.py
  - skills/master-controller/scripts/mc_lib/__init__.py
  - skills/master-controller/tests/test_mc.py
- Functions/classes/components allowed to change:
  - Run status constants.
  - State load/write helpers.
  - Locked state update or append-only event helper scaffolding.
  - Status/summarize display helpers.
  - Public exports for any new state helpers.
- Tests allowed or expected to change:
  - Unit tests for backwards-compatible loading of old run state.
  - Unit tests that append-only operational event writes do not rewrite unrelated `run.json` state.
  - Unit tests that current-slice state records `before_head`.
  - Unit tests for rendering paused/recovering summary fields.

### Explicit Non-Goals
- Do not add tmux observe/send commands yet.
- Do not change slice execution control flow yet.
- Do not implement operational event parsing yet.
- Do not implement pause/retry policy yet.

### Risk Flags
- Risky surfaces touched:
  - Run state schema.
  - Public CLI status semantics.
- Approval needed before implementation:
  - yes - changes durable state schema and user-visible run status.

### Validation Plan
- Tests to add/update:
  - Add tests for old run state without `supervision`.
  - Add tests for new paused state display.
  - Add tests for append-only operational event writes.
  - Add tests for persisted `current_slice.before_head`.
  - Add tests for pause-budget counters.
- Commands to run:
  - `python3 -m unittest skills.master-controller.tests.test_mc`
  - `git diff --check`
- Manual checks:
  - Inspect `run-state-schema.md` examples for consistency with code constants.
  - Confirm the state design avoids concurrent read-modify-write loss when `wait`, `send`, `finalize-slice`, and human `stop` run as separate processes.

### Rollback Path
- Revert schema/model/state/status changes and associated tests.

## Slice 3: Add Observation And Send Primitives

### Intended Change
- Add CLI support for observing the active MC slice without finalizing it.
- Add CLI support for sending a short literal instruction into the active tmux session.
- Ensure all observations and sent messages are recorded as artifacts or operational events.

### Acceptance Criteria
- Inputs:
  - Existing active run state.
  - Existing `TmuxHarnessAdapter.capture`, `detect_activity`, and session metadata.
  - Existing ai-orchestrator helper patterns for wait/activity monitoring and literal tmux injection.
- Outputs:
  - `mc.py observe --repo <path>` returns a structured snapshot.
  - `mc.py send --repo <path> --text <text> --reason <reason>` sends text only to the active current slice session and records the action in the operational event log.
  - `send` uses literal tmux input and the existing settle/double-submit discipline so it does not reproduce the known single-Enter prompt-submission race.
  - `send` refuses when `observe` or the current pane text indicates a trust, approval, credential, permission, or external-side-effect prompt.
  - Observation includes current time, elapsed time, pane text path, running/dead state, result-file existence, git status summary, prompt-on-screen hard-stop flags, and artifact paths.
  - Implementation reuses or adapts existing `worker_jobs.py`, `ai-reminder`, and `TmuxHarnessAdapter` patterns where they already solve activity monitoring, transcript lookup, safe literal tmux injection, or prompt submission.
- User-visible behaviour:
  - The MC model can inspect live state during a running slice without waiting for `run-next` to finish.
  - The MC model can send continuation prompts without manually composing tmux commands.
- Behaviour that must not change:
  - Existing `run-next`, `run`, `stop`, `summarize`, and `reconcile` commands still work.
  - `send` cannot target arbitrary tmux sessions outside the current run.

### Authorized Surface
- Files allowed to change:
  - skills/master-controller/scripts/mc_lib/cli.py
  - skills/master-controller/scripts/mc_lib/commands.py
  - skills/master-controller/scripts/mc_lib/tmux_adapter.py
  - skills/master-controller/scripts/mc_lib/runtime.py
  - skills/master-controller/scripts/mc_lib/state.py
  - skills/master-controller/scripts/mc_lib/utils.py
  - skills/master-controller/scripts/mc_lib/__init__.py
  - skills/master-controller/README.md
  - skills/master-controller/SKILL.md
  - skills/master-controller/tests/test_mc.py
- Functions/classes/components allowed to change:
  - CLI parser command registration.
  - Tmux adapter literal send helper.
  - Tmux adapter prompt/trust guard reuse.
  - Observation serialization helper.
  - Operational event append helper.
- Tests allowed or expected to change:
  - Unit tests for observe output with no current slice.
  - Unit tests for observe output with mocked tmux capture.
  - Unit tests for send refusing no-current-slice, refusing prompt-on-screen hard stops, and recording sent text for current slice.
  - Unit tests proving `send` uses literal input and robust submit semantics.

### Explicit Non-Goals
- Do not implement pause-until or long wait loops in this slice.
- Do not add usage-limit parsing yet.
- Do not change deterministic gate verification.

### Risk Flags
- Risky surfaces touched:
  - Tmux command injection.
  - Run state mutation.
- Approval needed before implementation:
  - yes - adds a command that sends text into a live harness session.

### Validation Plan
- Tests to add/update:
  - Mocked adapter tests for literal send escaping and target restriction.
  - Mocked adapter tests for trust/approval prompt refusal before sending.
  - Regression test for the prompt-submission race that requires the robust submit path.
  - Runtime tmux smoke test if feasible, skipped when tmux is unavailable.
- Commands to run:
  - `python3 -m unittest skills.master-controller.tests.test_mc`
  - `git diff --check`
- Manual checks:
  - Verify `send` uses tmux literal input (`send-keys -l` or equivalent) and does not shell-evaluate user text.
  - Verify `send` does not press Enter into a trust, approval, credential, permission, or external-side-effect prompt.
  - Compare new observe/wait/send helpers against `worker_jobs.py`, `ai-reminder`, and existing `TmuxHarnessAdapter` logic and document why any duplicated logic is necessary.
  - Verify observation JSON is compact enough for the MC model to read repeatedly.

### Rollback Path
- Remove observe/send CLI commands, adapter helper, state event writes, docs, and tests.

## Slice 4: Add Model-Supervised Start, Wait, Pause, And Finalize Flow

### Intended Change
- Split current blocking `run-next` execution into primitives that let the MC model supervise the live session:
  - start a slice and return immediately
  - wait for a bounded duration
  - pause until an absolute reset time
  - finalize a slice after `orchestrator-result.json` appears
  - stop with evidence when the MC model decides to stop
- Preserve current `run-next` and `run --scope remaining` compatibility.

### Acceptance Criteria
- Inputs:
  - Existing `execute_slice` control flow in `runner.py`.
  - Existing gate verification in `gates.py`.
  - Existing artifact capture helpers.
- Outputs:
  - `start-slice` launches the next eligible slice, writes `current_slice` including `before_head`, sends the prompt, and returns control to the MC model.
  - `wait` observes for a bounded duration and returns when result appears, process exits, hard-stop hint appears, or wait expires.
  - `pause-until` persists pause state, enforces pause budgets, refuses deterministic hard-stop conditions, and observes until a timestamp plus buffer.
  - `finalize-slice` captures artifacts, runs existing gates using persisted `before_head`, appends a slice entry, updates run status, and closes the session.
  - `stop-with-evidence` captures artifacts and records a structured stop reason without accepting the slice.
  - Stale or orphaned sessions from interrupted model-supervised runs can be detected and reaped with evidence before a new slice starts.
- User-visible behaviour:
  - A Mode C launcher can keep the MC model in a loop: start, observe/wait, decide, send, finalize, advance.
- Behaviour that must not change:
  - Existing deterministic `run-next` path still accepts passing fake harness tests.
  - Existing deterministic `run --scope remaining` still completes toy two-slice plans.
  - Gate failures still stop fail-closed.

### Authorized Surface
- Files allowed to change:
  - skills/master-controller/scripts/mc_lib/cli.py
  - skills/master-controller/scripts/mc_lib/commands.py
  - skills/master-controller/scripts/mc_lib/runner.py
  - skills/master-controller/scripts/mc_lib/tmux_adapter.py
  - skills/master-controller/scripts/mc_lib/state.py
  - skills/master-controller/scripts/mc_lib/runtime.py
  - skills/master-controller/scripts/mc_lib/constants.py
  - skills/master-controller/scripts/mc_lib/__init__.py
  - skills/master-controller/references/run-state-schema.md
  - skills/master-controller/references/harness-adapter-contract.md
  - skills/master-controller/README.md
  - skills/master-controller/SKILL.md
  - skills/master-controller/tests/test_mc.py
- Functions/classes/components allowed to change:
  - Extract reusable start/poll/capture/finalize helpers from `execute_slice`.
  - CLI command handlers for start/wait/pause/finalize/stop-with-evidence.
  - Run state status transitions.
  - Stale session detection/reaping tied to the current run.
- Tests allowed or expected to change:
  - Existing runtime tests must continue passing.
  - New fake harness tests for start/wait/finalize lifecycle.
  - New tests for pause-until state persistence.
  - New tests that `finalize-slice` uses persisted `before_head` and does not guess `HEAD^`.
  - New tests for repairable finalize results and repair-attempt caps.
  - New tests for stale running session detection after an interrupted model-supervised run.
  - New tests for stop-with-evidence preserving pane and git evidence.

### Explicit Non-Goals
- Do not make model calls from inside Python.
- Do not accept any slice without existing deterministic gate verification.
- Do not modify `gates.py`; this slice should reuse existing gate verification. If gate signatures must change, stop and create a narrower follow-up slice.
- Do not remove `run-next` or `run --scope remaining`.

### Risk Flags
- Risky surfaces touched:
  - Runtime control flow.
  - Tmux session lifecycle.
  - Gate finalization timing.
  - Durable run state.
- Approval needed before implementation:
  - yes - major runtime workflow transition.

### Validation Plan
- Tests to add/update:
  - Toy harness start/wait/finalize pass.
  - Toy harness process-exit-without-result stop.
  - Pause-until with mocked time.
  - Finalize with multiple commits in one slice to prove `before_head` is honored.
  - Repairable result restart path within the configured attempt cap.
  - Stale session reaper path.
  - Compatibility tests for existing run-next/run remaining.
- Commands to run:
  - `python3 -m unittest skills.master-controller.tests.test_mc`
  - `git diff --check`
- Manual checks:
  - Run a safe local trial from `skills/master-controller/README.md`.
  - Inspect `.ai-mc/runs/<run-id>/run.json` after start, wait, pause, finalize, and stop.

### Rollback Path
- Revert new primitive commands and helper extraction, then restore prior `execute_slice` control flow.

## Slice 5: Add Operational Event Hints And Recovery Guidance

### Intended Change
- Add lightweight deterministic hint extraction from pane/transcript text for common operational events.
- Expose hints through `observe` and `wait`.
- Keep ordinary hints advisory; the MC model remains responsible for deciding the action.
- Enforce hard-stop hints as deterministic guards that prevent unattended pause/resume.

### Acceptance Criteria
- Inputs:
  - Live pane captures.
  - Transcript tail when available.
  - Current time and timezone.
- Outputs:
  - Hints for rolling usage limits, weekly/monthly/account limits, service unavailable, network transient, auth/trust/permission prompts, external side-effect requests, idle/no-progress, result-ready, and process-exited-without-result.
  - Parse reset times/durations when explicit enough.
  - Mark weekly/monthly/account limits as hard-stop hints that `pause-until`, continuation `send`, and automatic retry/resume commands must refuse.
  - Mark unknown limit messages as hard-stop or ambiguous, never automatically recoverable.
  - Prefer relative reset durations over absolute local times; classify ambiguous absolute times as stop-for-user.
  - Distinguish rolling-limit-with-live-process from rolling-limit-after-process-exit, because live sessions can receive a continuation prompt while exited sessions require restart-from-clean or user stop.
  - Enforce pause budgets for maximum single pause, consecutive pauses, and cumulative paused time.
- User-visible behaviour:
  - The MC model sees structured hints and evidence excerpts in observation output.
- Behaviour that must not change:
  - Hints do not finalize gates.
  - Hints do not automatically authorize code changes.
  - Hints do not override approval-gated slices.
  - Hard-stop hints prevent unattended pause/resume even if the MC model would otherwise choose to wait.

### Authorized Surface
- Files allowed to change:
  - skills/master-controller/scripts/mc_lib/constants.py
  - skills/master-controller/scripts/mc_lib/models.py
  - skills/master-controller/scripts/mc_lib/runtime.py
  - skills/master-controller/scripts/mc_lib/commands.py
  - skills/master-controller/scripts/mc_lib/state.py
  - skills/master-controller/scripts/mc_lib/utils.py
  - skills/master-controller/tests/test_mc.py
  - skills/master-controller/references/run-state-schema.md
  - skills/master-controller/README.md
  - skills/master-controller/SKILL.md
- Functions/classes/components allowed to change:
  - New operational hint extraction helper.
  - Observation payload builder.
  - Time parsing helpers.
  - Hard-stop guard helper shared by `pause-until`, `send`, and retry/resume paths.
- Tests allowed or expected to change:
  - Unit tests for rolling limit with absolute time.
  - Unit tests for rolling limit with duration.
  - Unit tests for weekly/monthly/account hard-stop classification.
  - Unit tests for service-unavailable bounded retry hint.
  - Unit tests for ambiguous/unparseable messages.
  - Unit tests for process-alive versus process-exited rolling-limit recovery guidance.
  - Unit tests for pause-budget exhaustion.

### Explicit Non-Goals
- Do not make the hint extractor a comprehensive natural-language decision engine.
- Do not add network calls to verify service status.
- Do not auto-wait from the deterministic `run --scope remaining` path unless separately documented and tested.
- Do not permit any hard-stop hint to become an unattended wait, retry, resume, or continuation send.

### Risk Flags
- Risky surfaces touched:
  - Operational classification.
  - Timezone and timestamp parsing.
- Approval needed before implementation:
  - yes - controls when MC advises waiting versus stopping.

### Validation Plan
- Tests to add/update:
  - Table-driven parser tests for representative Codex and Claude phrasing.
  - Mock-time tests for local reset times around midnight, including already-passed-today cases.
  - Tests that weekly/monthly/account/unknown limits never produce resumable wait actions.
  - Tests that relative durations are preferred when both duration and absolute time appear.
  - Tests that exited-process rolling limits do not attempt to send into a dead session.
  - Tests that pause-budget exhaustion stops for the user.
- Commands to run:
  - `python3 -m unittest skills.master-controller.tests.test_mc`
  - `git diff --check`
- Manual checks:
  - Inspect observation JSON for clarity and compactness.
  - Confirm timezone assumptions are documented.

### Rollback Path
- Remove hint extractor, hint fields in observations, docs, and tests.

## Slice 6: Update Mode C Launcher And End-To-End Supervision Trial

### Intended Change
- Update root Mode C launcher to use the model-supervised primitive loop by default when nuanced supervision is desired.
- Keep deterministic batch MC documented as a lower-judgment fail-closed option.
- Add a safe local trial showing a fake harness that simulates a rolling usage limit and then continues after MC sends the continuation prompt.

### Acceptance Criteria
- Inputs:
  - New primitive commands from Slices 3-5.
  - Root `README.md` Mode C section.
  - Master-controller README safe local trial.
- Outputs:
  - Root `README.md` includes model-supervised Mode C launcher.
  - Launcher tells the MC model to observe tmux, reason from screen/log/json evidence, recover obvious bounded operational pauses, and stop on unclear or hard-stop conditions.
  - Master-controller README includes fake harness trials for live-session usage-limit pause/resume and exited-process usage-limit handling.
- User-visible behaviour:
  - A fresh MC model receives enough instructions to act as the supervisor rather than outsourcing all live judgment to `mc.py run`.
- Behaviour that must not change:
  - The deterministic batch command remains available for simple runs.
  - Users are not asked to hand-compose Codex/Claude sandbox flags.

### Authorized Surface
- Files allowed to change:
  - README.md
  - skills/master-controller/README.md
  - skills/master-controller/SKILL.md
  - skills/master-controller/references/harness-adapter-contract.md
  - skills/master-controller/references/run-state-schema.md
  - skills/master-controller/tests/test_mc.py
- Functions/classes/components allowed to change:
  - Documentation and tests for end-to-end supervision trial.
- Tests allowed or expected to change:
  - Add or update fake harness tests to simulate operational pause and continuation.
  - Add or update fake harness tests to simulate usage-limit process exit before result creation.

### Explicit Non-Goals
- Do not add new runtime primitives in this slice.
- Do not change gate verification.
- Do not remove deterministic batch MC.

### Risk Flags
- Risky surfaces touched:
  - Public README workflow.
  - End-to-end operational expectations.
- Approval needed before implementation:
  - yes - changes user-facing launch guidance.

### Validation Plan
- Tests to add/update:
  - Fake harness usage-limit pause/resume test where feasible.
  - Fake harness usage-limit process-exit test where feasible.
- Commands to run:
  - `python3 -m unittest skills.master-controller.tests.test_mc`
  - `git diff --check`
- Manual checks:
  - Read the launcher prompt as a fresh MC model and confirm it includes: observe, decide, wait/retry, send continuation for live rolling-limit sessions, restart-or-stop handling for exited rolling-limit sessions, finalize, advance, and stop on weekly limits.

### Rollback Path
- Revert README/SKILL/README trial updates and associated tests.

## Slice 7: Remove Stale Or Redundant Instructions And Confirm Compatibility

### Intended Change
- Audit MC docs and code comments for stale assumptions from the old fully blocking supervisor model.
- Remove or rewrite wording that implies all timeouts are terminal, all sessions close after timeout, or pane text is never a basis for operational action.
- Keep safety-critical wording that acceptance gates cannot rely on natural-language transcript interpretation alone.
- Reconcile pre-existing adapter-contract drift, including method names and failure semantics that no longer match the current `TmuxHarnessAdapter`.

### Acceptance Criteria
- Inputs:
  - All MC docs after Slices 1-6.
  - Existing tests and compatibility paths.
- Outputs:
  - Docs consistently describe model-supervised and deterministic batch modes.
  - No stale command examples suggest the wrong mode for nuanced operational supervision.
  - Safety language is precise: screen text can guide operational triage, not acceptance.
  - `harness-adapter-contract.md` matches the implemented adapter concepts or clearly marks aspirational/future contract items.
  - The default operating path in `skills/master-controller/SKILL.md` no longer implies `run --scope remaining` is the only normal Mode C path when model judgment is desired.
- User-visible behaviour:
  - Fresh users and agents do not receive contradictory MC instructions.
- Behaviour that must not change:
  - No runtime behaviour changes beyond documentation/comment cleanup unless tests expose a contradiction.

### Authorized Surface
- Files allowed to change:
  - README.md
  - skills/master-controller/README.md
  - skills/master-controller/SKILL.md
  - skills/master-controller/references/harness-adapter-contract.md
  - skills/master-controller/references/orchestrator-prompt.md
  - skills/master-controller/references/run-state-schema.md
  - skills/master-controller/scripts/mc_lib/tmux_adapter.py
  - skills/master-controller/scripts/mc_lib/runner.py
  - skills/master-controller/scripts/mc_lib/commands.py
  - skills/master-controller/tests/test_mc.py
- Functions/classes/components allowed to change:
  - Documentation and comments.
  - Tests only if they encode stale wording or stale assumptions.
- Tests allowed or expected to change:
  - Documentation-adjacent tests only, if any exist.

### Explicit Non-Goals
- Do not introduce new behaviour in this cleanup slice.
- Do not broaden public CLI scope.
- Do not change plan parsing or gate verification.

### Risk Flags
- Risky surfaces touched:
  - Broad documentation consistency.
  - Comments in runtime modules that could be mistaken for behavior changes.
- Approval needed before implementation:
  - yes - broad cleanup surface; implementer should keep this slice tightly constrained and avoid runtime behaviour changes.

### Validation Plan
- Tests to add/update:
  - None expected unless stale tests must be updated.
- Commands to run:
  - `python3 -m unittest skills.master-controller.tests.test_mc`
  - `git diff --check`
- Manual checks:
  - Search for stale wording: `timeout`, `close the session`, `run --scope remaining`, `transcript interpretation`, `usage limit`, `reset`, `resume`, `continue`.
  - Compare `harness-adapter-contract.md` against `TmuxHarnessAdapter` and either align the wording or label future-only adapter requirements.
  - Review every changed file for accidental behavioural edits.

### Rollback Path
- Revert documentation/comment cleanup changes.

## Suggested Model-Supervised Mode C Loop

The final launcher should direct the MC model to follow a loop like this:

1. Read the plan and MC docs.
2. Confirm repo, plan, branch, scope, harness, and worker tools.
3. Initialize or reuse the run.
4. Run preflight and dry-run next slice.
5. Start the next slice with `start-slice`.
6. Observe immediately and record the tmux session/artifact paths.
7. Repeatedly call `observe` or `wait` on a calm cadence.
8. If the result appears, run `finalize-slice`.
9. If pane/log evidence shows a rolling usage reset and the harness process is still alive, compute reset plus buffer, call `pause-until`, re-observe for hard-stop prompts, then send the continuation prompt.
10. If pane/log evidence shows a weekly/monthly/account cap, call `stop-with-evidence` and report.
11. If pane/log evidence shows a rolling usage reset but the harness process has exited before writing a result, restart only from a clean authorized state; otherwise call `stop-with-evidence`.
12. If pane/log evidence shows a bounded transient service problem, wait/retry within policy.
13. If evidence is unclear, stop with evidence and report.
14. After a finalized pass, advance to the next eligible slice.
15. After any stop or completion, run summarize, inspect run state/artifacts, and report git status.

## Next Chat Prompt

```md
Plan file: /Users/dcroton/Documents/AI Tools/docs/MC-MODEL-SUPERVISED-TRANSITION-PLAN.md
Slices or batch this session: Slice 1

Read the full plan file first. If a selected slice or batch receipt is incomplete or the plan state is unclear, stop and tell me before coding.

Work on the current feature branch for this plan.

Use ai-orchestrator as the controlling skill only if delegation improves quality or independent review; otherwise keep the work local. This transition touches MC runtime supervision, so do not broaden scope beyond the selected slice.

For each selected slice or batch, in plan order:
1. Restate the frozen contract (authorized surface + non-goals) from the plan.
2. If any included slice's Risk Flags mark approval-needed, stop and get my approval before coding.
3. Apply scoped-implementation against the selected contract.
4. Apply drift-audit. Report the authorization gate result before any quality review.
5. If the gate passes, apply code-review. If it fails, fix the drift and re-audit.
6. Surface drift and review findings to me, fix them, then re-run the relevant gate.
7. Ask me before committing. On my approval, commit the selected slice or batch with the commit skill.

After the selected slice or batch is committed, use handoff to record state and the next slice or batch to resume from. Do not continue past the selected scope.

Confirm before starting: plan file read, selected slice or batch, branch, and the first slice.
```
