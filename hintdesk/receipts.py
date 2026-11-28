"""Receipts -- the only record the reader carries between stations.

A receipt is public, self-describing data.  It records *what the reader saw*:
each request's wording, the offered page contents, the pages actually handed
over (content digest + verbatim text), the status and the reason.  It never
names a winner and never carries a page id, so it cannot be used to look up the
truth -- only to replay what was shown.

Two routes that put the same pages in the reader's hands yield equal receipts:
wording is the reader's own question, page *content* is hashed, and every set is
sorted.  A ticket may be continued at another station purely from its receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .engine import Step
from .util import canon, digest


class ReceiptError(ValueError):
    """The receipt is malformed or its integrity check fails."""


@dataclass(frozen=True)
class Receipt:
    ticket: str
    library: str          # library digest
    puzzle_id: str
    transcript: tuple[Step, ...]
    sign: str = ""

    # -- construction -------------------------------------------------------
    @staticmethod
    def build(*, ticket: str, library: str, puzzle_id: str,
              transcript: Iterable[Step]) -> "Receipt":
        steps = tuple(transcript)
        body = {
            "ticket": ticket,
            "library": library,
            "puzzle_id": puzzle_id,
            "transcript": [s.to_json() for s in steps],
        }
        return Receipt(ticket, library, puzzle_id, steps, digest(body))

    def to_json(self) -> dict:
        return {
            "ticket": self.ticket,
            "library": self.library,
            "puzzle_id": self.puzzle_id,
            "transcript": [s.to_json() for s in self.transcript],
            "sign": self.sign,
        }

    @staticmethod
    def from_json(d: Mapping) -> "Receipt":
        steps = tuple(Step.from_json(s) for s in d["transcript"])
        body = {
            "ticket": d["ticket"],
            "library": d["library"],
            "puzzle_id": d["puzzle_id"],
            "transcript": [s.to_json() for s in steps],
        }
        sign = digest(body)
        if d.get("sign") != sign:
            raise ReceiptError("receipt integrity check failed")
        return Receipt(d["ticket"], d["library"], d["puzzle_id"], steps, sign)

    def canonical(self) -> str:
        return canon(self.to_json())

    # -- convenience --------------------------------------------------------
    def extend(self, step: Step) -> "Receipt":
        return Receipt.build(ticket=self.ticket, library=self.library,
                             puzzle_id=self.puzzle_id,
                             transcript=self.transcript + (step,))

    @property
    def stage(self) -> int:
        return sum(1 for s in self.transcript
                   if s.kind == "pickup" and s.status in ("spent", "answered"))

    def delivered_pages(self) -> list[dict]:
        """The pages the reader physically took, as they can describe them."""
        out = []
        for s in self.transcript:
            for d in s.delivered:
                out.append({"content": d.content, "text": d.text,
                            "wording": s.wording, "status": s.status})
        return out

    def step(self, wording: str) -> Step | None:
        for s in self.transcript:
            if s.wording == wording:
                return s
        return None

    def record_signature(self) -> tuple:
        """What the reader can tell apart, independent of *which* question.

        Two different question wordings that deliver the same content with the
        same offered set, status and reason produce the same record in the
        reader's hands and therefore share this signature -- so they must yield
        the same candidate set under replay.  ``wording`` is deliberately
        excluded; ``kind``/``offered``/``delivered``/``status``/``reason`` are
        included because the reader sees all of those.
        """
        return tuple(
            (s.kind, s.status, s.offered, tuple(d.content for d in s.delivered),
             s.reason)
            for s in self.transcript
        )
