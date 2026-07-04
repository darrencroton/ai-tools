# Master Controller

Master Controller (MC) supervises execution of an already-approved implementation plan. It is not a planner and it is not an implementer. It runs one frozen slice at a time through an AI coding harness, records durable artifacts, verifies gates from outside the harness session, and stops whenever policy requires human approval.

The first implementation slice provides the contract, schemas, CLI state setup, plan discovery, and dry-run eligibility checks. Runtime tmux execution and gate verification are documented now but implemented in later slices.

## What MC Owns

- Creating `.ai-mc/runs/<timestamp>/run.json` under the target repo.
- Updating `.ai-mc/current` to the active run.
- Recording repo, branch, harness, plan, environment preflight, and policy.
- Parsing implementation-plan markdown conservatively enough to identify frozen slice contracts.
- Refusing incomplete, ambiguous, approval-gated, or unauthorized slices.
- Reporting the next eligible slice in `run-next --dry-run`.

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

Preview the next runnable slice:

```bash
python3 skills/master-controller/scripts/mc.py run-next --repo /path/to/repo --dry-run
```

For Batch A, `run-next` requires `--dry-run`; actual tmux execution is deferred. A dry run exits successfully only when the next uncompleted slice has the required sections, has a non-empty authorized file surface, and is not approval-gated.

## Run State

State is stored under the target repository:

```text
.ai-mc/
  current -> runs/<timestamp>
  runs/
    <timestamp>/
      run.json
```

Later runtime slices add per-slice artifacts under `.ai-mc/runs/<timestamp>/slices/<slice-id>/`.

Target projects should usually add `.ai-mc/` to their own `.gitignore`, but MC does not edit `.gitignore` as part of initialization.

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

Use a temporary git repo and a small plan before supervising real work:

```bash
tmp="$(mktemp -d)"
git -C "$tmp" init
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
python3 skills/master-controller/scripts/mc.py init --repo "$tmp" --plan "$tmp/plan.md" --harness codex
python3 skills/master-controller/scripts/mc.py run-next --repo "$tmp" --dry-run
```

This trial creates only `.ai-mc/` state inside the temporary repo.
