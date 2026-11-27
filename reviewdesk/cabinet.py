from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def append_event(state_path: str | Path, *, cabinet_id: str, word: str, source: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "at": _now(),
        "cabinet_id": cabinet_id,
        "word": word,
        "source": source,
    }
    if extra:
        event["extra"] = extra
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def read_events(state_path: str | Path) -> list[dict[str, Any]]:
    path = Path(state_path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def query_word(state_path: str | Path, word: str | None = None) -> list[dict[str, Any]]:
    events = read_events(state_path)
    if word is None:
        return events
    return [event for event in events if event.get("word") == word]


def snapshot_words(state_path: str | Path) -> set[str]:
    return {str(event.get("word")) for event in read_events(state_path)}
