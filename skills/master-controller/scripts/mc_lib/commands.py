from __future__ import annotations

import argparse
import os
import shlex
import shutil
import time
from pathlib import Path
from typing import Any

from .constants import (
    COMPLETED_SLICE_STATUSES,
    HARNESS_PROFILES,
    PARSER_NAME,
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
    result_schema_path,
    sensitive_artifact_dirs,
    slice_dir_name,
    slice_paths,
    worker_credential_source,
    worker_jobs_path,
)
from .runner import execute_slice
from .state import (
    idle_status_after_pass,
    load_run,
    normalize_stop_status,
    previous_completed_head,
    relative_artifact_path,
    resolve_run_dir,
    resolve_run_path,
    slice_entry_from_gate,
    update_state_for_stop,
    write_run,
)
from .tmux_adapter import TmuxHarnessAdapter
from .utils import run_id, utc_now


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
    if state.get("stop_reason"):
        print(f"Stop reason: {state['stop_reason']}")
    return 0


def summarize(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    state = load_run(resolve_run_path(repo, args.run))
    completed = completed_slice_ids(state)
    print(f"MC run {state['run_id']} summary")
    print(f"Status: {state['status']}")
    if state["status"] == "partial":
        slices = parse_plan(resolve_plan(Path(state["plan_path"])))
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
        slices = parse_plan(resolve_plan(Path(state["plan_path"])))
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
    slices = parse_plan(resolve_plan(Path(state["plan_path"])))
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
    check("plan file", plan_path.exists(), str(plan_path))
    slices = parse_plan(plan_path)
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
