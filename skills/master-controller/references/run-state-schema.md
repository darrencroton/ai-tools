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
    "parser": "implementation-plan-markdown-v1"
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
- `needs-human`
- `blocked`
- `failed`
- `complete`
- `cancelled`

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

Each slice artifact directory contains the rendered `prompt.md`, `activity-attempt-<n>.jsonl`, `pane-capture.txt`, `git-status-before.txt`, `git-status-after.txt`, `git-diff.patch`, `validation-summary.md`, `drift-audit.md`, `code-review.md`, and `orchestrator-result.json` when the orchestrator reaches the structured result stage. Timeout and failure paths preserve whatever capture and git evidence is available. Each activity log line is a JSON object with `checked_at`, `running`, and `active` fields.

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
