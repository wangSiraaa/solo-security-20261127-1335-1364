"""The only channel between a hall and the membership desk.

Halls cannot import the desk or the other hall directly; they get a
``ControlChannel`` (a capability) that forwards queries and notifications.  The
channel also models the *link*: while a hall is offline the channel raises
``Rejected("OFFLINE")`` on any query and queues desk->hall pushes for delivery
when the link returns.  This is what makes "断线时刻" observable and forces the
hall to fall back to the authority it was granted *before* the outage.
"""

from __future__ import annotations

from typing import Dict, List

from .model import Rejected


class ControlChannel:
    def __init__(self, desk):
        self._desk = desk

    # hall -> desk ------------------------------------------------------
    def query(self, hall: str, question: str, **kw) -> Dict:
        h = self._desk._hall(hall)
        if not h.online:
            raise Rejected("OFFLINE", f"{hall} has no link")
        return self._desk._answer(hall, question, **kw)

    def notify(self, hall: str, message: str, **kw) -> None:
        h = self._desk._hall(hall)
        # Receipt reports travel with the hall's local post, so they are allowed
        # to *reach* the desk only when the link is up; offline the hall simply
        # keeps them locally and ``sync`` replays them later.
        if not h.online:
            raise Rejected("OFFLINE", f"{hall} cannot notify while offline")
        self._desk._receive(hall, message, **kw)

    # desk -> hall ------------------------------------------------------
    def push(self, hall: str, message: str, **kw) -> None:
        h = self._desk._hall(hall)
        if h.online:
            self._deliver(h, message, **kw)
        else:
            h._pending_pushes.append((message, kw))

    def flush(self, hall: str) -> int:
        h = self._desk._hall(hall)
        n = 0
        queued: List = h._pending_pushes
        h._pending_pushes = []
        for message, kw in queued:
            self._deliver(h, message, **kw)
            n += 1
        return n

    @staticmethod
    def _deliver(h, message: str, **kw) -> None:
        if message == "grant":
            h.apply_grant(kw["token_id"], kw.get("kind"),
                          kw.get("final_hall"))
        elif message == "revoke":
            h.apply_revoke(kw["token_id"])
        else:  # pragma: no cover - defensive
            raise Rejected("BAD_PUSH", message)


__all__ = ["ControlChannel"]
