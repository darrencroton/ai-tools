#!/usr/bin/env python3
"""Master Controller state and plan eligibility CLI."""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import os
import platform
import re
import shutil
import shlex
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any


SCHEMA_VERSION = 1
PARSER_NAME = "implementation-plan-markdown-v1"
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_SECTIONS = (
    "Intended Change",
    "Acceptance Criteria",
    "Authorized Surface",
    "Explicit Non-Goals",
    "Risk Flags",
    "Validation Plan",
    "Rollback Path",
)
COMPLETED_SLICE_STATUSES = {"pass", "committed", "complete"}
ORCHESTRATOR_STATUSES = {"pass", "repairable", "needs-human", "fail", "blocked"}
RUN_STOP_STATUSES = {"needs-human", "blocked", "failed", "cancelled"}
DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_POLL_SECONDS = 2.0

# User-approved exception (explicit choice via AskUserQuestion, this session):
# the operator may opt in to these two known unattended-safe launch commands
# with --allow-unattended-default. Without that flag, or for any other
# harness name, MC still fails closed and requires --harness-command. Bare
# harness names otherwise resolve to an interactive session (see
# TmuxHarnessAdapter): tmux pastes the prompt and presses enter as if a human
# were typing, so an unflagged `codex`/`claude` process would still prompt
# for per-action approval that nothing in this loop can answer, silently
# deadlocking the run until --timeout-seconds expires.
#
# claude uses `--permission-mode auto`, not `bypassPermissions`: `auto` still
# routes actions through Claude Code's own risk classifier (the same one
# governing this session), so genuinely dangerous or irreversible actions
# still stop for a human, while routine actions proceed unattended. It is a
# real safety backstop, not a rubber stamp. codex has no equivalent
# classifier-driven mode (its only approval policies are untrusted /
# on-failure / on-request / never), so `-a never` is paired with
# `-s workspace-write` (not `danger-full-access`) to keep its OS-level
# filesystem/network sandbox as the backstop instead. `--no-alt-screen` keeps
# the tmux pane capture useful for MC artifacts and debugging. Either way, this still
# shifts part of the enforced safety boundary from "the harness asks before
# acting" to "MC verifies after the fact" (drift-audit, code-review,
# unauthorized-file checks) plus, for claude, its own classifier -- that must
# stay an explicit, visible per-run choice, never a silent default.
KNOWN_UNATTENDED_HARNESS_COMMANDS: dict[str, str] = {
    "codex": "codex --no-alt-screen -s workspace-write -a never",
    "claude": "claude --permission-mode auto",
}

HARNESS_PROFILES: dict[str, dict[str, Any]] = {
    "codex": {
        "roles": ["orchestrator", "senior-worker"],
        "base_command": ["codex", "--no-alt-screen", "-s", "workspace-write", "-a", "never"],
        "worker_network_flag": ["-c", "sandbox_workspace_write.network_access=true"],
        "commit_git_access_flag": "--add-dir",
        "notes": [
            "Use --no-alt-screen for durable tmux captures.",
            "Worker-backed runs need sandbox network access.",
            "Commit-required runs need scoped write access to the repository git directory.",
            "When used as a worker (not orchestrator), gets a per-slice CODEX_HOME seeded with a copy of the "
            "real auth.json, since Codex's home dir doubles as its credential store.",
        ],
    },
    "claude": {
        "roles": ["orchestrator", "senior-worker"],
        "base_command": ["claude", "--permission-mode", "auto"],
        "model_flag": "--model",
        "notes": [
            "Uses Claude Code's permission classifier for unattended routine actions.",
            "Optional MC profile model override is composed with --model while preserving --session-id transcript capture.",
            "Do not launch Claude workers from inside a Claude orchestrator.",
            "As orchestrator, launched with --session-id so MC can capture the full JSONL transcript "
            "as orchestrator-transcript.jsonl (pane capture alone loses detail behind Claude Code's "
            "'ctrl+o to expand' collapsing).",
            "When used as a worker (not orchestrator), gets a per-slice CLAUDE_CONFIG_DIR seeded with a "
            "copy of the real .credentials.json, since Claude Code's home dir doubles as its credential store.",
        ],
    },
    "copilot": {
        "roles": ["junior-worker"],
        "base_command": ["copilot"],
        "notes": [
            "Copilot is a worker profile only; it is not an MC orchestrator harness.",
            "Use a per-slice COPILOT_HOME for sandboxed session state.",
        ],
    },
    "opencode": {
        "roles": ["pending"],
        "base_command": ["opencode"],
        "notes": [
            "Profile placeholder for future validation.",
            "Do not use as an MC harness until an unattended prompt, permission, and capture contract has been tested.",
        ],
    },
}

SENSITIVE_ARTIFACT_NAMES = {"copilot-home", "codex-home", "claude-config-dir"}

# Worker-tool home directories are not interchangeable: Copilot's real GitHub
# credential lives outside ~/.copilot (gh CLI config / OS keychain), so
# redirecting COPILOT_HOME to an isolated per-slice directory only needs a
# writable dir. Codex's auth.json and Claude Code's .credentials.json live
# directly inside their respective home directories, so isolating those homes
# for a worker requires seeding the credential file first or the worker gets a
# 401. Map each tool that needs seeding to (env var MC/the operator uses for
# this tool's home, real home dirname fallback when the env var is unset,
# credential filename to copy into the isolated per-slice home).
WORKER_CREDENTIAL_HOMES: dict[str, tuple[str, str, str]] = {
    "codex": ("CODEX_HOME", ".codex", "auth.json"),
    "claude": ("CLAUDE_CONFIG_DIR", ".claude", ".credentials.json"),
}


class McError(Exception):
    """User-facing MC error."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class PlanSlice:
    number: int
    title: str
    body: str
    sections: dict[str, str]

    @property
    def slice_id(self) -> str:
        return f"Slice {self.number}"

    @property
    def missing_sections(self) -> list[str]:
        return [section for section in REQUIRED_SECTIONS if not self.sections.get(section, "").strip()]

    @property
    def authorized_files(self) -> list[str]:
        section = self.sections.get("Authorized Surface", "")
        match = re.search(
            r"Files allowed to change:\s*(?P<body>.*?)(?:\n-\s*Functions/classes/components allowed to change:|\n-\s*Tests allowed or expected to change:|\Z)",
            section,
            flags=re.DOTALL,
        )
        if not match:
            return []
        return _bullet_values(match.group("body"))

    @property
    def approval_needed(self) -> bool | None:
        section = self.sections.get("Risk Flags", "")
        match = re.search(r"Approval needed before implementation:\s*(?P<value>[^\n]+)", section, flags=re.IGNORECASE)
        if not match:
            return None
        value = match.group("value").strip().lower()
        if value.startswith("no"):
            return False
        if value.startswith("yes"):
            return True
        return None


@dataclass(frozen=True)
class GateDecision:
    status: str
    reason: str
    result: dict[str, Any] | None = None
    actual_changed_files: tuple[str, ...] = ()


class TmuxHarnessAdapter:
    """Single tmux-backed harness adapter for the configured command."""

    def __init__(
        self,
        harness_name: str,
        command_override: str | None = None,
        allow_unattended_default: bool = False,
        worker_tools: tuple[str, ...] = (),
    ):
        self.harness_name = harness_name
        self.command_override = command_override
        self.allow_unattended_default = allow_unattended_default
        self.worker_tools = worker_tools
        if command_override:
            self.command = command_override
        elif allow_unattended_default and harness_name in KNOWN_UNATTENDED_HARNESS_COMMANDS:
            self.command = KNOWN_UNATTENDED_HARNESS_COMMANDS[harness_name]
        else:
            self.command = harness_name

    def preflight(self) -> None:
        if not shutil.which("tmux"):
            raise McError("tmux is required for runtime execution")
        if not self.command.strip():
            raise McError("harness command is empty")
        using_known_default = self.allow_unattended_default and self.harness_name in KNOWN_UNATTENDED_HARNESS_COMMANDS
        if not self.command_override and not using_known_default:
            if self.harness_name in KNOWN_UNATTENDED_HARNESS_COMMANDS:
                raise McError(
                    f"harness {self.harness_name!r} defaults to an interactive session that will deadlock on an "
                    "unattended approval prompt (nothing here can answer it, so the run hangs until "
                    "--timeout-seconds expires). Pass --harness-command '<full non-interactive-approval "
                    f"command>', or pass --allow-unattended-default to use the known unattended-safe default: "
                    f"{KNOWN_UNATTENDED_HARNESS_COMMANDS[self.harness_name]!r}"
                )
            raise McError(
                f"harness {self.harness_name!r} has no known unattended-safe default command; "
                "pass --harness-command with a full non-interactive-approval command"
            )
        executable = shlex.split(self.command)[0] if self.command.strip() else ""
        if not executable:
            raise McError("harness command is empty")
        if not shutil.which(executable):
            raise McError(f"harness command not found: {executable}")

    def build_shell_command(self, slice_artifact_dir: Path, run_json: Path, plan_path: Path, plan_slice: PlanSlice) -> str:
        env = slice_environment(slice_artifact_dir, run_json, plan_path, plan_slice, self.harness_name, self.worker_tools)
        env_prefix = " ".join(
            f"{key}={shlex.quote(value)}"
            for key, value in env.items()
        )
        return f"{env_prefix} {self.command}"

    def start(self, repo: Path, session_name: str, slice_artifact_dir: Path, run_json: Path, plan_path: Path, plan_slice: PlanSlice) -> None:
        self.preflight()
        shell_command = self.build_shell_command(slice_artifact_dir, run_json, plan_path, plan_slice)
        run_command(
            [
                "tmux",
                "new-session",
                "-d",
                "-s",
                session_name,
                "-c",
                str(repo),
                shell_command,
            ],
            error_prefix="tmux start failed",
        )

    def wait_until_prompt_ready(self, session_name: str) -> None:
        command_parts = shlex.split(self.command) if self.command.strip() else []
        executable = Path(command_parts[0]).name if command_parts else ""
        if executable != "codex":
            return
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if not self.session_exists(session_name):
                raise McError("codex session exited before the prompt could be sent")
            result = run_command(["tmux", "capture-pane", "-p", "-S", "-200", "-t", session_name], allow_failure=True)
            capture = result.stdout if result.returncode == 0 else ""
            if "Do you trust the contents of this directory" in capture:
                raise McError("codex directory trust prompt blocked unattended launch; trust the repo before running MC")
            if "OpenAI Codex" in capture and "›" in capture:
                time.sleep(0.5)
                return
            time.sleep(0.25)
        raise McError("codex TUI did not become ready for prompt injection")

    def send_prompt(self, session_name: str, prompt_path: Path) -> None:
        buffer_name = f"{session_name}_prompt"
        self.wait_until_prompt_ready(session_name)
        run_command(["tmux", "load-buffer", "-b", buffer_name, str(prompt_path)], error_prefix="tmux prompt load failed")
        run_command(["tmux", "paste-buffer", "-b", buffer_name, "-t", session_name], error_prefix="tmux prompt paste failed")
        run_command(["tmux", "delete-buffer", "-b", buffer_name], allow_failure=True)
        # Confirmed by reproduction: submitting immediately races the harness
        # TUI's own paste handling. A single C-m sent right after paste-buffer
        # can be consumed finalizing the pasted multi-line block instead of
        # submitting it, leaving the prompt sitting unsent (composer at "0
        # tok") until MC's timeout fires -- there is no approval prompt to
        # detect, just a message that was never actually sent. A second C-m
        # after the TUI settles reliably submits it. Both sends tolerate a
        # session that has already ended (a fast-finishing harness can exit
        # before either fires) -- that is a normal completion path the result
        # /activity checks below handle, not a send_prompt failure.
        time.sleep(1.0)
        run_command(["tmux", "send-keys", "-t", session_name, "C-m"], allow_failure=True)
        time.sleep(1.0)
        run_command(["tmux", "send-keys", "-t", session_name, "C-m"], allow_failure=True)

    def capture(self, session_name: str, destination: Path) -> None:
        result = run_command(["tmux", "capture-pane", "-p", "-S", "-32768", "-t", session_name], allow_failure=True)
        if result.returncode == 0:
            destination.write_text(result.stdout, encoding="utf-8")
        else:
            destination.write_text("tmux pane was unavailable during capture\n", encoding="utf-8")

    def session_exists(self, session_name: str) -> bool:
        return run_command(["tmux", "has-session", "-t", session_name], allow_failure=True).returncode == 0

    def detect_activity(self, session_name: str, previous_capture: str) -> dict[str, Any]:
        if not self.session_exists(session_name):
            return {"running": False, "active": False, "capture": ""}
        result = run_command(["tmux", "capture-pane", "-p", "-S", "-32768", "-t", session_name], allow_failure=True)
        capture = result.stdout if result.returncode == 0 else ""
        return {"running": True, "active": capture != previous_capture, "capture": capture}

    def request_stop(self, session_name: str) -> None:
        if self.session_exists(session_name):
            run_command(["tmux", "send-keys", "-t", session_name, "C-c"], allow_failure=True)

    def force_stop(self, session_name: str) -> None:
        if self.session_exists(session_name):
            run_command(["tmux", "kill-session", "-t", session_name], allow_failure=True)


def _bullet_values(text: str) -> list[str]:
    values: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if value and value.lower() != "none." and value.lower() != "none":
                values.append(value)
    return values


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise McError(message)
    return result.stdout.strip()


def run_command(command: list[str], *, error_prefix: str = "command failed", allow_failure: bool = False) -> CommandResult:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    command_result = CommandResult(result.returncode, result.stdout, result.stderr)
    if result.returncode != 0 and not allow_failure:
        message = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise McError(f"{error_prefix}: {message}")
    return command_result


def git_result(repo: Path, *args: str) -> CommandResult:
    return run_command(["git", "-C", str(repo), *args], allow_failure=True)


def parse_worker_tools(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(tool.strip().lower() for tool in value.split(",") if tool.strip())


def harness_supports_role(harness_name: str, role: str) -> bool:
    return role in HARNESS_PROFILES.get(harness_name, {}).get("roles", [])


def profile_command(
    harness_name: str,
    repo: Path,
    state: dict[str, Any],
    worker_tools: tuple[str, ...],
    orchestrator_session_id: str | None = None,
    harness_model: str | None = None,
) -> str:
    profile = HARNESS_PROFILES.get(harness_name)
    if not profile:
        raise McError(f"no MC harness profile is defined for {harness_name!r}")
    if not harness_supports_role(harness_name, "orchestrator"):
        raise McError(f"harness profile {harness_name!r} is not approved for the orchestrator role")

    command = list(profile.get("base_command") or [])
    if not command:
        raise McError(f"harness profile {harness_name!r} has no base command")

    if harness_model:
        model_flag = profile.get("model_flag")
        if not model_flag:
            raise McError(f"harness profile {harness_name!r} does not support MC-composed model overrides")
        command.extend([model_flag, harness_model])

    if harness_name == "codex":
        if worker_tools:
            command.extend(profile["worker_network_flag"])
        if state.get("policy", {}).get("commit_required", True):
            command.extend([profile["commit_git_access_flag"], str(git_access_path(repo))])
    elif worker_tools and harness_name not in {"claude"}:
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
        )
    return None


def git_head(repo: Path) -> str | None:
    result = git_result(repo, "rev-parse", "--verify", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else None


def git_status_text(repo: Path) -> str:
    return git(repo, "status", "--short", "--untracked-files=all")


def status_path(line: str) -> str:
    path = line[3:] if len(line) > 3 else line
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[1]
    return path.strip().strip('"')


def meaningful_status_lines(status_text: str) -> list[str]:
    lines: list[str] = []
    for line in status_text.splitlines():
        path = status_path(line)
        if path == ".ai-mc" or path.startswith(".ai-mc/"):
            continue
        lines.append(line)
    return lines


def require_clean_worktree(repo: Path) -> None:
    dirty = meaningful_status_lines(git_status_text(repo))
    if dirty:
        raise McError("starting git state is dirty outside .ai-mc/: " + "; ".join(dirty))


def status_changed_files(status_text: str) -> set[str]:
    return {status_path(line) for line in meaningful_status_lines(status_text)}


def changed_files_between(repo: Path, before_head: str | None, after_head: str | None, after_status: str) -> set[str]:
    files: set[str] = set()
    if before_head and after_head and before_head != after_head:
        files.update(git(repo, "diff", "--name-only", before_head, after_head).splitlines())
    elif after_head and before_head is None:
        files.update(git(repo, "show", "--name-only", "--format=", after_head).splitlines())
    files.update(status_changed_files(after_status))
    return {path for path in files if path}


def write_git_diff(repo: Path, before_head: str | None, after_head: str | None, destination: Path) -> None:
    if before_head and after_head and before_head != after_head:
        result = git_result(repo, "diff", "--binary", before_head, after_head)
    else:
        result = git_result(repo, "diff", "--binary")
    destination.write_text(result.stdout if result.returncode == 0 else result.stderr, encoding="utf-8")


def is_full_commit_hash(value: str | None) -> bool:
    return bool(value and FULL_COMMIT_RE.fullmatch(value))


def commit_is_descendant(repo: Path, before_head: str | None, after_head: str | None) -> bool:
    if not after_head:
        return False
    if not before_head:
        return True
    result = git_result(repo, "merge-base", "--is-ancestor", before_head, after_head)
    return result.returncode == 0


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


def normalize_authorized_entry(entry: str) -> str:
    return entry.strip().strip("`").rstrip(".")


def is_authorized_path(path: str, authorized_entries: list[str]) -> bool:
    for raw_entry in authorized_entries:
        entry = normalize_authorized_entry(raw_entry)
        if entry.endswith("/"):
            if path.startswith(entry):
                return True
        elif any(marker in entry for marker in ("*", "?", "[")):
            if fnmatch.fnmatch(path, entry):
                return True
        elif path == entry:
            return True
    return False


def unauthorized_files(changed_files: set[str], authorized_entries: list[str]) -> list[str]:
    return sorted(path for path in changed_files if not is_authorized_path(path, authorized_entries))


def resolve_repo(path: Path) -> Path:
    repo = path.expanduser().resolve()
    if not repo.exists():
        raise McError(f"repo path does not exist: {repo}")
    root = git(repo, "rev-parse", "--show-toplevel")
    return Path(root).resolve()


def resolve_plan(path: Path) -> Path:
    plan = path.expanduser().resolve()
    if not plan.is_file():
        raise McError(f"plan file does not exist: {plan}")
    return plan


def parse_plan(path: Path) -> list[PlanSlice]:
    text = path.read_text(encoding="utf-8")
    headers = list(re.finditer(r"^## Slice\s+(?P<number>\d+):\s*(?P<title>.+?)\s*$", text, flags=re.MULTILINE))
    slices: list[PlanSlice] = []
    for index, header in enumerate(headers):
        start = header.end()
        end_candidates = []
        if index + 1 < len(headers):
            end_candidates.append(headers[index + 1].start())
        next_non_slice_heading = re.search(r"^## (?!Slice\s+\d+:).+$", text[start:], flags=re.MULTILINE)
        if next_non_slice_heading:
            end_candidates.append(start + next_non_slice_heading.start())
        end = min(end_candidates) if end_candidates else len(text)
        body = text[start:end].strip()
        sections = parse_sections(body)
        slices.append(
            PlanSlice(
                number=int(header.group("number")),
                title=header.group("title").strip(),
                body=body,
                sections=sections,
            )
        )
    return slices


def parse_sections(slice_body: str) -> dict[str, str]:
    matches = list(re.finditer(r"^### (?P<name>.+?)\s*$", slice_body, flags=re.MULTILINE))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(slice_body)
        sections[match.group("name").strip()] = slice_body[start:end].strip()
    return sections


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


def completed_slice_ids(state: dict[str, Any]) -> set[str]:
    complete: set[str] = set()
    for entry in state.get("slices", []):
        if str(entry.get("status", "")).lower() in COMPLETED_SLICE_STATUSES:
            slice_id = entry.get("slice_id")
            if slice_id:
                complete.add(str(slice_id))
    return complete


def next_slice(slices: list[PlanSlice], state: dict[str, Any]) -> PlanSlice | None:
    complete = completed_slice_ids(state)
    for plan_slice in sorted(slices, key=lambda item: item.number):
        if plan_slice.slice_id not in complete:
            return plan_slice
    return None


def eligibility(plan_slice: PlanSlice) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    missing = plan_slice.missing_sections
    if missing:
        reasons.append(f"missing required sections: {', '.join(missing)}")
    if not plan_slice.authorized_files:
        reasons.append("authorized surface has no files allowed to change")
    approval = plan_slice.approval_needed
    if approval is True:
        reasons.append("slice is approval-needed")
    elif approval is None:
        reasons.append("approval-needed risk flag is missing or unclear")
    return not reasons, reasons


def environment_preflight() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": sys.executable,
        "python_version": platform.python_version(),
        "git": shutil.which("git"),
        "tmux": shutil.which("tmux"),
    }


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def result_schema_path() -> Path:
    return skill_root() / "references" / "run-state-schema.md"


def worker_jobs_path() -> Path:
    return skill_root().parent / "ai-orchestrator" / "scripts" / "worker_jobs.py"


_WORKER_JOBS_MODULE: Any = None


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


def git_access_path(repo: Path) -> Path:
    return Path(git(repo, "rev-parse", "--absolute-git-dir")).resolve()


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


def slice_entry_from_gate(repo: Path, plan_slice: PlanSlice, slice_artifact_dir: Path, started_at: str, gate: GateDecision) -> dict[str, Any]:
    result = gate.result or {}
    return {
        "slice_id": plan_slice.slice_id,
        "title": plan_slice.title,
        "status": gate.status,
        "started_at": started_at,
        "completed_at": utc_now(),
        "artifact_dir": relative_artifact_path(repo, slice_artifact_dir),
        "changed_files": list(gate.actual_changed_files or tuple(result.get("changed_files") or ())),
        "validation": result.get("validation", []),
        "drift_audit": result.get("drift_audit", {"verdict": None, "path": ""}),
        "code_review": result.get("code_review", {"verdict": None, "path": ""}),
        "commit": result.get("commit", {"requested": False, "created": False, "hash": None}),
        "next_action": result.get("next_action", ""),
        "blockers": result.get("blockers", []),
        "gate_reason": gate.reason,
    }


def init_run(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    plan = resolve_plan(Path(args.plan))
    slices = parse_plan(plan)
    if not slices:
        raise McError("plan contains no slices")
    rid = run_id()
    mc_dir = repo / ".ai-mc"
    run_dir = mc_dir / "runs" / rid
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = mc_dir / "runs" / f"{rid}-{suffix}"
    run_dir.mkdir(parents=True, exist_ok=False)

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


def update_state_for_stop(run_json: Path, state: dict[str, Any], status_value: str, reason: str) -> None:
    state["status"] = status_value
    state["stop_reason"] = reason
    state["current_slice"] = None
    write_run(run_json, state)


def idle_status_after_pass(state: dict[str, Any]) -> str:
    return "complete" if len(completed_slice_ids(state)) >= state["plan"]["slice_count"] else "partial"


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

    orchestrator_session_id = str(uuid.uuid4()) if harness_name == "claude" else None
    adapter = TmuxHarnessAdapter(
        harness_name,
        resolve_harness_command(args, repo, state, orchestrator_session_id),
        getattr(args, "allow_unattended_default", False),
        configured_worker_tools,
    )
    if getattr(args, "allow_profile_command", False) and not getattr(args, "harness_command", None):
        print(f"Using MC profile command for harness {adapter.harness_name!r}: {adapter.command!r}")
    if adapter.allow_unattended_default and not adapter.command_override and adapter.harness_name in KNOWN_UNATTENDED_HARNESS_COMMANDS:
        print(
            f"Using known unattended-safe default for harness {adapter.harness_name!r}: {adapter.command!r} "
            "(per-action approval is disabled; MC's post-hoc gates are the safety boundary for this run)"
        )
    max_attempts = int(state.get("policy", {}).get("max_repair_attempts", 1)) + 1
    last_gate: GateDecision | None = None
    for attempt in range(1, max_attempts + 1):
        started_at = utc_now()
        before_head = git_head(repo)
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
                adapter.force_stop(session_name)
                after_head = git_head(repo)
                after_status = git_status_text(repo)
                (slice_artifact_dir / f"git-status-after-attempt-{attempt}.txt").write_text(after_status, encoding="utf-8")
                (slice_artifact_dir / "git-status-after.txt").write_text(after_status, encoding="utf-8")
                write_git_diff(repo, before_head, after_head, slice_artifact_dir / "git-diff.patch")
                last_gate = GateDecision("blocked", "timeout waiting for orchestrator-result.json")
            else:
                adapter.force_stop(session_name)
                after_head = git_head(repo)
                after_status = git_status_text(repo)
                (slice_artifact_dir / f"git-status-after-attempt-{attempt}.txt").write_text(after_status, encoding="utf-8")
                (slice_artifact_dir / "git-status-after.txt").write_text(after_status, encoding="utf-8")
                write_git_diff(repo, before_head, after_head, slice_artifact_dir / "git-diff.patch")
                last_gate = verify_gate(repo, state, plan_slice, slice_artifact_dir, before_head, after_head, after_status)
        except McError as exc:
            adapter.capture(session_name, slice_artifact_dir / "pane-capture.txt")
            capture_orchestrator_transcript(harness_name, repo, orchestrator_session_id, slice_artifact_dir)
            capture_worker_runs_summary(slice_artifact_dir)
            adapter.force_stop(session_name)
            after_head = git_head(repo)
            after_status = git_status_text(repo)
            (slice_artifact_dir / f"git-status-after-attempt-{attempt}.txt").write_text(after_status, encoding="utf-8")
            (slice_artifact_dir / "git-status-after.txt").write_text(after_status, encoding="utf-8")
            write_git_diff(repo, before_head, after_head, slice_artifact_dir / "git-diff.patch")
            last_gate = GateDecision("failed", str(exc))

        if last_gate.status == "repairable" and attempt < max_attempts:
            continue
        entry = slice_entry_from_gate(repo, plan_slice, slice_artifact_dir, started_at, last_gate)
        state["slices"].append(entry)
        state["current_slice"] = None
        if last_gate.status == "pass":
            state["status"] = idle_status_after_pass(state)
            state["stop_reason"] = None
            write_run(run_json, state)
            print(f"{plan_slice.slice_id} passed MC gates.")
            return 0
        status_value = "failed" if last_gate.status == "fail" else last_gate.status
        if status_value not in RUN_STOP_STATUSES:
            status_value = "blocked"
        update_state_for_stop(run_json, state, status_value, last_gate.reason)
        print(f"{plan_slice.slice_id} stopped: {last_gate.reason}")
        return 2

    fallback = last_gate or GateDecision("blocked", "slice ended without a gate decision")
    update_state_for_stop(run_json, state, "blocked", fallback.reason)
    return 2


def run_next(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    state = load_run(run_dir)
    plan = resolve_plan(Path(state["plan_path"]))
    slices = parse_plan(plan)
    if not slices:
        raise McError("plan contains no slices")
    candidate = next_slice(slices, state)
    if candidate is None:
        print("No remaining slices.")
        return 0
    runnable, reasons = eligibility(candidate)
    print(f"Next slice: {candidate.slice_id} - {candidate.title}")
    if runnable:
        print("Eligibility: runnable")
        print("Authorized files:")
        for path in candidate.authorized_files:
            print(f"- {path}")
        if not args.dry_run:
            return execute_slice(args, repo, state, candidate, run_dir)
        return 0
    print("Eligibility: blocked")
    for reason in reasons:
        print(f"- {reason}")
    if not args.dry_run:
        update_state_for_stop(run_dir / "run.json", state, "needs-human", "; ".join(reasons))
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


def plan_slice_by_id(slices: list[PlanSlice], slice_id: str) -> PlanSlice | None:
    for plan_slice in slices:
        if plan_slice.slice_id == slice_id:
            return plan_slice
    return None


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
    before_head = previous_completed_head(state, slice_id)
    if before_head is None:
        parent = git_result(repo, "rev-parse", "HEAD^")
        before_head = parent.stdout.strip() if parent.returncode == 0 else None
    after_head = git_head(repo)
    after_status = git_status_text(repo)
    capture_worker_runs_summary(artifact_dir)
    gate = verify_gate(repo, state, plan_slice, artifact_dir, before_head, after_head, after_status)
    reconciled_entry = slice_entry_from_gate(repo, plan_slice, artifact_dir, str(entry.get("started_at") or utc_now()), gate)
    state["slices"][entry_index] = reconciled_entry
    state["current_slice"] = None
    if gate.status == "pass":
        state["status"] = idle_status_after_pass(state)
        state["stop_reason"] = None
        write_run(run_json, state)
        print(f"{slice_id} reconciled and accepted: {gate.reason}")
        return 0
    status_value = "failed" if gate.status == "fail" else gate.status
    if status_value not in RUN_STOP_STATUSES:
        status_value = "blocked"
    state["status"] = status_value
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


def sensitive_artifact_dirs(run_dir: Path) -> list[Path]:
    paths: list[Path] = []
    slices_dir = run_dir / "slices"
    if not slices_dir.exists():
        return paths
    for path in slices_dir.glob("slice-*/*"):
        if path.is_dir() and path.name in SENSITIVE_ARTIFACT_NAMES:
            paths.append(path)
    return sorted(paths)


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Master Controller state and eligibility CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create a durable MC run")
    init.add_argument("--repo", required=True, help="target git repository")
    init.add_argument("--plan", required=True, help="implementation plan markdown file")
    init.add_argument("--harness", required=True, help="harness adapter name")
    init.add_argument("--worktree-root", help="optional worktree root")
    init.set_defaults(func=init_run)

    for name, func, help_text in (
        ("status", status, "show current MC run state"),
        ("summarize", summarize, "summarize current MC run"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--repo", default=".", help="target git repository")
        command.add_argument("--run", default="current", help="run directory, run.json path, or 'current'")
        command.set_defaults(func=func)

    profiles_parser = subparsers.add_parser("profiles", help="list MC harness and worker capability profiles")
    profiles_parser.set_defaults(func=list_profiles)

    preflight_parser = subparsers.add_parser("preflight", help="check the next MC slice launch before running it")
    preflight_parser.add_argument("--repo", default=".", help="target git repository")
    preflight_parser.add_argument("--run", default="current", help="run directory, run.json path, or 'current'")
    preflight_parser.add_argument("--harness-command", help="override harness command for controlled local validation")
    preflight_parser.add_argument("--harness-model", help="model name/alias to compose through the MC harness profile, e.g. sonnet")
    preflight_parser.add_argument("--worker-tools", default="", help="comma-separated worker tools expected for this run, e.g. copilot")
    preflight_parser.add_argument(
        "--allow-profile-command",
        action="store_true",
        help="use MC's capability profile to compose the unattended harness command from run requirements",
    )
    preflight_parser.set_defaults(func=preflight)

    run_next_parser = subparsers.add_parser("run-next", help="inspect the next slice")
    run_next_parser.add_argument("--repo", default=".", help="target git repository")
    run_next_parser.add_argument("--run", default="current", help="run directory, run.json path, or 'current'")
    run_next_parser.add_argument("--dry-run", action="store_true", help="only report next-slice eligibility")
    run_next_parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="maximum seconds to wait for orchestrator result")
    run_next_parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS, help="seconds between tmux/result checks")
    run_next_parser.add_argument("--harness-command", help="override harness command for controlled local validation")
    run_next_parser.add_argument("--harness-model", help="model name/alias to compose through the MC harness profile, e.g. sonnet")
    run_next_parser.add_argument("--worker-tools", default="", help="comma-separated worker tools expected for this run, e.g. copilot")
    run_next_parser.add_argument(
        "--allow-profile-command",
        action="store_true",
        help="use MC's capability profile to compose the unattended harness command from run requirements",
    )
    run_next_parser.add_argument(
        "--allow-unattended-default",
        action="store_true",
        help="opt in to a known unattended-safe launch command for --harness codex/claude (disables per-action approval; MC's post-hoc gates become the safety boundary)",
    )
    run_next_parser.set_defaults(func=run_next)

    run_parser = subparsers.add_parser("run", help="run eligible slices until complete or stopped")
    run_parser.add_argument("--repo", default=".", help="target git repository")
    run_parser.add_argument("--run", default="current", help="run directory, run.json path, or 'current'")
    run_parser.add_argument("--scope", required=True, choices=["remaining"], help="run scope")
    run_parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="maximum seconds to wait for each orchestrator result")
    run_parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS, help="seconds between tmux/result checks")
    run_parser.add_argument("--harness-command", help="override harness command for controlled local validation")
    run_parser.add_argument("--harness-model", help="model name/alias to compose through the MC harness profile, e.g. sonnet")
    run_parser.add_argument("--worker-tools", default="", help="comma-separated worker tools expected for this run, e.g. copilot")
    run_parser.add_argument(
        "--allow-profile-command",
        action="store_true",
        help="use MC's capability profile to compose the unattended harness command from run requirements",
    )
    run_parser.add_argument(
        "--allow-unattended-default",
        action="store_true",
        help="opt in to a known unattended-safe launch command for --harness codex/claude (disables per-action approval; MC's post-hoc gates become the safety boundary)",
    )
    run_parser.set_defaults(func=run_remaining, dry_run=False)

    reconcile_parser = subparsers.add_parser("reconcile", help="re-check and repair a stopped slice from local evidence")
    reconcile_parser.add_argument("--repo", default=".", help="target git repository")
    reconcile_parser.add_argument("--run", default="current", help="run directory, run.json path, or 'current'")
    reconcile_parser.set_defaults(func=reconcile)

    stop_parser = subparsers.add_parser("stop", help="cancel the current MC run")
    stop_parser.add_argument("--repo", default=".", help="target git repository")
    stop_parser.add_argument("--run", default="current", help="run directory, run.json path, or 'current'")
    stop_parser.add_argument("--reason", default="cancelled by user", help="reason recorded in run state")
    stop_parser.add_argument("--harness-command", help="override harness command for controlled local validation")
    stop_parser.add_argument("--harness-model", help="model name/alias to compose through the MC harness profile, e.g. sonnet")
    stop_parser.add_argument("--worker-tools", default="", help="comma-separated worker tools expected for this run, e.g. copilot")
    stop_parser.add_argument(
        "--allow-profile-command",
        action="store_true",
        help="use MC's capability profile to compose the unattended harness command from run requirements",
    )
    stop_parser.add_argument(
        "--allow-unattended-default",
        action="store_true",
        help="opt in to a known unattended-safe launch command for --harness codex/claude (disables per-action approval; MC's post-hoc gates become the safety boundary)",
    )
    stop_parser.set_defaults(func=stop)

    archive_parser = subparsers.add_parser("archive-sensitive", help="archive sensitive worker state from a run")
    archive_parser.add_argument("--repo", default=".", help="target git repository")
    archive_parser.add_argument("--run", default="current", help="run directory, run.json path, or 'current'")
    archive_parser.add_argument("--dry-run", action="store_true", help="print artifact moves without changing files")
    archive_parser.set_defaults(func=archive_sensitive)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except McError as exc:
        print(f"mc: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
