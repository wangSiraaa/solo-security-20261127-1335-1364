"""Fictional audiobook catalogue.

The book is a toy "audio drama script": "The Lamplit Ledger". Each chapter is an
ordered list of segments (spoken lines).

Segment kinds:
  PREVIEW_ONLY  at the FRONT of each chapter (index 0): a taster that may only
                be auditioned live, on the spot, while connected to the desk.
                It is NEVER provisioned/cached and never costs a listen credit.
  NORMAL        the rest: cacheable, may be paused and resumed; each first open
                spends one listen credit.

The front preview-only segment is what a "front-preview only" borrower may
sample -- and nothing more, so the whole chapter can never be lent to them.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List


class SegKind(str, Enum):
    NORMAL = "normal"
    PREVIEW_ONLY = "preview-only"


@dataclass(frozen=True)
class Segment:
    seg_id: str
    chapter_id: str
    index: int
    kind: SegKind
    text: str


@dataclass(frozen=True)
class Chapter:
    chapter_id: str
    title: str
    segments: List[Segment]

    @property
    def normal_segments(self) -> List[Segment]:
        return [s for s in self.segments if s.kind == SegKind.NORMAL]

    @property
    def preview_segments(self) -> List[Segment]:
        return [s for s in self.segments if s.kind == SegKind.PREVIEW_ONLY]


_LINES = [
    "Scene: a rain-slick tram, midnight, one lamp swinging.",
    "NARRATOR: The ledger was the only passenger who never blinked.",
    "TRAVELER: I carry a receipt, not a story. Mind the ink.",
    "CLERK: Every line you hear is already spent; none can be recalled.",
    "NARRATOR: The conductor checked the counter, not the clock.",
    "TRAVELER: Then lend the remaining listens to whoever waits.",
    "CLERK: Only once your proof is home, not while it still plays.",
    "NARRATOR: The tunnel swallowed the last unissued line whole.",
    "TRAVELER: Some pages I never claimed. Show them as missing, honestly.",
    "NARRATOR: Dawn arrived before the counter reached zero.",
    "CLERK: Return the proof; the future is all we can still withhold.",
    "NARRATOR: And the waiting borrower took what remained, in order.",
]


def _build() -> Dict[str, Chapter]:
    chapters: Dict[str, Chapter] = {}
    # 6 segments each: index 0 preview-only (front taster), indices 1..5 normal.
    for ci, (cid, title) in enumerate(
        [("ch1", "The Lamplit Ledger"), ("ch2", "The Waiting Borrower")]
    ):
        segs = []
        for idx in range(6):
            kind = SegKind.PREVIEW_ONLY if idx == 0 else SegKind.NORMAL
            li = (ci * 6 + idx) % len(_LINES)
            segs.append(Segment(f"{cid}.s{idx}", cid, idx, kind, _LINES[li]))
        chapters[cid] = Chapter(cid, title, segs)
    return chapters


CATALOG: Dict[str, Chapter] = _build()


def chapter(chapter_id: str) -> Chapter:
    return CATALOG[chapter_id]


def all_chapters() -> List[Chapter]:
    return list(CATALOG.values())
