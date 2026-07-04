from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .constants import REQUIRED_SECTIONS


class McError(Exception):
    """User-facing MC error."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _bullet_values(text: str) -> list[str]:
    values: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if value and value.lower() != "none." and value.lower() != "none":
                values.append(value)
    return values


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
