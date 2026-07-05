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
    "started_at": "2026-07-04T01:35:00Z"
  },
  "slices": [],
  "stop_reason": null
}
```

Allowed run `status` values:

- `initialized`
- `running`
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
- MC assumes a single controller process per run directory. It does not lock
  `run.json` or the `current` symlink; do not run two MC commands against the
  same run concurrently.

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

Each slice artifact directory contains the rendered `prompt.md`, `activity-attempt-<n>.jsonl`, `pane-capture.txt`, `pane-capture-live-latest.txt` when live pane text was observed, `git-status-before.txt`, `git-status-after.txt`, `git-diff.patch`, `validation-summary.md`, `drift-audit.md`, `code-review.md`, optional `worker-evidence.md`, optional `worker-runs-summary.json`, optional `mc-reconciliation.json` / `mc-reconciliation.md`, and `orchestrator-result.json` when the orchestrator reaches the structured result stage. Timeout and failure paths preserve whatever capture and git evidence is available. Each activity log line is a JSON object with `checked_at`, `running`, and `active` fields.

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
