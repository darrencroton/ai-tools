# Master Controller

Master Controller (MC) supervises execution of an already-approved implementation plan. It is not a planner and it is not an implementer. It runs one frozen slice at a time through an AI coding harness, records durable artifacts, verifies gates from outside the harness session, and stops whenever policy requires human approval.

The current implementation provides contract docs, durable run state, conservative plan discovery, tmux-backed slice execution, structured result capture, fail-closed gate verification, looping over remaining slices, cancellation, and summaries.

## What MC Owns

- Creating `.ai-mc/runs/<timestamp>/run.json` under the target repo.
- Updating `.ai-mc/current` to the active run.
- Recording repo, branch, harness, plan, environment preflight, and policy.
- Parsing implementation-plan markdown conservatively enough to identify frozen slice contracts.
- Refusing incomplete, ambiguous, approval-gated, or unauthorized slices.
- Reporting the next eligible slice in `run-next --dry-run`.
- Running one eligible slice with `run-next`.
- Running eligible slices sequentially with `run --scope remaining`.
- Capturing prompt, pane output, git status, git diff, validation, drift audit, code review, and `orchestrator-result.json` artifacts.
- Verifying orchestrator claims against git evidence and stopping on missing validation, unresolved drift/review failures, unauthorized files, missing commits, dirty post-commit state, or approval-gated slices.

## What MC Does Not Own

- Creating or repairing implementation plans.
- Docker or container setup.
- Semantic code review inside MC.
- Dependency, license, remote push, PR, release, or deployment actions.
- Bypassing human approval for approval-gated work.
- Inferring authorization when plan sections are missing.

## CLI

Initialize a run:

```bash
python3 skills/master-controller/scripts/mc.py init \
  --repo /path/to/repo \
  --plan /path/to/plan.md \
  --harness codex
```

Check state:

```bash
python3 skills/master-controller/scripts/mc.py status --repo /path/to/repo
python3 skills/master-controller/scripts/mc.py summarize --repo /path/to/repo
```

List harness and worker capability profiles:

```bash
python3 skills/master-controller/scripts/mc.py profiles
```

Preflight the next slice before starting a tmux harness:

```bash
python3 skills/master-controller/scripts/mc.py preflight \
  --repo /path/to/repo \
  --worker-tools copilot \
  --allow-profile-command
```

Preview the next runnable slice:

```bash
python3 skills/master-controller/scripts/mc.py run-next --repo /path/to/repo --dry-run
```

Run the next eligible slice:

```bash
python3 skills/master-controller/scripts/mc.py run-next \
  --repo /path/to/repo \
  --worker-tools copilot \
  --allow-profile-command
```

Run eligible slices until all are complete or a stop condition is reached:

```bash
python3 skills/master-controller/scripts/mc.py run \
  --repo /path/to/repo \
  --scope remaining \
  --worker-tools copilot \
  --allow-profile-command
```

Cancel a run and record the reason:

```bash
python3 skills/master-controller/scripts/mc.py stop --repo /path/to/repo --reason "manual stop"
```

Archive sensitive worker state from a completed or stopped run:

```bash
python3 skills/master-controller/scripts/mc.py archive-sensitive --repo /path/to/repo --dry-run
python3 skills/master-controller/scripts/mc.py archive-sensitive --repo /path/to/repo
```

`run-next --dry-run` exits successfully only when the next uncompleted slice has the required sections, has a non-empty authorized file surface, and is not approval-gated. Runtime execution also requires a clean target worktree outside `.ai-mc/`.

## Default MC Execution Flow

When a user provides a complete implementation plan and asks MC to implement it, the MC should not need a bespoke launch prompt. Use this sequence unless the user specifies a different scope or harness:

1. Resolve the target repo, plan path, branch, harness, and worker tools from the user request and plan. Default harness is `codex`; worker tools are omitted unless the plan or user requires them.
2. Initialize a run with `init` if needed, or reuse `.ai-mc/current` only after checking it points at the same repo and plan.
3. Run `preflight --allow-profile-command`, adding `--worker-tools <tool[,tool]>` when workers are required.
4. Run `run-next --dry-run` to verify the next eligible slice and authorized files.
5. Run `run-next` for one requested slice, or `run --scope remaining` when the user asked MC to execute the remaining plan.
6. Run `summarize`, inspect `run.json`, inspect slice artifacts, and check git status before reporting.

Do not ask users to hand-compose Codex or Claude sandbox flags. Use `profiles`, `preflight`, `--worker-tools`, and `--allow-profile-command` so MC chooses the tested launch path from tool capabilities plus run requirements.

## Profiles and Launch Requirements

MC stores one profile per tool instead of one profile per possible role combination. Tool profiles describe stable capabilities and constraints: Codex and Claude can be orchestrators or senior workers, Copilot is a junior worker, and OpenCode is a placeholder until its unattended harness contract is tested.

At runtime, MC composes the launch command from the selected harness plus explicit requirements:

- `--worker-tools copilot` tells MC the slice will use a Copilot worker and the harness needs worker-compatible setup.
- `--allow-profile-command` tells MC to use the tested profile command instead of requiring a hand-written `--harness-command`.
- `commit_required=true` in run policy tells the Codex profile to add scoped git-directory access for local commits.

This avoids a large matrix of incomplete names such as `codex-copilot-commit`. The role information stays with each tool profile, and the slice requirements are visible on the command line.

## Run State

State is stored under the target repository:

```text
.ai-mc/
  current -> runs/<timestamp>
  runs/
    <timestamp>/
      run.json
      slices/
        slice-001/
          prompt.md
          activity-attempt-1.jsonl
          pane-capture.txt
          pane-capture-live-latest.txt
          worker-runs/
          tmp/
          tool-homes/
          copilot-home/
          git-status-before.txt
          git-status-after.txt
          git-diff.patch
          validation-summary.md
          drift-audit.md
          code-review.md
          worker-evidence.md
          orchestrator-result.json
```

Target projects should usually add `.ai-mc/` to their own `.gitignore`, but MC does not edit `.gitignore` as part of initialization.

Each `activity-attempt-<n>.jsonl` line records `checked_at`, `running`, and `active` fields from the tmux pane activity check. `pane-capture-live-latest.txt` preserves the last live pane text seen during polling, which is useful when the final pane capture is unavailable after a fast harness exit.

Worker state and temporary files should stay under the slice artifact directory. MC exports fixed paths for worker runs, temporary files, and tool-specific home directories so orchestrators do not have to invent locations.

## Plan Eligibility

MC expects implementation-plan slice sections with these headings:

- `### Intended Change`
- `### Acceptance Criteria`
- `### Authorized Surface`
- `### Explicit Non-Goals`
- `### Risk Flags`
- `### Validation Plan`
- `### Rollback Path`

The parser fails closed when a required section is missing, when no files are listed under `Files allowed to change`, or when `Approval needed before implementation` is anything other than an explicit `no`.

## Safe Local Trial

Use a temporary git repo and a small plan before supervising real work. The local harness below writes the same structured artifacts expected from an AI orchestrator, commits only the authorized file, and then waits long enough for MC to capture the tmux pane:

```bash
tmp="$(mktemp -d)"
git -C "$tmp" init
git -C "$tmp" config user.email mc-test@example.invalid
git -C "$tmp" config user.name "MC Test"
cat > "$tmp/plan.md" <<'PLAN'
# Toy Plan

## Slice 1: Add docs

### Intended Change
- Add README content.

### Acceptance Criteria
- Dry run identifies this slice.

### Authorized Surface
- Files allowed to change:
  - README.md
- Functions/classes/components allowed to change: none.
- Tests allowed or expected to change: none.

### Explicit Non-Goals
- Do not change runtime code.

### Risk Flags
- Risky surfaces touched: none.
- Approval needed before implementation: no.

### Validation Plan
- Commands to run:
  - git diff --check

### Rollback Path
- Revert README.md.
PLAN
touch "$tmp/seed.txt"
git -C "$tmp" add plan.md seed.txt
git -C "$tmp" commit -m "Seed toy repo"
cat > "$tmp/fake_harness.py" <<'PY'
import json
import os
import subprocess
import time
from pathlib import Path

artifact = Path(os.environ["MC_SLICE_ARTIFACT_DIR"])
Path("README.md").write_text("toy slice complete\n", encoding="utf-8")
subprocess.run(["git", "add", "README.md"], check=True)
subprocess.run(["git", "commit", "-m", "Complete toy slice"], check=True)
commit_hash = subprocess.run(["git", "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
(artifact / "validation-summary.md").write_text("PASS\n", encoding="utf-8")
(artifact / "drift-audit.md").write_text("PASS\n", encoding="utf-8")
(artifact / "code-review.md").write_text("PASS\n", encoding="utf-8")
(artifact / "orchestrator-result.json").write_text(json.dumps({
    "schema_version": 1,
    "slice_id": "Slice 1",
    "status": "pass",
    "summary": "toy slice complete",
    "changed_files": ["README.md"],
    "validation": [{"command": "toy validation", "result": "pass", "notes": ""}],
    "drift_audit": {"verdict": "PASS", "path": "drift-audit.md"},
    "code_review": {"verdict": "PASS", "path": "code-review.md"},
    "commit": {"requested": True, "created": True, "hash": commit_hash},
    "next_action": "",
    "blockers": []
}), encoding="utf-8")
time.sleep(5)
PY
python3 skills/master-controller/scripts/mc.py init --repo "$tmp" --plan "$tmp/plan.md" --harness codex
python3 skills/master-controller/scripts/mc.py run-next --repo "$tmp" --dry-run
python3 skills/master-controller/scripts/mc.py run-next --repo "$tmp" --harness-command "python3 $tmp/fake_harness.py" --timeout-seconds 10 --poll-seconds 0.1
python3 skills/master-controller/scripts/mc.py summarize --repo "$tmp"
```

This trial creates only `.ai-mc/` state and the authorized toy commit inside the temporary repo. The `--harness-command` override is for controlled local validation; normal runs use the command named by `--harness`.
