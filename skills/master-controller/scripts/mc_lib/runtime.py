from __future__ import annotations

import importlib.util
import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from .constants import SENSITIVE_ARTIFACT_NAMES, WORKER_CREDENTIAL_HOMES
from .models import McError, PlanSlice


_WORKER_JOBS_MODULE: Any = None


def environment_preflight() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": sys.executable,
        "python_version": platform.python_version(),
        "git": shutil.which("git"),
        "tmux": shutil.which("tmux"),
    }


def skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def result_schema_path() -> Path:
    return skill_root() / "references" / "run-state-schema.md"


def worker_jobs_path() -> Path:
    return skill_root().parent / "ai-orchestrator" / "scripts" / "worker_jobs.py"


def worker_jobs_module() -> Any:
    """Load ai-orchestrator's worker_jobs.py as a library module.

    Reused (not reimplemented) here for its session-path conventions, e.g.
    claude_project_root, which already correctly match how Claude Code and
    Codex lay out their on-disk session transcripts for worker sessions.
    """
    global _WORKER_JOBS_MODULE
    if _WORKER_JOBS_MODULE is None:
        path = worker_jobs_path()
        spec = importlib.util.spec_from_file_location("mc_worker_jobs", path)
        if spec is None or spec.loader is None:
            raise McError(f"could not load worker_jobs module from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _WORKER_JOBS_MODULE = module
    return _WORKER_JOBS_MODULE


def claude_orchestrator_transcript_path(repo: Path, session_id: str) -> Path:
    return worker_jobs_module().claude_project_root(repo.resolve()) / f"{session_id}.jsonl"


def capture_orchestrator_transcript(harness_name: str, repo: Path, session_id: str | None, slice_artifact_dir: Path) -> None:
    """Copy the orchestrator's own structured session transcript into the slice artifacts.

    This is a full-fidelity complement to pane-capture.txt, not a replacement:
    the tmux pane capture is still required to detect harness-level stuck/
    blocked states (e.g. approval or trust prompts) and to support further
    prompting mid-session; the JSONL transcript exists because Claude Code's
    interactive TUI collapses verbose tool output behind "ctrl+o to expand"
    in the pane, so exact commands/output are not always reconstructable from
    pane-capture.txt alone.
    """
    if harness_name != "claude" or not session_id:
        return
    destination = slice_artifact_dir / "orchestrator-transcript.jsonl"
    note_path = slice_artifact_dir / "orchestrator-transcript-note.txt"
    try:
        source = claude_orchestrator_transcript_path(repo, session_id)
    except McError as exc:
        note_path.write_text(f"orchestrator transcript lookup failed: {exc}\n", encoding="utf-8")
        return
    if source.exists():
        shutil.copy2(source, destination)
        note_path.unlink(missing_ok=True)
    else:
        note_path.write_text(
            "orchestrator transcript not found at expected path: "
            f"{source}\n"
            "This can happen if the launched command did not honor --session-id "
            "(e.g. a custom --harness-command without --session-id).\n",
            encoding="utf-8",
        )


def real_tool_home(env_var: str, default_dirname: str) -> Path:
    override = os.environ.get(env_var)
    if override:
        return Path(override).expanduser()
    return Path.home() / default_dirname


def worker_credential_source(tool: str) -> tuple[Path, str] | None:
    entry = WORKER_CREDENTIAL_HOMES.get(tool)
    if not entry:
        return None
    env_var, default_dirname, filename = entry
    return real_tool_home(env_var, default_dirname), filename


def slice_paths(slice_artifact_dir: Path) -> dict[str, Path]:
    return {
        "artifact_dir": slice_artifact_dir,
        "worker_artifact_root": slice_artifact_dir / "worker-runs",
        "tmp_dir": slice_artifact_dir / "tmp",
        "tool_home_root": slice_artifact_dir / "tool-homes",
        "copilot_home": slice_artifact_dir / "copilot-home",
        "codex_home": slice_artifact_dir / "codex-home",
        "claude_config_dir": slice_artifact_dir / "claude-config-dir",
    }


def seed_worker_credentials(paths: dict[str, Path], worker_tools: tuple[str, ...], orchestrator_harness_name: str) -> list[str]:
    warnings: list[str] = []
    home_by_tool = {"codex": "codex_home", "claude": "claude_config_dir"}
    for tool, home_key in home_by_tool.items():
        if tool not in worker_tools or tool == orchestrator_harness_name:
            continue
        source = worker_credential_source(tool)
        if source is None:
            continue
        source_dir, filename = source
        source_path = source_dir / filename
        destination = paths[home_key] / filename
        if not source_path.exists():
            warnings.append(f"{tool} worker credential source not found: {source_path}")
            continue
        shutil.copy2(source_path, destination)
        os.chmod(destination, 0o600)
    return warnings


def ensure_slice_runtime_dirs(slice_artifact_dir: Path, worker_tools: tuple[str, ...] = (), orchestrator_harness_name: str = "") -> list[str]:
    paths = slice_paths(slice_artifact_dir)
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return seed_worker_credentials(paths, worker_tools, orchestrator_harness_name)


def slice_environment(
    slice_artifact_dir: Path,
    run_json: Path,
    plan_path: Path,
    plan_slice: PlanSlice,
    orchestrator_harness_name: str = "",
    worker_tools: tuple[str, ...] = (),
) -> dict[str, str]:
    paths = slice_paths(slice_artifact_dir)
    env = {
        "AI_ORCHESTRATOR_ARTIFACT_ROOT": str(paths["worker_artifact_root"]),
        "COPILOT_HOME": str(paths["copilot_home"]),
        "MC_RESULT_SCHEMA_PATH": str(result_schema_path()),
        "MC_RUN_JSON_PATH": str(run_json),
        "MC_PLAN_PATH": str(plan_path),
        "MC_SLICE_ARTIFACT_DIR": str(slice_artifact_dir),
        "MC_SLICE_ID": plan_slice.slice_id,
        "MC_SLICE_TMP_DIR": str(paths["tmp_dir"]),
        "MC_TOOL_HOME_ROOT": str(paths["tool_home_root"]),
        "MC_WORKER_ARTIFACT_ROOT": str(paths["worker_artifact_root"]),
        "MC_WORKER_JOBS_PATH": str(worker_jobs_path()),
        "TMPDIR": str(paths["tmp_dir"]),
    }
    # Only redirect a tool's own home when that tool is a *worker* for this
    # run, and never when it is also the orchestrator harness itself: codex
    # and claude can both be orchestrators, and clobbering the orchestrator's
    # own home/auth with an isolated (and possibly unseeded) per-slice
    # directory would break the orchestrator, not just a worker. Copilot is
    # never an MC orchestrator, so COPILOT_HOME above is always safe to set.
    if "codex" in worker_tools and orchestrator_harness_name != "codex":
        env["CODEX_HOME"] = str(paths["codex_home"])
    if "claude" in worker_tools and orchestrator_harness_name != "claude":
        env["CLAUDE_CONFIG_DIR"] = str(paths["claude_config_dir"])
    return env


def load_prompt_template() -> str:
    path = skill_root() / "references" / "orchestrator-prompt.md"
    text = path.read_text(encoding="utf-8")
    match = re.search(r"```md\n(?P<template>.*?)\n```", text, flags=re.DOTALL)
    if not match:
        raise McError(f"orchestrator prompt template not found in {path}")
    return match.group("template")


def render_orchestrator_prompt(
    state: dict[str, Any],
    plan_slice: PlanSlice,
    slice_artifact_dir: Path,
    run_json: Path,
    worker_tools: tuple[str, ...] = (),
) -> str:
    template = load_prompt_template()
    paths = slice_paths(slice_artifact_dir)
    values = {
        "plan_path": state["plan_path"],
        "run_json_path": str(run_json),
        "slice_artifact_dir": str(slice_artifact_dir),
        "result_schema_path": str(result_schema_path()),
        "worker_jobs_path": str(worker_jobs_path()),
        "worker_artifact_root": str(paths["worker_artifact_root"]),
        "slice_tmp_dir": str(paths["tmp_dir"]),
        "tool_home_root": str(paths["tool_home_root"]),
        "copilot_home": str(paths["copilot_home"]),
        "codex_home": str(paths["codex_home"]),
        "claude_config_dir": str(paths["claude_config_dir"]),
        "worker_tools": ", ".join(worker_tools) if worker_tools else "none configured for this run",
        "slice_id": plan_slice.slice_id,
        "slice_title": plan_slice.title,
        "intended_change": plan_slice.sections.get("Intended Change", ""),
        "acceptance_criteria": plan_slice.sections.get("Acceptance Criteria", ""),
        "authorized_surface": plan_slice.sections.get("Authorized Surface", ""),
        "explicit_non_goals": plan_slice.sections.get("Explicit Non-Goals", ""),
        "risk_flags": plan_slice.sections.get("Risk Flags", ""),
        "validation_plan": plan_slice.sections.get("Validation Plan", ""),
        "rollback_path": plan_slice.sections.get("Rollback Path", ""),
    }
    return template.format(**values).rstrip() + "\n"


def slice_dir_name(plan_slice: PlanSlice) -> str:
    return f"slice-{plan_slice.number:03d}"


def tmux_session_name(run_id_value: str, plan_slice: PlanSlice, attempt: int) -> str:
    raw = f"mc_{run_id_value}_{slice_dir_name(plan_slice)}_a{attempt}"
    return re.sub(r"[^A-Za-z0-9_-]", "_", raw)[:80]


def relative_artifact_path(repo: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def capture_worker_runs_summary(slice_artifact_dir: Path) -> None:
    worker_root = slice_artifact_dir / "worker-runs"
    if not worker_root.exists():
        return
    runs: list[dict[str, Any]] = []
    for run_dir in sorted(path for path in worker_root.iterdir() if path.is_dir()):
        run_entry: dict[str, Any] = {"run_dir": str(run_dir), "workers": []}
        manifest_path = run_dir / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                run_entry["manifest"] = manifest
            except json.JSONDecodeError as exc:
                run_entry["manifest_error"] = str(exc)
        for status_path_obj in sorted(run_dir.glob("*-status.json")):
            try:
                status = json.loads(status_path_obj.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                status = {"path": str(status_path_obj), "error": str(exc)}
            run_entry["workers"].append(status)
        runs.append(run_entry)
    if not runs:
        return
    (slice_artifact_dir / "worker-runs-summary.json").write_text(json.dumps({"runs": runs}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sensitive_artifact_dirs(run_dir: Path) -> list[Path]:
    paths: list[Path] = []
    slices_dir = run_dir / "slices"
    if not slices_dir.exists():
        return paths
    for path in slices_dir.glob("slice-*/*"):
        if path.is_dir() and path.name in SENSITIVE_ARTIFACT_NAMES:
            paths.append(path)
    return sorted(paths)
