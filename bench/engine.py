"""Issuance engine: the two accepted methods and the serving state.

Provenance model
================
A physical reserve is keyed ``(pool_id, epoch)`` -- the noise box and declared
time period.  Two declarations that name the same key are the SAME source; two
different keys are different sources.  Literal bytes are *never* compared to
decide provenance, and a shared source is counted only once no matter how many
chassis name it.

METHOD A -- partitioned stock (primary contract)
-----------------------------------------------
An independence-guaranteed batch is treated as sub-warehouses: it may be drawn
from repeatedly, and the *cut-off leftover fragment is kept*.  Disjoint
fragments of one uniform batch are independent of each other, so a pool of ``P``
live bytes can back ``floor(P / L)`` orders of ``L`` bytes.  The key rule from
the assignment still holds: a fragment already taken is spent and can never
again be the basis of a *separately independent* order; only untouched,
disjoint bytes can.

Capacity is therefore:

    independent_bytes = sum of live bytes over accepted, unpaused pools
                        (each pool counted once)
    max_orders(L)     = sum over those pools of floor(live / L)

A shared pool (two chassis, same noise box) is deduplicated by pool key, so the
collapse is automatic: literally-different source *names* do not create a
second pool.

METHOD B -- combiner (also accepted)
------------------------------------
Per ``bench/combiner.py``: XOR several equal-length paths.  The output carries
an independence guarantee only if one path (the *head*) is genuinely
independent-uniform of the rest.  The head must be a fresh, accepted, unpaused
pool with at least ``L`` live bytes, distinct from every other path's pool.
Dependent paths only add volume; they never supply the guarantee.

Device pause
------------
A paused pool contributes to no capacity until re-verified.  Already-issued
receipts stay valid.  The charter states how much is promised to the next
customer while a device is paused (answer: only fully-verified material, i.e.
the paused pool's capacity is subtracted).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .combiner import CombinablePath, CombinerContract
from .content import DeclarationBook
from .ledger import Draw, IndependenceLedger
from .reserve import ReserveBook

PoolKey = Tuple[str, int]  # (pool_id, epoch)


class IssuanceError(Exception):
    pass


@dataclass(frozen=True)
class PoolStatus:
    key: PoolKey
    live: int
    accepted: bool
    paused: bool

    @property
    def independent(self) -> bool:
        return self.accepted and not self.paused and self.live > 0

    @property
    def usable_as_dependent(self) -> bool:
        return not self.paused and self.live > 0


@dataclass
class Issued:
    order_id: str
    length_bytes: int
    material_hex: str
    method: str
    basis: Tuple[PoolKey, ...]
    draws: Tuple[Draw, ...]
    receipt: str
    remaining: Dict[str, int]


@dataclass
class OrderRecord:
    order_id: str
    length_bytes: int
    method: str
    issued: Optional[Issued]
    error: Optional[str] = None


class IssuanceEngine:
    def __init__(
        self,
        reserves: ReserveBook,
        declarations: DeclarationBook,
        method: str = "partition",
    ):
        if method not in ("partition", "combiner"):
            raise ValueError("method must be 'partition' or 'combiner'")
        self.reserves = reserves
        self.declarations = declarations
        self.method = method
        self.ledger = IndependenceLedger(reserves)
        self.paused: set[PoolKey] = set()
        self.records: Dict[str, OrderRecord] = {}
        self._seq = 0

    # ==================================================================
    # Pool classification
    # ==================================================================

    def accepted_pools(self) -> set[PoolKey]:
        """Pools named by at least one non-synthetic feed (provenance accepted)."""
        out: set[PoolKey] = set()
        for _, feed in self.declarations.all_feeds():
            if not feed.synthetic:
                out.add((feed.pool_id, feed.epoch))
        return out

    def declared_pools(self) -> Dict[PoolKey, List[str]]:
        """pool -> deduped chassis ids naming it (any feed)."""
        out: Dict[PoolKey, List[str]] = {}
        for cid, feed in self.declarations.all_feeds():
            key = (feed.pool_id, feed.epoch)
            out.setdefault(key, [])
            if cid not in out[key]:
                out[key].append(cid)
        return out

    def status(self, key: PoolKey) -> Optional[PoolStatus]:
        r = self.reserves.get(key)
        if r is None:
            return None
        return PoolStatus(
            key=key,
            live=r.live(),
            accepted=key in self.accepted_pools(),
            paused=key in self.paused,
        )

    def all_status(self) -> List[PoolStatus]:
        return [s for s in (self.status(k) for k in self.reserves.keys()) if s]

    def independent_pools(self, length_bytes: int = 0) -> List[PoolKey]:
        """Accepted, unpaused pools with at least ``length_bytes`` live."""
        out = []
        for key in self.reserves.keys():
            st = self.status(key)
            if st is None or not st.independent:
                continue
            if st.live >= length_bytes:
                out.append(key)
        return sorted(out)

    # ==================================================================
    # Balance
    # ==================================================================

    def independent_bytes(self) -> int:
        """Live bytes over accepted, unpaused pools; each distinct pool once."""
        return sum(self.reserves.get(k).live() for k in self.independent_pools())

    def paused_bytes(self) -> int:
        total = 0
        for key in self.reserves.keys():
            if key in self.paused:
                r = self.reserves.get(key)
                if r is not None:
                    total += r.live()
        return total

    def dependent_bytes(self) -> int:
        """Live bytes that can only ever be dependent input (not accepted)."""
        acc = self.accepted_pools()
        total = 0
        for key in self.reserves.keys():
            if key in acc or key in self.paused:
                continue
            total += self.reserves.get(key).live()
        return total

    def drawn_bytes_total(self) -> int:
        """Total bytes ever taken from all reserves (spend watermark)."""
        total = 0
        for key in self.reserves.keys():
            r = self.reserves.get(key)
            if r is not None:
                total += r.total_bytes - r.live()
        return total

    def max_independent_orders(self, length_bytes: int) -> int:
        """Sum over accepted/unpaused pools of floor(live / L), per pool once."""
        if length_bytes <= 0:
            return 0
        return sum(
            self.reserves.get(k).live() // length_bytes
            for k in self.independent_pools()
        )

    def can_issue(self, length_bytes: int) -> Tuple[bool, str]:
        if length_bytes <= 0:
            return False, "length must be positive"
        if self.method == "partition":
            if self.independent_pools(length_bytes):
                return True, "ok"
            return False, "no accepted, unpaused source can cover the length"
        # combiner
        heads = self.independent_pools(length_bytes)
        if not heads:
            return False, "no independent head can cover the length"
        for h in heads:
            if self._dependent_paths(length_bytes, exclude={h}):
                return True, "ok"
        return False, "no dependent equal-length path to combine with a head"

    def balance(self, length_bytes: int) -> Dict[str, int]:
        return {
            "independent_bytes": self.independent_bytes(),
            "dependent_bytes": self.dependent_bytes(),
            "paused_bytes": self.paused_bytes(),
            "max_independent_orders": self.max_independent_orders(length_bytes),
        }

    # ==================================================================
    # Issuance
    # ==================================================================

    def _next_order_id(self) -> str:
        self._seq += 1
        return f"ORD-{self._seq:04d}"

    def _pick_head(self, length_bytes: int, exclude: Sequence[PoolKey] = ()) -> PoolKey:
        candidates = [k for k in self.independent_pools(length_bytes) if k not in exclude]
        if not candidates:
            raise IssuanceError(
                "no accepted, unpaused source can cover the requested length"
            )
        # Prefer the pool with the most live bytes (reduces fragmentation);
        # deterministic tie-break by pool key.
        candidates.sort(key=lambda k: (-self.reserves.get(k).live(), k))
        return candidates[0]

    def issue(self, length_bytes: int, order_id: Optional[str] = None) -> Issued:
        if length_bytes <= 0:
            raise IssuanceError("length must be positive")
        oid = order_id or self._next_order_id()

        prev = self.records.get(oid)
        if prev is not None:
            if prev.length_bytes != length_bytes:
                raise IssuanceError(
                    f"order {oid} exists with length {prev.length_bytes}, "
                    f"not {length_bytes}"
                )
            if prev.issued is not None:
                # Idempotent replay: same receipt/material, no re-spend, and the
                # balance is recomputed live so a resend reports the truth.
                return self.refetch(oid)
            raise IssuanceError(f"order {oid} previously failed: {prev.error}")

        try:
            if self.method == "partition":
                issued = self._issue_partition(oid, length_bytes)
            else:
                issued = self._issue_combiner(oid, length_bytes)
            self.records[oid] = OrderRecord(
                order_id=oid,
                length_bytes=length_bytes,
                method=self.method,
                issued=issued,
            )
            return issued
        except IssuanceError as exc:
            self.records[oid] = OrderRecord(
                order_id=oid,
                length_bytes=length_bytes,
                method=self.method,
                issued=None,
                error=str(exc),
            )
            raise

    def _take(self, key: PoolKey, order_id: str, role: str, length_bytes: int) -> Tuple[Draw, bytes]:
        r = self.reserves.get(key)
        if r is None:
            raise IssuanceError(f"unknown reserve {key}")
        offset, material = r.take(length_bytes)
        draw = Draw(key=key, offset=offset, nbytes=length_bytes, role=role)
        self.ledger.mark(draw, order_id)
        return draw, material

    def _issue_partition(self, oid: str, length_bytes: int) -> Issued:
        head = self._pick_head(length_bytes)
        draw, material = self._take(head, oid, "independent", length_bytes)
        return self._finish(oid, length_bytes, "partition", material, (head,), (draw,))

    def _issue_combiner(self, oid: str, length_bytes: int) -> Issued:
        head = self._pick_head(length_bytes)
        others = self._dependent_paths(length_bytes, exclude={head})
        if not others:
            raise IssuanceError(
                "combiner needs at least one more equal-length path beside head"
            )
        draws: List[Draw] = []
        paths: List[CombinablePath] = []
        hd, hm = self._take(head, oid, "head", length_bytes)
        draws.append(hd)
        paths.append((True, hm))
        for key in others:
            d, mat = self._take(key, oid, "dependent", length_bytes)
            draws.append(d)
            paths.append((False, mat))
        material = CombinerContract.combine(paths)
        return self._finish(
            oid, length_bytes, "combiner", material, (head, *others), tuple(draws)
        )

    def _dependent_paths(self, length_bytes: int, exclude: set[PoolKey]) -> List[PoolKey]:
        """Up to two other equal-length paths.

        Prefer volume that is *already* only usable as dependent input (not
        accepted, or previously drawn from) so we do not squander a fresh
        independent pool as a mere dependent path.
        """
        accepted = self.accepted_pools()
        dependent_first: List[PoolKey] = []
        other: List[PoolKey] = []
        for key in sorted(self.reserves.keys()):
            if key in exclude or key in self.paused:
                continue
            r = self.reserves.get(key)
            if r is None or r.live() < length_bytes:
                continue
            (other if key in accepted else dependent_first).append(key)
        return (dependent_first + other)[:2]

    def _finish(
        self,
        oid: str,
        length_bytes: int,
        method: str,
        material: bytes,
        basis: Tuple[PoolKey, ...],
        draws: Tuple[Draw, ...],
    ) -> Issued:
        receipt = self._receipt(oid, length_bytes, method, basis, material)
        return Issued(
            order_id=oid,
            length_bytes=length_bytes,
            material_hex=material.hex(),
            method=method,
            basis=basis,
            draws=draws,
            receipt=receipt,
            remaining=self.balance(length_bytes),
        )

    # ==================================================================
    # Device control & declarations
    # ==================================================================

    def pause_pool(self, key: PoolKey) -> None:
        self.paused.add(key)

    def resume_pool(self, key: PoolKey) -> None:
        self.paused.discard(key)

    def replace_declarations(self, declarations: DeclarationBook) -> None:
        self.declarations = declarations

    # ==================================================================
    # Receipts / re-fetch
    # ==================================================================

    def _receipt(
        self,
        order_id: str,
        length_bytes: int,
        method: str,
        basis: Tuple[PoolKey, ...],
        material: bytes,
    ) -> str:
        basis_txt = ",".join(f"{p}:{e}" for p, e in basis)
        h = hashlib.sha256(
            f"{order_id}|{length_bytes}|{method}|{basis_txt}|".encode() + material
        ).hexdigest()
        return f"RC-{order_id}-{h[:16]}"

    def refetch(self, order_id: str, at_length: Optional[int] = None) -> Issued:
        """Recover an order's receipt + material + the TRUE live balance.

        A lost reply is recovered purely by order id; nothing is re-spent.
        ``at_length`` selects the length at which the *remaining* balance is
        reported (default: the order's own length).  The receipt and material
        are always the order's originals.
        """
        rec = self.records.get(order_id)
        if rec is None:
            raise IssuanceError(f"unknown order {order_id}")
        if rec.issued is None:
            raise IssuanceError(f"order {order_id} has no issued material")
        issued = rec.issued
        return Issued(
            order_id=issued.order_id,
            length_bytes=issued.length_bytes,
            material_hex=issued.material_hex,
            method=issued.method,
            basis=issued.basis,
            draws=issued.draws,
            receipt=issued.receipt,
            remaining=self.balance(at_length or rec.length_bytes),  # live
        )
