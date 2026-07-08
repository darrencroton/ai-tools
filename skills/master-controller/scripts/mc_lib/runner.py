from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any

from .constants import DEFAULT_MAX_REPAIR_ATTEMPTS, KNOWN_UNATTENDED_HARNESS_COMMANDS
from .gates import verify_gate
from .git_ops import git, git_head, git_status_text, require_clean_worktree, write_git_diff
from .models import GateDecision, McError, PlanSlice
from .plan import eligibility
from .profiles import parse_worker_tools, resolve_harness_command
from .runtime import (
    capture_orchestrator_transcript,
    capture_worker_runs_summary,
    ensure_slice_runtime_dirs,
    render_orchestrator_prompt,
    render_repair_prompt,
    slice_dir_name,
    tmux_session_name,
)
from .state import (
    append_operational_event,
    current_slice_state,
    default_repair_state,
    idle_status_after_pass,
    normalize_stop_status,
    previous_completed_head,
    relative_artifact_path,
    slice_entry_from_gate,
    update_state_for_stop,
    write_run,
)
from .tmux_adapter import TmuxHarnessAdapter
from .utils import utc_now


def _capture_git_evidence(repo: Path, slice_artifact_dir: Path, attempt: int, before_head: str | None) -> tuple[str | None, str]:
    after_head = git_head(repo)
    after_status = git_status_text(repo)
    (slice_artifact_dir / f"git-status-after-attempt-{attempt}.txt").write_text(after_status, encoding="utf-8")
    (slice_artifact_dir / "git-status-after.txt").write_text(after_status, encoding="utf-8")
    write_git_diff(repo, before_head, after_head, slice_artifact_dir / "git-diff.patch")
    return after_head, after_status


def _capture_failure_evidence(
    adapter: TmuxHarnessAdapter,
    *,
    session_name: str,
    harness_name: str,
    repo: Path,
    orchestrator_session_id: str | None,
    slice_artifact_dir: Path,
    attempt: int,
    before_head: str | None,
) -> None:
    """Best-effort evidence capture for paths already handling a failure."""
    for capture_step in (
        lambda: adapter.capture(session_name, slice_artifact_dir / "pane-capture.txt"),
        lambda: capture_orchestrator_transcript(harness_name, repo, orchestrator_session_id, slice_artifact_dir),
        lambda: capture_worker_runs_summary(slice_artifact_dir),
        lambda: _capture_git_evidence(repo, slice_artifact_dir, attempt, before_head),
    ):
        try:
            capture_step()
        except Exception:
            continue


def _check_runtime_start_preconditions(repo: Path, state: dict[str, Any], plan_slice: PlanSlice, run_json: Path) -> bool:
    runnable, reasons = eligibility(plan_slice)
    if not runnable:
        update_state_for_stop(run_json, state, "needs-human", "; ".join(reasons))
        print(f"Next slice: {plan_slice.slice_id} - {plan_slice.title}")
        print("Eligibility: blocked")
        for reason in reasons:
            print(f"- {reason}")
        return False

    try:
        require_clean_worktree(repo)
    except McError as exc:
        update_state_for_stop(run_json, state, "needs-human", str(exc))
        print(f"{plan_slice.slice_id} stopped: {exc}")
        return False

    current_branch = git(repo, "branch", "--show-current") or "DETACHED"
    if current_branch != state.get("branch"):
        reason = f"branch changed since init: expected {state.get('branch')!r}, found {current_branch!r}"
        update_state_for_stop(run_json, state, "needs-human", reason)
        print(f"{plan_slice.slice_id} stopped: {reason}")
        return False
    return True


def _attempt_for_slice(state: dict[str, Any], plan_slice: PlanSlice) -> int:
    return 1 + sum(1 for entry in state.get("slices", []) if entry.get("slice_id") == plan_slice.slice_id)


def _reap_stale_sessions(adapter: TmuxHarnessAdapter, run_dir: Path, run_id_value: str) -> list[dict[str, str]]:
    reaped: list[dict[str, str]] = []
    stale_dir = run_dir / "stale-sessions"
    for session_name in adapter.sessions_with_prefix(f"mc_{run_id_value}_"):
        safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in session_name)
        capture_path = stale_dir / f"{safe_name}.txt"
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        adapter.capture(session_name, capture_path)
        adapter.force_stop(session_name)
        reaped.append({"tmux_session": session_name, "evidence_path": str(capture_path)})
    return reaped


def start_model_supervised_slice(
    args: argparse.Namespace,
    repo: Path,
    state: dict[str, Any],
    plan_slice: PlanSlice,
    run_dir: Path,
) -> dict[str, Any]:
    run_json = run_dir / "run.json"
    if isinstance(state.get("current_slice"), dict):
        raise McError("run already has a current slice; finalize or stop it before starting another")
    if not _check_runtime_start_preconditions(repo, state, plan_slice, run_json):
        return {"started": False, "status": state.get("status"), "reason": state.get("stop_reason")}

    harness_name = state["harness"]["name"]
    configured_worker_tools = parse_worker_tools(getattr(args, "worker_tools", None))
    harness_model = getattr(args, "harness_model", None)
    harness_effort = getattr(args, "harness_effort", None)
    if harness_model:
        state.setdefault("harness", {})["model_requested"] = harness_model
    if harness_effort:
        state.setdefault("harness", {})["effort_requested"] = harness_effort
    if harness_model or harness_effort:
        write_run(run_json, state)

    slice_artifact_dir = run_dir / "slices" / slice_dir_name(plan_slice)
    credential_warnings = ensure_slice_runtime_dirs(slice_artifact_dir, configured_worker_tools, harness_name)
    for warning in credential_warnings:
        print(f"warning: {warning}")
    prompt_path = slice_artifact_dir / "prompt.md"
    prompt_path.write_text(
        render_orchestrator_prompt(
            state,
            plan_slice,
            slice_artifact_dir,
            run_json,
            configured_worker_tools,
            getattr(args, "worker_model", None),
            getattr(args, "worker_effort", None),
        ),
        encoding="utf-8",
    )

    max_attempts = int(state.get("policy", {}).get("max_repair_attempts", 1)) + 1
    attempt = _attempt_for_slice(state, plan_slice)
    if attempt > max_attempts:
        reason = f"repair attempt cap exhausted for {plan_slice.slice_id}: {attempt - 1}/{max_attempts}"
        update_state_for_stop(run_json, state, "blocked", reason)
        return {"started": False, "status": "blocked", "reason": reason}

    orchestrator_session_id = str(uuid.uuid4()) if harness_name == "claude" else None
    adapter = TmuxHarnessAdapter(
        harness_name,
        resolve_harness_command(args, repo, state, orchestrator_session_id),
        getattr(args, "allow_unattended_default", False),
        configured_worker_tools,
    )
    reaped_stale_sessions = _reap_stale_sessions(adapter, run_dir, str(state["run_id"]))
    if getattr(args, "allow_profile_command", False) and not getattr(args, "harness_command", None):
        print(f"Using MC profile command for harness {adapter.harness_name!r}: {adapter.command!r}")
    if adapter.allow_unattended_default and not adapter.command_override and adapter.harness_name in KNOWN_UNATTENDED_HARNESS_COMMANDS:
        print(
            f"Using known unattended-safe default for harness {adapter.harness_name!r}: {adapter.command!r} "
            "(per-action approval is disabled; MC's post-hoc gates become the safety boundary for this run)"
        )

    started_at = utc_now()
    before_head = git_head(repo)
    before_status = git_status_text(repo)
    (slice_artifact_dir / f"git-status-before-attempt-{attempt}.txt").write_text(before_status, encoding="utf-8")
    (slice_artifact_dir / "git-status-before.txt").write_text(before_status, encoding="utf-8")
    session_name = tmux_session_name(state["run_id"], plan_slice, attempt)
    state["status"] = "running"
    state["current_slice"] = current_slice_state(
        repo,
        plan_slice,
        slice_artifact_dir,
        session_name,
        attempt,
        started_at,
        before_head,
        orchestrator_session_id,
    )
    state.setdefault("supervision", {})["mode"] = "model-supervised"
    state["stop_reason"] = None
    write_run(run_json, state)

    try:
        result_path = slice_artifact_dir / "orchestrator-result.json"
        if result_path.exists():
            result_path.unlink()
        adapter.start(repo, session_name, slice_artifact_dir, run_json, Path(state["plan_path"]), plan_slice)
        adapter.send_prompt(session_name, prompt_path)
    except Exception:
        _capture_failure_evidence(
            adapter,
            session_name=session_name,
            harness_name=harness_name,
            repo=repo,
            orchestrator_session_id=orchestrator_session_id,
            slice_artifact_dir=slice_artifact_dir,
            attempt=attempt,
            before_head=before_head,
        )
        adapter.force_stop(session_name)
        state["current_slice"] = None
        update_state_for_stop(run_json, state, "failed", "failed to start model-supervised slice")
        raise

    return {
        "started": True,
        "slice_id": plan_slice.slice_id,
        "title": plan_slice.title,
        "attempt": attempt,
        "tmux_session": session_name,
        "artifact_dir": relative_artifact_path(repo, slice_artifact_dir),
        "prompt_path": relative_artifact_path(repo, prompt_path),
        "before_head": before_head,
        "reaped_stale_sessions": reaped_stale_sessions,
    }


def finalize_model_supervised_slice(
    args: argparse.Namespace,
    repo: Path,
    state: dict[str, Any],
    plan_slice: PlanSlice,
    run_dir: Path,
) -> dict[str, Any]:
    current = state.get("current_slice") if isinstance(state.get("current_slice"), dict) else None
    if not current:
        raise McError("run has no current slice to finalize")
    if current.get("slice_id") != plan_slice.slice_id:
        raise McError(f"current slice does not match next plan slice: {current.get('slice_id')} != {plan_slice.slice_id}")

    artifact_value = current.get("artifact_dir")
    if not artifact_value:
        raise McError("current slice has no artifact_dir")
    slice_artifact_dir = Path(str(artifact_value))
    if not slice_artifact_dir.is_absolute():
        slice_artifact_dir = repo / slice_artifact_dir
    attempt = int(current.get("attempt") or 1)
    session_name = str(current.get("tmux_session") or "")
    harness_name = state["harness"]["name"]
    orchestrator_session_id = current.get("orchestrator_session_id")
    adapter = TmuxHarnessAdapter(
        harness_name,
        resolve_harness_command(args, repo, state, str(orchestrator_session_id) if orchestrator_session_id else None),
        getattr(args, "allow_unattended_default", False),
        parse_worker_tools(getattr(args, "worker_tools", None)),
    )
    before_head = str(current.get("before_head") or "") or previous_completed_head(state, plan_slice.slice_id)
    started_at = str(current.get("started_at") or utc_now())
    # Recovered from persisted current_slice state rather than args.worker_tools:
    # this is a separate invocation and may not re-supply --worker-tools.
    current_worker_tools = current.get("worker_tools")
    worker_tools = tuple(current_worker_tools) if isinstance(current_worker_tools, list) else ()

    adapter.capture(session_name, slice_artifact_dir / f"pane-capture-attempt-{attempt}.txt")
    attempt_capture = slice_artifact_dir / f"pane-capture-attempt-{attempt}.txt"
    if attempt_capture.exists():
        (slice_artifact_dir / "pane-capture.txt").write_text(attempt_capture.read_text(encoding="utf-8"), encoding="utf-8")
    capture_orchestrator_transcript(harness_name, repo, str(orchestrator_session_id) if orchestrator_session_id else None, slice_artifact_dir)
    capture_worker_runs_summary(slice_artifact_dir)
    after_head, after_status = _capture_git_evidence(repo, slice_artifact_dir, attempt, before_head)
    gate = verify_gate(repo, state, plan_slice, slice_artifact_dir, before_head, after_head, after_status, worker_tools)
    adapter.force_stop(session_name)

    entry = slice_entry_from_gate(repo, plan_slice, slice_artifact_dir, started_at, gate, before_head, worker_tools)
    state["slices"].append(entry)
    state["current_slice"] = None
    if gate.status == "pass":
        state["status"] = idle_status_after_pass(state)
        state["stop_reason"] = None
        write_run(run_dir / "run.json", state)
        return {"finalized": True, "status": "pass", "reason": gate.reason, "entry": entry}

    if gate.status == "repairable":
        max_attempts = int(state.get("policy", {}).get("max_repair_attempts", 1)) + 1
        if attempt < max_attempts:
            state["status"] = "partial"
            state["stop_reason"] = None
            write_run(run_dir / "run.json", state)
            return {
                "finalized": True,
                "status": "repairable",
                "reason": gate.reason,
                "next_action": "start-slice may retry the same slice within the repair-attempt cap",
                "attempt": attempt,
                "max_attempts": max_attempts,
                "entry": entry,
            }
        gate = GateDecision("blocked", f"repair attempt cap exhausted after attempt {attempt}: {gate.reason}", gate.result, gate.actual_changed_files)

    update_state_for_stop(run_dir / "run.json", state, normalize_stop_status(gate.status), gate.reason)
    return {"finalized": True, "status": gate.status, "reason": gate.reason, "entry": entry}


def _record_repair_round_evidence(
    adapter: TmuxHarnessAdapter,
    session_name: str,
    slice_artifact_dir: Path,
    round_number: int,
    after_status: str,
) -> None:
    """Preserve the failing round's evidence before the next round overwrites it.

    Per-attempt artifacts are keyed on the session generation and keep being
    rewritten across in-session repair rounds that share one session; these
    per-round copies keep every round independently auditable.
    """
    result_path = slice_artifact_dir / "orchestrator-result.json"
    if result_path.exists():
        # Atomic rename, not read+write+unlink: the poll loop breaks as soon
        # as orchestrator-result.json exists, so the stale failing result must
        # be gone before re-polling — and a rename can never destroy a result
        # the orchestrator happens to rewrite mid-archive (whatever is there
        # at rename time is preserved in the round archive).
        result_path.replace(slice_artifact_dir / f"orchestrator-result-repair-{round_number}.json")
    adapter.capture(session_name, slice_artifact_dir / f"pane-capture-repair-{round_number}.txt")
    (slice_artifact_dir / f"git-status-repair-{round_number}.txt").write_text(after_status, encoding="utf-8")


def _repair_delivery_message(plan_slice: PlanSlice, repair_prompt_path: Path) -> str:
    """One-line in-session pointer to the rendered repair prompt on disk.

    Deliberately a single line: send_literal types keystrokes into the live
    TUI, where a newline can submit a partial message. The full multi-line
    correction is persisted at the named path instead of being typed.
    """
    return (
        f"MC verification did NOT pass for {plan_slice.slice_id}; the slice is NOT accepted. "
        f"Read and follow the repair instructions in {repair_prompt_path} now, fix only the gap it names, "
        "re-run the failed gate, and rewrite orchestrator-result.json for this same slice."
    )


def execute_slice(args: argparse.Namespace, repo: Path, state: dict[str, Any], plan_slice: PlanSlice, run_dir: Path) -> int:
    run_json = run_dir / "run.json"
    if not _check_runtime_start_preconditions(repo, state, plan_slice, run_json):
        return 2

    slice_artifact_dir = run_dir / "slices" / slice_dir_name(plan_slice)
    harness_name = state["harness"]["name"]
    configured_worker_tools = parse_worker_tools(getattr(args, "worker_tools", None))
    harness_model = getattr(args, "harness_model", None)
    harness_effort = getattr(args, "harness_effort", None)
    if harness_model:
        state.setdefault("harness", {})["model_requested"] = harness_model
    if harness_effort:
        state.setdefault("harness", {})["effort_requested"] = harness_effort
    if harness_model or harness_effort:
        write_run(run_json, state)
    credential_warnings = ensure_slice_runtime_dirs(slice_artifact_dir, configured_worker_tools, harness_name)
    for warning in credential_warnings:
        print(f"warning: {warning}")
    prompt_path = slice_artifact_dir / "prompt.md"
    prompt_path.write_text(
        render_orchestrator_prompt(
            state,
            plan_slice,
            slice_artifact_dir,
            run_json,
            configured_worker_tools,
            getattr(args, "worker_model", None),
            getattr(args, "worker_effort", None),
        ),
        encoding="utf-8",
    )

    # Repair budget counts repairable gate failures handled (in-session nudge,
    # fresh-session escalation, or dead-session relaunch), enforced from the
    # persisted current_slice.repair round counter — not from counting slice
    # entries, which in-session repairs deliberately do not append.
    max_repairs = int(state.get("policy", {}).get("max_repair_attempts", DEFAULT_MAX_REPAIR_ATTEMPTS))
    repair = default_repair_state()
    repair["session_generation"] = 0  # incremented to 1 at the first launch
    last_gate: GateDecision | None = None
    slice_start_head: str | None = None
    started_at = utc_now()

    while True:  # one iteration per tmux session (session generation)
        repair["session_generation"] += 1
        generation = int(repair["session_generation"])
        # A fresh session id per generation: reusing one id across a fresh
        # session would collide with the previous session's Claude transcript.
        orchestrator_session_id = str(uuid.uuid4()) if harness_name == "claude" else None
        adapter = TmuxHarnessAdapter(
            harness_name,
            resolve_harness_command(args, repo, state, orchestrator_session_id),
            getattr(args, "allow_unattended_default", False),
            configured_worker_tools,
        )
        if generation == 1:
            if getattr(args, "allow_profile_command", False) and not getattr(args, "harness_command", None):
                print(f"Using MC profile command for harness {adapter.harness_name!r}: {adapter.command!r}")
            if adapter.allow_unattended_default and not adapter.command_override and adapter.harness_name in KNOWN_UNATTENDED_HARNESS_COMMANDS:
                print(
                    f"Using known unattended-safe default for harness {adapter.harness_name!r}: {adapter.command!r} "
                    "(per-action approval is disabled; MC's post-hoc gates are the safety boundary for this run)"
                )

        started_at = utc_now()
        before_head = git_head(repo)
        if slice_start_head is None:
            slice_start_head = before_head
        before_status = git_status_text(repo)
        (slice_artifact_dir / f"git-status-before-attempt-{generation}.txt").write_text(before_status, encoding="utf-8")
        (slice_artifact_dir / "git-status-before.txt").write_text(before_status, encoding="utf-8")
        session_name = tmux_session_name(state["run_id"], plan_slice, generation)
        state["status"] = "running"
        state["current_slice"] = current_slice_state(
            repo,
            plan_slice,
            slice_artifact_dir,
            session_name,
            generation,
            started_at,
            before_head,
            orchestrator_session_id,
            configured_worker_tools,
            repair,
        )
        state["stop_reason"] = None
        write_run(run_json, state)

        relaunch = False
        try:
            result_path = slice_artifact_dir / "orchestrator-result.json"
            if result_path.exists():
                result_path.unlink()
            adapter.start(repo, session_name, slice_artifact_dir, run_json, Path(state["plan_path"]), plan_slice)
            adapter.send_prompt(session_name, prompt_path)
            previous_capture = ""
            activity_log = slice_artifact_dir / f"activity-attempt-{generation}.jsonl"
            live_capture_path = slice_artifact_dir / f"pane-capture-live-attempt-{generation}.txt"
            deadline = time.monotonic() + float(args.timeout_seconds)
            while True:  # one iteration per verify/repair round in this session
                timed_out = False
                while True:
                    # Always record at least one activity snapshot before
                    # deciding, even if the result already landed: audit
                    # evidence should not depend on winning a race against a
                    # fast-finishing harness.
                    activity = adapter.detect_activity(session_name, previous_capture)
                    previous_capture = str(activity.get("capture", ""))
                    if previous_capture:
                        live_capture_path.write_text(previous_capture, encoding="utf-8")
                        (slice_artifact_dir / "pane-capture-live-latest.txt").write_text(previous_capture, encoding="utf-8")
                    with activity_log.open("a", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(
                                {
                                    "checked_at": utc_now(),
                                    "running": bool(activity.get("running")),
                                    "active": bool(activity.get("active")),
                                },
                                sort_keys=True,
                            )
                            + "\n"
                        )
                    if result_path.exists() or not activity.get("running"):
                        break
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    time.sleep(float(args.poll_seconds))

                adapter.capture(session_name, slice_artifact_dir / f"pane-capture-attempt-{generation}.txt")
                (slice_artifact_dir / "pane-capture.txt").write_text(
                    (slice_artifact_dir / f"pane-capture-attempt-{generation}.txt").read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                capture_orchestrator_transcript(harness_name, repo, orchestrator_session_id, slice_artifact_dir)
                capture_worker_runs_summary(slice_artifact_dir)
                if timed_out:
                    adapter.request_stop(session_name)
                    time.sleep(min(float(args.poll_seconds), 1.0))
                    adapter.capture(session_name, slice_artifact_dir / "pane-capture-timeout.txt")
                    _capture_git_evidence(repo, slice_artifact_dir, generation, slice_start_head)
                    last_gate = GateDecision("blocked", "timeout waiting for orchestrator-result.json")
                    break
                # git-diff.patch is written against the slice starting commit
                # so the final evidence stays cumulative across repair commits
                # and fresh-session generations, matching what the gate checks.
                after_head, after_status = _capture_git_evidence(repo, slice_artifact_dir, generation, slice_start_head)
                # Verified against the slice starting commit, not this
                # session's launch HEAD: a repair round or relaunch may follow
                # earlier legitimate commits, and the gate must see the
                # cumulative slice diff (MC accepts final verified state, not
                # commit count).
                last_gate = verify_gate(
                    repo, state, plan_slice, slice_artifact_dir, slice_start_head, after_head, after_status, configured_worker_tools
                )
                if last_gate.status != "repairable":
                    # pass, or a terminal decision (integrity/trust breaches
                    # and orchestrator's own considered stops): no repair.
                    break

                signature = last_gate.signature or "orchestrator-repairable"
                if repair["round"] >= max_repairs:
                    last_gate = GateDecision(
                        "blocked",
                        f"repair budget exhausted for {plan_slice.slice_id} "
                        f"({repair['round']}/{max_repairs} repairs used); last gate failure: {last_gate.reason}",
                        last_gate.result,
                        last_gate.actual_changed_files,
                        signature,
                    )
                    break
                session_alive = adapter.session_exists(session_name)
                streak = int(repair["signature_streak"]) + 1 if signature == repair["last_signature"] else 1
                if session_alive and streak >= 3:
                    # Circuit breaker: an in-session nudge, then a fresh
                    # session, then the same signature again — terminal
                    # regardless of remaining budget.
                    last_gate = GateDecision(
                        "needs-human",
                        f"circuit breaker: gate signature {signature!r} failed {streak} consecutive times "
                        f"(after an in-session repair and a fresh-session retry); last gate failure: {last_gate.reason}",
                        last_gate.result,
                        last_gate.actual_changed_files,
                        signature,
                    )
                    break
                round_number = int(repair["round"]) + 1
                _record_repair_round_evidence(adapter, session_name, slice_artifact_dir, round_number, after_status)
                if not session_alive:
                    # The session died before a repair could be delivered:
                    # relaunch fresh. Consumes budget but is not a
                    # circuit-breaker step, so the breaker state is untouched.
                    mode = "relaunch"
                    repair["round"] = round_number
                elif streak == 1:
                    mode = "in-session"
                    repair.update(round=round_number, last_signature=signature, signature_streak=1)
                else:
                    # Same signature failed again after a nudge: one
                    # fresh-session escalation, on the theory the session is
                    # anchored on a wrong premise.
                    mode = "fresh-session"
                    repair.update(round=round_number, last_signature=signature, signature_streak=2)
                state["current_slice"]["repair"] = dict(repair)
                write_run(run_json, state)
                append_operational_event(
                    repo,
                    state,
                    {
                        "kind": "repair",
                        "slice_id": plan_slice.slice_id,
                        "round": round_number,
                        "signature": signature,
                        "mode": mode,
                        "tmux_session": session_name,
                        "gate_reason": last_gate.reason,
                    },
                )
                if mode == "in-session":
                    repair_prompt_text = render_repair_prompt(
                        plan_slice, slice_artifact_dir, last_gate, before_head=slice_start_head
                    )
                    repair_prompt_path = slice_artifact_dir / f"repair-prompt-repair-{round_number}.md"
                    repair_prompt_path.write_text(repair_prompt_text, encoding="utf-8")
                    (slice_artifact_dir / "repair-prompt.md").write_text(repair_prompt_text, encoding="utf-8")
                    try:
                        adapter.send_literal(session_name, _repair_delivery_message(plan_slice, repair_prompt_path))
                    except McError as exc:
                        # send_literal refuses when a hard prompt / hard-stop
                        # hint is on screen. That refusal must stop the run
                        # with evidence, never surface as an uncaught
                        # exception that orphans it.
                        adapter.capture(
                            session_name, slice_artifact_dir / f"pane-capture-repair-refused-{round_number}.txt"
                        )
                        last_gate = GateDecision(
                            "needs-human",
                            f"repair prompt could not be delivered into the live session: {exc}",
                            last_gate.result,
                            last_gate.actual_changed_files,
                            signature,
                        )
                        break
                    # Each repair round gets a fresh timeout window for the
                    # orchestrator to respond with a new result.
                    deadline = time.monotonic() + float(args.timeout_seconds)
                    continue
                relaunch = True
                break
        except KeyboardInterrupt:
            _capture_failure_evidence(
                adapter,
                session_name=session_name,
                harness_name=harness_name,
                repo=repo,
                orchestrator_session_id=orchestrator_session_id,
                slice_artifact_dir=slice_artifact_dir,
                attempt=generation,
                before_head=slice_start_head,
            )
            last_gate = GateDecision("cancelled", "interrupted by user")
        except Exception as exc:
            # Any failure — an McError from the harness/tmux path or an
            # unexpected exception — must not orphan the tmux session or leave
            # run.json stuck at "running". Capture whatever evidence exists and
            # record a failed gate so the run stops fail-closed. force_stop runs
            # in the finally block below regardless of which path we took.
            _capture_failure_evidence(
                adapter,
                session_name=session_name,
                harness_name=harness_name,
                repo=repo,
                orchestrator_session_id=orchestrator_session_id,
                slice_artifact_dir=slice_artifact_dir,
                attempt=generation,
                before_head=slice_start_head,
            )
            last_gate = GateDecision("failed", str(exc) or repr(exc))
        finally:
            # Reached only when this session is finished with — pass, a
            # terminal decision, an escalation/relaunch, or an exception.
            # In-session repair rounds never leave the try block, so the live
            # session survives them.
            adapter.force_stop(session_name)

        if relaunch:
            continue
        break

    fallback = last_gate or GateDecision("blocked", "slice ended without a gate decision")
    entry = slice_entry_from_gate(
        repo,
        plan_slice,
        slice_artifact_dir,
        started_at,
        fallback,
        slice_start_head,
        configured_worker_tools,
        # Only a slice that actually consumed repair rounds records them; a
        # first-attempt pass keeps the pre-repair-loop entry shape.
        repair=dict(repair) if repair["round"] else None,
    )
    state["slices"].append(entry)
    state["current_slice"] = None
    if fallback.status == "pass":
        state["status"] = idle_status_after_pass(state)
        state["stop_reason"] = None
        write_run(run_json, state)
        print(f"{plan_slice.slice_id} passed MC gates.")
        return 0
    update_state_for_stop(run_json, state, normalize_stop_status(fallback.status), fallback.reason)
    print(f"{plan_slice.slice_id} stopped: {fallback.reason}")
    return 2
