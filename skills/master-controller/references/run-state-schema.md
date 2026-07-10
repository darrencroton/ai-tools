# Run State Schema

MC writes durable JSON state under `.ai-mc/runs/<run-id>/run.json` in the target repository. The schema is intentionally explicit so a stopped run can be audited or resumed without reading chat history.

## `run.json`

```json
{
  "schema_version": 1,
  "run_id": "20260704T013000Z",
  "created_at": "2026-07-04T01:30:00Z",
  "updated_at": "2026-07-04T01:30:00Z",
  "status": "initialized",
  "repo_path": "/absolute/path/to/repo",
  "plan_path": "/absolute/path/to/plan.md",
  "worktree_root": null,
  "branch": "feature/example",
  "harness": {
    "name": "codex",
    "adapter": null,
    "preflight": {
      "git": "/usr/bin/git",
      "tmux": "/usr/bin/tmux",
      "python": "/usr/bin/python3"
    }
  },
  "policy": {
    "dirty_state": "clean-required",
    "approval_gated_slices": "stop",
    "max_repair_attempts": 3,
    "commit_required": true
  },
  "plan": {
    "slice_count": 4,
    "parser": "implementation-plan-markdown-v1",
    "sha256": "<hex digest of the plan file at init>"
  },
  "current_slice": {
    "slice_id": "Slice 1",
    "title": "Define Skill Contract and Reference Docs",
    "artifact_dir": ".ai-mc/runs/20260704T013000Z/slices/slice-001",
    "tmux_session": "mc_20260704T013000Z_slice-001_a1",
    "attempt": 1,
    "started_at": "2026-07-04T01:35:00Z",
    "before_head": "<commit HEAD immediately before this slice attempt started>",
    "orchestrator_session_id": "<optional Claude session id for transcript capture>",
    "worker_tools": ["<tool names required for this slice attempt, empty if none>"],
    "repair": {
      "round": 0,
      "last_signature": "",
      "signature_streak": 0,
      "session_generation": 1
    },
    "pause": null
  },
  "supervision": {
    "mode": "deterministic-batch",
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
    "max_transient_retries_per_slice": 3,
    "pause_counters": {
      "consecutive_pauses_current_slice": 0,
      "cumulative_pause_seconds_run": 0
    }
  },
  "operational_events_path": ".ai-mc/runs/20260704T013000Z/operational-events.jsonl",
  "approvals": {},
  "slices": [],
  "stop_reason": null
}
```

`policy.max_repair_attempts` and `policy.commit_required` are set at `init` (`--max-repair-attempts`, `--no-commit-required`) and default to 3 and true. Supervision pause budgets keep their defaults; editing them by hand in `run.json` before the first slice starts is the supported way to change them for a run.

`approvals` maps an approval-gated slice id to a recorded operator approval:

```json
{
  "Slice 3": {
    "approved_at": "2026-07-04T02:00:00Z",
    "reason": "risk reviewed with operator",
    "approved_by": "operator"
  }
}
```

Written only by the `approve` command (which also appends a `"kind": "approval"` operational event). An entry clears exactly one condition: a plan slice whose `Approval needed before implementation` flag is an explicit `yes`. Missing or unclear flags stay blocking regardless of approvals.

Allowed run `status` values:

- `initialized`
- `running`
- `paused`
- `resuming`
- `partial`
- `needs-human`
- `blocked`
- `failed`
- `complete`
- `cancelled`

## Run Integrity

- `plan.sha256` freezes the plan file at init. Before each slice, MC re-hashes
  the plan and stops with an error if it changed, so a mid-run plan edit cannot
  silently alter authorization, ordering, or approval flags. A revised plan
  requires a fresh `init`. Runs created before digests were recorded have no
  baseline and skip this check.
- Slice numbers must be unique; `init` fails closed on a duplicate `## Slice N:`
  because completion tracking keys on the slice id.
- A revised plan requires a fresh `init`. When earlier slices were already
  completed and committed under the previous run, `init --assume-complete
  "Slice 1,Slice 2"` records operator-attested `assumed-complete` entries so
  the new run resumes at the next real slice instead of re-running finished
  work. These entries are attestations, not gate verdicts: MC never assigns
  the status itself, and the entry's `gate_reason` says so.
- MC assumes one logical controller for a run. Concurrency control is
  deliberately partial: the operational-events JSONL and `approve` use
  advisory locks (`update_run_locked` / the events lock), and `pause-until`
  locks its counter updates because it overlaps long waits — but
  `start-slice`, `finalize-slice`, `stop`, and the batch loop rewrite
  `run.json` unlocked under the one-controller assumption (they also refuse
  to run while another slice is live). Do not drive one run from two
  controllers concurrently. High-frequency observations must be appended to
  JSONL artifacts instead of repeatedly rewriting `run.json`.

## Supervision State

`supervision.mode` is set explicitly by both paths: the batch driver (`run-next` / `run --scope remaining`) records `deterministic-batch`, and the model-supervised `start-slice` primitive records `model-supervised`. The policy fields describe defaults and budgets; they do not by themselves authorize accepting a slice.

`pause_policy` names the intended operational policy:

- rolling usage limits: wait until reset plus buffer when evidence is clear and the harness process is still resumable
- weekly, monthly, account, billing, and unknown limits: stop for the user
- transient service unavailable: bounded retry
- unknown operational event: stop for the user

Pause budget fields:

- `default_reset_buffer_seconds`: buffer added after a clear reset time
- `max_single_pause_seconds`: maximum one pause may wait
- `max_consecutive_pauses_per_slice`: maximum repeated pauses in the same slice
- `max_cumulative_pause_seconds_per_run`: maximum total paused time in the run
- `pause_counters.consecutive_pauses_current_slice`: count for the active slice
- `pause_counters.cumulative_pause_seconds_run`: total paused seconds for the run

Existing run files without `supervision` or `operational_events_path` load with these defaults. Loading defaults must remain backwards-compatible and must not mark old completed slice entries incomplete.

## Operational Events

`operational_events_path` points at an append-only JSONL file. Model-supervised primitives append observations, waits, sends, pauses, resumes, retries, hard-stop detections, approvals, finalization attempts, and stop-with-evidence records there. Event ids come from a sidecar `.counter` file maintained under the same lock (seeded by a one-time line count for runs created before the counter existed). During `wait`/`pause-until` polling, observation events are recorded on decision-relevant change or on a 60-second floor — not on every poll — plus always for the final snapshot, so a multi-hour pause does not flood the log with identical entries.

Example line:

```json
{
  "event_id": "op-0001",
  "slice_id": "Slice 1",
  "attempt": 1,
  "kind": "usage_limit",
  "subtype": "rolling_window",
  "status": "handled",
  "detected_at": "2026-07-04T01:40:00Z",
  "evidence_path": ".ai-mc/runs/20260704T013000Z/slices/slice-001/pane-capture-live-latest.txt",
  "evidence_excerpt": "session limit reached and will reset at 6:30pm",
  "decision": "pause-until",
  "decided_by": "mc-model",
  "resume_at": "2026-07-04T08:33:00Z",
  "action_taken": "sent continuation prompt",
  "notes": ""
}
```

Append-only event writes must not rewrite unrelated `run.json` state.

## Operational Hints

`observe` and `wait` include an `operational_hints` array in their JSON output. Hints are extracted from live pane text and the transcript tail when present. They are not acceptance evidence and they do not finalize gates.

Example hint:

```json
{
  "kind": "usage_limit",
  "confidence": "high",
  "subtype": "rolling_window",
  "reset_at": "2026-07-04T08:30:00+10:00",
  "retry_after_seconds": null,
  "hard_stop": false,
  "evidence_excerpt": "session limit reached and will reset at 8:30am",
  "source": "tmux-pane",
  "detected_at": "2026-07-04T01:40:00+10:00",
  "recovery_guidance": "pause-until-reset-plus-buffer-then-send-continuation"
}
```

Current hint kinds are:

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

Usage-limit subtypes are:

- `rolling_window`
- `weekly_window`
- `monthly_window`
- `account_or_billing`
- `unknown_limit`

Hard-stop hints are deterministic guards, not just advice. `send`, `pause-until`, and unattended retry/resume paths must refuse when the strongest visible hint is weekly, monthly, account, billing, unknown-limit, auth, trust, permission, or external-side-effect related. Relative reset durations are preferred over absolute times. Absolute local reset times are accepted only when they are unambiguously near-future for the controller timezone or include an explicit timezone; otherwise they become `unknown_limit` hard stops.

## Current Slice

`current_slice.before_head` records the commit at the beginning of the active slice attempt. This is mandatory for `finalize-slice` because out-of-process finalization must compare changed files against the real slice start. Guessing `HEAD^` can miss earlier commits made by the same slice.

`current_slice.orchestrator_session_id` is optional and records the launched Claude session id when MC composed one. `finalize-slice` and `stop-with-evidence` use it to capture `orchestrator-transcript.jsonl` without relying only on pane text.

`current_slice.repair` tracks the self-correcting repair loop for the active slice:

- `round`: repairable gate failures handled so far (in-session nudge, fresh-session escalation, or dead-session relaunch). The repair budget (`policy.max_repair_attempts`, default 3) is enforced from this persisted counter — never from counting appended slice entries, because in-session repairs deliberately append none.
- `last_signature` / `signature_streak`: the signature-keyed circuit breaker. The first failure of a signature earns an in-session nudge into the live orchestrator session; the same signature failing again earns exactly one fresh-session retry; a third consecutive failure is terminal regardless of remaining budget. A dead-session relaunch consumes a round but leaves the breaker untouched.
- `session_generation`: increments only when a fresh tmux session is launched; the session name keys on it, so in-session repair rounds keep one live session.

Every repair is re-verified by the complete, unrelaxed gate against the slice starting commit, so a `repairable` classification can only grant another chance to satisfy the identical gate — it can never accept a bad slice. Repairable signatures are `validation`, `drift`, `review`, `worker-evidence`, `unauthorized-files` (restore-only), `changed-files-mismatch`, `result-malformed`, `commit-missing`, `dirty-worktree`, and `orchestrator-repairable`. Terminal `needs-human` signatures are `integrity-head` and `slice-id-mismatch` — integrity/trust breaches are never steered, because continuing to reason from a context that already holds a false belief about reality is itself the risk. The `integrity-head` gate validates HEAD advance and descent from the slice starting commit on git evidence alone, before any comparison with the self-reported hash, so a truthful report of a reset-to-unrelated HEAD still fails. A missing `orchestrator-result.json` stays terminal `blocked` (a dead or unresponsive session is a runner condition, not a steerable content defect).

Readers must tolerate a missing `repair` key (runs created before the repair loop) by defaulting to round 0; `normalize_run_state` deliberately does not backfill it.

Both execution paths drive the identical loop from this state — by construction: the deterministic-batch path (`run-next` / `run --scope remaining`) is an in-process driver over the same start/wait/finalize primitives with a fixed no-judgment policy. The batch driver never interrupts a wait for hard-prompt or hard-stop-hint heuristics (their markers are broad substring matches that routinely occur in harness output; the unconditional safety boundary is the send-time refusal to type into a session showing a hard prompt, and the signals are still observed and recorded); it delivers in-session repair prompts itself, immediately; and it converts timeout, interrupt, and unexpected exception into forced fail-closed terminal entries. Batch runs therefore also record `observation` operational events during polling, refresh `observation-latest.json`, may briefly show run status `resuming` while an in-session repair round is live, and reap stale run sessions at slice start. The model-supervised path spreads the same loop across separate invocations: on a repairable gate with budget remaining, `finalize-slice` does **not** force-stop the session, appends **no** slice entry, keeps `current_slice` populated (so `start-slice` still refuses a concurrent second attempt), records the new repair state, and returns `"finalized": false, "status": "repairable"` with a `mode` field. For `"mode": "in-session"` it also returns `send_text` — a single-line pointer to the rendered `repair-prompt-repair-<round>.md` — and sets run status to `resuming` (send-eligible); the MC model delivers `send_text` with `send`, `wait`s for a fresh result, and finalizes again. For `"mode": "fresh-session"` (circuit-breaker escalation) or `"mode": "relaunch"` (dead session), `finalize-slice` has already force-stopped the old session and launched a new one itself with the original frozen prompt — `start-slice` cannot be used because it refuses while `current_slice` is populated, and clearing `current_slice` would drop the breaker state — leaving status `running`; the MC model just `wait`s and re-finalizes. `current_slice.before_head` never changes across rounds or relaunches, so verification stays cumulative, and `current_slice.worker_tools` (persisted at `start-slice`) keeps the worker-evidence gate enforced across invocations without re-supplying `--worker-tools`. The relaunch composes its harness launch from the current `finalize-slice` invocation's flags, so invoke `finalize-slice` with the same `--harness-command`/`--allow-profile-command`/model/effort flags used at `start-slice`. On budget exhaustion, a tripped breaker, or an integrity gate, `finalize-slice` force-stops, appends the terminal entry, clears `current_slice`, and stops for a human as before. `run-next` and `run --scope remaining` refuse to start while any `current_slice` is populated, so a batch command cannot orphan a live model-supervised repair session.

`current_slice.pause` is either `null` or:

```json
{
  "paused_until": "2026-07-04T08:33:00Z",
  "reason": "rolling usage limit reset",
  "evidence_event_id": "op-0001"
}
```

## Slice Entry

Runtime slices append entries to `slices`:

```json
{
  "slice_id": "Slice 1",
  "title": "Define Skill Contract and Reference Docs",
  "status": "pass",
  "started_at": "2026-07-04T01:35:00Z",
  "completed_at": "2026-07-04T01:42:00Z",
  "artifact_dir": ".ai-mc/runs/20260704T013000Z/slices/slice-001",
  "before_head": "<commit HEAD immediately before this slice ran, or null>",
  "changed_files": [],
  "validation": [],
  "drift_audit": {
    "verdict": "PASS",
    "path": ".ai-mc/runs/20260704T013000Z/slices/slice-001/drift-audit.md"
  },
  "code_review": {
    "verdict": "PASS",
    "path": ".ai-mc/runs/20260704T013000Z/slices/slice-001/code-review.md"
  },
  "commit": {
    "requested": true,
    "created": true,
    "hash": "abc123"
  },
  "next_action": "",
  "blockers": [],
  "gate_reason": "all gates passed",
  "worker_tools": ["<tool names required for this slice attempt, empty if none>"],
  "repair": {
    "round": 1,
    "last_signature": "validation",
    "signature_streak": 1,
    "session_generation": 1
  }
}
```

`repair` is present only when the slice actually consumed repair rounds and records the final repair-loop state for the attempt that produced this entry; a slice accepted on its first attempt (and every entry written before the repair loop existed) keeps the exact pre-repair-loop entry shape without it.

Completed statuses for slice selection are `pass`, `committed`, `complete`, and `assumed-complete` (the last written only by `init --assume-complete` as an operator attestation). Any other status is treated as not completed unless a future policy explicitly says otherwise.

Each slice artifact directory contains the rendered `prompt.md`, `activity-attempt-<n>.jsonl`, `pane-capture.txt`, `pane-capture-live-latest.txt` when live pane text was observed, `observation-latest.json` when `observe`, `wait`, or batch polling has run, `git-status-before.txt`, `git-status-after.txt`, `git-diff.patch`, `validation-summary.md`, `drift-audit.md`, `code-review.md`, optional `worker-evidence.md`, optional `worker-runs-summary.json`, optional `mc-reconciliation.json` / `mc-reconciliation.md`, and `orchestrator-result.json` when the orchestrator reaches the structured result stage. Timeout and failure paths preserve whatever capture and git evidence is available. Each activity log line is a JSON object with `checked_at`, `running`, and `active` fields.

Repair rounds add per-round artifacts keyed on the round number, so evidence from rounds sharing one session is never overwritten: `orchestrator-result-repair-<round>.json` (the failing result, archived before deletion so the re-poll waits for a genuinely new result), `pane-capture-repair-<round>.txt`, `git-status-repair-<round>.txt`, `repair-prompt-repair-<round>.md` (plus `repair-prompt.md` as the latest), `pane-capture-repair-refused-<round>.txt` when repair delivery was refused by a hard prompt on screen, and one `"kind": "repair"` operational event per round recording the signature and delivery mode (`in-session`, `fresh-session`, or `relaunch`).

MC sets these environment variables for every slice harness:

- `MC_SLICE_ARTIFACT_DIR`
- `MC_RUN_JSON_PATH`
- `MC_PLAN_PATH`
- `MC_SLICE_ID`
- `MC_RESULT_SCHEMA_PATH`
- `MC_WORKER_JOBS_PATH`
- `MC_WORKER_ARTIFACT_ROOT`
- `AI_ORCHESTRATOR_ARTIFACT_ROOT`
- `MC_SLICE_TMP_DIR`
- `TMPDIR`
- `MC_TOOL_HOME_ROOT`
- `COPILOT_HOME` when Copilot is a required worker and not the orchestrator
- `CODEX_HOME` when Codex is a required worker and not the orchestrator

MC does not set `CLAUDE_CONFIG_DIR` for Claude workers. Claude Code subscription OAuth is not portable by copying `.credentials.json` into an isolated config directory; use normal Claude Code auth, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or `CLAUDE_CODE_OAUTH_TOKEN` for unattended isolated auth.

## `orchestrator-result.json`

Every orchestrator session must write this file in the slice artifact directory:

```json
{
  "schema_version": 1,
  "slice_id": "Slice 1",
  "status": "pass",
  "summary": "",
  "changed_files": [],
  "validation": [
    {
      "command": "",
      "result": "pass",
      "notes": ""
    }
  ],
  "drift_audit": {
    "verdict": "PASS",
    "path": ""
  },
  "code_review": {
    "verdict": "PASS",
    "path": ""
  },
  "commit": {
    "requested": true,
    "created": false,
    "hash": null
  },
  "next_action": "",
  "blockers": []
}
```

Allowed orchestrator `status` values:

- `pass`
- `repairable`
- `needs-human`
- `fail`
- `blocked`

MC verifies this result against git state, artifacts, validation output, drift audit, code review, and commit state before accepting a slice.

When a run specifies required worker tool(s) (`--worker-tools`), MC additionally requires mechanical evidence that a worker actually ran — and finished — before it will accept a `pass`: a non-empty `worker-evidence.md` in the slice artifact directory, and a `worker-runs-summary.json` (built only from real `worker_jobs.py` `manifest.json` / `*-status.json` artifacts, so it cannot be satisfied by prose alone) containing at least one worker whose mechanically derived tool name matches a required tool **and** whose status recorded state `completed` with returncode 0. A matching worker that crashed on start, was cancelled, or is still running at finalize does not satisfy the gate. An orchestrator that narrates why it chose not to launch a required worker, or substitutes its own direct checks and calls that "insufficient" evidence, does not satisfy this gate — it fails like any other unmet contract requirement, even if validation, drift audit, and code review all pass. This closes a gap observed in testing: an orchestrator self-declared its own worker evidence "insufficient" per the plan's "stop and report why no worker was available" instruction, but nothing previously stopped it from proceeding through commit anyway. The required worker tool(s) for a slice attempt are persisted at `current_slice.worker_tools` and carried into each slice entry's `worker_tools` field so `finalize-slice` and `reconcile` (separate invocations from the one that started the slice) can recover the requirement without depending on that invocation's own `--worker-tools` flag.

When all authorization, validation, drift, review, changed-file, ancestry, and clean-worktree evidence passes but `commit.hash` is wrong or abbreviated, MC may reconcile that evidence field to the proven current `HEAD`, write `mc-reconciliation.json` / `mc-reconciliation.md`, update `orchestrator-result.json`, and accept the slice. This reconciliation is limited to commit-hash evidence; it must not mask unauthorized files, missing validation, failed audits/reviews, dirty worktrees, or missing commits.
