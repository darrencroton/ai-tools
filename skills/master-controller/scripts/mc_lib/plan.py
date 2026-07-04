from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .models import PlanSlice
from .constants import COMPLETED_SLICE_STATUSES


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


def plan_slice_by_id(slices: list[PlanSlice], slice_id: str) -> PlanSlice | None:
    for plan_slice in slices:
        if plan_slice.slice_id == slice_id:
            return plan_slice
    return None
