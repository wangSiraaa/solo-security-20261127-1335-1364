"""Local dictionary loading and indexing.

The module intentionally knows nothing about the network.  Egress behavior is
owned by the caller/compatibility layer so review records can identify the
actually-invoked boundary code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def normalize_text(value: str | None) -> str:
    return "".join((value or "").split())


@dataclass(frozen=True)
class Entry:
    id: str
    query: str
    canonical: str
    legacy_id: str | None = None
    note: str | None = None
    raw: dict[str, Any] | None = None


class DictionarySet:
    """One or more independently versioned local word lists."""

    def __init__(self, dictionaries: list["Dictionary"]):
        self.dictionaries = dictionaries
        self.by_id: dict[str, Entry] = {}
        self.by_legacy_id: dict[str, Entry] = {}
        self.by_query: dict[str, Entry] = {}
        for dictionary in dictionaries:
            for entry in dictionary.entries:
                self.by_id[entry.id] = entry
                if entry.legacy_id:
                    self.by_legacy_id[str(entry.legacy_id)] = entry
                self.by_query[normalize_text(entry.query)] = entry

    @classmethod
    def load_paths(cls, paths: list[str | Path]) -> "DictionarySet":
        return cls([Dictionary.load(path) for path in paths])

    def versions(self) -> list[dict[str, str]]:
        return [
            {"list_id": d.list_id, "version": d.version, "format": d.format, "path": str(d.path)}
            for d in self.dictionaries
        ]


class Dictionary:
    def __init__(self, path: Path, payload: dict[str, Any]):
        self.path = Path(path)
        self.payload = payload
        self.format = payload.get("format", "unknown")
        self.list_id = payload.get("list_id", self.path.stem)
        self.version = str(payload.get("version", "0"))
        self.entries = self._entries(payload)

    @classmethod
    def load(cls, path: str | Path) -> "Dictionary":
        path = Path(path)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls(path, payload)

    @staticmethod
    def _entries(payload: dict[str, Any]) -> list[Entry]:
        if payload.get("format") == "mojiao-legacy-wordtable/1":
            return [
                Entry(
                    id=f"LEGACY-{word_id}",
                    legacy_id=str(word_id),
                    query=str(row.get("query", "")),
                    canonical=str(row.get("output", row.get("canonical", ""))),
                    note=row.get("card"),
                    raw=row,
                )
                for word_id, row in sorted(payload.get("words", {}).items())
            ]
        return [
            Entry(
                id=str(row["id"]),
                legacy_id=(None if row.get("legacy_id") is None else str(row.get("legacy_id"))),
                query=str(row.get("query", "")),
                canonical=str(row.get("canonical", row.get("output", ""))),
                note=row.get("note"),
                raw=row,
            )
            for row in payload.get("entries", [])
        ]
