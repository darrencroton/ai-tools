from __future__ import annotations

import argparse
import sys

from .commands import (
    archive_sensitive,
    init_run,
    list_profiles,
    preflight,
    reconcile,
    run_next,
    run_remaining,
    status,
    stop,
    summarize,
)
from .constants import DEFAULT_POLL_SECONDS, DEFAULT_TIMEOUT_SECONDS
from .models import McError


def add_repo_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", default=".", help="target git repository")
    parser.add_argument("--run", default="current", help="run directory, run.json path, or 'current'")


def add_harness_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--harness-command", help="override harness command for controlled local validation")
    parser.add_argument("--harness-model", help="model name/alias to compose through the MC harness profile, e.g. sonnet")
    parser.add_argument("--worker-tools", default="", help="comma-separated worker tools expected for this run, e.g. copilot")
    parser.add_argument(
        "--allow-profile-command",
        action="store_true",
        help="use MC's capability profile to compose the unattended harness command from run requirements",
    )


def add_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="maximum seconds to wait for orchestrator result")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS, help="seconds between tmux/result checks")


def add_unattended_default_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--allow-unattended-default",
        action="store_true",
        help="opt in to a known unattended-safe launch command for --harness codex/claude (disables per-action approval; MC's post-hoc gates become the safety boundary)",
    )


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
        add_repo_run_args(command)
        command.set_defaults(func=func)

    profiles_parser = subparsers.add_parser("profiles", help="list MC harness and worker capability profiles")
    profiles_parser.set_defaults(func=list_profiles)

    preflight_parser = subparsers.add_parser("preflight", help="check the next MC slice launch before running it")
    add_repo_run_args(preflight_parser)
    add_harness_args(preflight_parser)
    preflight_parser.set_defaults(func=preflight)

    run_next_parser = subparsers.add_parser("run-next", help="inspect the next slice")
    add_repo_run_args(run_next_parser)
    run_next_parser.add_argument("--dry-run", action="store_true", help="only report next-slice eligibility")
    add_runtime_args(run_next_parser)
    add_harness_args(run_next_parser)
    add_unattended_default_arg(run_next_parser)
    run_next_parser.set_defaults(func=run_next)

    run_parser = subparsers.add_parser("run", help="run eligible slices until complete or stopped")
    add_repo_run_args(run_parser)
    run_parser.add_argument("--scope", required=True, choices=["remaining"], help="run scope")
    add_runtime_args(run_parser)
    add_harness_args(run_parser)
    add_unattended_default_arg(run_parser)
    run_parser.set_defaults(func=run_remaining, dry_run=False)

    reconcile_parser = subparsers.add_parser("reconcile", help="re-check and repair a stopped slice from local evidence")
    add_repo_run_args(reconcile_parser)
    reconcile_parser.set_defaults(func=reconcile)

    stop_parser = subparsers.add_parser("stop", help="cancel the current MC run")
    add_repo_run_args(stop_parser)
    stop_parser.add_argument("--reason", default="cancelled by user", help="reason recorded in run state")
    add_harness_args(stop_parser)
    add_unattended_default_arg(stop_parser)
    stop_parser.set_defaults(func=stop)

    archive_parser = subparsers.add_parser("archive-sensitive", help="archive sensitive worker state from a run")
    add_repo_run_args(archive_parser)
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
