---
name: master-controller
description: Supervise execution of an existing implementation-plan by running eligible slices one at a time, preserving durable state, enforcing authorization and quality gates, and stopping for human approval when policy requires it.
---

# Master Controller

Use this skill when the user wants a controller to execute an already-approved implementation plan one slice at a time. The master controller (MC) is a supervisor backed by deterministic tools: it creates durable run state, launches one orchestrator harness per slice, observes or records harness evidence, verifies the orchestrator's claims against objective evidence, and — when its verification finds a fixable gap — steers the live orchestrator session back onto the correct path through a bounded self-correcting repair loop. Only when the orchestrator repeatedly fails to satisfy the same gate, exhausts the repair budget, or breaches integrity does MC hard-stop and wait for a human.

MC supports two operating styles. In model-supervised mode, the MC model stays in the loop for operational judgment while deterministic commands own exact state transitions, tmux control, artifact capture, and gate verification. In deterministic batch mode, MC runs conservatively without live model judgment and stops fail-closed when evidence is missing or unclear.

Do not use this skill to create, repair, broaden, or materially amend an implementation plan. Planning belongs to `implementation-plan`. If the plan is missing, ambiguous, incomplete, or needs material edits, MC stops and reports that a separate planning step is required.

## Roles

- **Master Controller (MC)** — the deterministic supervisor. Owns run state, launches exactly one orchestrator harness per slice, and verifies every claim against objective evidence (git, artifacts) rather than trusting a self-report. Drives the bounded self-correcting repair loop by surfacing gate violations back into the live orchestrator session. Holds sole authority to hard-stop for a human when a threshold is crossed (repair budget exhausted, integrity breach, approval-gated slice). MC never writes slice code and never delegates to a worker itself.
- **Orchestrator** — the harness in tmux (codex / claude / opencode / copilot). Executes one slice under MC's supervision by applying `scoped-implementation` → validation → `drift-audit` → `code-review` → `commit`, self-correcting within its own run first, and delegating bounded sub-tasks to workers. Reports a structured result but holds no final authority; MC verifies it. On an MC-surfaced correction it fixes the specific gap using the context it already built. It may not expand scope, push, deploy, or perform external side effects.
- **Worker** — a bounded, single-purpose helper the orchestrator launches via `ai-orchestrator`/`worker_jobs.py` for narrow sub-tasks. Owns no gates, never commits, never re-delegates. Its launch mechanics belong to `ai-orchestrator`; MC only verifies mechanical evidence that a required worker actually ran.

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
5. **Verify gates** - independently compare the orchestrator result to git state, plan authorization, validation, drift audit verdict, review verdict, commit state, and — when the run specifies `--worker-tools` — mechanical evidence that a required worker was actually launched through `worker_jobs.py` with a matching executable and completed successfully (`worker-evidence.md` plus `worker-runs-summary.json` status `completed` with returncode 0), not just the orchestrator's own narration about the worker. An orchestrator that skips a required worker, mislabels a different executable, or self-reports the gap does not pass; it fails the gate like any other unmet contract requirement. Every non-pass outcome is classified with a stable failure signature as either `repairable` (a fixable gap — validation, drift, review, worker evidence, unauthorized files, changed-files bookkeeping, malformed result, missing commit, dirty worktree) or terminal `needs-human` (an integrity/trust breach — HEAD did not advance or is not descended from the slice start, or the orchestrator worked the wrong slice).
6. **Repair, advance, or stop** - on a repairable gate with budget remaining, MC archives the stale result, writes a targeted repair prompt naming the exact violation, surfaces it into the **live** orchestrator session (preserving the context the orchestrator already built), and re-verifies the complete, unrelaxed gate when a fresh result lands — a repair can never lower the bar, only grant another chance to clear the identical one. A signature-keyed circuit breaker bounds the loop: first failure earns an in-session correction, the same signature failing again earns exactly one fresh-session retry with the original frozen prompt, and a third consecutive failure is terminal regardless of remaining budget (`policy.max_repair_attempts`, default 3). MC advances to the next slice only when every gate passes, and stops with a precise reason for human approval, integrity breach, exhausted budget, harness failure, or incomplete evidence.

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
7. When `finalize-slice` returns `"status": "repairable"` with `"mode": "in-session"`, the session is still alive and `current_slice` remains populated: deliver the returned `send_text` (a one-line pointer to the rendered `repair-prompt-repair-<round>.md`) into the live session with `send`, `wait` for a fresh result, then `finalize-slice` again. With `"mode": "fresh-session"` or `"mode": "relaunch"`, finalize has already relaunched a new session for the same slice itself — just `wait` and re-finalize. Budget and the same-signature circuit breaker are enforced deterministically from persisted `current_slice.repair`; finalize goes terminal on its own when they trip. Because each command is a separate invocation, always pass the same launch flags (`--harness-command` or `--allow-profile-command`, `--harness-model`/`--harness-effort`) to `finalize-slice` that were used at `start-slice`: a fresh-session relaunch composes its launch command from the current invocation's flags.
8. Do not use `run-next` / `run --scope remaining` while a model-supervised slice is live (they refuse when `current_slice` is populated); finish it through wait/send/finalize or stop it explicitly first.
9. Stop with evidence for weekly, monthly, account, billing, credential, trust, permission, dependency/license, remote-side-effect, destructive-action, approval-gated, ambiguous, or policy-sensitive conditions.

Ask the user only when required information cannot be inferred safely, such as the target repo, plan path, intended branch, whether to run the next slice or all remaining slices, or whether an approval-gated slice should proceed. Do not ask the user to hand-compose harness sandbox flags; use MC profiles and preflight instead.

Approval-gated slices: when MC stops on an approval-needed slice and the user explicitly approves it, record that approval with `approve --slice "<Slice N>" --reason "<why>"` and re-run. The approval is stored in run state and logged as an operational event; it clears only an exact `yes` approval flag. Never edit the plan's approval flag to get past the gate — that changes the frozen digest and forces a fresh `init`. If a plan genuinely must be revised mid-run, re-`init` with `--assume-complete "<Slice 1,Slice 2>"` naming the slices already completed and committed, so the new run resumes at the right slice instead of re-running finished work.

## Long-Running Command Discipline

MC's blocking commands outlive the tool-call limits of the assistants that drive them (for example a 10-minute shell-tool cap). A foreground `run --scope remaining` (≈30 minutes per slice) or a multi-hour `pause-until` that is killed mid-call leaves run.json stuck at `running` with the tmux harness still editing the repo unsupervised; `status` warns when it detects this (active status, recorded tmux session gone).

- Run `run --scope remaining` and `run-next` in the background (or under `nohup`/a detached shell), then poll `status`/`summarize`.
- In model-supervised operation, prefer repeated bounded `wait --seconds <n>` calls that fit inside the tool limit (for example 240–540 seconds) over one long wait.
- For long pauses, prefer scheduling a later re-observe over a single blocking `pause-until` when the controller cannot safely block that long; `pause-until` remains correct when the controlling process genuinely can wait.
- For C1 runs on subscription harnesses, prefer an MC model on a different provider than the orchestrator harness: if both share one subscription, a usage window stalls the supervisor and the supervised session at the same time.

## Safety Rules

The safety invariant of the repair loop: MC re-runs the complete gate with unrelaxed rigor after every repair attempt, so a `repairable` classification can never let a bad slice through — the worst case of a generous repair is a wasted attempt followed by a hard stop at the cap. Fixable violations (failed/missing validation, non-PASS drift or review verdicts, missing worker evidence, unauthorized changed files — repaired restore-only, changed-files bookkeeping, malformed results, missing commits, dirty worktrees) are therefore steered in-session within the repair budget rather than stopping the run on first occurrence.

MC must stop, without attempting a repair, on:

- Missing or ambiguous plan/slice contract.
- Approval-needed slice without a recorded operator approval (`approve` command).
- Dirty starting git state outside configured policy.
- Integrity/trust breaches: a required commit that did not advance HEAD, a HEAD not descended from the slice starting commit, or a result reported for the wrong slice. These are never steered — continuing to reason from a context that already holds a false belief about reality is itself the risk.
- Repair budget exhaustion or a signature that keeps failing through the circuit breaker (in-session correction, then one fresh session, then terminal).
- A hard prompt on screen when a repair would be delivered (the delivery refuses; MC stops with evidence).
- Harness or tmux failure, terminal timeout, or missing required result evidence. (Pane/transcript capture failures degrade to recorded placeholder/note files rather than stopping the run; the structured result and git evidence remain the acceptance basis.)
- Any proposed destructive filesystem action outside the target repo/worktree.
- Secret exposure, credential prompt, dependency/license change, remote push, release, deploy, or external side effect not explicitly authorized.
- Weekly, monthly, account, or billing usage caps.
- Unknown or ambiguous usage-limit messages without a clear bounded reset.
- Ambiguous operational interruptions after reasonable observation.

MC may recover from a rolling 5-hour usage window, temporary service interruption, or similar transient only when pane/log evidence is clear, the recovery is bounded, the same slice contract remains in force, no hard-stop prompt is visible, and incomplete work is not accepted as passing. Operational screen text can guide wait, retry, resume, or stop decisions; it can never accept a slice.

`observe` and `wait` include lightweight `operational_hints` extracted from pane and transcript tails. Treat ordinary hints as evidence for MC-model judgment, not as automatic decisions. Treat hard-stop hints as a deterministic floor: `send`, `pause-until`, and any unattended retry/resume must refuse when the visible evidence indicates weekly, monthly, account, billing, unknown-limit, auth, trust, permission, or external-side-effect conditions. Prefer relative reset durations over absolute local times; absolute local reset times are usable only when they are unambiguously near-future for the controller timezone or include an explicit timezone.

MC decisions must not rely only on natural-language transcript interpretation. The orchestrator must produce `orchestrator-result.json`, and MC must verify claims against local evidence.

Trust boundary for audit verdicts: MC recomputes the highest-risk gate itself — the set of changed files against the authorized surface, using segment-aware matching so `*.md` does not cross directory boundaries — and it independently verifies commit ancestry, HEAD advancement, and a clean post-commit worktree. For the drift-audit, code-review, and validation gates, MC verifies the reported verdict/result fields plus the existence of a non-empty artifact file inside the run; it does not re-derive those verdicts from the transcript and it does not re-run validation commands. A dishonest orchestrator that both writes passing fields and fabricates non-empty artifacts is therefore outside what MC detects, by design. The mitigation is that the file-level authorization gate — the change most likely to cause real harm — is always MC's own computation, never the orchestrator's claim. Worker delegation is verified mechanically one step further: a required worker must appear in `worker_jobs.py`'s own manifest/status artifacts with a matching executable name **and** a successful completion (state `completed`, returncode 0) — launch alone, narration alone, or a crashed worker does not pass.

Be equally candid about the heuristic stops: the dependency/license, secret/credential, remote-push/deploy, and external-side-effect stop conditions are enforced by pane-marker detection plus the orchestrator prompt's prohibitions, not by mechanical inspection of the diff. A silent dependency edit inside an authorized file surface would pass the file-authorization gate; keeping such files out of authorized surfaces (or approval-gating slices that touch them) is the plan-level control.

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
python3 skills/master-controller/scripts/mc.py init --repo <path> --plan <path> --harness <name> --assume-complete "Slice 1,Slice 2" --max-repair-attempts 3
python3 skills/master-controller/scripts/mc.py approve --repo <path> --slice "Slice 3" --reason <why>
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

Runtime commands require `tmux`, the selected harness command, and a clean target worktree outside MC's `.ai-mc/` audit directory. MC starts one tmux session per slice and keeps it alive across in-session repair rounds; a fresh session is launched only for a circuit-breaker escalation or a dead-session relaunch. MC stops rather than advancing when evidence is missing or a gate fails and cannot be repaired within budget or safely reconciled from local evidence.

When all other slice gates pass and the only defect is an incorrect or abbreviated reported `commit.hash`, MC may correct `orchestrator-result.json` to the proven current `HEAD`, write `mc-reconciliation.json` / `mc-reconciliation.md`, and accept the slice. This recovery is allowed only when local git evidence proves the commit advanced from the slice starting point, changed files match the authorized surface and reported result, validation/drift/review artifacts pass, and the post-commit worktree is clean.

For a run that already stopped on a recoverable evidence problem, use `reconcile` to re-run MC's local gates against the stopped slice and update run state only when the same strict reconciliation criteria pass.

## References

- `references/run-state-schema.md`
- `references/orchestrator-prompt.md`
- `references/harness-adapter-contract.md`
