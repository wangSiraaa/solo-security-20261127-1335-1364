"""Shared constants and JSON helpers for the offline remediation desk."""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class AdvisoryStatus(StrEnum):
    ACTIVE = "ACTIVE"
    WITHDRAWN = "WITHDRAWN"
    SUPERSEDED = "SUPERSEDED"


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    MITIGATED = "MITIGATED"
    CLOSED = "CLOSED"
    CANCELED = "CANCELED"


class ItemKind(StrEnum):
    INSTALLED_COMPONENT = "INSTALLED_COMPONENT"
    RUNNING_PROCESS = "RUNNING_PROCESS"


class ItemStatus(StrEnum):
    OPEN = "OPEN"
    CLEARED = "CLEARED"


class ClosureKind(StrEnum):
    FINAL = "FINAL"
    TEMPORARY_CONTROL = "TEMPORARY_CONTROL"
    WITHDRAWN_BULLETIN = "WITHDRAWN_BULLETIN"


class PromiseStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    AT_RISK = "AT_RISK"
    IMPOSSIBLE = "IMPOSSIBLE"
    KEPT = "KEPT"


DEFAULT_DATA: dict[str, Any] = {
    "sequence": 0,
    "current_shift": "SHIFT-0-TONIGHT",
    "advisories": [],
    "machines": [],
    "inventory_reports": [],
    "repair_receipts": [],
    "disablement_receipts": [],
    "staffing_reports": [],
    "resource_events": [],
    "tickets": [],
    "closures": [],
    "plans": [],
}


def load_desk(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_DATA))
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    # Forward-compatible defaults for older paper files.
    for key, value in DEFAULT_DATA.items():
        data.setdefault(key, json.loads(json.dumps(value)))
    return data


def save_desk(data: dict[str, Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(path)


def next_id(data: dict[str, Any], prefix: str) -> str:
    data["sequence"] = int(data.get("sequence", 0)) + 1
    return f"{prefix}-{data['sequence']:04d}"
