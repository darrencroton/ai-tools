# Master Controller — Holistic Review, Bug Hunt, and Hardening Report

- **Date:** 2026-07-05
- **Scope:** `skills/master-controller/` — `SKILL.md`, `README.md`, `references/` (3 docs), `scripts/mc.py`, all 15 modules in `scripts/mc_lib/` (~2,000 LOC), `tests/test_mc.py` (47 tests). Cross-skill surfaces included where they materially affect MC: the `ai-orchestrator` `worker_jobs.py` dependency and the orchestrator prompt contract.
- **Method:** Combined `/code-review` (full review matrix, evidence-based bug hunt) and `/code-simplifier` (tightening/consistency pass) over the whole skill, not a diff. Findings below were verified by running the code where practical; each verified finding is marked **[verified]** with the reproduction described.
- **Validation run:** Full unit suite (`python3 -m unittest discover -s skills/master-controller/tests`): **47 tests, all pass, 22.6s** (includes live tmux integration tests). Installed-tool flag check: `claude --permission-mode auto` and `codex --no-alt-screen -s … -a …` both confirmed valid against the locally installed CLIs. `worker_jobs.claude_project_root` confirmed present at the path MC imports.

## Verdict

**PASS WITH RISKS.** The architecture is sound and unusually well-defended for its age: fail-closed plan parsing, evidence-first gate verification, narrowly-scoped reconciliation, per-slice credential isolation with `0600` modes, and genuinely good tests including real tmux runs. No finding invalidates the design. However, three P1 issues undermine specific safety claims the docs make (approval gating, fail-closed gate verification, credential containment) and should be fixed before MC supervises real work on repos that matter.

---

## P1 Findings (fix before next real run)

### 1. Approval gate fails open on "not…" values — `scripts/mc_lib/models.py:66` **[verified]**

`PlanSlice.approval_needed` classifies with `value.startswith("no")` / `value.startswith("yes")`. Any value beginning with the letters "no" is treated as *no approval required*, which makes these all **eligible to run unattended**:

```
Approval needed before implementation: not yet decided        -> eligible
Approval needed before implementation: not required — ask first -> eligible
Approval needed before implementation: none                   -> eligible
```

(Verified by direct execution against `eligibility()`.)

This directly contradicts README.md's contract: *"the parser fails closed … when `Approval needed before implementation` is anything other than an explicit `no`."* A human writing "not yet decided" in a plan has flagged exactly the situation MC exists to stop on, and MC will run the slice. This is the highest-value one-line fix in the codebase.

**Fix:** match explicitly, e.g. `re.fullmatch(r"no\.?", value)` → `False`, `re.fullmatch(r"yes\.?", value)` → `True`, anything else → `None` (which `eligibility()` already treats as blocking). Add the "not yet decided" case to the tests.

### 2. Malformed orchestrator result crashes MC instead of failing closed — `scripts/mc_lib/gates.py:119`, `scripts/mc_lib/runner.py:162` **[verified]**

`verify_gate` checks that `validation` is a non-empty list, but not that its entries are objects. A result containing `"validation": ["git diff --check ran fine"]` (a plausible LLM output shape) hits `entry.get("result", …)` on a string:

```
CRASH: AttributeError: 'str' object has no attribute 'get'
```

(Verified against a real toy repo/plan/result.)

The blast radius is worse than the crash itself, because `runner.execute_slice` only catches `McError`:

- the `AttributeError` propagates out of the attempt loop as a raw traceback;
- `adapter.force_stop` is never reached → **the tmux session is orphaned**;
- `run.json` is left with `status: "running"` and a stale `current_slice` → the durable state MC exists to keep truthful is now wrong.

The same escape hatch exists for *any* unexpected exception in the polling/verification block (`OSError` on artifact writes, `KeyboardInterrupt`, etc.) — see P2 finding 7.

**Fix (two layers):**
1. In `verify_gate`, treat non-dict entries as missing evidence: `if not all(isinstance(e, dict) for e in validation): return GateDecision("fail", "validation entries are malformed", …)`. Audit the rest of the function for the same pattern (`result.get("changed_files")` tolerates junk because set inequality fails closed, but make it explicit).
2. In `runner.execute_slice`, add a `finally`-style guarantee (or broaden the except to `Exception`, re-raising after cleanup) that force-stops the tmux session and writes a `blocked` stop state before propagating.

### 3. Seeded worker credentials sit unprotected inside the target repo — `scripts/mc_lib/runtime.py:125`, `scripts/mc_lib/commands.py:58` **[design gap]**

`seed_worker_credentials` copies **live auth material** (Codex `auth.json`, Claude Code `.credentials.json`) into `.ai-mc/runs/<id>/slices/slice-NNN/{codex-home,claude-config-dir}/` in the target repository. Three deliberate MC behaviours then combine badly:

- MC "does not edit `.gitignore` as part of initialization" (README.md:161);
- every dirty-worktree and changed-file check *excludes* `.ai-mc/` (`meaningful_status_lines`), so nothing in MC will ever flag these files;
- `archive-sensitive` is manual, and even when run it only **moves** the credentials to `.ai-mc/sensitive-archive/` — still inside the repo, still plaintext.

A user running `git add -A` (or any tool doing so) in a repo whose `.gitignore` doesn't cover `.ai-mc/` will stage live API credentials. Nothing warns them.

**Fix:** at `init_run`, write `.ai-mc/.gitignore` containing `*`. This is the standard self-ignoring-directory pattern: it keeps the "MC doesn't edit the project's `.gitignore`" promise while making the entire audit directory (credentials included) invisible to git. One line, closes the whole class. Additionally consider: `archive-sensitive` gaining a `--purge` mode (team's no-delete policy permitting — archive outside the repo, e.g. under `~/.ai-mc-archive/`, would satisfy both).

---

## P2 Findings (address soon)

### 4. Authorized-surface globs cross directory boundaries — `scripts/mc_lib/git_ops.py:107` **[verified]**

`fnmatch.fnmatch` lets `*` match `/`, so an authorized entry `*.md` authorizes `deep/nested/anything.md` anywhere in the repo (verified). Plan authors will read `*.md` as "top-level markdown files". This silently widens the authorization gate — the one gate MC computes itself rather than trusting the orchestrator. **Fix:** match path segments (e.g. translate globs with `pathlib.PurePath.full_match`-style semantics, or reject `*`/`?` patterns that aren't explicitly suffixed `**`), and document the accepted pattern language in README's Plan Eligibility section.

### 5. The "frozen" plan is not actually frozen — `scripts/mc_lib/commands.py` (init/run)

MC records `plan_path` and re-parses the file before every slice, but never records a content digest. Editing the plan mid-run (renumbering slices, widening an authorized surface, flipping an approval flag) is silently honored on the next `run-next`. That breaks the core "frozen contract" story, and `completed_slice_ids` matching by `"Slice N"` string means renumbering can skip or re-run work. **Fix:** store `sha256` of the plan in `run.json` at init; verify before each slice; stop with `needs-human` on mismatch (a deliberate plan revision then requires `init` of a new run, which is the right ceremony).

### 6. No branch guard at runtime

`branch` is captured at init and never re-checked. If the user (or a previous slice's orchestrator) switches branches, subsequent slices commit onto the wrong branch and MC's gates all still pass — commits advance HEAD, files match, worktree clean. **Fix:** in `execute_slice` (next to `require_clean_worktree`), compare `git branch --show-current` to `state["branch"]` and stop on mismatch. Cheap, and it closes a real "supervised run drifted onto main" scenario.

### 7. Exception handling in the runner is too narrow — `scripts/mc_lib/runner.py:162`

Same mechanism as P1-2, filed separately as hardening: only `McError` triggers evidence capture + force-stop + state update. Any other exception (filesystem errors writing artifacts, JSON errors, Ctrl-C) leaks the tmux session and leaves `run.json` claiming `running`. Also note `status` command has no way to detect this staleness. **Fix:** cleanup that runs on all exits; optionally have `status` flag a `running` state whose tmux session no longer exists.

### 8. Preflight passes launch configs that run-next will refuse — `scripts/mc_lib/commands.py:314`

Without `--allow-profile-command`/`--harness-command`, `preflight` checks only `which codex` and reports PASS, but `run-next` with the same flags hits `TmuxHarnessAdapter.preflight`'s interactive-deadlock refusal. Preflight's whole point is predicting the launch; it should run the same resolution (`resolve_harness_command` + `adapter.preflight`) and fail with the same message. Note the CLI asymmetry that enables this: `preflight` has no `--allow-unattended-default` flag while the run commands do.

### 9. Claude harness has no readiness or trust-prompt detection — `scripts/mc_lib/tmux_adapter.py:85`

`wait_until_prompt_ready` is codex-only (early-returns for anything else). For Claude, the prompt is pasted immediately after `new-session`, then two blind `C-m` are sent. If Claude Code shows its directory-trust dialog or is slow to start, at best the prompt is lost until timeout; at worst a blind `C-m` **confirms whatever dialog is highlighted** (auto-trusting a directory is exactly the class of side effect MC promises not to have). The codex path already demonstrates the right pattern (detect trust prompt → `McError`; detect composer → proceed). **Fix:** add the equivalent Claude detection (its TUI banner and trust-prompt strings), and a generic fallback of "pane text stable for N seconds" for other harnesses.

### 10. Claude repair attempts reuse the same `--session-id` — `scripts/mc_lib/runner.py:62`

`orchestrator_session_id` is generated once per slice, outside the attempt loop, and baked into the resolved command. A `repairable` first attempt means attempt 2 launches `claude --session-id <same-uuid>`, which collides with the attempt-1 session (fails or resumes stale context — either way not the "fresh session per attempt" the design intends), and attempt 2's transcript overwrites/misses. Tests only exercise retry via a fake codex-named harness, so this path is untested. **Fix:** generate the session id (and resolve the command) inside the attempt loop; name transcripts per attempt.

### 11. `reconcile` guesses the slice's starting commit — `scripts/mc_lib/commands.py:234`

When no previous completed slice recorded a hash, `reconcile` falls back to `HEAD^` as `before_head`. A slice whose orchestrator made **multiple commits** then has its earlier commits excluded from `changed_files_between`; an unauthorized file changed in the first commit escapes the unauthorized-files check, and if the orchestrator also underreported `changed_files`, the slice reconciles to `pass`. The live-run path is safe (it snapshots `before_head` before launch) — **fix by using the recorded evidence instead of guessing:** the runner already writes `git-status-before.txt`; also persist `before_head` into the slice entry (`slice_entry_from_gate`) so `reconcile` can use the exact value.

### 12. `archive-sensitive` misses `tool-homes/` — `scripts/mc_lib/constants.py:86`

`SENSITIVE_ARTIFACT_NAMES` covers `copilot-home`, `codex-home`, `claude-config-dir`, but MC also exports `MC_TOOL_HOME_ROOT` (`tool-homes/`) and the orchestrator prompt advertises it as the place for tool homes. Anything a worker parks there (session state, tokens) is never archived. **Fix:** add `tool-homes` to the sensitive set.

### 13. Duplicate slice numbers are silently coalesced — `scripts/mc_lib/plan.py:47`

Two `## Slice 3:` headings parse into two `PlanSlice`s sharing `slice_id` "Slice 3"; once the first passes, `completed_slice_ids` marks both complete and the second never runs — a silent skip in a tool whose job is completeness. **Fix:** `init_run` (and `parse_plan` consumers) should fail closed on duplicate slice numbers.

### 14. Verdict trust boundary is thinner than the docs imply — `scripts/mc_lib/gates.py:61,125-135`

MC verifies drift/review **verdict strings from `orchestrator-result.json`** plus mere *existence* of the artifact files — and `artifact_exists` accepts any absolute path that exists (`"path": "/etc/hosts"` passes). SKILL.md's "MC decisions must not rely only on natural-language transcript interpretation" is honored for git evidence but not for audit verdicts: a sloppy or lying orchestrator writes `"verdict": "PASS"` and an empty file. Unauthorized-file checking is MC's own real drift check, which covers the biggest risk — but the residual gap is worth either (a) documenting explicitly as a trust boundary in SKILL.md, or (b) minimally validating content: artifact must be non-empty, live under the slice artifact dir, and contain a verdict line consistent with the JSON.

---

## P3 Findings and Simplifications (code-simplifier pass)

Behaviour-preserving cleanups, ordered by value. None are urgent; together they'd remove ~80 lines and several traps.

1. **`gates.verify_gate` duplicates the ~25-line commit-hash reconciliation block twice** (`gates.py:146-171` vs `172-192`, identical apart from the reason string). Extract `_reconcile_commit_hash(result, result_path, slice_artifact_dir, reported, corrected, reason) -> GateDecision`. The function is the safety-critical core of MC; halving its length is a real audit win.
2. **`runner.execute_slice` has three near-identical evidence-capture blocks** (normal / timeout / exception paths each write `git-status-after*`, `git-diff.patch`, pane captures). Extract a `capture_post_run_evidence(...)` helper; this also makes fix P2-7 (cleanup on all exits) natural to implement.
3. **Stop-status normalization duplicated** (`"fail"→"failed"`, unknown→`"blocked"`) in `runner.py:185-187` and `commands.py:251-253`. One helper next to `RUN_STOP_STATUSES`.
4. **Eligibility is evaluated twice per run** (`run_next` then again in `execute_slice`). Pass the result through, or let `execute_slice` own it exclusively.
5. **`mc_lib/__init__.py` star-imports every module**, re-exporting stdlib names (`re`, `json`, `Any`, …) into the `mc` namespace; tests then rely on the flat `mc.X` surface. Replace with explicit exports (`__all__`) so the public surface is deliberate — this is also the file most likely to mask a future name collision.
6. **`tmux_adapter.py` uses `Any` without importing it** (`detect_activity` annotation). Harmless today only because of `from __future__ import annotations`; breaks under `typing.get_type_hints` or future tooling. Add the import.
7. **`run-next` help text says "inspect the next slice"** but the command executes it unless `--dry-run` is passed (`cli.py:78`). Say what it does.
8. **`authorized_files` regex over-captures**: the files block only terminates at the two known following bullets; any other stray bullet under "Files allowed to change" becomes an authorized "entry". Harmless unless path-like, but terminate the capture at *any* next `- ` bullet at the same level.
9. **`write_git_diff` writes stderr into `git-diff.patch`** on git failure — mislabeled evidence; write a `git-diff-error.txt` instead.
10. **No concurrency guard**: two MC processes on one repo interleave `run.json` writes and the `current` symlink. A trivial `os.open(lock, O_CREAT|O_EXCL)` per run dir would do; at minimum document single-instance assumption.
11. **Git-quoted paths (non-ASCII, embedded quotes) aren't unescaped** in `status_path`/`changed_files_between` — comparisons then fail closed at the gate. Safe direction, but confusing; note it or normalize with `-z` output.
12. **Prompt template renders via `str.format`** (`runtime.py:229`): any literal `{`/`}` ever added to `references/orchestrator-prompt.md` (a JSON example, a shell `${var}`) breaks rendering at runtime. Plan-section *values* are safe (single-pass format), but the template is a tripwire; `string.Template` with `$placeholders` is immune. At minimum add a comment in the template file warning editors.

## Test Coverage Assessment

The suite is strong: 47 tests including real tmux end-to-end runs, gate fail-closed cases, reconciliation, credential seeding/isolation, and preflight. Gaps that map to findings above, in priority order:

- approval-flag free-text values ("not yet decided", "none") — would have caught P1-1;
- malformed `orchestrator-result.json` shapes beyond `drift_audit: None` (string validation entries, string `changed_files`) — would have caught P1-2;
- claude-harness runtime paths (readiness, retry/session-id) — everything runtime is exercised through fake codex-named commands;
- glob authorized surfaces (`*.md`), duplicate slice numbers, mid-run plan edits, branch switches;
- `archive-sensitive` (currently untested entirely);
- `stop` while a session is live.

## Cross-Skill Notes (included only where they affect MC)

- **`ai-orchestrator/scripts/worker_jobs.py`**: MC's reuse (session-path conventions, worker helper CLI in the prompt) is the right call versus reimplementation, and preflight checks the file exists. Risk: the import is by *path convention* (`skills/ai-orchestrator/...`) with no version/contract check — a refactor of worker_jobs' function names breaks MC at runtime mid-slice. Suggest: a one-line contract test in MC's suite asserting the imported module exposes `claude_project_root` (and whatever else MC touches), so the break is caught in CI, not in a run.
- **`implementation-plan`**: MC's parser hardcodes the seven `###` headings and the exact bullet labels ("Files allowed to change:", "Approval needed before implementation:"). That contract lives implicitly in two skills. Suggest adding a short "machine-consumed fields" section to the implementation-plan skill so future wording edits there don't silently strand MC (which would at least fail closed, but with a confusing "missing required sections" report).
- No changes recommended to `drift-audit`, `code-review`, `commit`, or `handoff` — MC consumes their outputs via the orchestrator contract, and the gaps found are all on MC's verification side.

## What's Good (keep doing this)

- Fail-closed posture is real, not aspirational: missing sections, ambiguous approval (when not hitting P1-1), empty authorized surface, missing evidence, dirty worktrees, unknown statuses all stop the run.
- MC computes its own git evidence (changed files, ancestry, clean-tree, HEAD advancement) instead of trusting the orchestrator — the single most important design decision here, and it's implemented correctly on the live path.
- Reconciliation is narrowly scoped (commit-hash only), double-artifacted (`mc-reconciliation.json`/`.md`), and honestly documented.
- The inline comments explaining *why* (tmux paste race, credential-home seeding rationale, orchestrator-vs-worker home isolation) are exactly the kind of constraint documentation that survives refactors.
- Test suite runs real tmux sessions in seconds and asserts on durable artifacts, not just return codes.

## Recommended Action Order

| # | Action | Findings | Effort |
|---|--------|----------|--------|
| 1 | Exact-match approval flag + tests | P1-1 | ~30 min |
| 2 | Type-check result entries; broaden runner cleanup to all exceptions | P1-2, P2-7 | ~2 h |
| 3 | Write `.ai-mc/.gitignore` (`*`) at init; add `tool-homes` to sensitive set | P1-3, P2-12 | ~30 min |
| 4 | Plan sha256 in run.json + verify per slice; duplicate-slice-number check | P2-5, P2-13 | ~2 h |
| 5 | Branch guard in `execute_slice` | P2-6 | ~30 min |
| 6 | Preflight runs the real launch resolution (add `--allow-unattended-default` to preflight) | P2-8 | ~1 h |
| 7 | Claude readiness/trust detection; per-attempt session ids | P2-9, P2-10 | ~3 h |
| 8 | Persist `before_head` in slice entries; use it in `reconcile` | P2-11 | ~1 h |
| 9 | Segment-aware glob matching + document pattern language | P2-4 | ~2 h |
| 10 | Simplifier pass (dedupe reconciliation/capture/status blocks, explicit `__init__` exports, misc P3) | P3-1…12 | ~half day |

Items 1–3 are the "before the next real supervised run" set. Items 4–8 harden the trust story the docs already advertise. Items 9–10 are quality follow-through and can ride along with any future slice work — ideally executed as an implementation-plan that MC itself supervises, which would double as a live trial.
