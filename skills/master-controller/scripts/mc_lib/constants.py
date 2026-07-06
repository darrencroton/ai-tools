from __future__ import annotations

import re
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
RUN_ACTIVE_STATUSES = {"initialized", "running", "paused", "resuming", "partial"}
RUN_STOP_STATUSES = {"needs-human", "blocked", "failed", "cancelled"}
DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_POLL_SECONDS = 2.0
OPERATIONAL_EVENTS_FILENAME = "operational-events.jsonl"
DEFAULT_SUPERVISION: dict[str, Any] = {
    "mode": "deterministic-batch",
    "pause_policy": {
        "rolling_usage_limit": "wait-until-reset-plus-buffer",
        "weekly_usage_limit": "stop-for-user",
        "transient_service_unavailable": "bounded-retry",
        "unknown_operational_event": "stop-for-user",
    },
    "default_resume_prompt": "You were interrupted. Review what you were doing then continue.",
    "default_reset_buffer_seconds": 180,
    "max_single_pause_seconds": 21600,
    "max_consecutive_pauses_per_slice": 2,
    "max_cumulative_pause_seconds_per_run": 43200,
    "max_transient_retries_per_slice": 3,
    "pause_counters": {
        "consecutive_pauses_current_slice": 0,
        "cumulative_pause_seconds_run": 0,
    },
}

# User-approved exception (explicit choice via AskUserQuestion, this session):
# the operator may opt in to these two known unattended-safe launch commands
# with --allow-unattended-default. Without that flag, or for any other
# harness name, MC still fails closed and requires --harness-command. Bare
# harness names otherwise resolve to an interactive session (see
# TmuxHarnessAdapter): tmux pastes the prompt and presses enter as if a human
# were typing, so an unflagged `codex`/`claude` process would still prompt
# for per-action approval that nothing in this loop can answer, silently
# deadlocking the run until --timeout-seconds expires.
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
            "When used as a worker (not orchestrator), uses the operator's normal Claude Code auth/config unless "
            "standard Claude auth environment variables are provided; copying .credentials.json into an isolated "
            "CLAUDE_CONFIG_DIR is not portable.",
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

SENSITIVE_ARTIFACT_NAMES = {"copilot-home", "codex-home", "claude-config-dir", "tool-homes"}

# Worker-tool home directories are not interchangeable. Copilot's real GitHub
# credential lives outside ~/.copilot (gh CLI config / OS keychain), so
# redirecting COPILOT_HOME to an isolated per-slice directory only needs a
# writable dir. Codex's auth.json is portable enough for the local MC worker
# profile and can be copied into per-slice CODEX_HOME. Claude Code's subscription
# OAuth state is not safely reproduced by copying ~/.claude/.credentials.json
# into an isolated CLAUDE_CONFIG_DIR; use the operator's normal Claude config or
# standard Claude auth environment variables instead.
WORKER_CREDENTIAL_HOMES: dict[str, tuple[str, str, str]] = {
    "codex": ("CODEX_HOME", ".codex", "auth.json"),
}
