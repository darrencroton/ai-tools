from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any

from .constants import KNOWN_UNATTENDED_HARNESS_COMMANDS
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
    slice_dir_name,
    tmux_session_name,
)
from .state import (
    idle_status_after_pass,
    normalize_stop_status,
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


def execute_slice(args: argparse.Namespace, repo: Path, state: dict[str, Any], plan_slice: PlanSlice, run_dir: Path) -> int:
    runnable, reasons = eligibility(plan_slice)
    run_json = run_dir / "run.json"
    if not runnable:
        update_state_for_stop(run_json, state, "needs-human", "; ".join(reasons))
        print(f"Next slice: {plan_slice.slice_id} - {plan_slice.title}")
        print("Eligibility: blocked")
        for reason in reasons:
            print(f"- {reason}")
        return 2

    try:
        require_clean_worktree(repo)
    except McError as exc:
        update_state_for_stop(run_json, state, "needs-human", str(exc))
        print(f"{plan_slice.slice_id} stopped: {exc}")
        return 2

    # The branch is frozen at init; a slice must not commit onto a different
    # branch than the run was started on (e.g. if a prior slice or the user
    # switched branches). All of MC's other gates would still pass on the wrong
    # branch, so this is checked explicitly.
    current_branch = git(repo, "branch", "--show-current") or "DETACHED"
    if current_branch != state.get("branch"):
        reason = f"branch changed since init: expected {state.get('branch')!r}, found {current_branch!r}"
        update_state_for_stop(run_json, state, "needs-human", reason)
        print(f"{plan_slice.slice_id} stopped: {reason}")
        return 2

    slice_artifact_dir = run_dir / "slices" / slice_dir_name(plan_slice)
    harness_name = state["harness"]["name"]
    configured_worker_tools = parse_worker_tools(getattr(args, "worker_tools", None))
    harness_model = getattr(args, "harness_model", None)
    if harness_model:
        state.setdefault("harness", {})["model_requested"] = harness_model
        write_run(run_json, state)
    credential_warnings = ensure_slice_runtime_dirs(slice_artifact_dir, configured_worker_tools, harness_name)
    for warning in credential_warnings:
        print(f"warning: {warning}")
    prompt_path = slice_artifact_dir / "prompt.md"
    prompt_path.write_text(
        render_orchestrator_prompt(state, plan_slice, slice_artifact_dir, run_json, configured_worker_tools),
        encoding="utf-8",
    )

    max_attempts = int(state.get("policy", {}).get("max_repair_attempts", 1)) + 1
    last_gate: GateDecision | None = None
    slice_start_head: str | None = None
    for attempt in range(1, max_attempts + 1):
        # A fresh session id per attempt: reusing one id across a repair retry
        # would collide with the previous attempt's Claude session and clobber
        # its transcript.
        orchestrator_session_id = str(uuid.uuid4()) if harness_name == "claude" else None
        adapter = TmuxHarnessAdapter(
            harness_name,
            resolve_harness_command(args, repo, state, orchestrator_session_id),
            getattr(args, "allow_unattended_default", False),
            configured_worker_tools,
        )
        if attempt == 1:
            if getattr(args, "allow_profile_command", False) and not getattr(args, "harness_command", None):
                print(f"Using MC profile command for harness {adapter.harness_name!r}: {adapter.command!r}")
            if adapter.allow_unattended_default and not adapter.command_override and adapter.harness_name in KNOWN_UNATTENDED_HARNESS_COMMANDS:
                print(
                    f"Using known unattended-safe default for harness {adapter.harness_name!r}: {adapter.command!r} "
                    "(per-action approval is disabled; MC's post-hoc gates are the safety boundary for this run)"
                )

        started_at = utc_now()
        before_head = git_head(repo)
        if attempt == 1:
            slice_start_head = before_head
        before_status = git_status_text(repo)
        (slice_artifact_dir / f"git-status-before-attempt-{attempt}.txt").write_text(before_status, encoding="utf-8")
        (slice_artifact_dir / "git-status-before.txt").write_text(before_status, encoding="utf-8")
        session_name = tmux_session_name(state["run_id"], plan_slice, attempt)
        state["status"] = "running"
        state["current_slice"] = {
            "slice_id": plan_slice.slice_id,
            "title": plan_slice.title,
            "artifact_dir": relative_artifact_path(repo, slice_artifact_dir),
            "tmux_session": session_name,
            "attempt": attempt,
            "started_at": started_at,
        }
        state["stop_reason"] = None
        write_run(run_json, state)

        try:
            result_path = slice_artifact_dir / "orchestrator-result.json"
            if result_path.exists():
                result_path.unlink()
            adapter.start(repo, session_name, slice_artifact_dir, run_json, Path(state["plan_path"]), plan_slice)
            adapter.send_prompt(session_name, prompt_path)
            deadline = time.monotonic() + float(args.timeout_seconds)
            timed_out = False
            previous_capture = ""
            activity_log = slice_artifact_dir / f"activity-attempt-{attempt}.jsonl"
            live_capture_path = slice_artifact_dir / f"pane-capture-live-attempt-{attempt}.txt"
            while True:
                # Always record at least one activity snapshot before deciding,
                # even if the result already landed: audit evidence should not
                # depend on winning a race against a fast-finishing harness.
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

            adapter.capture(session_name, slice_artifact_dir / f"pane-capture-attempt-{attempt}.txt")
            (slice_artifact_dir / "pane-capture.txt").write_text(
                (slice_artifact_dir / f"pane-capture-attempt-{attempt}.txt").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            capture_orchestrator_transcript(harness_name, repo, orchestrator_session_id, slice_artifact_dir)
            capture_worker_runs_summary(slice_artifact_dir)
            if timed_out:
                adapter.request_stop(session_name)
                time.sleep(min(float(args.poll_seconds), 1.0))
                adapter.capture(session_name, slice_artifact_dir / "pane-capture-timeout.txt")
                after_head, after_status = _capture_git_evidence(repo, slice_artifact_dir, attempt, before_head)
                last_gate = GateDecision("blocked", "timeout waiting for orchestrator-result.json")
            else:
                after_head, after_status = _capture_git_evidence(repo, slice_artifact_dir, attempt, before_head)
                last_gate = verify_gate(repo, state, plan_slice, slice_artifact_dir, before_head, after_head, after_status)
        except Exception as exc:
            # Any failure — an McError from the harness/tmux path or an
            # unexpected exception — must not orphan the tmux session or leave
            # run.json stuck at "running". Capture whatever evidence exists and
            # record a failed gate so the run stops fail-closed. force_stop runs
            # in the finally block below regardless of which path we took.
            adapter.capture(session_name, slice_artifact_dir / "pane-capture.txt")
            capture_orchestrator_transcript(harness_name, repo, orchestrator_session_id, slice_artifact_dir)
            capture_worker_runs_summary(slice_artifact_dir)
            _capture_git_evidence(repo, slice_artifact_dir, attempt, before_head)
            last_gate = GateDecision("failed", str(exc) or repr(exc))
        finally:
            adapter.force_stop(session_name)

        if last_gate.status == "repairable" and attempt < max_attempts:
            continue
        entry = slice_entry_from_gate(repo, plan_slice, slice_artifact_dir, started_at, last_gate, slice_start_head)
        state["slices"].append(entry)
        state["current_slice"] = None
        if last_gate.status == "pass":
            state["status"] = idle_status_after_pass(state)
            state["stop_reason"] = None
            write_run(run_json, state)
            print(f"{plan_slice.slice_id} passed MC gates.")
            return 0
        update_state_for_stop(run_json, state, normalize_stop_status(last_gate.status), last_gate.reason)
        print(f"{plan_slice.slice_id} stopped: {last_gate.reason}")
        return 2

    fallback = last_gate or GateDecision("blocked", "slice ended without a gate decision")
    update_state_for_stop(run_json, state, "blocked", fallback.reason)
    return 2
