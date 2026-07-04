#!/usr/bin/env python3
"""Master Controller state and plan eligibility CLI."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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


class McError(Exception):
    """User-facing MC error."""


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


def run_next(args: argparse.Namespace) -> int:
    if not args.dry_run:
        raise McError("run-next execution is not implemented in this slice; use --dry-run")
    repo = resolve_repo(Path(args.repo))
    state = load_run(resolve_run_path(repo, args.run))
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
        return 0
    print("Eligibility: blocked")
    for reason in reasons:
        print(f"- {reason}")
    return 2


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
    run_next_parser.set_defaults(func=run_next)

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
