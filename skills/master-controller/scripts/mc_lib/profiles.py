from __future__ import annotations

import argparse
import shlex
from pathlib import Path
from typing import Any

from .constants import HARNESS_PROFILES
from .git_ops import git_access_path
from .models import McError


def parse_worker_tools(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(tool.strip().lower() for tool in value.split(",") if tool.strip())


def harness_supports_role(harness_name: str, role: str) -> bool:
    return role in HARNESS_PROFILES.get(harness_name, {}).get("roles", [])


def _append_model_override(command: list[str], profile: dict[str, Any], model: str | None, tool_name: str) -> None:
    if not model:
        return
    model_flag = profile.get("model_flag")
    model_config_key = profile.get("model_config_key")
    if model_flag:
        command.extend([str(model_flag), model])
        return
    if model_config_key:
        command.extend(["-c", f'{model_config_key}="{model}"'])
        return
    raise McError(f"harness profile {tool_name!r} does not support MC-composed model overrides")


def _append_effort_override(command: list[str], profile: dict[str, Any], effort: str | None, tool_name: str) -> None:
    if not effort:
        return
    effort_flag = profile.get("effort_flag")
    effort_config_key = profile.get("effort_config_key")
    if effort_flag:
        command.extend([str(effort_flag), effort])
        return
    if effort_config_key:
        command.extend(["-c", f'{effort_config_key}="{effort}"'])
        return
    raise McError(f"harness profile {tool_name!r} does not support MC-composed effort overrides")


def profile_command(
    harness_name: str,
    repo: Path,
    state: dict[str, Any],
    worker_tools: tuple[str, ...],
    orchestrator_session_id: str | None = None,
    harness_model: str | None = None,
    harness_effort: str | None = None,
) -> str:
    profile = HARNESS_PROFILES.get(harness_name)
    if not profile:
        raise McError(f"no MC harness profile is defined for {harness_name!r}")
    if not harness_supports_role(harness_name, "orchestrator"):
        raise McError(f"harness profile {harness_name!r} is not approved for the orchestrator role")

    command = list(profile.get("base_command") or [])
    if not command:
        raise McError(f"harness profile {harness_name!r} has no base command")

    _append_model_override(command, profile, harness_model, harness_name)
    _append_effort_override(command, profile, harness_effort, harness_name)

    if harness_name == "codex":
        if worker_tools:
            command.extend(profile["worker_network_flag"])
        if state.get("policy", {}).get("commit_required", True):
            command.extend([profile["commit_git_access_flag"], str(git_access_path(repo))])
    elif worker_tools and harness_name not in {"claude", "copilot", "opencode"}:
        raise McError(f"harness profile {harness_name!r} has no tested worker-enabled launch path")
    if harness_name == "claude" and orchestrator_session_id:
        # Pins the session transcript to a deterministic path under
        # ~/.claude/projects/<repo-slug>/<session_id>.jsonl so MC can capture
        # it as a full-fidelity artifact after the run (see
        # capture_orchestrator_transcript). Claude Code's interactive TUI
        # collapses verbose tool output behind "ctrl+o to expand" in the tmux
        # pane capture; this transcript is not subject to that collapsing.
        command.extend(["--session-id", orchestrator_session_id])
    return shlex.join(command)


def resolve_harness_command(
    args: argparse.Namespace,
    repo: Path,
    state: dict[str, Any],
    orchestrator_session_id: str | None = None,
) -> str | None:
    if getattr(args, "harness_model", None) and not getattr(args, "allow_profile_command", False):
        raise McError("--harness-model is only supported with --allow-profile-command")
    if getattr(args, "harness_effort", None) and not getattr(args, "allow_profile_command", False):
        raise McError("--harness-effort is only supported with --allow-profile-command")
    if getattr(args, "harness_command", None):
        return args.harness_command
    if getattr(args, "allow_profile_command", False):
        return profile_command(
            state["harness"]["name"],
            repo,
            state,
            parse_worker_tools(getattr(args, "worker_tools", None)),
            orchestrator_session_id,
            getattr(args, "harness_model", None),
            getattr(args, "harness_effort", None),
        )
    return None
