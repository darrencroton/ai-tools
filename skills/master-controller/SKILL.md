---
name: master-controller
description: Supervise execution of an existing implementation-plan by running eligible slices one at a time, preserving durable state, enforcing authorization and quality gates, and stopping for human approval when policy requires it.
---

# Master Controller

Use this skill when the user wants a controller to execute an already-approved implementation plan one slice at a time. The master controller (MC) is a supervisor backed by deterministic tools: it creates durable run state, launches a fresh orchestrator session for each eligible slice, observes or records harness evidence, verifies the orchestrator's claims, commits only passing slices, and stops when a human decision is required.

MC supports two operating styles. In model-supervised mode, the MC model stays in the loop for operational judgment while deterministic commands own exact state transitions, tmux control, artifact capture, and gate verification. In deterministic batch mode, MC runs conservatively without live model judgment and stops fail-closed when evidence is missing or unclear.

Do not use this skill to create, repair, broaden, or materially amend an implementation plan. Planning belongs to `implementation-plan`. If the plan is missing, ambiguous, incomplete, or needs material edits, MC stops and reports that a separate planning step is required.

## Preconditions

Before running MC, confirm:

- The plan file exists and contains frozen slice contracts.
- The target repo path is a git worktree.
- The current branch is the intended feature branch.
- Required local tools for the selected operation are available.
- The selected harness is configured for this environment.
- The starting worktree is clean, unless the user has explicitly authorized a dirty-state policy.

Docker and container setup are out of scope. MC may run inside a container or on a host machine, but it does not create, configure, or rely on container isolation.

## Workflow

1. **Initialize** - create `.ai-mc/runs/<timestamp>/run.json` in the target repo and update `.ai-mc/current`.
2. **Check eligibility** - parse the plan, identify the next uncompleted slice, and fail closed on missing sections, approval-needed risk flags, or incomplete authorized surfaces.
3. **Run or supervise one slice** - launch a fresh tmux-backed harness session for one eligible slice. In model-supervised operation, keep the MC model in the loop to observe live pane/log/json/git evidence and choose safe operational actions.
4. **Capture artifacts** - preserve prompt, transcript or pane capture, git status, diff, validation summary, drift audit, code review, commit data, operational events when present, and structured orchestrator result.
5. **Verify gates** - independently compare the orchestrator result to git state, plan authorization, validation, drift audit verdict, review verdict, and commit state.
6. **Advance or stop** - move to the next slice only when every gate passes. Stop with a precise reason for human approval, drift, failed validation, failed review, harness failure, or incomplete evidence.

The CLI supports state creation, dry-run eligibility checks, one-slice tmux execution, model-supervised observing/sending/waiting/pausing/finalizing, structured artifact capture, MC-side gate verification, sequential remaining-slice execution, cancellation, and summaries. Keep deterministic acceptance gates unchanged: model-supervised primitives provide operational control and evidence, not acceptance.

## Default Operating Path

When the user gives MC a complete implementation plan and asks to implement it, do not require them to restate the whole launch recipe. First decide whether the request needs model-supervised operational judgment or deterministic batch execution. Use model-supervised operation when live recovery from quota/session/service interruptions matters; use deterministic batch execution for simple fail-closed runs.

Current deterministic batch path:

1. Use `codex` as the default orchestrator harness when no harness is specified. `claude`, `copilot`, and `opencode` are also validated orchestrator harnesses (see `references/harness-adapter-contract.md`) — use one of them when the user names it explicitly, or when the task and configured model make it the better functional fit per `ai-orchestrator`'s role definitions.
2. Initialize an MC run if `.ai-mc/current` is missing or is for a different plan; otherwise reuse the current run after checking status. When the user has authorized a specific branch and it is not already current, pass `--branch <name>`; add `--create-branch` only when branch creation is explicitly authorized.
3. Run `preflight` before the first slice. Include `--worker-tools <tool[,tool]>` when the plan or user requires workers, and include `--allow-profile-command` for normal local execution.
4. Run `run-next --dry-run` and confirm the selected slice is eligible.
5. If the user requested one slice, run `run-next`. If the user requested the plan or all remaining work and deterministic batch execution is appropriate, run `run --scope remaining`.
6. After the run stops or completes, run `summarize`, inspect `run.json`, inspect the selected slice artifact directories, and check git status before reporting.

Model-supervised path:

1. Initialize or reuse the run, run preflight, and dry-run the next slice. When the user has authorized a specific branch and it is not already current, initialize with `--branch <name>` and, if explicitly authorized, `--create-branch`.
2. Start the next eligible slice with `start-slice`; do not use `run --scope remaining` for model-supervised operation.
3. Observe live pane/log/json/git evidence with `observe` or bounded `wait` on a calm cadence.
4. If the harness reports a clear rolling 5-hour usage reset and the process is still alive, pause until reset plus buffer with `pause-until`, re-observe for hard-stop prompts, then send a short continuation prompt with `send`.
5. If a rolling-limit message appears after the harness process exits before a structured result, restart only from a clean authorized state or stop with evidence; do not send into the old session.
6. If a structured result appears, finalize through deterministic MC gates with `finalize-slice` before advancing.
7. Stop with evidence for weekly, monthly, account, billing, credential, trust, permission, dependency/license, remote-side-effect, destructive-action, approval-gated, ambiguous, or policy-sensitive conditions.

Ask the user only when required information cannot be inferred safely, such as the target repo, plan path, intended branch, whether to run one slice or all remaining slices, or whether an approval-gated slice should proceed. Do not ask the user to hand-compose harness sandbox flags; use MC profiles and preflight instead.

## Safety Rules

MC must stop on:

- Missing or ambiguous plan/slice contract.
- Approval-needed slice without explicit user approval.
- Dirty starting git state outside configured policy.
- Unauthorized changed files or behaviour drift.
- Required validation failure or missing validation evidence.
- Drift audit `FAIL`, `BLOCKED`, or unresolved `PASS WITH RISKS`.
- Code review `FAIL`, any P0/P1 finding, or unresolved material P2 finding.
- Harness, tmux, terminal timeout, transcript, or artifact capture failure.
- Any proposed destructive filesystem action outside the target repo/worktree.
- Secret exposure, credential prompt, dependency/license change, remote push, release, deploy, or external side effect not explicitly authorized.
- Weekly, monthly, account, or billing usage caps.
- Unknown or ambiguous usage-limit messages without a clear bounded reset.
- Ambiguous operational interruptions after reasonable observation.

MC may recover from a rolling 5-hour usage window, temporary service interruption, or similar transient only when pane/log evidence is clear, the recovery is bounded, the same slice contract remains in force, no hard-stop prompt is visible, and incomplete work is not accepted as passing. Operational screen text can guide wait, retry, resume, or stop decisions; it can never accept a slice.

`observe` and `wait` include lightweight `operational_hints` extracted from pane and transcript tails. Treat ordinary hints as evidence for MC-model judgment, not as automatic decisions. Treat hard-stop hints as a deterministic floor: `send`, `pause-until`, and any unattended retry/resume must refuse when the visible evidence indicates weekly, monthly, account, billing, unknown-limit, auth, trust, permission, or external-side-effect conditions. Prefer relative reset durations over absolute local times; absolute local reset times are usable only when they are unambiguously near-future for the controller timezone or include an explicit timezone.

MC decisions must not rely only on natural-language transcript interpretation. The orchestrator must produce `orchestrator-result.json`, and MC must verify claims against local evidence.

Trust boundary for audit verdicts: MC recomputes the highest-risk gate itself — the set of changed files against the authorized surface, using segment-aware matching so `*.md` does not cross directory boundaries — and it independently verifies commit ancestry, HEAD advancement, and a clean post-commit worktree. For the drift-audit and code-review gates, MC verifies the reported verdict string plus the existence of a non-empty artifact file inside the run; it does not re-derive those verdicts from the transcript. A dishonest orchestrator that both writes `"verdict": "PASS"` and fabricates a non-empty artifact is therefore outside what MC detects, by design. The mitigation is that the file-level authorization gate — the change most likely to cause real harm — is always MC's own computation, never the orchestrator's claim.

## Relationship To Other Skills

- `implementation-plan`: produces the plan MC consumes. MC does not plan.
- `ai-orchestrator`: may run inside the tmux session as the slice orchestrator when delegation improves quality or context economy.
- `scoped-implementation`: used by the orchestrator to implement one frozen slice.
- `drift-audit`: required before quality review; MC treats the verdict as an authorization gate and verifies the evidence exists.
- `code-review`: required after drift audit passes; MC treats unresolved material findings as blocking.
- `commit`: used by the orchestrator for passing slices. MC verifies the commit state.
- `handoff`: records stop state and the next slice when a run cannot continue.

## Commands

```bash
python3 skills/master-controller/scripts/mc.py init --repo <path> --plan <path> --harness <name>
python3 skills/master-controller/scripts/mc.py init --repo <path> --plan <path> --harness <name> --branch <branch> --create-branch
python3 skills/master-controller/scripts/mc.py profiles
python3 skills/master-controller/scripts/mc.py preflight --repo <path> --worker-tools <tool[,tool]> --allow-profile-command
python3 skills/master-controller/scripts/mc.py status --repo <path>
python3 skills/master-controller/scripts/mc.py summarize --repo <path>
python3 skills/master-controller/scripts/mc.py run-next --repo <path> --dry-run
python3 skills/master-controller/scripts/mc.py run-next --repo <path> --worker-tools <tool[,tool]> --allow-profile-command
python3 skills/master-controller/scripts/mc.py run-next --repo <path> --harness-model <model> --harness-effort <effort> --worker-tools <tool[,tool]> --worker-model <model> --worker-effort <effort> --allow-profile-command
python3 skills/master-controller/scripts/mc.py run --repo <path> --scope remaining --worker-tools <tool[,tool]> --allow-profile-command
python3 skills/master-controller/scripts/mc.py start-slice --repo <path> --worker-tools <tool[,tool]> --allow-profile-command
python3 skills/master-controller/scripts/mc.py observe --repo <path>
python3 skills/master-controller/scripts/mc.py wait --repo <path> --seconds <n>
python3 skills/master-controller/scripts/mc.py send --repo <path> --text <text> --reason <reason>
python3 skills/master-controller/scripts/mc.py pause-until --repo <path> --until <iso-timestamp-with-timezone> --reason <reason>
python3 skills/master-controller/scripts/mc.py finalize-slice --repo <path>
python3 skills/master-controller/scripts/mc.py stop-with-evidence --repo <path> --reason <reason>
python3 skills/master-controller/scripts/mc.py reconcile --repo <path>
python3 skills/master-controller/scripts/mc.py stop --repo <path> --reason <reason>
python3 skills/master-controller/scripts/mc.py archive-sensitive --repo <path> --dry-run
```

Model-supervised primitives are `observe`, `send`, `wait`, `pause-until`, `start-slice`, `finalize-slice`, and `stop-with-evidence`. They preserve the same trust boundary: the MC model may reason over operational evidence, but acceptance still requires deterministic local gates.

Runtime commands require `tmux`, the selected harness command, and a clean target worktree outside MC's `.ai-mc/` audit directory. MC starts a fresh tmux session for every slice and stops rather than advancing when evidence is missing or a gate fails and cannot be safely reconciled from local evidence.

When all other slice gates pass and the only defect is an incorrect or abbreviated reported `commit.hash`, MC may correct `orchestrator-result.json` to the proven current `HEAD`, write `mc-reconciliation.json` / `mc-reconciliation.md`, and accept the slice. This recovery is allowed only when local git evidence proves the commit advanced from the slice starting point, changed files match the authorized surface and reported result, validation/drift/review artifacts pass, and the post-commit worktree is clean.

For a run that already stopped on a recoverable evidence problem, use `reconcile` to re-run MC's local gates against the stopped slice and update run state only when the same strict reconciliation criteria pass.

## References

- `references/run-state-schema.md`
- `references/orchestrator-prompt.md`
- `references/harness-adapter-contract.md`
