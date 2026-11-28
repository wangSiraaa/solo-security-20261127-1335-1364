"""Two independent classroom client programs sharing a memory board.

Each classroom gets:

* a set of **shared read-only pages** (same content for both), and
* its own **private correction page** that only that classroom may rewrite.

Isolation is enforced here, not merely documented: a classroom cannot write a
shared page, and cannot see or touch the other classroom's private page.  When
a classroom rewrites its private page it keeps every previous revision as
reference material, which is what the exercise asks a student to compare.

The journal records every operation with a logical tick, so we can answer
"at which moment was an answer confirmed?" precisely.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import Geometry
from .sim import BoardSim, PageData


class ClientIsolationError(Exception):
    pass


@dataclass(frozen=True)
class JournalEntry:
    tick: int
    kind: str  # "read" | "write"
    page: int
    revision: int | None
    value: int | None
    ok: bool
    receipt_id: str


@dataclass
class SharedClock:
    """A single logical clock both classroom programs stamp their journal with."""

    tick: int = 0

    def advance(self) -> int:
        self.tick += 1
        return self.tick


@dataclass
class Client:
    name: str
    board: BoardSim
    shared_pages: tuple[int, ...]
    private_page: int
    clock: SharedClock = field(default_factory=SharedClock)
    # every revision written to the private page, newest last
    private_history: list[tuple[int, int]] = field(default_factory=list)
    journal: list[JournalEntry] = field(default_factory=list)

    # -- isolation guards ----------------------------------------------

    def _guard_write(self, page: int) -> None:
        if page in self.shared_pages:
            raise ClientIsolationError(
                f"{self.name}: shared page {page} is read-only for classrooms"
            )
        if page != self.private_page:
            raise ClientIsolationError(
                f"{self.name}: page {page} is not this classroom's private page"
                f" ({self.private_page})"
            )

    def _guard_read(self, page: int) -> None:
        if page in self.shared_pages or page == self.private_page:
            return
        raise ClientIsolationError(
            f"{self.name}: page {page} is outside this classroom's assignment"
        )

    # -- operations -----------------------------------------------------

    def _next_tick(self) -> int:
        return self.clock.advance()

    def write_private(self, value: int, receipt_id: str | None = None) -> JournalEntry:
        self._guard_write(self.private_page)
        prev = self.private_history[-1] if self.private_history else None
        revision = (prev[0] + 1) if prev else 1
        rid = receipt_id or f"{self.name}-w{revision}"
        receipt = self.board.write(self.private_page, revision, value, rid)
        if receipt.ok:
            self.private_history.append((revision, value))
        entry = JournalEntry(
            tick=self._next_tick(),
            kind="write",
            page=self.private_page,
            revision=revision if receipt.ok else None,
            value=value if receipt.ok else None,
            ok=receipt.ok,
            receipt_id=rid,
        )
        self.journal.append(entry)
        return entry

    def read_page(self, page: int, receipt_id: str | None = None) -> JournalEntry:
        self._guard_read(page)
        rid = receipt_id or f"{self.name}-r{self.clock.tick + 1}-pg{page}"
        data, receipt = self.board.read(page, rid)
        entry = JournalEntry(
            tick=self._next_tick(),
            kind="read",
            page=page,
            revision=None if data is None else data[0],
            value=None if data is None else data[1],
            ok=receipt.ok and data is not None,
            receipt_id=rid,
        )
        self.journal.append(entry)
        return entry

    def read_volume(self, prefix: str = "run") -> dict[int, PageData | None]:
        out: dict[int, PageData | None] = {}
        for page in (*self.shared_pages, self.private_page):
            entry = self.read_page(page, receipt_id=f"{prefix}-{self.name}-pg{page}")
            out[page] = None if entry.value is None else (entry.revision, entry.value)
        return out

    # -- comparisons / confirmations -----------------------------------

    def latest_private(self) -> tuple[int, int] | None:
        return self.private_history[-1] if self.private_history else None

    def previous_private(self) -> tuple[int, int] | None:
        return self.private_history[-2] if len(self.private_history) >= 2 else None

    def revision_view(self, revision: int) -> int | None:
        """The reference value kept for a past revision of the private page."""

        for rev, val in self.private_history:
            if rev == revision:
                return val
        return None

    def confirmation_tick(self, write: JournalEntry) -> int | None:
        """First later read that confirms the write's revision+value.

        Returns the tick at which the answer was confirmed, or ``None`` if it
        never was.
        """

        if not write.ok:
            return None
        for entry in self.journal:
            if entry.tick <= write.tick:
                continue
            if (
                entry.kind == "read"
                and entry.page == write.page
                and entry.ok
                and entry.revision == write.revision
                and entry.value == write.value
            ):
                return entry.tick
        return None

    def confirmations(self) -> list[tuple[JournalEntry, int | None]]:
        writes = [e for e in self.journal if e.kind == "write"]
        return [(w, self.confirmation_tick(w)) for w in writes]


def build_classroom_pair(
    board: BoardSim,
    shared_pages: tuple[int, ...],
    page_a: int,
    page_b: int,
) -> tuple[Client, Client]:
    return (
        Client(name="class-A", board=board, shared_pages=shared_pages, private_page=page_a),
        Client(name="class-B", board=board, shared_pages=shared_pages, private_page=page_b),
    )
