from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import append_jsonl, now_iso, read_jsonl


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)

    def record(self, actor: str, action: str, **fields: Any) -> dict[str, Any]:
        row = {
            "at": now_iso(),
            "actor": actor,
            "action": action,
            **fields,
        }
        append_jsonl(self.path, row)
        return row

    def entries(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path)
