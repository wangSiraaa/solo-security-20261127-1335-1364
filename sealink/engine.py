"""Hand-off proxy and port control for the sealink exercise.

The proxy sits between stations and carriers and enforces the rules:

  * ordinary carriers only accept sealed bags; only the designated island
    station may unseal a sealed bag,
  * a carrier's local copy is released only after a signed *returned* receipt
    ("实交后带回签收再释放本地副本"),
  * a voyage's byte cap and rendezvous windows are port-controlled, and the port
    may withdraw an agreed contact ("港口随后撤去一笔约定接触"),
  * a voyage may legally miss a window ("允许航次临时错过"),
  * a copy of the same bag reproduces its fingerprint, so it cannot double the
    promise ("复印同一袋不得产生两份可承诺的恢复能力"),
  * receipt bookkeeping is hash-chained and keyed per station, so the carrier
    "只看实际交换回单并能重开目录续运".

Custody follows *actual shipment* ("保管责任 ... 跟实际装运改变").
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from typing import Optional

from .guarantee import Guarantee, IslandStore
from .model import (
    PUBLIC,
    SEALED,
    Bag,
    CustodyChain,
    Manifest,
    PortOrder,
    Receipt,
    TrackRange,
)


# --------------------------------------------------------------------------- #
# Hash-chained, keyed ledger
# --------------------------------------------------------------------------- #


class Ledger:
    def __init__(self, path: Optional[str] = None, secret: bytes = b"deadbeef"):
        self.path = path
        self.secret = secret
        self.events: list[dict] = []
        self._prev = "0" * 64
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        self.events.append(json.loads(line))
                if self.events:
                    self._prev = self.events[-1]["hash"]

    def append(self, kind: str, **fields) -> dict:
        body = {"kind": kind, "prev": self._prev, **fields}
        h = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        ev = {**body, "hash": h}
        self.events.append(ev)
        self._prev = h
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, sort_keys=True) + "\n")
        return ev

    def verify_chain(self) -> bool:
        prev = "0" * 64
        for ev in self.events:
            if ev.get("prev") != prev:
                return False
            body = {k: v for k, v in ev.items() if k != "hash"}
            if body.get("prev") != prev:
                return False
            h = hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if h != ev.get("hash"):
                return False
            prev = h
        return True


# --------------------------------------------------------------------------- #
# Voyage
# --------------------------------------------------------------------------- #


@dataclass
class Voyage:
    name: str
    carrier: str
    trusted: bool          # a trusted courier may carry/open public bags
    cap_bytes: int
    windows: list[tuple[str, int]] = field(default_factory=list)  # (station, cycle)


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


class HandoffEngine:
    def __init__(
        self,
        manifest: Manifest,
        islands: dict[str, IslandStore],
        port: Optional[PortOrder] = None,
        ledger: Optional[Ledger] = None,
    ):
        self.manifest = manifest
        self.islands = islands
        self.port = port or PortOrder()
        self.ledger = ledger or Ledger()
        # voyage name -> list[(loader_station, bag)]
        self.loads: dict[str, list[tuple[str, Bag]]] = {}
        self.local_ids: dict[str, set[str]] = {}   # carrier -> ids held locally
        self.released: dict[str, bool] = {}
        self.receipts: list[Receipt] = []
        self._rct = 0

    # ---- byte accounting -------------------------------------------------- #

    def bag_bytes(self, bag: Bag) -> int:
        by_id = self.manifest.by_id()
        return sum(by_id[sid].length for sid in bag.segment_ids if sid in by_id)

    def voyage_loaded_bytes(self, voyage: str) -> int:
        return sum(self.bag_bytes(b) for _, b in self.loads.get(voyage, []))

    # ---- receipts --------------------------------------------------------- #

    def _receipt_mac(self, r: Receipt) -> str:
        body = json.dumps(
            {
                "receipt_id": r.receipt_id, "voyage": r.voyage,
                "carrier": r.carrier, "station": r.station, "cycle": r.cycle,
                "direction": r.direction, "bag_fingerprint": r.bag_fingerprint,
                "segment_root": r.segment_root, "n_segments": r.n_segments,
            },
            sort_keys=True, separators=(",", ":"),
        ).encode()
        key = self.ledger.secret + b"|station|" + r.station.encode()
        return hmac.new(key, body, hashlib.sha256).hexdigest()

    def verify_receipt(self, r: Receipt) -> bool:
        return hmac.compare_digest(r.mac, self._receipt_mac(r))

    def _mint_receipt(self, voyage, carrier, station, cycle, direction, bag, n) -> Receipt:
        self._rct += 1
        rid = f"R{self._rct:04d}"
        tmp = Receipt(rid, voyage, carrier, station, cycle, direction,
                      bag.fingerprint(), bag.root, n, "")
        r = Receipt(rid, voyage, carrier, station, cycle, direction,
                    bag.fingerprint(), bag.root, n, self._receipt_mac(tmp))
        self.receipts.append(r)
        self.ledger.append("receipt", receipt=r.as_dict())
        return r

    # ---- loading (station -> carrier) ------------------------------------- #

    def load(self, voyage: Voyage, station: str, bag: Bag) -> bool:
        if not voyage.trusted and bag.klass != SEALED:
            self.ledger.append("load_refused", voyage=voyage.name,
                               carrier=voyage.carrier, bag=bag.bag_id,
                               reason="ordinary_carrier_requires_sealed")
            return False
        cost = self.bag_bytes(bag)
        if self.voyage_loaded_bytes(voyage.name) + cost > voyage.cap_bytes:
            self.ledger.append("load_refused", voyage=voyage.name,
                               carrier=voyage.carrier, bag=bag.bag_id,
                               reason="byte_cap_exceeded", cost=cost,
                               cap=voyage.cap_bytes)
            return False
        self.loads.setdefault(voyage.name, []).append((station, bag))
        self.local_ids.setdefault(voyage.carrier, set()).update(bag.segment_ids)
        self.released[voyage.carrier] = False
        self.ledger.append("load", voyage=voyage.name, carrier=voyage.carrier,
                           station=station, bag=bag.bag_id, klass=bag.klass,
                           root=bag.root, fingerprint=bag.fingerprint(),
                           n=len(bag.segment_ids), cost=cost)
        return True

    # ---- meeting ---------------------------------------------------------- #

    def meet(self, voyage: Voyage, station: str, cycle: int, rng: TrackRange) -> dict:
        """One rendezvous. Honours port contact control and allowed misses."""
        if not self.port.contact_allowed(voyage.name, station, cycle):
            self.ledger.append("meet_skipped", voyage=voyage.name,
                               station=station, cycle=cycle,
                               reason="port_dropped_contact")
            return {"contact": False, "delivered": [], "guarantee": None,
                    "receipt": None, "reason": "port_dropped_contact"}

        delivered: dict[str, Bag] = {}
        for _, bag in self.loads.get(voyage.name, []):
            if bag.can_open_by(station, voyage.trusted):
                delivered[bag.bag_id] = bag

        if not delivered:
            self.ledger.append("meet_no_usable", voyage=voyage.name,
                               station=station, cycle=cycle)
            return {"contact": True, "delivered": [], "guarantee": None,
                    "receipt": None, "reason": "no_usable_bag_for_station"}

        ids: set[str] = set()
        for bag in delivered.values():
            ids |= set(bag.segment_ids)
        pieces = {sid: self.manifest.by_id()[sid].data for sid in ids}

        island = self.islands[station]
        before = set(island._source_positions())
        guarantee = island.merge(rng, pieces)
        after = set(island._source_positions())
        # "签收了一袋也不等于岛站得到了有用的新片": a delivered bag only counts as
        # a useful new piece if it added coverage the island did not already have.
        new_positions = after - before
        by_id = self.manifest.by_id()
        new_pieces = sorted(
            sid for sid in ids
            if (by_id[sid].seg, by_id[sid].t) in new_positions
        )
        useful = bool(new_pieces)

        # A receipt records the *physical exchange*, never the recovery promise:
        # signing for a bag does not mean a useful new piece arrived.
        fp_bag = next(iter(delivered.values()))
        if len(delivered) > 1:
            fp_bag = Bag(f"{voyage.name}:composite:{station}", self.manifest.obs_id,
                         PUBLIC, frozenset(ids))
        receipt = self._mint_receipt(voyage.name, voyage.carrier, station,
                                     cycle, "out", fp_bag, len(ids))
        island.record_receipt(receipt.receipt_id)
        self.ledger.append("meet_exchange", voyage=voyage.name, station=station,
                           cycle=cycle, delivered=sorted(ids),
                           new_pieces=new_pieces, useful=useful,
                           receipt=receipt.receipt_id,
                           guarantee=guarantee.as_dict() if guarantee else None)
        return {"contact": True, "delivered": sorted(ids),
                "new_pieces": new_pieces, "useful": useful,
                "guarantee": guarantee, "receipt": receipt,
                "reason": "exchanged"}

    # ---- custody ---------------------------------------------------------- #

    def return_receipt(self, carrier: str, receipt: Receipt,
                       custody: CustodyChain, ids: set[str]) -> bool:
        if not self.verify_receipt(receipt):
            self.ledger.append("receipt_invalid", carrier=carrier,
                               receipt=receipt.receipt_id)
            return False
        if carrier in self.local_ids:
            self.local_ids[carrier] -= ids
            if not self.local_ids[carrier]:
                self.released[carrier] = True
        custody.return_receipt(carrier, receipt, ids)
        self.ledger.append("local_released", carrier=carrier,
                           receipt=receipt.receipt_id, ids=sorted(ids))
        return True

    def transfer_custody(self, custody: CustodyChain, carrier: str, ids: set[str],
                         *, bag: Optional[Bag] = None, independent: bool = False) -> None:
        custody.ship(carrier, ids, bag=bag, independent=independent)
        self.ledger.append("custody_ship", carrier=carrier, ids=sorted(ids),
                           mode=custody.mode,
                           responsibility={k: sorted(v) for k, v
                                           in custody.responsibility().items()})

    # ---- audits / queries ------------------------------------------------- #

    def guarantees_for(self, station: str) -> list[Guarantee]:
        """Past guarantees this island produced (kept on the store)."""
        return list(getattr(self.islands[station], "guarantees", []))

    def settled_islands(self) -> set[str]:
        """Islands that have produced at least one verified guarantee."""
        out = set()
        for name, island in self.islands.items():
            if getattr(island, "guarantees", []):
                out.add(name)
        return out

    def promise_capability(self) -> dict[str, set[tuple[int, int]]]:
        """Positional recovery per island, deduped by actual piece identity."""
        return {name: island._source_positions() for name, island in self.islands.items()}
