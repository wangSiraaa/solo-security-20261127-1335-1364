"""Declaration model: what each chassis *claims* about where its material is from.

The bench never treats a claim as a fact.  Key ideas:

* A declaration is a :class:`ChassisDeclaration` listing :class:`Feed` entries.
  Each feed maps a *literal source name* the chassis uses to a *physical pool id*
  (the noise box).  Names are decoration; the pool id is provenance.
* Two feeds are the *same source* iff they name the same ``(pool_id, epoch)`` --
  regardless of literal name, and regardless of whether the bits happen to look
  equal or different.
* ``synthetic=True`` marks a device declaration we cannot independently vouch
  for.  It may be used to route material, but never as the basis of an
  independence promise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

PoolKey = Tuple[str, int]  # (pool_id, epoch)


@dataclass(frozen=True)
class Feed:
    """One upstream claim: 'this chassis can draw from ``pool_id`` at ``epoch``'."""

    source_name: str
    pool_id: str
    epoch: int
    synthetic: bool = False
    note: str = ""


@dataclass(frozen=True)
class ChassisDeclaration:
    chassis_id: str
    feeds: Tuple[Feed, ...]


class DeclarationBook:
    """All synthetic device declarations the bench currently holds."""

    def __init__(self) -> None:
        self._chassis: Dict[str, ChassisDeclaration] = {}

    def set(self, decl: ChassisDeclaration) -> None:
        self._chassis[decl.chassis_id] = decl

    def get(self, chassis_id: str) -> Optional[ChassisDeclaration]:
        return self._chassis.get(chassis_id)

    def chassis_ids(self) -> List[str]:
        return sorted(self._chassis)

    def all_feeds(self) -> Iterable[Tuple[str, Feed]]:
        for cid in self.chassis_ids():
            for feed in self._chassis[cid].feeds:
                yield cid, feed

    def declared_source_names(self) -> List[str]:
        """Literal names in use (decoration only; never used for independence)."""
        names = set()
        for _, feed in self.all_feeds():
            names.add(feed.source_name)
        return sorted(names)

    def clone(self) -> "DeclarationBook":
        out = DeclarationBook()
        for decl in self._chassis.values():
            out.set(decl)
        return out
