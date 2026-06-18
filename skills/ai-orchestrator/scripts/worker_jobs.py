#!/usr/bin/env python3
"""Manage tracked worker runs for ai-orchestrator.

Worker artifacts are written to .ai-orchestrator/runs/ in the current project
by default (override with AI_ORCHESTRATOR_ARTIFACT_ROOT). Provides per-run
directories, manifest tracking, status, activity, cancel, and extract commands.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ARTIFACT_ROOT_ENV = "AI_ORCHESTRATOR_ARTIFACT_ROOT"
STATE_DIR_NAME = ".ai-orchestrator"
RUNS_DIR_NAME = "runs"
MANIFEST_NAME = "manifest.json"
MANIFEST_LOCK_NAME = ".manifest.lock"
INDEX_NAME = "index.json"
INDEX_LOCK_NAME = ".index.lock"
LABEL_RE = re.compile(r"^\d{2}-[a-z0-9]+-[a-z0-9]+(?:-[a-z0-9]+)*(?:-r\d+)?$")
# Match line-based SECTION headers even when a model prefixes them with Markdown.
SECTION_RE = re.compile(r"^\s*(?:#+\s*)?SECTION:\s*([A-Za-z0-9_ -]+)\s*$", re.MULTILINE)
SESSION_ID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
CODEX_SESSION_ID_RE = re.compile(r"^session id:\s*([0-9a-f-]+)\s*$", re.MULTILINE)


class WorkerJobsError(RuntimeError):
    """Raised for expected operational errors."""


def artifact_root_override() -> Path | None:
    override = os.environ.get(ARTIFACT_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return None


def existing_state_dir(start: Path | None = None) -> Path | None:
    base = (start or Path.cwd()).expanduser().resolve()
    for candidate in (base, *base.parents):
        state_dir = candidate / STATE_DIR_NAME
        if state_dir.exists():
            return state_dir
    return None


def default_state_dir(start: Path | None = None) -> Path:
    override = artifact_root_override()
    if override is not None:
        return override
    existing = existing_state_dir(start)
    if existing is not None:
        return existing
    return ((start or Path.cwd()).expanduser().resolve() / STATE_DIR_NAME)


def default_root(start: Path | None = None) -> Path:
    override = artifact_root_override()
    if override is not None:
        return override
    return default_state_dir(start) / RUNS_DIR_NAME


def state_dir_from_run_dir(path: Path) -> Path | None:
    resolved = path.expanduser().resolve()
    override = artifact_root_override()
    if override is not None:
        try:
            resolved.relative_to(override)
        except ValueError:
            return None
        return override
    for parent in resolved.parents:
        if parent.name != RUNS_DIR_NAME:
            continue
        state_dir = parent.parent
        if state_dir.name == STATE_DIR_NAME:
            return state_dir
    return None


def resolve_run_dir(arg: str) -> Path:
    """Resolve --run-dir; 'current' follows the .ai-orchestrator/current symlink."""
    if arg == "current":
        current_link = default_state_dir() / "current"
        if not current_link.exists():
            raise WorkerJobsError("No current run: .ai-orchestrator/current symlink not found.")
        return current_link.resolve()
    return Path(arg).expanduser().resolve()


def ensure_managed_run_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    state_dir = state_dir_from_run_dir(resolved)
    if state_dir is None:
        managed_root = default_root()
        raise WorkerJobsError(
            f"Run directory must live under helper-managed artifact root {managed_root}. "
            f"Set {ARTIFACT_ROOT_ENV} to override the root."
        )
    return resolved


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc).timestamp()
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None


def process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temp_path.replace(path)


def normalize_command(command: list[str]) -> list[str]:
    if command and command[0] == "--":
        return command[1:]
    return command


def normalize_section_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def infer_tool_name(command: list[str]) -> str:
    return Path(command[0]).name if command else ""


def has_flag(command: list[str], flags: set[str]) -> bool:
    return any(arg in flags for arg in command)


def option_values(command: list[str], flags: set[str]) -> list[str]:
    values: list[str] = []
    idx = 0
    while idx < len(command):
        arg = command[idx]
        if arg in flags:
            if idx + 1 < len(command):
                values.append(command[idx + 1])
            idx += 2
            continue
        matched = False
        for flag in flags:
            prefix = f"{flag}="
            if arg.startswith(prefix):
                values.append(arg[len(prefix) :])
                matched = True
                break
        idx += 1 if matched else 1
    return values


def codex_prompt_from_command(command: list[str]) -> str | None:
    if len(command) < 2 or command[1] != "exec":
        return None
    idx = 2

    exec_skip_value_flags = {
        "--enable",
        "--disable",
        "-c",
        "--config",
        "-i",
        "--image",
        "-m",
        "--model",
        "--local-provider",
        "-s",
        "--sandbox",
        "-p",
        "--profile",
        "-C",
        "--cd",
        "--add-dir",
        "--output-schema",
        "--color",
        "-o",
        "--output-last-message",
    }
    exec_standalone_flags = {
        "--oss",
        "--full-auto",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--ephemeral",
        "--progress-cursor",
        "--json",
    }

    while idx < len(command):
        arg = command[idx]
        if arg in exec_skip_value_flags:
            idx += 2
            continue
        if any(arg.startswith(f"{flag}=") for flag in exec_skip_value_flags):
            idx += 1
            continue
        if arg in exec_standalone_flags or any(arg.startswith(f"{flag}=") for flag in exec_standalone_flags):
            idx += 1
            continue
        if arg.startswith("-"):
            idx += 1
            continue
        if arg == "review":
            idx += 1
            break
        if arg in {"resume", "help"}:
            return None
        return arg

    review_skip_value_flags = {
        "--enable",
        "--disable",
        "-c",
        "--config",
        "--base",
        "--commit",
        "-m",
        "--model",
        "--title",
        "-o",
        "--output-last-message",
    }
    review_standalone_flags = {
        "--uncommitted",
        "--full-auto",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--ephemeral",
        "--json",
    }

    while idx < len(command):
        arg = command[idx]
        if arg in review_skip_value_flags:
            idx += 2
            continue
        if any(arg.startswith(f"{flag}=") for flag in review_skip_value_flags):
            idx += 1
            continue
        if arg in review_standalone_flags or any(arg.startswith(f"{flag}=") for flag in review_standalone_flags):
            idx += 1
            continue
        if arg.startswith("-"):
            idx += 1
            continue
        return arg
    return None


def prompt_from_command(command: list[str]) -> str | None:
    tool = infer_tool_name(command)
    if tool == "codex":
        return codex_prompt_from_command(command)
    values = option_values(command, {"-p", "--print", "--prompt"})
    return values[-1] if values else None


def prompt_marker(prompt: str | None) -> str | None:
    if not prompt:
        return None
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("TASK:") or stripped.startswith("REVIEW THIS") or stripped.startswith("RESEARCH:"):
            return stripped[:200]
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("RETURN:") or stripped.startswith("FILES:"):
            return stripped[:200]
    collapsed = prompt.strip().replace("\n", " ")
    return collapsed[:200] if collapsed else None


def infer_project_dirs(command: list[str], tool: str) -> list[Path]:
    raw_values: list[str] = []
    if tool == "claude":
        raw_values.extend(option_values(command, {"--add-dir"}))
    elif tool == "codex":
        raw_values.extend(option_values(command, {"-C", "--cd", "--add-dir"}))
    elif tool == "copilot":
        raw_values.extend(option_values(command, {"--add-dir"}))

    project_dirs: list[Path] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        try:
            project_dir = Path(raw_value).expanduser().resolve()
        except OSError:
            continue
        key = str(project_dir)
        if key in seen:
            continue
        seen.add(key)
        project_dirs.append(project_dir)
    return project_dirs


def claude_project_root(project_dir: Path) -> Path:
    normalized = str(project_dir).replace(os.sep, "-")
    return Path.home() / ".claude" / "projects" / normalized


def codex_session_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def session_id_from_text(text: str) -> str | None:
    match = SESSION_ID_RE.search(text)
    return match.group(0) if match else None


def session_id_from_path(path: Path) -> str | None:
    return session_id_from_text(path.name)


def codex_session_id_from_output(path: Path, *, max_bytes: int = 8192) -> str | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            text = handle.read(max_bytes)
    except OSError:
        return None
    match = CODEX_SESSION_ID_RE.search(text)
    return match.group(1) if match else None


def file_head_contains(path: Path, marker: str | None, *, max_bytes: int = 131072) -> bool:
    if not marker:
        return True
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            return marker in handle.read(max_bytes)
    except OSError:
        return False


def candidate_prompt_matches(path: Path, prompt: str | None, *, max_lines: int = 6) -> bool:
    if not prompt:
        return False
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for _ in range(max_lines):
                line = handle.readline()
                if not line:
                    break
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") == "queue-operation" and row.get("content") == prompt:
                    return True
                if row.get("type") == "user":
                    message_content = row.get("message", {}).get("content")
                    if isinstance(message_content, str) and message_content == prompt:
                        return True
    except OSError:
        return False
    return False


def codex_candidate_prompt_matches(path: Path, prompt: str | None, *, max_lines: int = 40) -> bool:
    if not prompt:
        return False
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for _ in range(max_lines):
                line = handle.readline()
                if not line:
                    break
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                row_type = row.get("type")
                payload = row.get("payload", {})
                if row_type == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
                    for item in payload.get("content", []):
                        if item.get("type") == "input_text" and item.get("text") == prompt:
                            return True
                if row_type == "event_msg" and payload.get("type") == "user_message" and payload.get("message") == prompt:
                    return True
    except OSError:
        return False
    return False


def codex_candidate_cwd_matches(path: Path, cwd: Path | None) -> bool:
    if cwd is None:
        return False
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for _ in range(3):
                line = handle.readline()
                if not line:
                    break
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") != "session_meta":
                    continue
                session_cwd = row.get("payload", {}).get("cwd")
                if not isinstance(session_cwd, str):
                    return False
                try:
                    return Path(session_cwd).expanduser().resolve() == cwd
                except OSError:
                    return session_cwd == str(cwd)
    except OSError:
        return False
    return False


def codex_workdir_from_command(command: list[str]) -> Path | None:
    values = option_values(command, {"-C", "--cd"})
    if not values:
        return None
    try:
        return Path(values[-1]).expanduser().resolve()
    except OSError:
        return None


def resolve_claude_session_path(entry: dict[str, Any], *, wait_seconds: float = 0.0) -> Path | None:
    existing = entry.get("session_path")
    if isinstance(existing, str) and existing:
        path = Path(existing)
        if path.exists():
            return path

    command = entry.get("command", [])
    if not isinstance(command, list):
        return None

    prompt = prompt_from_command(command)
    marker = prompt_marker(prompt)
    started_at = parse_iso(entry.get("started_at"))
    deadline = time.time() + max(wait_seconds, 0.0)
    best_match: tuple[int, float, Path] | None = None

    while True:
        for project_dir in infer_project_dirs(command, "claude"):
            session_root = claude_project_root(project_dir)
            if not session_root.exists():
                continue
            candidates: list[tuple[float, Path]] = []
            for candidate in session_root.glob("*.jsonl"):
                try:
                    candidates.append((candidate.stat().st_mtime, candidate))
                except OSError:
                    continue
            for _, candidate in sorted(candidates, reverse=True):
                stat = candidate.stat()
                if started_at is not None and stat.st_mtime < (started_at - 300):
                    continue
                score = 0
                if started_at is not None and stat.st_mtime >= (started_at - 1):
                    score += 2
                if candidate_prompt_matches(candidate, prompt):
                    score += 10
                if file_head_contains(candidate, marker):
                    score += 4
                if best_match is None or (score, stat.st_mtime) > (best_match[0], best_match[1]):
                    best_match = (score, stat.st_mtime, candidate)

        threshold = 10 if prompt else 4
        if best_match and best_match[0] >= threshold:
            return best_match[2]
        if time.time() >= deadline:
            fallback_threshold = 6 if prompt else 2
            return best_match[2] if best_match and best_match[0] >= fallback_threshold else None
        time.sleep(0.5)


def resolve_codex_session_path(entry: dict[str, Any], *, wait_seconds: float = 0.0) -> Path | None:
    existing = entry.get("session_path")
    if isinstance(existing, str) and existing:
        path = Path(existing)
        if path.exists():
            return path

    command = entry.get("command", [])
    if not isinstance(command, list) or has_flag(command, {"--ephemeral"}):
        return None

    session_id = entry.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        outfile = Path(entry["outfile"])
        session_id = codex_session_id_from_output(outfile)

    session_root = codex_session_root()
    if session_id and session_root.exists():
        exact_matches = sorted(session_root.rglob(f"*{session_id}.jsonl"))
        if exact_matches:
            return exact_matches[-1]

    prompt = prompt_from_command(command)
    workdir = codex_workdir_from_command(command)
    started_at = parse_iso(entry.get("started_at"))
    deadline = time.time() + max(wait_seconds, 0.0)
    best_match: tuple[int, float, Path] | None = None

    while True:
        if session_root.exists():
            candidates: list[tuple[float, Path]] = []
            for candidate in session_root.rglob("*.jsonl"):
                try:
                    candidates.append((candidate.stat().st_mtime, candidate))
                except OSError:
                    continue
            for _, candidate in sorted(candidates, reverse=True):
                stat = candidate.stat()
                if started_at is not None and stat.st_mtime < (started_at - 300):
                    continue
                score = 0
                if started_at is not None and stat.st_mtime >= (started_at - 1):
                    score += 2
                if workdir is not None and codex_candidate_cwd_matches(candidate, workdir):
                    score += 6
                if codex_candidate_prompt_matches(candidate, prompt):
                    score += 10
                if best_match is None or (score, stat.st_mtime) > (best_match[0], best_match[1]):
                    best_match = (score, stat.st_mtime, candidate)

        threshold = 12 if prompt and workdir else 10 if prompt else 6 if workdir else 2
        if best_match and best_match[0] >= threshold:
            return best_match[2]
        if time.time() >= deadline:
            fallback_threshold = 8 if prompt and workdir else 6 if prompt else 4 if workdir else 2
            return best_match[2] if best_match and best_match[0] >= fallback_threshold else None
        time.sleep(0.5)


def read_tail_text(path: Path, *, max_bytes: int = 32768) -> str:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        return handle.read().decode("utf-8", errors="replace")


def summarize_session_row(row: dict[str, Any]) -> tuple[str, str | None]:
    row_type = row.get("type", "unknown")
    if row_type == "assistant":
        content = row.get("message", {}).get("content")
        if isinstance(content, list):
            for item in content:
                item_type = item.get("type")
                if item_type == "tool_use":
                    return ("assistant.tool_use", item.get("name"))
                if item_type == "text":
                    return ("assistant.text", "text")
                if item_type == "thinking":
                    return ("assistant.thinking", "thinking")
    if row_type == "user":
        content = row.get("message", {}).get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            if content[0].get("type") == "tool_result":
                return ("user.tool_result", None)
        return ("user", None)
    if row_type == "queue-operation":
        return ("queue-operation", row.get("operation"))
    return (row_type, None)


def summarize_codex_row(row: dict[str, Any]) -> tuple[str, str | None]:
    row_type = row.get("type", "unknown")
    payload = row.get("payload", {})
    if row_type == "response_item":
        payload_type = payload.get("type")
        if payload_type == "message":
            role = payload.get("role")
            if role == "assistant":
                return ("assistant.text", "text")
            if role == "user":
                return ("user", None)
            return (f"message.{role or 'unknown'}", None)
        if payload_type == "function_call":
            return ("assistant.function_call", payload.get("name"))
        if payload_type == "function_call_output":
            return ("tool.output", None)
        if payload_type == "reasoning":
            return ("assistant.reasoning", "reasoning")
        return (f"response_item.{payload_type or 'unknown'}", None)
    if row_type == "event_msg":
        event_type = payload.get("type")
        if event_type == "agent_message":
            return ("assistant.text", "text")
        return (f"event.{event_type or 'unknown'}", None)
    return (row_type, None)


def claude_session_activity(path: Path) -> dict[str, Any]:
    stat = path.stat()
    now = time.time()
    tail_rows: list[dict[str, Any]] = []
    for line in read_tail_text(path).splitlines():
        try:
            tail_rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    last_row_with_timestamp = next((row for row in reversed(tail_rows) if row.get("timestamp")), None)
    last_assistant_row = next(
        (row for row in reversed(tail_rows) if row.get("type") == "assistant" and row.get("timestamp")),
        None,
    )

    payload: dict[str, Any] = {
        "session_path": str(path),
        "session_mtime_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_mtime_age_s": max(0, int(now - stat.st_mtime)),
        "session_size": stat.st_size,
    }

    if last_row_with_timestamp is not None:
        row_type, detail = summarize_session_row(last_row_with_timestamp)
        payload["last_event_at"] = last_row_with_timestamp.get("timestamp")
        payload["last_event_type"] = row_type
        if detail:
            payload["last_event_detail"] = detail
        event_ts = parse_iso(last_row_with_timestamp.get("timestamp"))
        if event_ts is not None:
            payload["last_event_age_s"] = max(0, int(now - event_ts))

    if last_assistant_row is not None:
        row_type, detail = summarize_session_row(last_assistant_row)
        payload["last_assistant_at"] = last_assistant_row.get("timestamp")
        payload["last_assistant_type"] = row_type
        if detail:
            payload["last_assistant_detail"] = detail
        assistant_ts = parse_iso(last_assistant_row.get("timestamp"))
        if assistant_ts is not None:
            payload["last_assistant_age_s"] = max(0, int(now - assistant_ts))

    return payload


def codex_session_activity(path: Path) -> dict[str, Any]:
    stat = path.stat()
    now = time.time()
    tail_rows: list[dict[str, Any]] = []
    for line in read_tail_text(path).splitlines():
        try:
            tail_rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    last_row_with_timestamp = next((row for row in reversed(tail_rows) if row.get("timestamp")), None)
    last_assistant_row = next(
        (
            row
            for row in reversed(tail_rows)
            if row.get("timestamp") and summarize_codex_row(row)[0].startswith("assistant.")
        ),
        None,
    )

    payload: dict[str, Any] = {
        "session_path": str(path),
        "session_id": session_id_from_path(path),
        "session_mtime_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_mtime_age_s": max(0, int(now - stat.st_mtime)),
        "session_size": stat.st_size,
    }

    if last_row_with_timestamp is not None:
        row_type, detail = summarize_codex_row(last_row_with_timestamp)
        payload["last_event_at"] = last_row_with_timestamp.get("timestamp")
        payload["last_event_type"] = row_type
        if detail:
            payload["last_event_detail"] = detail
        event_ts = parse_iso(last_row_with_timestamp.get("timestamp"))
        if event_ts is not None:
            payload["last_event_age_s"] = max(0, int(now - event_ts))

    if last_assistant_row is not None:
        row_type, detail = summarize_codex_row(last_assistant_row)
        payload["last_assistant_at"] = last_assistant_row.get("timestamp")
        payload["last_assistant_type"] = row_type
        if detail:
            payload["last_assistant_detail"] = detail
        assistant_ts = parse_iso(last_assistant_row.get("timestamp"))
        if assistant_ts is not None:
            payload["last_assistant_age_s"] = max(0, int(now - assistant_ts))

    return payload


def extract_claude_session_text(path: Path) -> str | None:
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None

    best_plan: str | None = None
    for row in reversed(rows):
        if row.get("type") != "assistant":
            continue
        content = row.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            item_type = item.get("type")
            if item_type == "text":
                text = item.get("text", "").strip()
                if text:
                    return text
            if item_type == "tool_use" and item.get("name") == "ExitPlanMode":
                plan = item.get("input", {}).get("plan", "").strip()
                if plan and best_plan is None:
                    best_plan = plan
    return best_plan


def extract_codex_session_text(path: Path) -> str | None:
    best_agent_message: str | None = None
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            rows: list[dict[str, Any]] = []
            for line in handle:
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None

    for row in reversed(rows):
        row_type = row.get("type")
        payload = row.get("payload", {})
        if row_type == "response_item" and payload.get("type") == "message" and payload.get("role") == "assistant":
            parts = [
                item.get("text", "").strip()
                for item in payload.get("content", [])
                if item.get("type") == "output_text" and item.get("text", "").strip()
            ]
            if parts:
                return "\n".join(parts).strip()
        if row_type == "event_msg" and payload.get("type") == "agent_message":
            message = str(payload.get("message", "")).strip()
            if message and best_agent_message is None:
                best_agent_message = message
    return best_agent_message


def resolve_session_path(entry: dict[str, Any], *, wait_seconds: float = 0.0) -> Path | None:
    tool = entry.get("tool")
    if tool == "claude":
        return resolve_claude_session_path(entry, wait_seconds=wait_seconds)
    if tool == "codex":
        return resolve_codex_session_path(entry, wait_seconds=wait_seconds)
    return None


def session_activity(tool: str, path: Path) -> dict[str, Any]:
    if tool == "claude":
        return claude_session_activity(path)
    if tool == "codex":
        return codex_session_activity(path)
    return {}


def extract_session_text(tool: str, path: Path) -> str | None:
    if tool == "claude":
        return extract_claude_session_text(path)
    if tool == "codex":
        return extract_codex_session_text(path)
    return None


def looks_like_codex_exec_transcript(text: str) -> bool:
    stripped = text.lstrip()
    if stripped.startswith("OpenAI Codex v"):
        return True
    return "session id:" in text and "tokens used" in text


def validate_label(label: str) -> None:
    if LABEL_RE.fullmatch(label):
        return
    raise WorkerJobsError(
        "Worker label must match <nn>-<tool>-<subtask-slug>[-rN] in lowercase kebab-case, "
        f"for example 01-codex-trace-login or 03-claude-review-plan-r1: {label}"
    )


def manifest_path(run_dir: Path) -> Path:
    return run_dir / MANIFEST_NAME


def manifest_lock_path(run_dir: Path) -> Path:
    return run_dir / MANIFEST_LOCK_NAME


def index_path(state_dir: Path) -> Path:
    return state_dir / INDEX_NAME


def index_lock_path(state_dir: Path) -> Path:
    return state_dir / INDEX_LOCK_NAME


@contextmanager
def hold_lock(lock_path: Path, description: str):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise WorkerJobsError(f"Unable to lock {description}: {lock_path.parent}") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def hold_manifest_lock(run_dir: Path):
    with hold_lock(manifest_lock_path(run_dir), f"manifest for run directory {run_dir}"):
        yield


@contextmanager
def hold_index_lock(state_dir: Path):
    with hold_lock(index_lock_path(state_dir), f"index for state directory {state_dir}"):
        yield


def load_manifest(run_dir: Path) -> dict[str, Any]:
    path = manifest_path(run_dir)
    if not path.exists():
        raise WorkerJobsError(f"Run directory has no manifest: {run_dir}")
    return read_json(path)


def save_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    write_json(manifest_path(run_dir), manifest)


def ensure_manifest(run_dir: Path) -> dict[str, Any]:
    path = manifest_path(run_dir)
    if path.exists():
        return read_json(path)
    manifest = {
        "created_at": iso_now(),
        "run_dir": str(run_dir),
        "workers": {},
    }
    save_manifest(run_dir, manifest)
    return manifest


def derive_run_dir(root: Path, prefix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return root / f"{prefix}-{stamp}-{os.getpid()}"


def load_index(state_dir: Path) -> list[dict[str, Any]]:
    path = index_path(state_dir)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return payload if isinstance(payload, list) else []


def save_index(state_dir: Path, index: list[dict[str, Any]]) -> None:
    write_json(index_path(state_dir), index)


def worker_status(entry: dict[str, Any]) -> dict[str, Any]:
    pid = int(entry.get("pid", 0))
    status_file = Path(entry["status_file"])
    outfile = Path(entry["outfile"])
    errfile = Path(entry["errfile"])
    status_payload = read_json(status_file) if status_file.exists() else {}
    status_state = status_payload.get("state")
    if status_state in {"completed", "cancelled", "failed"}:
        running = False
    else:
        running = process_running(pid)
    returncode = status_payload.get("returncode")
    if running:
        state = "running"
    elif status_state == "cancelled":
        state = "cancelled"
    elif returncode not in (None, 0):
        state = "failed"
    elif returncode == 0:
        state = status_state or "completed"
    elif status_state == "running":
        state = "stalled"
    else:
        state = status_state or "finished"
    return {
        "label": entry["label"],
        "pid": pid,
        "tool": entry.get("tool"),
        "state": state,
        "running": running,
        "outfile": str(outfile),
        "outfile_exists": outfile.exists(),
        "outfile_size": outfile.stat().st_size if outfile.exists() else 0,
        "errfile": str(errfile),
        "errfile_exists": errfile.exists(),
        "errfile_size": errfile.stat().st_size if errfile.exists() else 0,
        "returncode": returncode,
        "started_at": entry.get("started_at"),
        "finished_at": status_payload.get("finished_at"),
        "command": entry.get("command", []),
    }


def run_status_from_manifest(manifest: dict[str, Any]) -> str:
    states = {worker_status(entry)["state"] for entry in manifest.get("workers", {}).values()}
    if not states or states & {"running", "stalled", "finished"}:
        return "active"
    if "failed" in states:
        return "failed"
    if "cancelled" in states:
        return "cancelled"
    return "completed"


def sync_run_index(run_dir: Path, *, manifest: dict[str, Any] | None = None, status: str | None = None) -> None:
    state_dir = state_dir_from_run_dir(run_dir)
    if state_dir is None:
        return
    manifest = manifest if manifest is not None else load_manifest(run_dir)
    run_status = status or run_status_from_manifest(manifest)
    created_at = manifest.get("created_at") or iso_now()
    with hold_index_lock(state_dir):
        index = load_index(state_dir)
        for entry in index:
            if entry.get("run") != run_dir.name:
                continue
            entry["created_at"] = entry.get("created_at") or created_at
            entry["status"] = run_status
            break
        else:
            index.append({"created_at": created_at, "run": run_dir.name, "status": run_status})
        save_index(state_dir, index)


def find_section_blocks(text: str) -> list[tuple[str, int, int]]:
    matches = list(SECTION_RE.finditer(text))
    blocks: list[tuple[str, int, int]] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        blocks.append((normalize_section_name(match.group(1)), start, end))
    return blocks


def extract_sections(text: str, names: list[str]) -> str:
    wanted = {normalize_section_name(name) for name in names}
    blocks = find_section_blocks(text)
    if not blocks:
        return text
    parts = [text[start:end].rstrip() for name, start, end in blocks if name in wanted]
    return "\n\n".join(part for part in parts if part).strip() or text


def helper_activity(entry: dict[str, Any], now: float) -> dict[str, Any]:
    latest_mtime = None
    latest_path: Path | None = None
    for path_key in ("outfile", "errfile", "status_file"):
        raw_path = entry.get(path_key)
        if not isinstance(raw_path, str) or not raw_path:
            continue
        path = Path(raw_path)
        if not path.exists():
            continue
        path_mtime = path.stat().st_mtime
        if latest_mtime is None or path_mtime > latest_mtime:
            latest_mtime = path_mtime
            latest_path = path
    if latest_mtime is None:
        return {}
    return {
        "activity_source": "helper_files",
        "last_activity_at": datetime.fromtimestamp(latest_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_activity_age_s": max(0, int(now - latest_mtime)),
        "last_activity_path": str(latest_path) if latest_path is not None else None,
    }


def extract_best_result(entry: dict[str, Any]) -> dict[str, str]:
    status_file = Path(entry["status_file"])
    status_payload = read_json(status_file) if status_file.exists() else {}
    outfile = Path(entry["outfile"])
    if outfile.exists() and outfile.stat().st_size > 0:
        out_text = outfile.read_text()
        if out_text.strip():
            if entry.get("tool") == "codex" and status_payload.get("returncode") == 0 and looks_like_codex_exec_transcript(out_text):
                session_path = resolve_session_path(entry)
                if session_path is not None:
                    session_text = extract_session_text("codex", session_path)
                    if session_text:
                        return {
                            "source": "codex_session",
                            "source_path": str(session_path),
                            "text": session_text,
                        }
            return {
                "source": "outfile",
                "source_path": str(outfile),
                "text": out_text,
            }

    if entry.get("tool") in {"claude", "codex"} and status_payload.get("returncode") == 0:
        session_path = resolve_session_path(entry)
        if session_path is not None:
            session_text = extract_session_text(str(entry.get("tool")), session_path)
            if session_text:
                return {
                    "source": f"{entry.get('tool')}_session",
                    "source_path": str(session_path),
                    "text": session_text,
                }

    errfile = Path(entry["errfile"])
    if errfile.exists() and errfile.stat().st_size > 0:
        err_text = errfile.read_text()
        blocks = find_section_blocks(err_text)
        if blocks:
            return {
                "source": "errfile_sections",
                "source_path": str(errfile),
                "text": err_text[blocks[-1][1] :].strip(),
            }
        result_idx = err_text.rfind("RESULT:")
        if result_idx != -1:
            return {
                "source": "errfile_result",
                "source_path": str(errfile),
                "text": err_text[result_idx:].strip(),
            }
        return {
            "source": "errfile",
            "source_path": str(errfile),
            "text": err_text,
        }

    raise WorkerJobsError(f"No output available for worker {entry['label']}")


def extract_best_text(entry: dict[str, Any]) -> str:
    return extract_best_result(entry)["text"]


def command_init(args: argparse.Namespace) -> int:
    root = default_root()
    if args.root:
        requested_root = Path(args.root).expanduser().resolve()
        if requested_root != root:
            raise WorkerJobsError(
                f"Custom --root is no longer supported. Use {ARTIFACT_ROOT_ENV}={requested_root} "
                "to override the helper-managed artifact root."
            )
    run_dir = derive_run_dir(root, args.prefix)
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = ensure_manifest(run_dir)

    state_dir = default_state_dir()
    current_link = state_dir / "current"
    if current_link.exists() or current_link.is_symlink():
        current_link.unlink()
    current_link.symlink_to(run_dir.relative_to(state_dir))

    sync_run_index(run_dir, manifest=manifest, status="active")

    print(run_dir)
    return 0


def normalize_dependencies(manifest: dict[str, Any], label: str, depends_on: list[str] | None) -> list[str]:
    if not depends_on:
        return []
    workers = manifest.get("workers", {})
    normalized: list[str] = []
    seen: set[str] = set()
    for dep in depends_on:
        validate_label(dep)
        if dep == label:
            raise WorkerJobsError(f"Worker '{label}' cannot depend on itself.")
        if dep not in workers:
            raise WorkerJobsError(f"Unknown dependency label: {dep}")
        if dep in seen:
            continue
        seen.add(dep)
        normalized.append(dep)
    return normalized


def command_start(args: argparse.Namespace) -> int:
    run_dir = ensure_managed_run_dir(resolve_run_dir(args.run_dir))
    run_dir.mkdir(parents=True, exist_ok=True)

    label = args.label
    validate_label(label)

    command = normalize_command(args.command)
    if not command:
        raise WorkerJobsError("No worker command supplied.")

    tool_name = infer_tool_name(command)

    with hold_manifest_lock(run_dir):
        manifest = ensure_manifest(run_dir)
        if label in manifest["workers"]:
            raise WorkerJobsError(f"Worker label already exists in manifest: {label}")
        depends_on = normalize_dependencies(manifest, label, args.depends_on)

        started_at = iso_now()
        outfile = run_dir / f"{label}-out.txt"
        errfile = run_dir / f"{label}-err.txt"
        status_file = run_dir / f"{label}-status.json"
        wrapper_cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "_runner",
            "--label",
            label,
            "--status-file",
            str(status_file),
            "--stdout",
            str(outfile),
            "--stderr",
            str(errfile),
            "--",
            *command,
        ]
        process = subprocess.Popen(
            wrapper_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

        manifest["workers"][label] = {
            "label": label,
            "tool": tool_name,
            "pid": process.pid,
            "command": command,
            "outfile": str(outfile),
            "errfile": str(errfile),
            "status_file": str(status_file),
            "started_at": started_at,
        }
        if depends_on:
            manifest["workers"][label]["depends_on"] = depends_on
        save_manifest(run_dir, manifest)
        sync_run_index(run_dir, manifest=manifest, status="active")

    if tool_name in {"claude", "codex"}:
        session_path = resolve_session_path(manifest["workers"][label], wait_seconds=5.0)
        if session_path is not None:
            with hold_manifest_lock(run_dir):
                manifest = ensure_manifest(run_dir)
                worker_entry = manifest["workers"].get(label)
                if worker_entry is not None:
                    worker_entry["session_path"] = str(session_path)
                    worker_entry["session_id"] = session_id_from_path(session_path) or session_path.stem
                    save_manifest(run_dir, manifest)

    print(
        json.dumps(
            {
                "label": label,
                "pid": process.pid,
                "outfile": str(outfile),
                "errfile": str(errfile),
                "status_file": str(status_file),
                "run_dir": str(run_dir),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def iter_selected_workers(manifest: dict[str, Any], label: str | None) -> list[dict[str, Any]]:
    workers = manifest.get("workers", {})
    if label is None:
        return [workers[key] for key in sorted(workers)]
    if label not in workers:
        raise WorkerJobsError(f"Unknown worker label: {label}")
    return [workers[label]]


def dependency_warnings(manifest: dict[str, Any], entry: dict[str, Any]) -> list[str]:
    """Return warning strings for invalid or incomplete worker dependencies."""
    deps = entry.get("depends_on") or []
    if not deps:
        return []
    ws = manifest.get("workers", {})
    warnings_list = []
    for dep in deps:
        dep_entry = ws.get(dep)
        if dep_entry is None:
            warnings_list.append(
                f"worker '{entry['label']}' depends on unknown worker '{dep}'"
            )
            continue
        dep_state = worker_status(dep_entry)["state"]
        if dep_state not in {"completed", "cancelled", "failed"}:
            warnings_list.append(
                f"worker '{entry['label']}' depends on '{dep}' which is {dep_state}"
            )
    return warnings_list


def command_status(args: argparse.Namespace) -> int:
    run_dir = resolve_run_dir(args.run_dir)
    manifest = load_manifest(run_dir)
    entries = iter_selected_workers(manifest, args.label)
    statuses = [worker_status(entry) for entry in entries]
    for entry in entries:
        for warn in dependency_warnings(manifest, entry):
            print(f"WARNING: {warn}", file=sys.stderr)
    if args.json:
        print(json.dumps(statuses, indent=2, sort_keys=True))
        return 0
    for status in statuses:
        suffix = ""
        if status["returncode"] is not None:
            suffix = f" returncode={status['returncode']}"
        print(
            f"{status['label']}: state={status['state']} pid={status['pid']} "
            f"out={status['outfile_size']}B err={status['errfile_size']}B{suffix}"
        )
    return 0


def command_activity(args: argparse.Namespace) -> int:
    run_dir = resolve_run_dir(args.run_dir)
    manifest = load_manifest(run_dir)
    entries = iter_selected_workers(manifest, args.label)
    for entry in entries:
        for warn in dependency_warnings(manifest, entry):
            print(f"WARNING: {warn}", file=sys.stderr)
    now = time.time()
    activity_rows: list[dict[str, Any]] = []

    for entry in entries:
        status = worker_status(entry)
        payload = {
            "label": status["label"],
            "state": status["state"],
            "running": status["running"],
            "tool": status["tool"],
            "outfile_size": status["outfile_size"],
            "errfile_size": status["errfile_size"],
        }

        if entry.get("tool") in {"claude", "codex"}:
            session_path = resolve_session_path(entry)
            if session_path is not None:
                session_payload = session_activity(str(entry.get("tool")), session_path)
                payload.update(session_payload)
                payload["activity_source"] = "session"
                payload["healthy"] = status["running"] and session_payload.get("session_mtime_age_s", args.max_idle + 1) <= args.max_idle
            else:
                payload["session_path"] = None
                fallback_payload = helper_activity(entry, now)
                payload.update(fallback_payload)
                if fallback_payload:
                    payload["healthy"] = status["running"] and fallback_payload.get("last_activity_age_s", args.max_idle + 1) <= args.max_idle
                else:
                    payload["healthy"] = False
        else:
            fallback_payload = helper_activity(entry, now)
            payload.update(fallback_payload)
            if fallback_payload:
                payload["healthy"] = status["running"] and payload["last_activity_age_s"] <= args.max_idle
            else:
                payload["healthy"] = False

        activity_rows.append(payload)

    if args.json:
        print(json.dumps(activity_rows, indent=2, sort_keys=True))
        return 0

    for payload in activity_rows:
        line = (
            f"{payload['label']}: state={payload['state']} healthy={'yes' if payload.get('healthy') else 'no'} "
            f"out={payload['outfile_size']}B err={payload['errfile_size']}B"
        )
        if payload.get("tool") in {"claude", "codex"} and payload.get("session_mtime_age_s") is not None:
            line += f" session_age={payload['session_mtime_age_s']}s"
        elif payload.get("last_activity_age_s") is not None:
            line += f" last_activity_age={payload['last_activity_age_s']}s"
        print(line)
        if payload.get("last_assistant_type"):
            detail = payload.get("last_assistant_detail")
            suffix = f":{detail}" if detail else ""
            print(f"  last_assistant={payload['last_assistant_type']}{suffix} at {payload['last_assistant_at']}")
        elif payload.get("last_event_type"):
            detail = payload.get("last_event_detail")
            suffix = f":{detail}" if detail else ""
            print(f"  last_event={payload['last_event_type']}{suffix} at {payload['last_event_at']}")
        elif payload.get("last_activity_at"):
            print(f"  last_activity_at={payload['last_activity_at']}")
        if payload.get("activity_source") == "helper_files" and payload.get("last_activity_path"):
            print(f"  helper_activity={payload['last_activity_path']}")
        if payload.get("session_path"):
            print(f"  session={payload['session_path']}")
    return 0


def command_wait(args: argparse.Namespace) -> int:
    run_dir = resolve_run_dir(args.run_dir)
    deadline = None if args.timeout is None else time.time() + args.timeout
    while True:
        manifest = load_manifest(run_dir)
        statuses = [worker_status(entry) for entry in iter_selected_workers(manifest, args.label)]
        if not any(status["running"] for status in statuses):
            failed = [status for status in statuses if status["returncode"] not in (None, 0)]
            if args.json:
                print(json.dumps(statuses, indent=2, sort_keys=True))
            else:
                for status in statuses:
                    print(f"{status['label']}: state={status['state']} returncode={status['returncode']}")
            return 1 if failed else 0
        if deadline is not None and time.time() >= deadline:
            if args.json:
                print(json.dumps(statuses, indent=2, sort_keys=True))
            else:
                for status in statuses:
                    print(f"{status['label']}: state={status['state']} pid={status['pid']}")
            return 1
        time.sleep(args.interval)


def command_extract(args: argparse.Namespace) -> int:
    manifest = load_manifest(resolve_run_dir(args.run_dir))
    entry = iter_selected_workers(manifest, args.label)[0]
    result = extract_best_result(entry)
    text = result["text"]
    if args.sections:
        section_names = [part.strip() for part in args.sections.split(",") if part.strip()]
        if section_names:
            text = extract_sections(text, section_names)
    if args.json:
        print(
            json.dumps(
                {
                    "label": entry["label"],
                    "source": result["source"],
                    "source_path": result["source_path"],
                    "text": text.rstrip(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    print(text.rstrip())
    return 0


def command_cancel(args: argparse.Namespace) -> int:
    run_dir = resolve_run_dir(args.run_dir)
    manifest = load_manifest(run_dir)
    entries = iter_selected_workers(manifest, args.label)

    requested = False
    for entry in entries:
        pid = int(entry.get("pid", 0))
        if pid <= 0 or not process_running(pid):
            continue
        os.kill(pid, signal.SIGTERM)
        requested = True

    if not requested:
        raise WorkerJobsError("No matching running worker wrapper found to cancel.")

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        manifest = load_manifest(run_dir)
        statuses = [worker_status(entry) for entry in iter_selected_workers(manifest, args.label)]
        if not any(status["running"] for status in statuses):
            if args.json:
                print(json.dumps(statuses, indent=2, sort_keys=True))
            else:
                for status in statuses:
                    print(f"{status['label']}: state={status['state']} returncode={status['returncode']}")
            return 0
        time.sleep(args.interval)

    statuses = [worker_status(entry) for entry in iter_selected_workers(load_manifest(run_dir), args.label)]
    if args.json:
        print(json.dumps(statuses, indent=2, sort_keys=True))
    else:
        for status in statuses:
            print(f"{status['label']}: state={status['state']} pid={status['pid']}")
    return 1


def command_runner(args: argparse.Namespace) -> int:
    command = normalize_command(args.command)
    if not command:
        raise WorkerJobsError("Runner requires a worker command.")

    status_file = Path(args.status_file)
    stdout_path = Path(args.stdout)
    stderr_path = Path(args.stderr)
    status_file.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    child: subprocess.Popen[bytes] | None = None
    cancel_requested = False

    def request_cancel(signum: int, frame: Any) -> None:
        del signum, frame
        nonlocal cancel_requested
        cancel_requested = True
        if child is None or child.poll() is not None:
            return
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            child.terminate()

    signal.signal(signal.SIGTERM, request_cancel)
    signal.signal(signal.SIGINT, request_cancel)

    with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
        child = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            close_fds=True,
            start_new_session=True,
        )
        write_json(
            status_file,
            {
                "label": args.label,
                "state": "running",
                "started_at": iso_now(),
                "child_pid": child.pid,
            },
        )
        returncode = child.wait()
    final_state = "cancelled" if cancel_requested else ("completed" if returncode == 0 else "failed")
    write_json(
        status_file,
        {
            "label": args.label,
            "state": final_state,
            "started_at": read_json(status_file).get("started_at"),
            "finished_at": iso_now(),
            "child_pid": child.pid,
            "cancel_requested": cancel_requested,
            "returncode": returncode,
        },
    )
    sync_run_index(status_file.parent)
    return returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worker_jobs.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help=(
            "Create a unique run directory under the helper-managed artifact root "
            f"(override with {ARTIFACT_ROOT_ENV})."
        ),
    )
    init_parser.add_argument("--root", help=argparse.SUPPRESS)
    init_parser.add_argument("--prefix", default="run")
    init_parser.set_defaults(func=command_init)

    start_parser = subparsers.add_parser(
        "start",
        help="Start one tracked worker. The helper owns stdout/stderr capture; do not add shell redirections unless you explicitly wrap the command in /bin/sh -lc.",
    )
    start_parser.add_argument(
        "--run-dir",
        required=True,
        help=(
            "Per-run directory created by `init`; must live under the helper-managed "
            f"artifact root (override with {ARTIFACT_ROOT_ENV})."
        ),
    )
    start_parser.add_argument(
        "--label",
        required=True,
        help="Worker label in the form <nn>-<tool>-<subtask-slug>[-rN], e.g. 01-codex-trace-login.",
    )
    start_parser.add_argument(
        "--depends-on",
        nargs="*",
        metavar="LABEL",
        help="Worker labels that must complete before this one (stored in manifest; checked by status/activity).",
    )
    start_parser.add_argument("command", nargs=argparse.REMAINDER)
    start_parser.set_defaults(func=command_start)

    status_parser = subparsers.add_parser("status", help="Show worker status.")
    status_parser.add_argument("--run-dir", required=True)
    status_parser.add_argument("--label")
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(func=command_status)

    activity_parser = subparsers.add_parser("activity", help="Show lightweight worker activity signals.")
    activity_parser.add_argument("--run-dir", required=True)
    activity_parser.add_argument("--label")
    activity_parser.add_argument("--max-idle", type=int, default=300)
    activity_parser.add_argument("--json", action="store_true")
    activity_parser.set_defaults(func=command_activity)

    wait_parser = subparsers.add_parser("wait", help="Wait for workers to finish.")
    wait_parser.add_argument("--run-dir", required=True)
    wait_parser.add_argument("--label")
    wait_parser.add_argument("--timeout", type=float)
    wait_parser.add_argument("--interval", type=float, default=2.0)
    wait_parser.add_argument("--json", action="store_true")
    wait_parser.set_defaults(func=command_wait)

    extract_parser = subparsers.add_parser("extract", help="Read the best available worker output.")
    extract_parser.add_argument("--run-dir", required=True)
    extract_parser.add_argument("--label", required=True)
    extract_parser.add_argument(
        "--sections",
        help="Comma-separated section names to extract from matching SECTION header lines. If omitted, print the whole final outfile.",
    )
    extract_parser.add_argument("--json", action="store_true", help="Print the extracted text plus its source metadata as JSON.")
    extract_parser.set_defaults(func=command_extract)

    cancel_parser = subparsers.add_parser("cancel", help="Ask tracked workers to stop and wait for status to settle.")
    cancel_parser.add_argument("--run-dir", required=True)
    cancel_parser.add_argument("--label")
    cancel_parser.add_argument("--timeout", type=float, default=10.0)
    cancel_parser.add_argument("--interval", type=float, default=0.5)
    cancel_parser.add_argument("--json", action="store_true")
    cancel_parser.set_defaults(func=command_cancel)

    runner_parser = subparsers.add_parser("_runner", help=argparse.SUPPRESS)
    runner_parser.add_argument("--label", required=True)
    runner_parser.add_argument("--status-file", required=True)
    runner_parser.add_argument("--stdout", required=True)
    runner_parser.add_argument("--stderr", required=True)
    runner_parser.add_argument("command", nargs=argparse.REMAINDER)
    runner_parser.set_defaults(func=command_runner)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except WorkerJobsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
