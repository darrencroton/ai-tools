from __future__ import annotations

import argparse
import copy
import json
import os
import shlex
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import (
    COMPLETED_SLICE_STATUSES,
    DEFAULT_SUPERVISION,
    HARNESS_PROFILES,
    OPERATIONAL_EVENTS_FILENAME,
    PARSER_NAME,
    RUN_ACTIVE_STATUSES,
    RUN_STOP_STATUSES,
    SCHEMA_VERSION,
)
from .gates import verify_gate
from .git_ops import (
    git,
    git_access_path,
    git_head,
    git_result,
    git_status_text,
    meaningful_status_lines,
    resolve_plan,
    resolve_repo,
    write_git_diff,
)
from .models import McError
from .plan import (
    completed_slice_ids,
    duplicate_slice_numbers,
    eligibility,
    next_slice,
    parse_plan,
    plan_digest,
    plan_slice_by_id,
    verify_plan_unchanged,
)
from .profiles import harness_supports_role, parse_worker_tools, profile_command, resolve_harness_command
from .runtime import (
    capture_worker_runs_summary,
    environment_preflight,
    extract_operational_hints,
    result_schema_path,
    sensitive_artifact_dirs,
    slice_dir_name,
    slice_paths,
    worker_credential_source,
    worker_jobs_path,
)
from .runner import execute_slice, finalize_model_supervised_slice, start_model_supervised_slice
from .state import (
    append_operational_event,
    idle_status_after_pass,
    load_run,
    normalize_stop_status,
    operational_events_file,
    previous_completed_head,
    relative_artifact_path,
    resolve_run_dir,
    resolve_run_path,
    slice_entry_from_gate,
    update_state_for_stop,
    update_run_locked,
    write_run,
)
from .tmux_adapter import TmuxHarnessAdapter
from .utils import parse_iso_datetime, run_id, utc_now


def init_run(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    plan = resolve_plan(Path(args.plan))
    slices = parse_plan(plan)
    if not slices:
        raise McError("plan contains no slices")
    duplicates = duplicate_slice_numbers(slices)
    if duplicates:
        raise McError(
            "plan has duplicate slice numbers: "
            + ", ".join(str(number) for number in duplicates)
            + " (each slice number must be unique so completion tracking cannot silently skip work)"
        )
    requested_branch = getattr(args, "branch", None)
    create_branch = bool(getattr(args, "create_branch", False))
    if create_branch and not requested_branch:
        raise McError("--create-branch requires --branch")
    if requested_branch:
        dirty = meaningful_status_lines(git_status_text(repo))
        if dirty:
            raise McError("cannot switch/create MC branch from dirty worktree outside .ai-mc/: " + "; ".join(dirty))
        current_branch = git(repo, "branch", "--show-current") or "DETACHED"
        if current_branch != requested_branch:
            branch_exists = git_result(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{requested_branch}").returncode == 0
            if branch_exists:
                git(repo, "switch", requested_branch)
            elif create_branch:
                git(repo, "switch", "-c", requested_branch)
            else:
                raise McError(
                    f"intended branch {requested_branch!r} does not exist; "
                    "create it first or rerun init with --create-branch"
                )
    rid = run_id()
    mc_dir = repo / ".ai-mc"
    run_dir = mc_dir / "runs" / rid
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = mc_dir / "runs" / f"{rid}-{suffix}"
    run_dir.mkdir(parents=True, exist_ok=False)

    # MC keeps live worker credentials and full transcripts under .ai-mc/. It
    # deliberately does not edit the project's own .gitignore, so it makes the
    # audit directory self-ignoring instead: this keeps a stray `git add -A`
    # from ever staging seeded auth material or transcripts. MC's own dirty-tree
    # and changed-file checks already exclude .ai-mc/.
    gitignore = mc_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")

    branch = git(repo, "branch", "--show-current") or "DETACHED"
    worktree_root = Path(args.worktree_root).expanduser().resolve() if args.worktree_root else None
    now = utc_now()
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_dir.name,
        "created_at": now,
        "updated_at": now,
        "status": "initialized",
        "repo_path": str(repo),
        "plan_path": str(plan),
        "worktree_root": str(worktree_root) if worktree_root else None,
        "branch": branch,
        "harness": {
            "name": args.harness,
            "adapter": None,
            "preflight": environment_preflight(),
        },
        "policy": {
            "dirty_state": "clean-required",
            "approval_gated_slices": "stop",
            "max_repair_attempts": 1,
            "commit_required": True,
        },
        "plan": {
            "slice_count": len(slices),
            "parser": PARSER_NAME,
            "sha256": plan_digest(plan),
        },
        "current_slice": None,
        "supervision": copy.deepcopy(DEFAULT_SUPERVISION),
        "operational_events_path": relative_artifact_path(repo, run_dir / OPERATIONAL_EVENTS_FILENAME),
        "slices": [],
        "stop_reason": None,
    }
    write_run(run_dir / "run.json", state)

    current = mc_dir / "current"
    if current.exists() or current.is_symlink():
        current.unlink()
    os.symlink(run_dir.relative_to(mc_dir), current)
    print(f"Initialized MC run: {run_dir}")
    print(f"Branch: {branch}")
    print(f"Slices discovered: {len(slices)}")
    return 0


def status(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    state = load_run(resolve_run_path(repo, args.run))
    harness = state.get("harness", {})
    print(f"Run: {state['run_id']}")
    print(f"Status: {state['status']}")
    print(f"Repo: {state['repo_path']}")
    print(f"Plan: {state['plan_path']}")
    print(f"Branch: {state['branch']}")
    harness_line = str(harness.get("name", "unknown"))
    if harness.get("model_requested"):
        harness_line += f" (requested model: {harness['model_requested']})"
    print(f"Harness: {harness_line}")
    print(f"Completed slices: {len(completed_slice_ids(state))}/{state['plan']['slice_count']}")
    supervision = state.get("supervision", {})
    print(f"Supervision mode: {supervision.get('mode', 'unknown')}")
    print(f"Operational events: {operational_events_file(repo, state)}")
    current = state.get("current_slice") if isinstance(state.get("current_slice"), dict) else None
    if current:
        print(f"Current slice: {current.get('slice_id')} - {current.get('title')}")
        print(f"Current before_head: {current.get('before_head')}")
        pause = current.get("pause") if isinstance(current.get("pause"), dict) else None
        if pause:
            print(f"Paused until: {pause.get('paused_until')} ({pause.get('reason', 'no reason recorded')})")
    if state.get("stop_reason"):
        print(f"Stop reason: {state['stop_reason']}")
    return 0


def summarize(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    state = load_run(resolve_run_path(repo, args.run))
    completed = completed_slice_ids(state)
    print(f"MC run {state['run_id']} summary")
    print(f"Status: {state['status']}")
    supervision = state.get("supervision", {})
    print(f"Supervision mode: {supervision.get('mode', 'unknown')}")
    if state["status"] in RUN_ACTIVE_STATUSES:
        current = state.get("current_slice") if isinstance(state.get("current_slice"), dict) else None
        if current:
            print(f"Current slice: {current.get('slice_id')} - {current.get('title')}")
            pause = current.get("pause") if isinstance(current.get("pause"), dict) else None
            if pause:
                print(f"Paused until: {pause.get('paused_until')} ({pause.get('reason', 'no reason recorded')})")
    if state["status"] == "partial":
        plan = resolve_plan(Path(state["plan_path"]))
        verify_plan_unchanged(state, plan)
        slices = parse_plan(plan)
        candidate = next_slice(slices, state)
        if candidate:
            print(f"Next slice: {candidate.slice_id} - {candidate.title}")
    if not state.get("slices"):
        print("No slices have run yet.")
    else:
        for entry in state["slices"]:
            print(f"- {entry.get('slice_id', 'unknown')}: {entry.get('status', 'unknown')}")
    print(f"Completed: {len(completed)}/{state['plan']['slice_count']}")
    return 0


def _json_print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _slice_artifact_dir(repo: Path, current: dict[str, Any]) -> Path:
    value = current.get("artifact_dir")
    if not value:
        raise McError("current slice has no artifact_dir")
    path = Path(str(value))
    return path if path.is_absolute() else repo / path


def _current_adapter(args: argparse.Namespace, repo: Path, state: dict[str, Any]) -> TmuxHarnessAdapter:
    current = state.get("current_slice") if isinstance(state.get("current_slice"), dict) else {}
    session_id = current.get("orchestrator_session_id") if isinstance(current, dict) else None
    return TmuxHarnessAdapter(
        state["harness"]["name"],
        resolve_harness_command(args, repo, state, str(session_id) if session_id else None),
        getattr(args, "allow_unattended_default", False),
        parse_worker_tools(getattr(args, "worker_tools", None)),
    )


def _result_status(result_path: Path) -> dict[str, Any]:
    if not result_path.exists():
        return {"exists": False, "parse_status": "absent", "path": str(result_path)}
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"exists": True, "parse_status": "invalid", "path": str(result_path), "error": str(exc)}
    return {
        "exists": True,
        "parse_status": "valid" if isinstance(data, dict) else "invalid",
        "path": str(result_path),
        "status": data.get("status") if isinstance(data, dict) else None,
        "slice_id": data.get("slice_id") if isinstance(data, dict) else None,
    }


def _read_tail(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - (limit * 4)))
        text = handle.read().decode("utf-8", errors="replace")
    return text[-limit:]


def _hard_stop_hint_kinds(snapshot: dict[str, Any]) -> list[str]:
    hints = snapshot.get("operational_hints")
    if not isinstance(hints, list):
        return []
    kinds: list[str] = []
    for hint in hints:
        if not isinstance(hint, dict) or not hint.get("hard_stop"):
            continue
        kind = str(hint.get("kind") or "unknown")
        subtype = hint.get("subtype")
        label = f"{kind}:{subtype}" if subtype else kind
        if label not in kinds:
            kinds.append(label)
    return kinds


def _raise_on_hard_stop_hints(snapshot: dict[str, Any], action: str) -> None:
    kinds = _hard_stop_hint_kinds(snapshot)
    if kinds:
        raise McError(f"refusing to {action} while hard-stop operational hint is present: " + ", ".join(kinds))


def build_observation(args: argparse.Namespace, repo: Path, run_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    now_text = utc_now()
    snapshot: dict[str, Any] = {
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "repo_path": state.get("repo_path"),
        "branch": state.get("branch"),
        "current_time": {
            "utc": now_text,
            "local": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        },
        "harness": state.get("harness", {}),
        "current_slice": None,
        "process": {"running": False},
        "prompt_on_screen": {"present": False, "kinds": [], "markers": []},
        "operational_hints": [],
        "artifacts": {"run_dir": str(run_dir), "operational_events": str(operational_events_file(repo, state))},
        "result": {"exists": False, "parse_status": "no-current-slice"},
        "git": {"status_lines": meaningful_status_lines(git_status_text(repo))},
    }
    current = state.get("current_slice") if isinstance(state.get("current_slice"), dict) else None
    if not current:
        return snapshot

    artifact_dir = _slice_artifact_dir(repo, current)
    attempt = int(current.get("attempt") or 1)
    session_name = str(current.get("tmux_session") or "")
    started_at = str(current.get("started_at") or "")
    elapsed_seconds: int | None = None
    if started_at:
        try:
            elapsed_seconds = int((datetime.now(timezone.utc) - parse_iso_datetime(started_at)).total_seconds())
        except ValueError:
            elapsed_seconds = None

    adapter = _current_adapter(args, repo, state)
    previous_path = artifact_dir / "pane-capture-live-latest.txt"
    previous_capture = previous_path.read_text(encoding="utf-8") if previous_path.exists() else ""
    activity = adapter.detect_activity(session_name, previous_capture)
    capture = str(activity.get("capture") or "")
    live_capture_path = artifact_dir / f"pane-capture-live-attempt-{attempt}.txt"
    if capture:
        live_capture_path.parent.mkdir(parents=True, exist_ok=True)
        live_capture_path.write_text(capture, encoding="utf-8")
        previous_path.write_text(capture, encoding="utf-8")
    hard_prompt = adapter.detect_hard_prompt(capture)
    result_path = artifact_dir / "orchestrator-result.json"
    transcript_path = artifact_dir / "orchestrator-transcript.jsonl"
    result = _result_status(result_path)
    transcript_tail = _read_tail(transcript_path)
    hints = extract_operational_hints(
        capture,
        transcript_text=transcript_tail,
        process_running=bool(activity.get("running")),
        process_active=bool(activity.get("active")),
        result_exists=bool(result.get("exists")),
        max_single_pause_seconds=int(state.get("supervision", {}).get("max_single_pause_seconds", 21600)),
    )
    snapshot.update(
        {
            "current_slice": {
                "slice_id": current.get("slice_id"),
                "title": current.get("title"),
                "attempt": attempt,
                "started_at": started_at,
                "elapsed_seconds": elapsed_seconds,
                "before_head": current.get("before_head"),
                "tmux_session": session_name,
                "artifact_dir": relative_artifact_path(repo, artifact_dir),
                "pause": current.get("pause"),
            },
            "process": {"running": bool(activity.get("running")), "active": bool(activity.get("active"))},
            "prompt_on_screen": hard_prompt,
            "pane": {
                "capture_path": relative_artifact_path(repo, previous_path),
                "tail": capture[-4000:],
                "tail_truncated": len(capture) > 4000,
            },
            "transcript": {
                "path": relative_artifact_path(repo, transcript_path) if transcript_path.exists() else None,
                "tail": transcript_tail,
                "tail_truncated": transcript_path.exists() and len(transcript_tail) >= 4000,
            },
            "artifacts": {
                "run_dir": str(run_dir),
                "artifact_dir": relative_artifact_path(repo, artifact_dir),
                "pane_capture_latest": relative_artifact_path(repo, previous_path),
                "transcript_path": relative_artifact_path(repo, transcript_path) if transcript_path.exists() else None,
                "operational_events": str(operational_events_file(repo, state)),
                "observation_latest": relative_artifact_path(repo, artifact_dir / "observation-latest.json"),
            },
            "result": result,
            "operational_hints": hints,
        }
    )
    return snapshot


def record_observation(repo: Path, state: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    current = state.get("current_slice") if isinstance(state.get("current_slice"), dict) else None
    event = append_operational_event(
        repo,
        state,
        {
            "kind": "observation",
            "status": "recorded",
            "slice_id": current.get("slice_id") if current else None,
            "attempt": current.get("attempt") if current else None,
            "evidence_path": snapshot.get("pane", {}).get("capture_path") if isinstance(snapshot.get("pane"), dict) else "",
            "process_running": snapshot.get("process", {}).get("running"),
            "result_exists": snapshot.get("result", {}).get("exists"),
            "hard_prompt": snapshot.get("prompt_on_screen", {}),
            "hard_stop_hints": _hard_stop_hint_kinds(snapshot),
        },
    )
    snapshot["operational_event_id"] = event["event_id"]
    current_snapshot = snapshot.get("current_slice") if isinstance(snapshot.get("current_slice"), dict) else None
    if current_snapshot:
        artifact_dir = _slice_artifact_dir(repo, current_snapshot)
        (artifact_dir / "observation-latest.json").write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return snapshot


def observe(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    state = load_run(run_dir)
    snapshot = record_observation(repo, state, build_observation(args, repo, run_dir, state))
    _json_print(snapshot)
    return 0


def send(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    state = load_run(run_dir)
    current = state.get("current_slice") if isinstance(state.get("current_slice"), dict) else None
    if not current:
        raise McError("run has no current slice")
    if state.get("status") not in {"running", "paused", "resuming"}:
        raise McError(f"run status is not sendable: {state.get('status')}")
    snapshot = record_observation(repo, state, build_observation(args, repo, run_dir, state))
    hard_prompt = snapshot.get("prompt_on_screen", {})
    if isinstance(hard_prompt, dict) and hard_prompt.get("present"):
        raise McError("refusing to send while hard prompt is visible: " + ", ".join(hard_prompt.get("kinds", [])))
    _raise_on_hard_stop_hints(snapshot, "send")
    session_name = str(current.get("tmux_session") or "")
    _current_adapter(args, repo, state).send_literal(session_name, args.text)
    event = append_operational_event(
        repo,
        state,
        {
            "kind": "send",
            "status": "sent",
            "slice_id": current.get("slice_id"),
            "attempt": current.get("attempt"),
            "tmux_session": session_name,
            "text": args.text,
            "reason": args.reason,
            "evidence_event_id": snapshot.get("operational_event_id"),
        },
    )
    _json_print({"sent": True, "event_id": event["event_id"], "tmux_session": session_name})
    return 0


def start_slice(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    state = load_run(run_dir)
    plan = resolve_plan(Path(state["plan_path"]))
    verify_plan_unchanged(state, plan)
    slices = parse_plan(plan)
    candidate = next_slice(slices, state)
    if candidate is None:
        _json_print({"started": False, "reason": "no remaining slices"})
        return 0
    result = start_model_supervised_slice(args, repo, state, candidate, run_dir)
    append_operational_event(
        repo,
        load_run(run_dir),
        {
            "kind": "start_slice",
            "status": "started" if result.get("started") else "not_started",
            "slice_id": candidate.slice_id,
            "attempt": result.get("attempt"),
            "tmux_session": result.get("tmux_session"),
            "artifact_dir": result.get("artifact_dir"),
            "reason": result.get("reason", ""),
        },
    )
    _json_print(result)
    return 0 if result.get("started") else 2


def _wait_observing(args: argparse.Namespace, repo: Path, run_dir: Path, seconds: float) -> tuple[str, dict[str, Any]]:
    deadline = time.monotonic() + max(0.0, float(seconds))
    final_snapshot: dict[str, Any] = {}
    reason = "timeout"
    while True:
        state = load_run(run_dir)
        final_snapshot = record_observation(repo, state, build_observation(args, repo, run_dir, state))
        if final_snapshot.get("result", {}).get("exists"):
            reason = "result-ready"
            break
        if not final_snapshot.get("process", {}).get("running"):
            reason = "process-exited"
            break
        hard_prompt = final_snapshot.get("prompt_on_screen", {})
        if isinstance(hard_prompt, dict) and hard_prompt.get("present"):
            reason = "hard-prompt"
            break
        if _hard_stop_hint_kinds(final_snapshot):
            reason = "hard-stop-hint"
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(min(float(args.poll_seconds), max(0.0, deadline - time.monotonic())))
    return reason, final_snapshot


def wait(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    reason, final_snapshot = _wait_observing(args, repo, run_dir, float(args.seconds))
    append_operational_event(
        repo,
        load_run(run_dir),
        {
            "kind": "wait",
            "status": reason,
            "slice_id": final_snapshot.get("current_slice", {}).get("slice_id") if isinstance(final_snapshot.get("current_slice"), dict) else None,
            "attempt": final_snapshot.get("current_slice", {}).get("attempt") if isinstance(final_snapshot.get("current_slice"), dict) else None,
            "wait_seconds": args.seconds,
            "evidence_event_id": final_snapshot.get("operational_event_id"),
        },
    )
    _json_print({"wait_status": reason, "observation": final_snapshot})
    return 0


def pause_until(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    state = load_run(run_dir)
    current = state.get("current_slice") if isinstance(state.get("current_slice"), dict) else None
    if not current:
        raise McError("run has no current slice")
    snapshot = record_observation(repo, state, build_observation(args, repo, run_dir, state))
    hard_prompt = snapshot.get("prompt_on_screen", {})
    if isinstance(hard_prompt, dict) and hard_prompt.get("present"):
        raise McError("refusing to pause while hard prompt is visible: " + ", ".join(hard_prompt.get("kinds", [])))
    _raise_on_hard_stop_hints(snapshot, "pause")
    try:
        until = parse_iso_datetime(args.until)
    except ValueError as exc:
        raise McError(f"invalid --until timestamp: {exc}") from exc
    buffer_seconds = args.buffer_seconds
    if buffer_seconds is None:
        buffer_seconds = int(state.get("supervision", {}).get("default_reset_buffer_seconds", 180))
    target = until.astimezone(timezone.utc).timestamp() + int(buffer_seconds)
    now = datetime.now(timezone.utc).timestamp()
    pause_seconds = max(0, int(target - now))
    supervision = state.get("supervision", {})
    counters = supervision.get("pause_counters", {}) if isinstance(supervision.get("pause_counters"), dict) else {}
    if pause_seconds > int(supervision.get("max_single_pause_seconds", 21600)):
        raise McError("pause exceeds max_single_pause_seconds")
    if int(counters.get("consecutive_pauses_current_slice", 0)) + 1 > int(supervision.get("max_consecutive_pauses_per_slice", 2)):
        raise McError("pause exceeds max_consecutive_pauses_per_slice")
    if int(counters.get("cumulative_pause_seconds_run", 0)) + pause_seconds > int(supervision.get("max_cumulative_pause_seconds_per_run", 43200)):
        raise McError("pause exceeds max_cumulative_pause_seconds_per_run")

    event = append_operational_event(
        repo,
        state,
        {
            "kind": "pause",
            "status": "started",
            "slice_id": current.get("slice_id"),
            "attempt": current.get("attempt"),
            "decision": "pause-until",
            "reason": args.reason,
            "resume_at": datetime.fromtimestamp(target, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "evidence_event_id": snapshot.get("operational_event_id"),
        },
    )

    def mark_paused(run_state: dict[str, Any]) -> None:
        run_state["status"] = "paused"
        run_state.setdefault("supervision", {}).setdefault("pause_counters", {})
        run_state["supervision"]["pause_counters"]["consecutive_pauses_current_slice"] = int(counters.get("consecutive_pauses_current_slice", 0)) + 1
        run_state["supervision"]["pause_counters"]["cumulative_pause_seconds_run"] = int(counters.get("cumulative_pause_seconds_run", 0)) + pause_seconds
        if isinstance(run_state.get("current_slice"), dict):
            run_state["current_slice"]["pause"] = {
                "paused_until": datetime.fromtimestamp(target, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "reason": args.reason,
                "evidence_event_id": event["event_id"],
            }

    update_run_locked(run_dir / "run.json", mark_paused)
    wait_args = copy.copy(args)
    wait_args.poll_seconds = args.poll_seconds
    wait_reason, wait_snapshot = _wait_observing(wait_args, repo, run_dir, pause_seconds)
    append_operational_event(
        repo,
        load_run(run_dir),
        {
            "kind": "wait",
            "status": wait_reason,
            "slice_id": wait_snapshot.get("current_slice", {}).get("slice_id") if isinstance(wait_snapshot.get("current_slice"), dict) else None,
            "attempt": wait_snapshot.get("current_slice", {}).get("attempt") if isinstance(wait_snapshot.get("current_slice"), dict) else None,
            "wait_seconds": pause_seconds,
            "evidence_event_id": wait_snapshot.get("operational_event_id"),
        },
    )

    def mark_resuming(run_state: dict[str, Any]) -> None:
        if run_state.get("status") == "paused":
            run_state["status"] = "resuming"
        if isinstance(run_state.get("current_slice"), dict):
            run_state["current_slice"]["pause"] = None

    update_run_locked(run_dir / "run.json", mark_resuming)
    _json_print({"paused": True, "pause_seconds": pause_seconds, "event_id": event["event_id"], "wait_status": wait_reason})
    return 0


def finalize_slice(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    state = load_run(run_dir)
    current = state.get("current_slice") if isinstance(state.get("current_slice"), dict) else None
    if not current:
        raise McError("run has no current slice")
    plan = resolve_plan(Path(state["plan_path"]))
    verify_plan_unchanged(state, plan)
    slices = parse_plan(plan)
    plan_slice = plan_slice_by_id(slices, str(current.get("slice_id")))
    if plan_slice is None:
        raise McError(f"current slice is not in the plan: {current.get('slice_id')}")
    result = finalize_model_supervised_slice(args, repo, state, plan_slice, run_dir)
    append_operational_event(
        repo,
        load_run(run_dir),
        {
            "kind": "finalize_slice",
            "status": result.get("status"),
            "slice_id": plan_slice.slice_id,
            "attempt": current.get("attempt"),
            "reason": result.get("reason"),
        },
    )
    _json_print(result)
    return 0 if result.get("status") in {"pass", "repairable"} else 2


def stop_with_evidence(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    state = load_run(run_dir)
    current = state.get("current_slice") if isinstance(state.get("current_slice"), dict) else None
    if not current:
        raise McError("run has no current slice")
    artifact_dir = _slice_artifact_dir(repo, current)
    attempt = int(current.get("attempt") or 1)
    session_name = str(current.get("tmux_session") or "")
    adapter = _current_adapter(args, repo, state)
    adapter.capture(session_name, artifact_dir / f"pane-capture-stop-attempt-{attempt}.txt")
    adapter.capture(session_name, artifact_dir / "pane-capture.txt")
    capture_orchestrator_transcript(
        state["harness"]["name"],
        repo,
        str(current.get("orchestrator_session_id")) if current.get("orchestrator_session_id") else None,
        artifact_dir,
    )
    capture_worker_runs_summary(artifact_dir)
    after_head = git_head(repo)
    after_status = git_status_text(repo)
    (artifact_dir / f"git-status-after-attempt-{attempt}.txt").write_text(after_status, encoding="utf-8")
    (artifact_dir / "git-status-after.txt").write_text(after_status, encoding="utf-8")
    write_git_diff(repo, str(current.get("before_head") or "") or None, after_head, artifact_dir / "git-diff.patch")
    adapter.request_stop(session_name)
    time.sleep(0.5)
    adapter.force_stop(session_name)
    append_operational_event(
        repo,
        state,
        {
            "kind": "stop_with_evidence",
            "status": args.status,
            "slice_id": current.get("slice_id"),
            "attempt": attempt,
            "reason": args.reason,
            "evidence_path": relative_artifact_path(repo, artifact_dir / "pane-capture.txt"),
        },
    )
    update_state_for_stop(run_dir / "run.json", state, args.status, args.reason)
    _json_print({"stopped": True, "status": args.status, "reason": args.reason, "artifact_dir": relative_artifact_path(repo, artifact_dir)})
    return 0


def run_next(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    state = load_run(run_dir)
    plan = resolve_plan(Path(state["plan_path"]))
    verify_plan_unchanged(state, plan)
    slices = parse_plan(plan)
    if not slices:
        raise McError("plan contains no slices")
    candidate = next_slice(slices, state)
    if candidate is None:
        print("No remaining slices.")
        return 0
    if not args.dry_run:
        # execute_slice owns the runtime eligibility gate and the stop-state
        # write; run_next only reports for --dry-run.
        return execute_slice(args, repo, state, candidate, run_dir)
    runnable, reasons = eligibility(candidate)
    print(f"Next slice: {candidate.slice_id} - {candidate.title}")
    if runnable:
        print("Eligibility: runnable")
        print("Authorized files:")
        for path in candidate.authorized_files:
            print(f"- {path}")
        return 0
    print("Eligibility: blocked")
    for reason in reasons:
        print(f"- {reason}")
    return 2


def run_remaining(args: argparse.Namespace) -> int:
    if args.scope != "remaining":
        raise McError("only --scope remaining is supported")
    repo = resolve_repo(Path(args.repo))
    while True:
        run_dir = resolve_run_dir(repo, args.run)
        state = load_run(run_dir)
        if state.get("status") in RUN_STOP_STATUSES:
            print(f"Run is stopped: {state['status']}")
            return 2
        plan = resolve_plan(Path(state["plan_path"]))
        verify_plan_unchanged(state, plan)
        slices = parse_plan(plan)
        if next_slice(slices, state) is None:
            state["status"] = "complete"
            state["current_slice"] = None
            state["stop_reason"] = None
            write_run(run_dir / "run.json", state)
            print("All slices complete.")
            return 0
        code = run_next(args)
        if code != 0:
            return code


def reconcile(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    run_json = run_dir / "run.json"
    state = load_run(run_dir)
    if not state.get("slices"):
        raise McError("run has no slice entries to reconcile")
    entry_index = len(state["slices"]) - 1
    entry = state["slices"][entry_index]
    if str(entry.get("status", "")).lower() in COMPLETED_SLICE_STATUSES:
        print(f"{entry.get('slice_id', 'unknown')} is already complete.")
        return 0
    slice_id = str(entry.get("slice_id", ""))
    plan = resolve_plan(Path(state["plan_path"]))
    verify_plan_unchanged(state, plan)
    slices = parse_plan(plan)
    plan_slice = plan_slice_by_id(slices, slice_id)
    if plan_slice is None:
        raise McError(f"failed slice is not in the plan: {slice_id}")
    artifact_dir_value = entry.get("artifact_dir")
    if not artifact_dir_value:
        raise McError(f"failed slice has no artifact_dir: {slice_id}")
    artifact_dir = Path(artifact_dir_value)
    if not artifact_dir.is_absolute():
        artifact_dir = repo / artifact_dir
    # Prefer the boundary the slice actually recorded; only fall back to
    # inference for entries written before before_head was tracked. Guessing
    # HEAD^ misses a slice's earlier commits and can let an unauthorized file
    # from a first commit escape the changed-file check.
    before_head = entry.get("before_head") or previous_completed_head(state, slice_id)
    if before_head is None:
        parent = git_result(repo, "rev-parse", "HEAD^")
        before_head = parent.stdout.strip() if parent.returncode == 0 else None
    after_head = git_head(repo)
    after_status = git_status_text(repo)
    capture_worker_runs_summary(artifact_dir)
    gate = verify_gate(repo, state, plan_slice, artifact_dir, before_head, after_head, after_status)
    reconciled_entry = slice_entry_from_gate(repo, plan_slice, artifact_dir, str(entry.get("started_at") or utc_now()), gate, before_head)
    state["slices"][entry_index] = reconciled_entry
    state["current_slice"] = None
    if gate.status == "pass":
        state["status"] = idle_status_after_pass(state)
        state["stop_reason"] = None
        write_run(run_json, state)
        print(f"{slice_id} reconciled and accepted: {gate.reason}")
        return 0
    state["status"] = normalize_stop_status(gate.status)
    state["stop_reason"] = gate.reason
    write_run(run_json, state)
    print(f"{slice_id} remains stopped: {gate.reason}")
    return 2


def stop(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    state = load_run(run_dir)
    current = state.get("current_slice") or {}
    session_name = current.get("tmux_session")
    if session_name:
        adapter = TmuxHarnessAdapter(
            state["harness"]["name"],
            resolve_harness_command(args, repo, state),
            getattr(args, "allow_unattended_default", False),
        )
        adapter.request_stop(str(session_name))
        time.sleep(0.5)
        adapter.force_stop(str(session_name))
    update_state_for_stop(run_dir / "run.json", state, "cancelled", args.reason)
    print(f"Run cancelled: {args.reason}")
    return 0


def print_check(label: str, ok: bool, detail: str = "") -> None:
    status_value = "PASS" if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"{status_value}: {label}{suffix}")


def list_profiles(args: argparse.Namespace) -> int:
    for name, profile in sorted(HARNESS_PROFILES.items()):
        print(f"{name}")
        print(f"  roles: {', '.join(profile.get('roles', []))}")
        base = profile.get("base_command") or []
        print(f"  base_command: {shlex.join(base)}")
        for note in profile.get("notes", []):
            print(f"  - {note}")
    return 0


def preflight(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    state = load_run(run_dir)
    errors: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print_check(label, ok, detail)
        if not ok:
            errors.append(label if not detail else f"{label}: {detail}")

    check("target repo", repo.exists(), str(repo))
    check("git worktree", git_result(repo, "rev-parse", "--is-inside-work-tree").returncode == 0)
    check("tmux available", shutil.which("tmux") is not None, shutil.which("tmux") or "not found")

    harness_name = state["harness"]["name"]
    executable = shlex.split(getattr(args, "harness_command", "") or harness_name)[0]
    if getattr(args, "harness_model", None) and not getattr(args, "allow_profile_command", False):
        check("harness model composition", False, "--harness-model requires --allow-profile-command")
    if getattr(args, "allow_profile_command", False):
        try:
            command = profile_command(harness_name, repo, state, parse_worker_tools(args.worker_tools), harness_model=getattr(args, "harness_model", None))
            executable = shlex.split(command)[0]
            check("profile command", True, command)
        except McError as exc:
            check("profile command", False, str(exc))
    check("harness executable", shutil.which(executable) is not None, f"{executable}: {shutil.which(executable) or 'not found'}")
    check("harness orchestrator role", harness_supports_role(harness_name, "orchestrator"), harness_name)

    # Resolve and preflight the exact launch command run-next would use, so
    # preflight cannot pass a configuration the run then refuses (for example a
    # bare interactive `codex`/`claude` that would deadlock without
    # --allow-profile-command, --harness-command, or --allow-unattended-default).
    try:
        session_hint = "preflight-session" if harness_name == "claude" else None
        launch_adapter = TmuxHarnessAdapter(
            harness_name,
            resolve_harness_command(args, repo, state, session_hint),
            getattr(args, "allow_unattended_default", False),
            parse_worker_tools(args.worker_tools),
        )
        launch_adapter.preflight()
        check("harness launch resolves", True, launch_adapter.command)
    except McError as exc:
        check("harness launch resolves", False, str(exc))

    plan_path = resolve_plan(Path(state["plan_path"]))
    try:
        verify_plan_unchanged(state, plan_path)
        plan_is_current = True
        plan_detail = str(plan_path)
    except McError as exc:
        plan_is_current = False
        plan_detail = str(exc)
    check("plan file", plan_path.exists(), str(plan_path))
    check("plan unchanged", plan_is_current, plan_detail)
    slices = parse_plan(plan_path) if plan_is_current else []
    candidate = next_slice(slices, state)
    check("remaining slice", candidate is not None, candidate.slice_id if candidate else "none")
    if candidate:
        runnable, reasons = eligibility(candidate)
        check("slice eligibility", runnable, "; ".join(reasons) if reasons else candidate.title)
        proposed_artifact_dir = run_dir / "slices" / slice_dir_name(candidate)
        check("run directory writable", os.access(run_dir, os.W_OK), str(run_dir))
        check("worker helper", worker_jobs_path().exists(), str(worker_jobs_path()))
        check("result schema", result_schema_path().exists(), str(result_schema_path()))
        for label, path in slice_paths(proposed_artifact_dir).items():
            parent = nearest_existing_parent(path)
            check(f"{label} parent writable", os.access(parent, os.W_OK), str(path))

    if state.get("policy", {}).get("commit_required", True):
        try:
            git_dir = git_access_path(repo)
            check("git directory writable", os.access(git_dir, os.W_OK), str(git_dir))
        except McError as exc:
            check("git directory writable", False, str(exc))

    worker_tools = parse_worker_tools(args.worker_tools)
    if worker_tools:
        unsupported = [tool for tool in worker_tools if tool not in HARNESS_PROFILES]
        check("worker profiles known", not unsupported, ", ".join(unsupported) if unsupported else ", ".join(worker_tools))
        if harness_name == "codex" and not (getattr(args, "allow_profile_command", False) or "sandbox_workspace_write.network_access=true" in (args.harness_command or "")):
            check("codex worker network launch", False, "use --allow-profile-command or include sandbox workspace network access in --harness-command")
        else:
            check("worker-enabled launch", True, ", ".join(worker_tools))
        for tool in worker_tools:
            if tool == harness_name:
                continue
            source = worker_credential_source(tool)
            if source is None:
                continue
            source_dir, filename = source
            credential_path = source_dir / filename
            check(f"{tool} worker credential source", credential_path.exists(), str(credential_path))

    if meaningful_status_lines(git_status_text(repo)):
        check("clean worktree", False, "dirty outside .ai-mc/")
    else:
        check("clean worktree", True)

    if errors:
        print("Preflight failed.")
        return 2
    print("Preflight passed.")
    return 0


def archive_sensitive(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    targets = sensitive_artifact_dirs(run_dir)
    if not targets:
        print("No sensitive worker artifact directories found.")
        return 0
    archive_root = repo / ".ai-mc" / "sensitive-archive" / run_dir.name
    for source in targets:
        relative = source.relative_to(run_dir)
        destination = archive_root / relative
        print(f"{source} -> {destination}")
        if args.dry_run:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise McError(f"archive destination already exists: {destination}")
        shutil.move(str(source), str(destination))
        marker = source.parent / f"{source.name}-ARCHIVED.txt"
        marker.write_text(f"Sensitive worker state archived to {destination}\n", encoding="utf-8")
    print("Dry run complete." if args.dry_run else "Sensitive worker artifacts archived.")
    return 0


def nearest_existing_parent(path: Path) -> Path:
    current = path if path.exists() else path.parent
    while not current.exists() and current != current.parent:
        current = current.parent
    return current
