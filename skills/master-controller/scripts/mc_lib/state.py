from __future__ import annotations

import copy
import fcntl
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .constants import COMPLETED_SLICE_STATUSES, DEFAULT_SUPERVISION, OPERATIONAL_EVENTS_FILENAME, RUN_STOP_STATUSES
from .git_ops import git_result
from .models import GateDecision, McError, PlanSlice
from .plan import completed_slice_ids
from .runtime import relative_artifact_path
from .utils import utc_now


def normalize_stop_status(gate_status: str) -> str:
    """Map a non-passing gate status onto an allowed run stop status."""
    status_value = "failed" if gate_status == "fail" else gate_status
    return status_value if status_value in RUN_STOP_STATUSES else "blocked"


def load_run(run_path: Path) -> dict[str, Any]:
    path = run_json_path(run_path)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise McError(f"run.json not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise McError(f"invalid run.json: {path}: {exc}") from exc
    return normalize_run_state(state, path.parent)


def write_run(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_json_path(run_path: Path) -> Path:
    path = run_path.expanduser().resolve()
    return path / "run.json" if path.is_dir() else path


def _merge_missing(base: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in defaults.items():
        if key not in merged:
            merged[key] = copy.deepcopy(value)
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_missing(merged[key], value)
    return merged


def default_operational_events_path(state: dict[str, Any], run_dir: Path) -> str:
    repo_path = state.get("repo_path")
    if repo_path:
        repo = Path(str(repo_path))
        try:
            return relative_artifact_path(repo, run_dir / OPERATIONAL_EVENTS_FILENAME)
        except ValueError:
            pass
    return str((run_dir / OPERATIONAL_EVENTS_FILENAME).resolve())


def normalize_run_state(state: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Apply backwards-compatible defaults to loaded run state."""
    normalized = dict(state)
    supervision = normalized.get("supervision")
    normalized["supervision"] = _merge_missing(supervision if isinstance(supervision, dict) else {}, DEFAULT_SUPERVISION)
    if not normalized.get("operational_events_path"):
        normalized["operational_events_path"] = default_operational_events_path(normalized, run_dir)
    return normalized


def update_run_locked(run_json: Path, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Update run.json under a per-run advisory lock."""
    path = run_json_path(run_json)
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = load_run(path)
        mutate(state)
        write_run(path, state)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return state


def operational_events_file(repo: Path, state: dict[str, Any]) -> Path:
    value = str(state.get("operational_events_path") or "")
    if not value:
        path = Path(default_operational_events_path(state, repo / ".ai-mc" / "runs" / str(state.get("run_id", ""))))
        return path if path.is_absolute() else repo / path
    path = Path(value)
    return path if path.is_absolute() else repo / path


def append_operational_event(repo: Path, state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Append one operational event without rewriting run.json."""
    event_path = operational_events_file(repo, state)
    event_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = event_path.with_suffix(event_path.suffix + ".lock")
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        record = dict(event)
        if "event_id" not in record:
            if event_path.exists():
                with event_path.open(encoding="utf-8") as handle:
                    next_number = sum(1 for _ in handle) + 1
            else:
                next_number = 1
            record["event_id"] = f"op-{next_number:04d}"
        record.setdefault("detected_at", utc_now())
        with event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return record


def resolve_run_path(repo: Path, value: str) -> Path:
    if value == "current":
        return repo / ".ai-mc" / "current"
    return Path(value).expanduser().resolve()


def resolve_run_dir(repo: Path, value: str) -> Path:
    path = resolve_run_path(repo, value).resolve()
    return path.parent if path.is_file() else path


def update_state_for_stop(run_json: Path, state: dict[str, Any], status_value: str, reason: str) -> None:
    state["status"] = status_value
    state["stop_reason"] = reason
    state["current_slice"] = None
    write_run(run_json, state)


def idle_status_after_pass(state: dict[str, Any]) -> str:
    return "complete" if len(completed_slice_ids(state)) >= state["plan"]["slice_count"] else "partial"


def current_slice_state(
    repo: Path,
    plan_slice: PlanSlice,
    slice_artifact_dir: Path,
    session_name: str,
    attempt: int,
    started_at: str,
    before_head: str | None,
    orchestrator_session_id: str | None = None,
    worker_tools: tuple[str, ...] = (),
) -> dict[str, Any]:
    state = {
        "slice_id": plan_slice.slice_id,
        "title": plan_slice.title,
        "artifact_dir": relative_artifact_path(repo, slice_artifact_dir),
        "tmux_session": session_name,
        "attempt": attempt,
        "started_at": started_at,
        "before_head": before_head,
        "pause": None,
        # Persisted so a later, separate invocation (finalize-slice,
        # stop-with-evidence) can recover the worker-tool requirement for
        # this slice attempt without depending on that invocation's own
        # --worker-tools flag, which may not be re-supplied.
        "worker_tools": list(worker_tools),
    }
    if orchestrator_session_id:
        state["orchestrator_session_id"] = orchestrator_session_id
    return state


def slice_entry_from_gate(
    repo: Path,
    plan_slice: PlanSlice,
    slice_artifact_dir: Path,
    started_at: str,
    gate: GateDecision,
    before_head: str | None = None,
    worker_tools: tuple[str, ...] = (),
) -> dict[str, Any]:
    result = gate.result or {}
    return {
        "slice_id": plan_slice.slice_id,
        "title": plan_slice.title,
        "status": gate.status,
        "started_at": started_at,
        "completed_at": utc_now(),
        "artifact_dir": relative_artifact_path(repo, slice_artifact_dir),
        # The commit HEAD immediately before this slice's work started. reconcile
        # uses it to recompute changed files against the exact slice boundary
        # instead of guessing HEAD^ (which misses a slice's earlier commits).
        "before_head": before_head,
        "changed_files": list(gate.actual_changed_files or tuple(result.get("changed_files") or ())),
        "validation": result.get("validation", []),
        "drift_audit": result.get("drift_audit", {"verdict": None, "path": ""}),
        "code_review": result.get("code_review", {"verdict": None, "path": ""}),
        "commit": result.get("commit", {"requested": False, "created": False, "hash": None}),
        "next_action": result.get("next_action", ""),
        "blockers": result.get("blockers", []),
        "gate_reason": gate.reason,
        # Preserved (not just read) so reconcile can recover the worker-tool
        # requirement for this attempt without a fresh --worker-tools flag.
        "worker_tools": list(worker_tools),
    }


def previous_completed_head(state: dict[str, Any], slice_id: str) -> str | None:
    previous: str | None = None
    for entry in state.get("slices", []):
        if entry.get("slice_id") == slice_id:
            return previous
        if str(entry.get("status", "")).lower() in COMPLETED_SLICE_STATUSES:
            commit = entry.get("commit") if isinstance(entry.get("commit"), dict) else {}
            if commit.get("hash"):
                previous = str(commit["hash"])
    return previous
