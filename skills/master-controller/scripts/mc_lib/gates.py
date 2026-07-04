from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import ORCHESTRATOR_STATUSES, SCHEMA_VERSION
from .git_ops import (
    changed_files_between,
    commit_is_descendant,
    is_full_commit_hash,
    meaningful_status_lines,
    unauthorized_files,
)
from .models import GateDecision, McError, PlanSlice
from .utils import utc_now


def write_reconciliation_artifact(
    slice_artifact_dir: Path,
    *,
    field: str,
    reported_value: str,
    corrected_value: str,
    reason: str,
) -> None:
    payload = {
        "field": field,
        "reported_value": reported_value,
        "corrected_value": corrected_value,
        "reason": reason,
        "reconciled_at": utc_now(),
    }
    (slice_artifact_dir / "mc-reconciliation.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (slice_artifact_dir / "mc-reconciliation.md").write_text(
        "# MC Reconciliation\n\n"
        f"- Field: `{field}`\n"
        f"- Reported value: `{reported_value}`\n"
        f"- Corrected value: `{corrected_value}`\n"
        f"- Reason: {reason}\n",
        encoding="utf-8",
    )


def write_orchestrator_result(path: Path, result: dict[str, Any]) -> None:
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_orchestrator_result(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise McError(f"orchestrator result missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise McError(f"invalid orchestrator result: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise McError(f"orchestrator result is not an object: {path}")
    return data


def artifact_exists(repo: Path, slice_artifact_dir: Path, result: dict[str, Any], field: str, default_name: str) -> bool:
    configured = result.get(field, {}).get("path") if isinstance(result.get(field), dict) else None
    if not configured:
        return (slice_artifact_dir / default_name).exists()
    candidate = Path(configured)
    if candidate.is_absolute():
        return candidate.exists()
    return any((base / candidate).exists() for base in (slice_artifact_dir, repo))


def object_field(result: dict[str, Any], field: str) -> dict[str, Any]:
    value = result.get(field)
    return value if isinstance(value, dict) else {}


def verify_gate(
    repo: Path,
    state: dict[str, Any],
    plan_slice: PlanSlice,
    slice_artifact_dir: Path,
    before_head: str | None,
    after_head: str | None,
    after_status: str,
) -> GateDecision:
    result_path = slice_artifact_dir / "orchestrator-result.json"
    try:
        result = load_orchestrator_result(result_path)
    except McError as exc:
        return GateDecision("blocked", str(exc))

    if result.get("schema_version") != SCHEMA_VERSION:
        return GateDecision("fail", "orchestrator result schema_version is missing or unsupported", result)
    if result.get("slice_id") != plan_slice.slice_id:
        return GateDecision("fail", "orchestrator result slice_id does not match selected slice", result)

    status_value = str(result.get("status", "")).lower()
    if status_value not in ORCHESTRATOR_STATUSES:
        return GateDecision("fail", f"orchestrator result status is invalid: {result.get('status')}", result)
    if status_value != "pass":
        return GateDecision(status_value, f"orchestrator reported {status_value}", result)

    actual_changed = changed_files_between(repo, before_head, after_head, after_status)
    unauthorized = unauthorized_files(actual_changed, plan_slice.authorized_files)
    if unauthorized:
        return GateDecision("fail", "unauthorized changed files: " + ", ".join(unauthorized), result, tuple(sorted(actual_changed)))

    reported_changed = set(result.get("changed_files") or [])
    if actual_changed != reported_changed:
        return GateDecision(
            "fail",
            "orchestrator changed_files does not match git evidence",
            result,
            tuple(sorted(actual_changed)),
        )

    validation = result.get("validation")
    if not isinstance(validation, list) or not validation:
        return GateDecision("fail", "validation evidence is missing", result, tuple(sorted(actual_changed)))
    failing_validation = [entry for entry in validation if str(entry.get("result", "")).lower() != "pass"]
    if failing_validation:
        return GateDecision("fail", "validation did not pass", result, tuple(sorted(actual_changed)))
    if not (slice_artifact_dir / "validation-summary.md").exists():
        return GateDecision("fail", "validation-summary.md is missing", result, tuple(sorted(actual_changed)))

    drift_verdict = str(object_field(result, "drift_audit").get("verdict", "")).upper()
    if drift_verdict != "PASS":
        return GateDecision("needs-human", f"drift audit verdict is not PASS: {drift_verdict or 'missing'}", result, tuple(sorted(actual_changed)))
    if not artifact_exists(repo, slice_artifact_dir, result, "drift_audit", "drift-audit.md"):
        return GateDecision("fail", "drift audit artifact is missing", result, tuple(sorted(actual_changed)))

    review_verdict = str(object_field(result, "code_review").get("verdict", "")).upper()
    if review_verdict != "PASS":
        return GateDecision("fail", f"code review verdict is not PASS: {review_verdict or 'missing'}", result, tuple(sorted(actual_changed)))
    if not artifact_exists(repo, slice_artifact_dir, result, "code_review", "code-review.md"):
        return GateDecision("fail", "code review artifact is missing", result, tuple(sorted(actual_changed)))

    commit = result.get("commit") if isinstance(result.get("commit"), dict) else {}
    if state.get("policy", {}).get("commit_required", True):
        if not commit.get("requested") or not commit.get("created") or not commit.get("hash"):
            return GateDecision("fail", "required commit was not created", result, tuple(sorted(actual_changed)))
        if meaningful_status_lines(after_status):
            return GateDecision("fail", "post-commit worktree is dirty outside .ai-mc/", result, tuple(sorted(actual_changed)))
        if not after_head or after_head == before_head:
            return GateDecision("fail", "required commit did not advance HEAD", result, tuple(sorted(actual_changed)))
        reported_hash = str(commit["hash"])
        if reported_hash != after_head:
            if not commit_is_descendant(repo, before_head, after_head):
                return GateDecision("fail", "current HEAD is not descended from the slice starting commit", result, tuple(sorted(actual_changed)))
            reason = (
                "orchestrator reported an incorrect commit hash, but MC proved the slice commit from local git "
                "evidence and corrected orchestrator-result.json"
            )
            result.setdefault("reconciliations", []).append(
                {
                    "field": "commit.hash",
                    "reported_value": reported_hash,
                    "corrected_value": after_head,
                    "reason": reason,
                    "reconciled_at": utc_now(),
                }
            )
            result["commit"]["hash"] = after_head
            write_orchestrator_result(result_path, result)
            write_reconciliation_artifact(
                slice_artifact_dir,
                field="commit.hash",
                reported_value=reported_hash,
                corrected_value=after_head,
                reason=reason,
            )
            return GateDecision("pass", "all gates passed; corrected reported commit hash to current HEAD", result, tuple(sorted(actual_changed)))
        if not is_full_commit_hash(reported_hash):
            reason = "orchestrator reported an abbreviated commit hash; MC corrected it to the full current HEAD"
            result.setdefault("reconciliations", []).append(
                {
                    "field": "commit.hash",
                    "reported_value": reported_hash,
                    "corrected_value": after_head,
                    "reason": reason,
                    "reconciled_at": utc_now(),
                }
            )
            result["commit"]["hash"] = after_head
            write_orchestrator_result(result_path, result)
            write_reconciliation_artifact(
                slice_artifact_dir,
                field="commit.hash",
                reported_value=reported_hash,
                corrected_value=after_head,
                reason=reason,
            )
            return GateDecision("pass", "all gates passed; expanded reported commit hash to full current HEAD", result, tuple(sorted(actual_changed)))

    return GateDecision("pass", "all gates passed", result, tuple(sorted(actual_changed)))
