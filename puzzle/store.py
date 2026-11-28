"""Two-local-directory handoff.

The brief asks for a run that fits in "two local directories": one held by the
offline referee (signed starts, receipts, midpoint attestations) and one held by
the demo station (published per-player guide tracks).  Everything is written as
canonical bytes so a downstream reader can compare *actual handed-over bytes*.

Nothing here is networked; the dirs can be a USB stick and a local folder.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .demo import DemoFile
from .referee import Continuation, Midpoint, RaceStart, Receipt


def _write(path: str, raw: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, path)


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _load(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8"))


@dataclass
class TwoDirStore:
    """Referee dir holds the trusted chain; demo dir holds player files."""

    referee_dir: str
    demo_dir: str

    # -- referee side -----------------------------------------------------
    def path_start(self, race_id: str) -> str:
        return os.path.join(self.referee_dir, "starts", f"{race_id}.json")

    def path_receipt(self, race_id: str) -> str:
        return os.path.join(self.referee_dir, "receipts", f"{race_id}.json")

    def path_midpoint(self, race_id: str) -> str:
        return os.path.join(self.referee_dir, "midpoints", f"{race_id}.json")

    def path_continuation(self, race_id: str) -> str:
        return os.path.join(self.referee_dir, "continuations", f"{race_id}.json")

    def save_start(self, start: RaceStart) -> str:
        p = self.path_start(start.race_id)
        _write(p, start.to_bytes())
        return p

    def load_start(self, race_id: str) -> RaceStart:
        return RaceStart(_load(_read(self.path_start(race_id))))

    def save_receipt(self, receipt: Receipt) -> str:
        p = self.path_receipt(receipt.race_id)
        _write(p, receipt.to_bytes())
        return p

    def load_receipt(self, race_id: str) -> Receipt:
        return Receipt(_load(_read(self.path_receipt(race_id))))

    def save_midpoint(self, midpoint: Midpoint) -> str:
        p = self.path_midpoint(midpoint.body["race_id"])
        _write(p, midpoint.to_bytes())
        return p

    def load_midpoint(self, race_id: str) -> Midpoint:
        return Midpoint(_load(_read(self.path_midpoint(race_id))))

    def save_continuation(self, cont: Continuation) -> str:
        p = self.path_continuation(cont.body["race_id"])
        _write(p, cont.to_bytes())
        return p

    # -- demo side --------------------------------------------------------
    def path_demo(self, race_id: str, player_id: str) -> str:
        return os.path.join(self.demo_dir, race_id, f"{player_id}.demo.json")

    def publish_demo(self, demo: DemoFile) -> str:
        p = self.path_demo(demo.body["race_id"], demo.body["player_id"])
        _write(p, demo.to_bytes())
        return p

    def download_demo(self, race_id: str, player_id: str) -> bytes:
        """Exactly the bytes a player pulls from the demo directory."""
        return _read(self.path_demo(race_id, player_id))
