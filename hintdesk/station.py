"""A station serves one reader a menu and hands over pages, via either route.

A station owns only the ticket and the receipt; *policy* lives in the route.
Two route adapters present one uniform interface (``library``, ``puzzle_id``,
``menu``, ``serve``):

* ``BookletRouteAdapter`` -- route A: a precompiled booklet + this round's truth
  (the outside hint-giver cannot invent pages);
* ``OfficeRouteAdapter``  -- route B: the inner office, live, honouring only
  material it can still redeem.

A receipt is the whole cross-station state; a second station replays it through
its own route and never reads the first station's private notes.
"""

from __future__ import annotations

from typing import Protocol

from .booklet import Booklet, CannotFulfil
from .desk import InnerOffice
from .engine import Step
from .receipts import Receipt


class Route(Protocol):
    library: str
    puzzle_id: str

    def menu(self, receipt: Receipt) -> list[dict]: ...
    def serve(self, receipt: Receipt, wording: str) -> Step: ...


class BookletRouteAdapter:
    """Route A: hold a precompiled booklet and this round's truth."""

    def __init__(self, booklet: Booklet, truth, *, puzzle_id: str = ""):
        self.booklet = booklet
        self.truth = truth
        self.library = booklet.library
        self.puzzle_id = puzzle_id or booklet.puzzle_id

    def menu(self, receipt: Receipt) -> list[dict]:
        return self.booklet.menu(receipt)

    def serve(self, receipt: Receipt, wording: str) -> Step:
        return self.booklet.serve(receipt, wording, self.truth)


class OfficeRouteAdapter:
    """Route B: the inner office, which holds material and knows the truth."""

    def __init__(self, office: InnerOffice):
        self.office = office
        self.library = office.lib.digest
        self.puzzle_id = office.lib.puzzle_id

    def menu(self, receipt: Receipt) -> list[dict]:
        return self.office.menu(receipt)

    def serve(self, receipt: Receipt, wording: str) -> Step:
        return self.office.serve(receipt, wording)


class Station:
    def __init__(self, route: Route):
        self.route = route
        self.library = route.library
        self.puzzle_id = getattr(route, "puzzle_id", "")
        self.ledger: list[dict] = []   # pages this station physically handed out

    def open(self, ticket: str) -> Receipt:
        return Receipt.build(ticket=ticket, library=self.library,
                             puzzle_id=self.puzzle_id, transcript=())

    def receive(self, receipt: Receipt) -> Receipt:
        """Take custody of a receipt from another station (validates it)."""
        Receipt.from_json(receipt.to_json())        # re-check integrity
        if receipt.library != self.library:
            raise CannotFulfil(
                "wrong_library",
                f"receipt {receipt.library!r} != station {self.library!r}")
        return receipt

    def menu(self, receipt: Receipt) -> list[dict]:
        return self.route.menu(receipt)

    def ask(self, receipt: Receipt, wording: str) -> tuple[Receipt, Step | None, str]:
        """Ask a question.  Returns (new_receipt, delivered_step_or_None, reason).

        Even a refusal extends the receipt -- the reader saw the refusal, so the
        record must stay faithful.
        """
        receipt = self.receive(receipt)
        step = self.route.serve(receipt, wording)
        if step.delivered_flag:
            # keep an auditable record of the pages this station physically
            # handed over, so it can retrieve what it actually gave out
            self.ledger.append({
                "ticket": receipt.ticket,
                "wording": step.wording,
                "status": step.status,
                "delivered": [d.to_json() for d in step.delivered],
            })
        return receipt.extend(step), (step if step.delivered_flag else None), step.reason

    def handed_over(self, ticket: str | None = None) -> list[dict]:
        """Pages this station actually handed out (optionally for one ticket)."""
        return [e for e in self.ledger
                if ticket is None or e["ticket"] == ticket]

