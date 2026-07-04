from __future__ import annotations

import shlex
import shutil
import time
from pathlib import Path

from .constants import KNOWN_UNATTENDED_HARNESS_COMMANDS
from .models import McError, PlanSlice
from .process import run_command
from .runtime import slice_environment


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
