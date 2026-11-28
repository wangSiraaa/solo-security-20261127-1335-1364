"""Route B -- the inner office.

The office holds the library and *usable material*: the pages it can still
honour right now.  It does not precompile branches.  When a reader turns up at
any station with a receipt, the office:

1. replays the receipt to learn which worlds remain consistent;
2. consults the round's truth (which it knows);
3. asks the shared ``Engine`` what the rules would deliver, given the material
   ledger;
4. withholds any page whose material it can no longer honour, substituting a
   truthful alternative if the rules allow, else answering ``withdrawn`` with
   reason ``no_material``.

Because the office answers from the receipt's current state, a ticket continues
across stations as long as the office still holds the material the receipt
needs.  That is the trade-off versus route A: no offline booklet, but material
must be redeemable at answer time.

The office can *revoke* material (a prop destroyed, a page recalled).  The
revocation lives in the engine's ledger, so it is part of the public round
configuration: future receipts replay exactly because the emitted step already
reflects the ledger, never a silent lie.
"""

from __future__ import annotations

from typing import Iterable

from .engine import Engine, Step
from .receipts import Receipt


class MaterialUnavailable(Exception):
    """The office cannot serve without material it no longer holds."""


class InnerOffice:
    def __init__(self, lib, *, withheld: Iterable[str] = ()):
        self.lib = lib
        self.engine = Engine(lib, material=withheld)
        self.notes: list[str] = []

    # -- material control ---------------------------------------------------
    def withhold(self, content: str, note: str = "") -> None:
        self.engine.withhold(content)
        self.notes.append(f"withheld {content[:8]}: {note or 'material recalled'}")

    def restore(self, content: str) -> None:
        self.engine.restore(content)

    def material(self) -> frozenset:
        return self.engine.material()

    # -- serving ------------------------------------------------------------
    def _truth_world(self):
        from .engine import _wkey
        target = _wkey(self.lib.truth)
        for w in self.engine.worlds():
            if _wkey(w) == target:
                return w
        raise MaterialUnavailable("truth is not a world of this library")

    def menu(self, receipt: Receipt) -> list[dict]:
        if receipt.library != self.lib.digest:
            raise MaterialUnavailable("receipt belongs to another library")
        return self.engine.menu(receipt.transcript)

    def serve(self, receipt: Receipt, wording: str) -> Step:
        """Answer from the receipt's current state, honouring held material.

        The engine already applies the material ledger inside the rules, so the
        office simply resolves the truth's step.  Any ``no_material`` reason in
        the emitted step is recorded for review.
        """
        if receipt.library != self.lib.digest:
            raise MaterialUnavailable("receipt belongs to another library")
        world = self._truth_world()
        step = self.engine.decide(world, receipt.transcript, wording)
        if step.reason in ("substituted", "no_material"):
            self.notes.append(
                f"{wording!r} -> {step.status} ({step.reason})")
        return step

    # -- introspection ------------------------------------------------------
    def usable_contents(self) -> set[str]:
        from .engine import content_of
        return {content_of(self.lib, pid) for pid in self.lib.pages} - set(
            self.engine.material())
