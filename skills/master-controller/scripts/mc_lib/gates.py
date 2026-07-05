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


def _within(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def artifact_exists(repo: Path, slice_artifact_dir: Path, result: dict[str, Any], field: str, default_name: str) -> bool:
    """Return True only for a real, non-empty evidence file inside the run.

    A verdict string in orchestrator-result.json is not enough on its own: MC
    also requires the named artifact to exist as a non-empty file that lives
    under the slice artifact directory or the repo. That stops a result from
    satisfying the gate by pointing `path` at an arbitrary existing file (for
    example `/etc/hosts`) or at an empty placeholder.
    """
    configured = result.get(field, {}).get("path") if isinstance(result.get(field), dict) else None
    if not configured:
        candidates = [slice_artifact_dir / default_name]
    else:
        candidate = Path(configured)
        candidates = [candidate] if candidate.is_absolute() else [slice_artifact_dir / candidate, repo / candidate]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            non_empty = candidate.stat().st_size > 0
        except OSError:
            continue
        if non_empty and (_within(candidate, slice_artifact_dir) or _within(candidate, repo)):
            return True
    return False


def object_field(result: dict[str, Any], field: str) -> dict[str, Any]:
    value = result.get(field)
    return value if isinstance(value, dict) else {}


def _validation_status(validation: Any) -> str | None:
    """Return a gate-failure reason for the validation block, or None if it passes."""
    if not isinstance(validation, list) or not validation:
        return "validation evidence is missing"
    if not all(isinstance(entry, dict) for entry in validation):
        return "validation entries are malformed (expected objects)"
    if any(str(entry.get("result", "")).lower() != "pass" for entry in validation):
        return "validation did not pass"
    return None


def _apply_commit_hash_reconciliation(
    result: dict[str, Any],
    result_path: Path,
    slice_artifact_dir: Path,
    *,
    reported_hash: str,
    corrected_hash: str,
    record_reason: str,
    decision_reason: str,
    actual_changed: tuple[str, ...],
) -> GateDecision:
    """Correct the reported commit hash to the proven HEAD and record why.

    Only ever called after every other gate has passed and MC has proven the
    corrected hash is the current HEAD descended from the slice start, so the
    correction cannot mask unauthorized files, missing validation, failed
    audits/reviews, a dirty worktree, or a missing commit.
    """
    result.setdefault("reconciliations", []).append(
        {
            "field": "commit.hash",
            "reported_value": reported_hash,
            "corrected_value": corrected_hash,
            "reason": record_reason,
            "reconciled_at": utc_now(),
        }
    )
    result["commit"]["hash"] = corrected_hash
    write_orchestrator_result(result_path, result)
    write_reconciliation_artifact(
        slice_artifact_dir,
        field="commit.hash",
        reported_value=reported_hash,
        corrected_value=corrected_hash,
        reason=record_reason,
    )
    return GateDecision("pass", decision_reason, result, actual_changed)


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
    changed_evidence = tuple(sorted(actual_changed))
    unauthorized = unauthorized_files(actual_changed, plan_slice.authorized_files)
    if unauthorized:
        return GateDecision("fail", "unauthorized changed files: " + ", ".join(unauthorized), result, changed_evidence)

    reported_files = result.get("changed_files")
    if not isinstance(reported_files, list) or not all(isinstance(item, str) for item in reported_files):
        return GateDecision("fail", "orchestrator changed_files is malformed (expected a list of paths)", result, changed_evidence)
    if actual_changed != set(reported_files):
        return GateDecision("fail", "orchestrator changed_files does not match git evidence", result, changed_evidence)

    validation_failure = _validation_status(result.get("validation"))
    if validation_failure:
        return GateDecision("fail", validation_failure, result, changed_evidence)
    if not (slice_artifact_dir / "validation-summary.md").exists():
        return GateDecision("fail", "validation-summary.md is missing", result, changed_evidence)

    drift_verdict = str(object_field(result, "drift_audit").get("verdict", "")).upper()
    if drift_verdict != "PASS":
        return GateDecision("needs-human", f"drift audit verdict is not PASS: {drift_verdict or 'missing'}", result, changed_evidence)
    if not artifact_exists(repo, slice_artifact_dir, result, "drift_audit", "drift-audit.md"):
        return GateDecision("fail", "drift audit artifact is missing", result, changed_evidence)

    review_verdict = str(object_field(result, "code_review").get("verdict", "")).upper()
    if review_verdict != "PASS":
        return GateDecision("fail", f"code review verdict is not PASS: {review_verdict or 'missing'}", result, changed_evidence)
    if not artifact_exists(repo, slice_artifact_dir, result, "code_review", "code-review.md"):
        return GateDecision("fail", "code review artifact is missing", result, changed_evidence)

    commit = result.get("commit") if isinstance(result.get("commit"), dict) else {}
    if state.get("policy", {}).get("commit_required", True):
        if not commit.get("requested") or not commit.get("created") or not commit.get("hash"):
            return GateDecision("fail", "required commit was not created", result, changed_evidence)
        if meaningful_status_lines(after_status):
            return GateDecision("fail", "post-commit worktree is dirty outside .ai-mc/", result, changed_evidence)
        if not after_head or after_head == before_head:
            return GateDecision("fail", "required commit did not advance HEAD", result, changed_evidence)
        reported_hash = str(commit["hash"])
        if reported_hash != after_head:
            if not commit_is_descendant(repo, before_head, after_head):
                return GateDecision("fail", "current HEAD is not descended from the slice starting commit", result, changed_evidence)
            # An abbreviated-but-correct hash and an outright-wrong hash both
            # differ from the proven full HEAD as strings and land here; the
            # only difference is the message we record.
            if is_full_commit_hash(reported_hash):
                record_reason = (
                    "orchestrator reported an incorrect commit hash, but MC proved the slice commit from local git "
                    "evidence and corrected orchestrator-result.json"
                )
                decision_reason = "all gates passed; corrected reported commit hash to current HEAD"
            else:
                record_reason = "orchestrator reported an abbreviated commit hash; MC corrected it to the full current HEAD"
                decision_reason = "all gates passed; expanded reported commit hash to full current HEAD"
            return _apply_commit_hash_reconciliation(
                result,
                result_path,
                slice_artifact_dir,
                reported_hash=reported_hash,
                corrected_hash=after_head,
                record_reason=record_reason,
                decision_reason=decision_reason,
                actual_changed=changed_evidence,
            )

    return GateDecision("pass", "all gates passed", result, changed_evidence)
