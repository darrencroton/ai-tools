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
    "max_repair_attempts": 1,
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
  "slices": [],
  "stop_reason": null
}
```

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
- MC assumes one logical controller for a run, but model-supervised operation
  may invoke separate commands such as wait, send, finalize, and human stop.
  Commands that rewrite `run.json` must use a single-writer strategy, such as
  an advisory lock around read-modify-write. High-frequency observations must
  be appended to JSONL artifacts instead of repeatedly rewriting `run.json`.

## Supervision State

`supervision.mode` is `deterministic-batch` for the compatibility path and is set to `model-supervised` by the model-supervised `start-slice` primitive. The policy fields describe defaults and budgets; they do not by themselves authorize accepting a slice.

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

`operational_events_path` points at an append-only JSONL file. Model-supervised primitives append observations, waits, sends, pauses, resumes, retries, hard-stop detections, finalization attempts, and stop-with-evidence records there.

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
  "gate_reason": "all gates passed"
}
```

Completed statuses for slice selection are `pass`, `committed`, and `complete`. Any other status is treated as not completed unless a future policy explicitly says otherwise.

Each slice artifact directory contains the rendered `prompt.md`, `activity-attempt-<n>.jsonl`, `pane-capture.txt`, `pane-capture-live-latest.txt` when live pane text was observed, `observation-latest.json` when `observe` or `wait` has run, `git-status-before.txt`, `git-status-after.txt`, `git-diff.patch`, `validation-summary.md`, `drift-audit.md`, `code-review.md`, optional `worker-evidence.md`, optional `worker-runs-summary.json`, optional `mc-reconciliation.json` / `mc-reconciliation.md`, and `orchestrator-result.json` when the orchestrator reaches the structured result stage. Timeout and failure paths preserve whatever capture and git evidence is available. Each activity log line is a JSON object with `checked_at`, `running`, and `active` fields.

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
- `COPILOT_HOME`
- `CODEX_HOME` when Codex is a required worker and not the orchestrator
- `CLAUDE_CONFIG_DIR` when Claude is a required worker and not the orchestrator

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

When all authorization, validation, drift, review, changed-file, ancestry, and clean-worktree evidence passes but `commit.hash` is wrong or abbreviated, MC may reconcile that evidence field to the proven current `HEAD`, write `mc-reconciliation.json` / `mc-reconciliation.md`, update `orchestrator-result.json`, and accept the slice. This reconciliation is limited to commit-hash evidence; it must not mask unauthorized files, missing validation, failed audits/reviews, dirty worktrees, or missing commits.
