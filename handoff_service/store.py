"""JSON state persistence with a small file lock."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from typing import Iterator

from .core import JSONDict, new_state


DEFAULT_STATE_PATH = os.environ.get("HANDOFF_STATE", os.path.join(os.getcwd(), ".handoff-state.json"))


def load_state(path: str | None = None) -> JSONDict:
    path = path or DEFAULT_STATE_PATH
    if not os.path.exists(path):
        return new_state()
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_state(state: JSONDict, path: str | None = None) -> None:
    path = path or DEFAULT_STATE_PATH
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


@contextmanager
def locked_state(path: str | None = None) -> Iterator[JSONDict]:
    path = path or DEFAULT_STATE_PATH
    lock_path = f"{path}.lock"
    directory = os.path.dirname(os.path.abspath(lock_path))
    os.makedirs(directory, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        state = load_state(path)
        try:
            yield state
        finally:
            save_state(state, path)
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
