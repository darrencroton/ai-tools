#!/usr/bin/env python3
"""Master Controller state and plan eligibility CLI."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import platform
import re
import shutil
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any


SCHEMA_VERSION = 1
PARSER_NAME = "implementation-plan-markdown-v1"
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

    def __init__(self, harness_name: str, command_override: str | None = None, allow_unattended_default: bool = False):
        self.harness_name = harness_name
        self.command_override = command_override
        self.allow_unattended_default = allow_unattended_default
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
        env_prefix = " ".join(
            f"{key}={shlex.quote(value)}"
            for key, value in {
                "MC_SLICE_ARTIFACT_DIR": str(slice_artifact_dir),
                "MC_RUN_JSON_PATH": str(run_json),
                "MC_PLAN_PATH": str(plan_path),
                "MC_SLICE_ID": plan_slice.slice_id,
            }.items()
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


def load_prompt_template() -> str:
    path = skill_root() / "references" / "orchestrator-prompt.md"
    text = path.read_text(encoding="utf-8")
    match = re.search(r"```md\n(?P<template>.*?)\n```", text, flags=re.DOTALL)
    if not match:
        raise McError(f"orchestrator prompt template not found in {path}")
    return match.group("template")


def render_orchestrator_prompt(state: dict[str, Any], plan_slice: PlanSlice, slice_artifact_dir: Path, run_json: Path) -> str:
    template = load_prompt_template()
    values = {
        "plan_path": state["plan_path"],
        "run_json_path": str(run_json),
        "slice_artifact_dir": str(slice_artifact_dir),
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
        commit_hash = git(repo, "rev-parse", str(commit["hash"]))
        if after_head != commit_hash:
            return GateDecision("fail", "reported commit is not the current HEAD", result, tuple(sorted(actual_changed)))
        if meaningful_status_lines(after_status):
            return GateDecision("fail", "post-commit worktree is dirty outside .ai-mc/", result, tuple(sorted(actual_changed)))

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
    print(f"Run: {state['run_id']}")
    print(f"Status: {state['status']}")
    print(f"Repo: {state['repo_path']}")
    print(f"Plan: {state['plan_path']}")
    print(f"Branch: {state['branch']}")
    print(f"Harness: {state['harness']['name']}")
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
    slice_artifact_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = slice_artifact_dir / "prompt.md"
    prompt_path.write_text(render_orchestrator_prompt(state, plan_slice, slice_artifact_dir, run_json), encoding="utf-8")

    adapter = TmuxHarnessAdapter(
        state["harness"]["name"],
        getattr(args, "harness_command", None),
        getattr(args, "allow_unattended_default", False),
    )
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
            while True:
                # Always record at least one activity snapshot before deciding,
                # even if the result already landed: audit evidence should not
                # depend on winning a race against a fast-finishing harness.
                activity = adapter.detect_activity(session_name, previous_capture)
                previous_capture = str(activity.get("capture", ""))
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
            state["status"] = "complete" if len(completed_slice_ids(state)) >= state["plan"]["slice_count"] else "initialized"
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


def stop(args: argparse.Namespace) -> int:
    repo = resolve_repo(Path(args.repo))
    run_dir = resolve_run_dir(repo, args.run)
    state = load_run(run_dir)
    current = state.get("current_slice") or {}
    session_name = current.get("tmux_session")
    if session_name:
        adapter = TmuxHarnessAdapter(
            state["harness"]["name"],
            getattr(args, "harness_command", None),
            getattr(args, "allow_unattended_default", False),
        )
        adapter.request_stop(str(session_name))
        time.sleep(0.5)
        adapter.force_stop(str(session_name))
    update_state_for_stop(run_dir / "run.json", state, "cancelled", args.reason)
    print(f"Run cancelled: {args.reason}")
    return 0


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

    run_next_parser = subparsers.add_parser("run-next", help="inspect the next slice")
    run_next_parser.add_argument("--repo", default=".", help="target git repository")
    run_next_parser.add_argument("--run", default="current", help="run directory, run.json path, or 'current'")
    run_next_parser.add_argument("--dry-run", action="store_true", help="only report next-slice eligibility")
    run_next_parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="maximum seconds to wait for orchestrator result")
    run_next_parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS, help="seconds between tmux/result checks")
    run_next_parser.add_argument("--harness-command", help="override harness command for controlled local validation")
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
    run_parser.add_argument(
        "--allow-unattended-default",
        action="store_true",
        help="opt in to a known unattended-safe launch command for --harness codex/claude (disables per-action approval; MC's post-hoc gates become the safety boundary)",
    )
    run_parser.set_defaults(func=run_remaining, dry_run=False)

    stop_parser = subparsers.add_parser("stop", help="cancel the current MC run")
    stop_parser.add_argument("--repo", default=".", help="target git repository")
    stop_parser.add_argument("--run", default="current", help="run directory, run.json path, or 'current'")
    stop_parser.add_argument("--reason", default="cancelled by user", help="reason recorded in run state")
    stop_parser.add_argument("--harness-command", help="override harness command for controlled local validation")
    stop_parser.add_argument(
        "--allow-unattended-default",
        action="store_true",
        help="opt in to a known unattended-safe launch command for --harness codex/claude (disables per-action approval; MC's post-hoc gates become the safety boundary)",
    )
    stop_parser.set_defaults(func=stop)

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
