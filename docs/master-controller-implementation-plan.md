# Master Controller Implementation Plan

## Objective

Build a master controller (MC) that executes an already-approved implementation plan one slice at a time by launching a fresh tmux session running an AI coding harness as the orchestrator, supervising that session from outside, enforcing quality and authorization gates, committing only passing slices, recording durable logs, then repeating for the next eligible slice.

Planning is explicitly out of scope for MC. The MC consumes a plan produced by `implementation-plan`; if the plan is missing, incomplete, ambiguous, or requires material amendment, MC stops and reports that the plan must be fixed in a separate planning step.

Docker/container setup is out of scope for this implementation. The MC must work in a normal local environment and inside a correctly configured container, but it must not create, configure, or assume responsibility for the container. All filesystem, git, tmux, harness, and credential access are treated as properties of the environment in which MC is invoked.

## Design Principles

- MC is a supervisor, not an implementer and not a planner.
- Each slice runs in a fresh tmux session to reduce context drift and make per-slice logs clean.
- The orchestrator inside tmux uses the existing Mode A-style workflow, but the MC is the checkpoint authority for low-risk gates that are explicitly pre-authorized by the plan.
- Human approval is still required for approval-gated slices, ambiguous or risky failures, destructive operations, secrets, dependency/license changes, and any condition outside the configured policy.
- MC decisions must not rely only on natural-language transcript interpretation. The orchestrator must produce a structured slice receipt, and MC must independently verify git state, changed files, validation claims, drift verdict, review verdict, and commit state.
- Local and container execution should share the same code path. Environment differences should be represented as configuration and preflight checks, not separate MC modes.

## Proposed Repository Shape

```text
skills/master-controller/
  SKILL.md
  README.md
  scripts/
    mc.py
  references/
    run-state-schema.md
    orchestrator-prompt.md
    harness-adapter-contract.md
```

The skill owns the user-facing contract and safety rules. The script owns mechanical execution, tmux control, run-state persistence, gate checks, and summaries.

## Run State Model

Each target project gets local state under `.ai-mc/`, which should usually be gitignored by target projects:

```text
.ai-mc/
  current -> runs/<timestamp>
  runs/
    <timestamp>/
      run.json
      run.log
      slices/
        slice-001/
          prompt.md
          orchestrator-result.json
          transcript.txt
          pane-capture.txt
          git-status-before.txt
          git-status-after.txt
          git-diff.patch
          validation-summary.md
          drift-audit.md
          code-review.md
          commit.txt
```

`run.json` tracks plan path, repo path, branch/worktree, policy, harness, current slice, state transitions, commit hashes, and stop reasons. Slice directories are append-only audit records.

## Orchestrator Result Contract

Every orchestrator session must end by writing or printing a structured result. Prefer a file in the slice artifact directory; allow transcript fallback only for early prototypes.

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

Allowed `status` values: `pass`, `repairable`, `needs-human`, `fail`, `blocked`.

## Gate Policy

MC may advance to the next slice only when all of these are true:

- The plan exists and the selected slice has a frozen contract.
- The slice is not marked approval-needed, unless the user explicitly configured MC to stop after reporting it.
- Starting git state is clean or matches an explicitly allowed dirty-state policy.
- Actual changed files are inside the authorized surface.
- Required validation was run and passed, or the plan explicitly allows skipped validation with a reason.
- Drift audit verdict is `PASS`.
- Code review verdict is `PASS`, or only preconfigured non-blocking residuals remain.
- Commit was created with the commit skill or equivalent approved commit contract.
- Post-commit worktree is clean.

MC must stop with `needs-human`, `fail`, or `blocked` on:

- Missing or ambiguous plan/slice contract.
- Approval-needed slice.
- Unauthorized changed files or behaviour drift.
- Required validation failure.
- Drift audit `FAIL`, `BLOCKED`, or unresolved `PASS WITH RISKS`.
- Code review `FAIL`, P0/P1 finding, or unresolved material P2 finding.
- Harness failure, tmux failure, timeout, or inability to capture transcript/artifacts.
- Any proposed destructive filesystem action outside the target repo/worktree.
- Any secret exposure, credential prompt, dependency/license change, remote push, release, deploy, or external side effect not explicitly authorized.

## Harness Assumptions

Harness support should be adapter-based. The first adapter can be intentionally narrow, but the MC core should not hardcode one harness everywhere.

Each harness adapter defines:

- Preflight command.
- How to start an interactive tmux session in a repo/worktree.
- How to send the initial prompt.
- How to detect activity.
- How to capture transcript or pane output.
- How to request graceful stop.
- How to detect completion markers.

The implementation may start with one adapter, but the state schema and CLI should accept `--harness <name>` from the beginning.

## CLI Shape

```bash
mc init --repo <path> --plan <path> --harness <name> [--worktree-root <path>]
mc run-next --run <path-or-current>
mc run --run <path-or-current> --scope remaining
mc status --run <path-or-current>
mc stop --run <path-or-current>
mc summarize --run <path-or-current>
```

For early implementation, `run-next` is the most important command. `run --scope remaining` should simply loop over `run-next` until complete or stopped.

## Plan Review Decision

The first version of this plan was over-sliced into eight slices. That was too conservative for a frontier model or senior human implementer because it separated tightly coupled documentation, schema, parser, runtime, gate, and end-to-end work into too many review loops. The useful safety boundaries are not "one file group per slice"; they are the transition from contract/schema to executable runtime, and then from runtime to autonomous loop/gate acceptance.

This revised plan keeps four atomic slices for weaker or less trusted implementers, but recommends two implementation batches for frontier models.

## Implementation Profiles

- Recommended for frontier/senior implementer: run Batch A, then Batch B. Each batch gets one drift audit and one code review.
- Recommended for standard strong implementer: run the four atomic slices individually unless the implementer explicitly confirms the selected batch contract.
- Recommended for weaker implementer: run atomic slices one at a time with no batching.

## Slice Batches

- Batch A: Slices 1-2 — define the MC contract, references, state schema, initial CLI, and plan eligibility checks. These are tightly coupled and low risk; one review can assess whether the contract and parser agree.
- Batch B: Slices 3-4 — implement tmux execution, structured result capture, gate verification, looping/resume/summary, docs, and a safe toy end-to-end trial. These are runtime-coupled; one review can assess the full execution path.

The minimum recommended frontier implementation is two batches. A single all-in-one pass is possible for a very strong model, but it is not the default because tmux supervision, git gate verification, and autonomous loop control are easier to review after the contract/parser foundation exists.

## Atomic Slices

## Slice 1: Define Skill Contract and Reference Docs

### Intended Change
- Add `skills/master-controller/SKILL.md` and `skills/master-controller/README.md`.
- Define MC purpose, trigger conditions, preconditions, safety model, gate policy, and relationship to `implementation-plan`, `ai-orchestrator`, `drift-audit`, `code-review`, `commit`, and `handoff`.
- State clearly that MC consumes an existing plan and does not generate or materially modify plans.
- State clearly that Docker/container setup is out of scope; MC runs in whatever local or container environment the user invoked.
- Add `references/run-state-schema.md`, `references/orchestrator-prompt.md`, and `references/harness-adapter-contract.md` under `skills/master-controller/`.
- Define durable JSON fields for `run.json` and `orchestrator-result.json`.
- Define the exact prompt MC sends to a slice orchestrator, including required skills, Mode A-style workflow, structured result file, and stop conditions.
- Define a harness adapter interface for tmux-backed sessions.
- Add the new skill to the maintained root README skill list.

### Acceptance Criteria
- Inputs: A user request to run or supervise an existing implementation plan.
- Outputs: Skill and reference instructions sufficient to implement `mc.py` without inventing gate semantics.
- User-visible behaviour: MC is documented as a plan executor/supervisor, not a planner; the orchestrator prompt treats MC as checkpoint authority only inside explicit policy.
- Behaviour that must not change: Existing skills and workflows continue to work independently.

### Authorized Surface
- Files allowed to change:
  - `README.md`
  - `skills/master-controller/SKILL.md`
  - `skills/master-controller/README.md`
  - `skills/master-controller/references/run-state-schema.md`
  - `skills/master-controller/references/orchestrator-prompt.md`
  - `skills/master-controller/references/harness-adapter-contract.md`
- Functions/classes/components allowed to change: none.
- Tests allowed or expected to change: none.

### Explicit Non-Goals
- Do not implement runtime code.
- Do not change existing skill semantics.
- Do not add Docker setup or policies.

### Risk Flags
- Risky surfaces touched: Global skill discovery documentation in `README.md`.
- Approval needed before implementation: no, if the root README only adds the new skill to the maintained skill list.

### Validation Plan
- Tests to add/update: none.
- Commands to run:
  - `git diff --check`
  - Verify root README skill list matches non-system skill directories.
- Manual checks:
  - Confirm skill text preserves the separate planning phase.
  - Confirm Docker is environment-only, not implemented here.
  - Schema has enough data to resume, audit, and summarize.
  - Prompt requires structured result output.
  - Adapter contract separates MC core from harness specifics.

### Rollback Path
- Remove `skills/master-controller/` and revert the README row.

## Slice 2: Implement CLI State, Plan Discovery, and Eligibility Checks

### Intended Change
- Add `skills/master-controller/scripts/mc.py`.
- Implement `init`, `status`, and `summarize` without launching tmux.
- Parse and validate CLI arguments.
- Create `.ai-mc/runs/<timestamp>/run.json` in the target repo.
- Resolve repo path, plan path, harness name, branch, and optional worktree root.
- Record environment preflight data without requiring Docker-specific checks.
- Extend `mc.py` to identify the next eligible slice from a plan file.
- Support a conservative markdown parser for the current implementation-plan receipt shape.
- Detect missing sections, approval-needed risk flags, incomplete authorized surface, and completed slices from run state.
- Add `run-next --dry-run` to report the next slice and whether MC can run it.

### Acceptance Criteria
- Inputs: `mc init --repo <repo> --plan <plan> --harness <harness>` and plan markdown following the existing slice receipt format.
- Outputs: Durable run directory with valid `run.json` and `current` symlink; `run-next --dry-run` identifies the next runnable slice or stops with a precise reason.
- User-visible behaviour: `mc status` reports run state, `mc summarize` reports no slices run yet, and MC refuses to run incomplete or approval-gated slices.
- Behaviour that must not change: No target repo files outside `.ai-mc/` are modified; MC does not modify the plan file.

### Authorized Surface
- Files allowed to change:
  - `skills/master-controller/scripts/mc.py`
  - `skills/master-controller/references/run-state-schema.md`
  - `skills/master-controller/README.md`
- Functions/classes/components allowed to change: MC parser/state functions only.
- Tests allowed or expected to change:
  - Add unit tests for run state creation if a test file is introduced.
  - Add parser tests with representative plan snippets.

### Explicit Non-Goals
- Do not launch tmux.
- Do not run or commit code.
- Do not create Docker/container integration.
- Do not repair plan files.
- Do not infer authority when required plan sections are missing.

### Risk Flags
- Risky surfaces touched: filesystem state creation under target repos; plan parsing and gate eligibility.
- Approval needed before implementation: no, because writes are confined to `.ai-mc/` and policy is fail-closed.

### Validation Plan
- Tests to add/update:
  - Run state creation.
  - Runnable slice.
  - Approval-needed slice.
  - Missing authorized surface.
  - Multiple slices with first completed in state.
- Commands to run:
  - `python3 -m py_compile skills/master-controller/scripts/mc.py`
  - Targeted test command for parser tests.
  - Run `mc.py init/status/summarize/run-next --dry-run` against a temporary toy repo.
- Manual checks:
  - Confirm generated files are under `.ai-mc/`.
  - Confirm paths are absolute in `run.json`.
  - Confirm parsing failure messages tell the user to fix the plan separately.

### Rollback Path
- Remove `skills/master-controller/scripts/mc.py` and any test files added in this slice.

## Slice 3: Implement Tmux Slice Execution and Structured Result Capture

### Intended Change
- Implement tmux session creation, prompt injection, pane capture, session termination, and basic activity monitoring for one initial harness adapter.
- Generate the orchestrator prompt from `references/orchestrator-prompt.md`.
- Run one slice in a fresh tmux session in a toy repo.
- Require the orchestrator to write `orchestrator-result.json` in the slice artifact directory.

### Acceptance Criteria
- Inputs: Existing run state and a runnable slice.
- Outputs: Slice artifact directory with prompt, pane capture/transcript, git status before/after, and orchestrator result.
- User-visible behaviour: `mc run-next` can supervise one safe toy slice and stop with a structured state.
- Behaviour that must not change: MC does not continue to the next slice automatically in this slice.

### Authorized Surface
- Files allowed to change:
  - `skills/master-controller/scripts/mc.py`
  - `skills/master-controller/references/orchestrator-prompt.md`
  - `skills/master-controller/references/harness-adapter-contract.md`
  - `skills/master-controller/README.md`
- Functions/classes/components allowed to change: MC runtime and adapter code only.
- Tests allowed or expected to change:
  - Add tests for prompt rendering and tmux command construction where practical.

### Explicit Non-Goals
- Do not implement multiple harnesses.
- Do not implement Docker setup.
- Do not commit target repo changes yet.
- Do not run against a non-toy repo for validation.

### Risk Flags
- Risky surfaces touched: process control and tmux sessions.
- Approval needed before implementation: no, if validation uses a temporary toy repo and does not touch external projects.

### Validation Plan
- Tests to add/update:
  - Prompt rendering test.
  - Adapter command construction test.
- Commands to run:
  - `python3 -m py_compile skills/master-controller/scripts/mc.py`
  - Targeted unit tests.
  - Manual toy `mc init` and `mc run-next`.
- Manual checks:
  - Confirm tmux session is closed after completion or stop.
  - Confirm artifact files are captured.
  - Confirm timeout path preserves logs.

### Rollback Path
- Revert tmux adapter code and prompt changes.

## Slice 4: Implement Gate Verification, Looping, Resume, Summary, and Documentation

### Intended Change
- Add MC-side verification of orchestrator claims before accepting a slice.
- Compare `orchestrator-result.json` to `git status`, `git diff`, plan authorized surface, validation results, drift verdict, and review verdict.
- Add gate states: `pass`, `repairable`, `needs-human`, `fail`, `blocked`.
- Allow at most one configured repair attempt for `repairable` states.
- Implement `mc run --scope remaining`, `mc stop`, and robust resume semantics.
- Ensure every slice starts a fresh tmux session and writes a slice summary.
- Generate a final run summary with slice statuses, commits, validation, gate verdicts, and human-needed stops.
- Update `skills/master-controller/README.md` with usage examples and safety expectations.
- Add a documented safe toy workflow for local testing.
- Run an end-to-end safe task on a temporary toy repo with two slices and no container.

### Acceptance Criteria
- Inputs: Completed slice artifact directory, target repo git state, and a multi-slice toy plan.
- Outputs: MC gate decision recorded in `run.json`; MC runs eligible slices until complete or stopped; summary is reproducible from `.ai-mc/`; docs explain safe local usage.
- User-visible behaviour: MC refuses to advance on unauthorized files, missing validation, missing audit/review, dirty post-commit state, or approval-needed slices. A failed or human-needed slice stops the loop without losing artifacts.
- Behaviour that must not change: Orchestrator remains responsible for implementing, auditing, reviewing, and committing inside the slice prompt; MC does not skip slices or reorder them.

### Authorized Surface
- Files allowed to change:
  - `skills/master-controller/scripts/mc.py`
  - `skills/master-controller/README.md`
  - `skills/master-controller/SKILL.md`
  - `skills/master-controller/references/*.md`
- Functions/classes/components allowed to change: MC gate, run loop, summary, and CLI help code only.
- Tests allowed or expected to change:
  - Gate, resume, loop, and summary tests where practical.

### Explicit Non-Goals
- Do not implement semantic code review inside MC.
- Do not make MC parse full natural-language review output beyond verdict fields.
- Do not permit approval-gated slices to proceed.
- Do not design or implement Docker.
- Do not add remote push/PR/release behavior.
- Do not add science workflow gates yet.

### Risk Flags
- Risky surfaces touched: commit acceptance, autonomous loop control, and run advancement.
- Approval needed before implementation: no, because policy is fail-closed and validation remains on toy repos.

### Validation Plan
- Tests to add/update:
  - Unauthorized changed file.
  - Missing validation.
  - Drift `PASS WITH RISKS`.
  - Review `FAIL`.
  - Clean passing committed slice.
  - Two passing toy slices.
  - Stop on approval-needed second slice.
  - Resume after first slice complete.
  - Stop command records cancelled state.
- Commands to run:
  - `python3 -m py_compile skills/master-controller/scripts/mc.py`
  - Targeted MC tests.
  - Manual toy run with an intentionally unauthorized file.
  - End-to-end toy run.
- Manual checks:
  - Confirm gate explanations are actionable.
  - Confirm every slice has its own tmux session and artifact directory.
  - Confirm final summary can be generated after process restart.
  - Confirm docs do not imply MC generates plans.
  - Confirm docs do not imply MC provides Docker safety.

### Rollback Path
- Revert gate verification, loop/resume/summary, and docs changes.

## Future Work Outside This Plan

- Container runtime design and validation.
- Science workflow gates: provenance, reproducibility, artifact hashing, reference data, numerical tolerances, and mandatory human scientific interpretation gates.
- Multiple harness adapters beyond the first one.
- Remote GitHub PR creation or CI monitoring.
- Web UI or dashboard.
- Parallel slice execution. Initial MC must stay sequential.

## Next Chat Prompt

```md
Plan file: /Users/dcroton/Documents/AI Tools/docs/master-controller-implementation-plan.md
Slices or batch this session: Batch A

Read the full plan file first. If the selected slice or batch receipt is incomplete or the plan state is unclear, stop and tell me before coding.

Work on the current feature branch for this plan; if none exists, create one and tell me the name.

Use ai-orchestrator as the controlling skill only if delegation materially improves quality or context economy. Keep the implementation local unless an independent review, hostile drift audit, or long-running test is worth delegating.

For the selected batch:
1. Restate the frozen contract (authorized surface + non-goals) from the plan.
2. If any included slice's Risk Flags mark approval-needed, stop and get my approval before coding.
3. Apply scoped-implementation against the selected contract.
4. Apply drift-audit. Report the authorization gate result before any quality review.
5. If the gate passes, apply code-review. If it fails, fix the drift and re-audit.
6. Surface drift and review findings to me, fix them, then re-run the relevant gate.
7. Ask me before committing. On my approval, commit the selected batch with the commit skill.

After the selected batch is committed, use handoff to record state and the next batch or slice to resume from. Do not continue past the selected batch.

Confirm before starting: plan file read, selected batch, branch, and the first slice.
```
