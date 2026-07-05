from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import COMPLETED_SLICE_STATUSES, RUN_STOP_STATUSES
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
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise McError(f"run.json not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise McError(f"invalid run.json: {path}: {exc}") from exc


def write_run(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_json_path(run_path: Path) -> Path:
    path = run_path.expanduser().resolve()
    return path / "run.json" if path.is_dir() else path


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


def slice_entry_from_gate(
    repo: Path,
    plan_slice: PlanSlice,
    slice_artifact_dir: Path,
    started_at: str,
    gate: GateDecision,
    before_head: str | None = None,
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
