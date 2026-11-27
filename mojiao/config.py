"""Classroom/package discovery for the standalone Mojiao CLI."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def find_root(explicit: str | Path | None = None) -> Path:
    if explicit:
        root = Path(explicit).resolve()
        if (root / "classroom.json").exists() or (root / "mojiao").exists():
            return root
        return root
    current = Path.cwd().resolve()
    for directory in (current, *current.parents):
        if (directory / "classroom.json").exists():
            return directory
    return current


def load_classroom(root: Path) -> dict[str, Any]:
    path = root / "classroom.json"
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    return {
        "classroom_id": "unconfigured-local",
        "entrypoint": "mojiao.reviewed_resolver:resolve",
        "wordlists": sorted(str(p) for p in (root / "wordlists").glob("*.json")) if (root / "wordlists").exists() else [],
        "network_policy": "deny",
    }


def resolve_wordlist_paths(root: Path, classroom: dict[str, Any]) -> list[Path]:
    explicit = classroom.get("wordlists")
    if explicit:
        return [root / p if not Path(p).is_absolute() else Path(p) for p in explicit]
    patterns = classroom.get("wordlist_globs") or ["wordlists/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(sorted(root.glob(pattern)))
    return paths
