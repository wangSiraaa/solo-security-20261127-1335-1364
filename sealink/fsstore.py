"""Filesystem-backed simulation of shore stations, round-trip vessels, islands.

This is the literal "用本地目录模拟" layer. Everything that travels is a small
file on disk, and entities go offline between meetings -- there is no shared
in-memory state, only directories that get read back when an actor resumes.

Layout under `root/`:

  port.json                         caps + withdrawn contacts (港口控制)
  shore/bags/<bag>/meta.json                 bag descriptor on the shore station
  shore/bags/<bag>/segs/<sid>.seg            one small file per segment
  ships/<ship>/hold/<bag>/...                what the vessel currently carries
  ships/<ship>/receipts/<rid>.json           actual exchange receipts on board
  ships/<ship>/manifest.json                 the directory the ship reopens to
                                             resume ("重开目录续运")
  islands/<island>/calendar.json             the island's own track calendar
  islands/<island>/held/<sid>.seg            previously ingested overlap pieces
  islands/<island>/inbox/<sid>.seg           pieces actually received & verified
  islands/<island>/receipts/<rid>.json       receipts co-signed at the island
  islands/<island>/merged/<name>.obsf        an independently-openable whole file

A meet only copies files that the receiving island can actually open (sealed
bags are only openable by their designated island). A receipt is written for a
real exchange; a withdrawn contact or a no-usable bag writes nothing to
`receipts/`. A vessel's local copy is deleted only when its signed receipt has
come back.

The pure-logic `IslandStore` is reused to decide whether an island can *open*
the window; the filesystem layer decides what was actually transferred.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, unquote

from .guarantee import IslandStore
from .model import (
    PUBLIC,
    SEALED,
    Bag,
    Calendar,
    Manifest,
    PortOrder,
    Receipt,
    SpaceTime,
    TrackRange,
    sha256_hex,
)


def _j(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, sort_keys=True)


def _r(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class FsWorld:
    def __init__(self, root: str, secret: bytes = b"deadbeef"):
        self.root = os.path.abspath(root)
        self.secret = secret
        os.makedirs(self.root, exist_ok=True)
        for d in ("shore/bags", "ships", "islands"):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)
        self.port = PortOrder()
        self._save_port()

    # ---- port control ----------------------------------------------------- #

    def _save_port(self):
        _j(os.path.join(self.root, "port.json"), {
            "caps": self.port.voyage_cap_bytes,
            "dropped": sorted(self.port.dropped_contacts),
        })

    def set_cap(self, voyage: str, cap_bytes: int):
        self.port.voyage_cap_bytes[voyage] = cap_bytes
        self._save_port()

    def drop_contact(self, voyage: str, station: str, cycle: int):
        self.port.drop_contact(voyage, station, cycle)
        self._save_port()

    def _contact_allowed(self, voyage, station, cycle) -> bool:
        return self.port.contact_allowed(voyage, station, cycle)

    # ---- paths ------------------------------------------------------------ #

    def _shore_bag(self, bag_id):
        return os.path.join(self.root, "shore", "bags", bag_id)

    def _ship(self, ship):
        return os.path.join(self.root, "ships", ship)

    def _island(self, island):
        return os.path.join(self.root, "islands", island)

    # ---- shore: publish a bag -------------------------------------------- #

    def publish_bag(self, manifest: Manifest, bag: Bag) -> str:
        """Write a bag to the shore station as many small files."""
        d = self._shore_bag(bag.bag_id)
        segd = os.path.join(d, "segs")
        os.makedirs(segd, exist_ok=True)
        by = manifest.by_id()
        for sid in sorted(bag.segment_ids):
            if sid not in by:
                raise KeyError(f"bag {bag.bag_id} references unknown segment {sid}")
            with open(os.path.join(segd, f"{_safe(sid)}.seg"), "wb") as fh:
                fh.write(by[sid].data)
        _j(os.path.join(d, "meta.json"), {
            "bag_id": bag.bag_id, "obs_id": bag.obs_id, "klass": bag.klass,
            "sealed_to": bag.sealed_to, "ids": sorted(bag.segment_ids),
            "root": bag.root, "fingerprint": bag.fingerprint(),
        })
        return d

    # ---- shore -> ship: load --------------------------------------------- #

    def load(self, voyage: str, ship: str, bag_id: str, *, ordinary: bool,
             manifest: Manifest) -> dict:
        """Copy a bag into the ship's hold, honouring sealed-only and byte cap."""
        src = self._shore_bag(bag_id)
        meta = _r(os.path.join(src, "meta.json"))
        if ordinary and meta["klass"] != SEALED:
            return {"ok": False, "reason": "ordinary_carrier_requires_sealed"}
        cost = sum(
            len(open(os.path.join(src, "segs", f), "rb").read())
            for f in os.listdir(os.path.join(src, "segs"))
        )
        cap = self.port.cap_for(voyage)
        used = self._ship_hold_bytes(ship)
        if used + cost > cap:
            return {"ok": False, "reason": "byte_cap_exceeded", "cost": cost, "cap": cap}

        dst = os.path.join(self._ship(ship), "hold", bag_id)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        self._append_ship_manifest(ship, {"kind": "load", "bag": bag_id,
                                          "root": meta["root"]})
        return {"ok": True, "cost": cost, "root": meta["root"]}

    def _ship_hold_bytes(self, ship) -> int:
        hold = os.path.join(self._ship(ship), "hold")
        if not os.path.isdir(hold):
            return 0
        n = 0
        for bag in os.listdir(hold):
            segd = os.path.join(hold, bag, "segs")
            for f in os.listdir(segd):
                n += os.path.getsize(os.path.join(segd, f))
        return n

    def _append_ship_manifest(self, ship, entry):
        mp = os.path.join(self._ship(ship), "manifest.json")
        data = _r(mp) if os.path.exists(mp) else {"entries": []}
        data["entries"].append(entry)
        _j(mp, data)

    def ship_manifest(self, ship) -> dict:
        """Reopen the ship directory to resume ("能重开目录续运")."""
        mp = os.path.join(self._ship(ship), "manifest.json")
        if not os.path.exists(mp):
            return {"entries": []}
        return _r(mp)

    def ship_receipts(self, ship) -> list[str]:
        rd = os.path.join(self._ship(ship), "receipts")
        if not os.path.isdir(rd):
            return []
        return sorted(f[:-5] for f in os.listdir(rd) if f.endswith(".json"))

    def ship_bags(self, ship) -> list[dict]:
        hold = os.path.join(self._ship(ship), "hold")
        if not os.path.isdir(hold):
            return []
        out = []
        for bag in sorted(os.listdir(hold)):
            out.append(_r(os.path.join(hold, bag, "meta.json")))
        return out

    # ---- ship <-> island: meet ------------------------------------------- #

    def meet(self, voyage: str, ship: str, island: str, cycle: int,
             manifest: Manifest, window: TrackRange) -> dict:
        """Exchange small files at a rendezvous.

        - withdrawn contact or nothing openable => no transfer, no receipt.
        - openable bag => copy only verified segment files into the island inbox,
          co-sign a receipt on ship and island, return it to the ship.
        """
        if not self._contact_allowed(voyage, island, cycle):
            self._island_log(island, {"kind": "meet_skipped", "voyage": voyage,
                                      "cycle": cycle, "reason": "port_dropped_contact"})
            return {"contact": False, "delivered": [], "receipt": None,
                    "reason": "port_dropped_contact"}

        by = manifest.by_id()
        inbox = os.path.join(self._island(island), "inbox")
        os.makedirs(inbox, exist_ok=True)
        cal_positions = {s.as_tuple() for s in self.island_calendar(island).steps}
        held_positions = self._positions_in_dir(
            os.path.join(self._island(island), "held"), by)
        before_positions = cal_positions | held_positions | \
            self._positions_in_dir(inbox, by)
        delivered: list[str] = []
        new_pieces: list[str] = []
        opened_bags: list[dict] = []
        for bag_meta in self.ship_bags(ship):
            if not self._openable_by(bag_meta, island):
                continue
            opened_bags.append(bag_meta)
            segd = os.path.join(self._ship(ship), "hold", bag_meta["bag_id"], "segs")
            for f in sorted(os.listdir(segd)):
                sid = _unsafe(f)
                data = open(os.path.join(segd, f), "rb").read()
                if sid not in by or sha256_hex(data) != by[sid].inner_digest:
                    continue  # inner digest does not verify -> not transferred
                out = os.path.join(inbox, f)
                if os.path.exists(out) and open(out, "rb").read() != data:
                    # content-addressed: a different piece at the same id is junk
                    self._island_log(island, {"kind": "reject_clobber", "sid": sid})
                    continue
                with open(out, "wb") as fh:
                    fh.write(data)
                delivered.append(sid)
                pos = (by[sid].seg, by[sid].t)
                if pos not in before_positions:
                    new_pieces.append(sid)
                    before_positions.add(pos)

        if not delivered:
            self._island_log(island, {"kind": "meet_no_usable", "voyage": voyage,
                                      "cycle": cycle})
            return {"contact": True, "delivered": [], "new_pieces": [],
                    "useful": False, "receipt": None,
                    "reason": "no_usable_bag_for_station"}

        # A signed receipt is not the same as a useful new piece: the files moved
        # and were receipted, but `new_pieces` may be empty if they were duplicates.
        useful = bool(new_pieces)

        # Receipt vouches for the exchange, not for a recovery promise.
        rid = f"{voyage}-{island}-{cycle}-{len(delivered)}"
        fp = _composite_fingerprint(opened_bags, manifest, sorted(delivered))
        unsigned = Receipt(rid, voyage, ship, island, cycle, "out", fp,
                           fp, len(delivered), "")
        r = Receipt(rid, voyage, ship, island, cycle, "out", fp,
                    fp, len(delivered), self._mac(unsigned))
        for base in (os.path.join(self._ship(ship), "receipts"),
                     os.path.join(self._island(island), "receipts")):
            _j(os.path.join(base, f"{rid}.json"), r.as_dict())
        self._append_ship_manifest(ship, {"kind": "receipt", "receipt": rid,
                                          "delivered": len(delivered)})
        self._island_log(island, {"kind": "meet_exchange", "voyage": voyage,
                                  "cycle": cycle, "delivered": sorted(delivered),
                                  "new_pieces": sorted(new_pieces),
                                  "useful": useful, "receipt": rid})
        return {"contact": True, "delivered": sorted(delivered),
                "new_pieces": sorted(new_pieces), "useful": useful,
                "receipt": r, "reason": "exchanged"}

    @staticmethod
    def _openable_by(bag_meta, island) -> bool:
        if bag_meta["klass"] == PUBLIC:
            return True
        return bag_meta["sealed_to"] == island

    def _positions_in_dir(self, d, by) -> set[tuple[int, int]]:
        out = set()
        for sid, _ in self._read_segdir(d).items():
            if sid in by:
                out.add((by[sid].seg, by[sid].t))
        return out

    # ---- island: merge desk --------------------------------------------- #

    def island_calendar(self, island) -> Calendar:
        p = os.path.join(self._island(island), "calendar.json")
        if not os.path.exists(p):
            return Calendar(island, ())
        steps = _r(p)["steps"]
        return Calendar(island, tuple(SpaceTime(s, t) for s, t in steps))

    def set_island_calendar(self, island, steps):
        _j(os.path.join(self._island(island), "calendar.json"),
           {"steps": [[s, t] for s, t in steps]})

    def seed_island_held(self, island, manifest: Manifest, sids):
        held = os.path.join(self._island(island), "held")
        os.makedirs(held, exist_ok=True)
        by = manifest.by_id()
        for sid in sids:
            with open(os.path.join(held, f"{_safe(sid)}.seg"), "wb") as fh:
                fh.write(by[sid].data)

    def merge(self, island: str, manifest: Manifest, window: TrackRange) -> Optional[dict]:
        """Independently assemble the window from disk and write an .obsf file."""
        cal = self.island_calendar(island)
        held = self._read_segdir(os.path.join(self._island(island), "held"))
        inbox = self._read_segdir(os.path.join(self._island(island), "inbox"))
        store = IslandStore(island, cal, manifest, held=held, received=inbox)
        g = store.merge(window, {})
        if g is None:
            self._island_log(island, {
                "kind": "merge_incomplete",
                "missing": sorted(store.missing(window)),
            })
            return None
        outdir = os.path.join(self._island(island), "merged")
        os.makedirs(outdir, exist_ok=True)
        name = f"{manifest.obs_id}_{window.seg_lo}-{window.seg_hi}_" \
               f"{window.t_lo}-{window.t_hi}.obsf"
        by = manifest.by_id()
        with open(os.path.join(outdir, name), "wb") as fh:
            # a self-describing, independently-openable whole observation file
            header = {"obs_id": manifest.obs_id, "root": g.root,
                      "window": [window.seg_lo, window.seg_hi, window.t_lo, window.t_hi],
                      "segments": []}
            fh.write(b"")
            for sid in g.openings:
                seg = by[sid]
                header["segments"].append([sid, seg.inner_digest, seg.length])
            fh.write(json.dumps(header).encode() + b"\n")
            for sid in g.openings:
                fh.write(by[sid].data + b"\n")
        self._island_log(island, {"kind": "merged", "file": name, "root": g.root})
        return {"file": os.path.join(outdir, name), "root": g.root,
                "openings": list(g.openings), "receipt_ids": list(g.receipt_ids)}

    def _read_segdir(self, d) -> dict[str, bytes]:
        if not os.path.isdir(d):
            return {}
        out = {}
        for f in os.listdir(d):
            if f.endswith(".seg"):
                out[_unsafe(f)] = open(os.path.join(d, f), "rb").read()
        return out

    # ---- receipt-based local release ------------------------------------ #

    def return_receipt(self, ship: str, receipt_id: str) -> dict:
        """A receipt that has come back lets the ship delete its local copy."""
        p = os.path.join(self._ship(ship), "receipts", f"{receipt_id}.json")
        if not os.path.exists(p):
            return {"ok": False, "reason": "no_returned_receipt"}
        d = _r(p)
        if not self._verify(d):
            return {"ok": False, "reason": "receipt_tampered"}
        # Determine which bag(s) the receipt covers and drop their segment files.
        hold = os.path.join(self._ship(ship), "hold")
        released = []
        if os.path.isdir(hold):
            for bag in os.listdir(hold):
                shutil.rmtree(os.path.join(hold, bag))
                released.append(bag)
        self._append_ship_manifest(ship, {"kind": "released", "receipt": receipt_id})
        return {"ok": True, "released_bags": released}

    # ---- receipts -------------------------------------------------------- #

    def _mac(self, r: Receipt) -> str:
        import hmac, hashlib
        body = json.dumps({
            "receipt_id": r.receipt_id, "voyage": r.voyage, "carrier": r.carrier,
            "station": r.station, "cycle": r.cycle, "direction": r.direction,
            "bag_fingerprint": r.bag_fingerprint, "segment_root": r.segment_root,
            "n_segments": r.n_segments,
        }, sort_keys=True, separators=(",", ":")).encode()
        key = self.secret + b"|station|" + r.station.encode()
        return hmac.new(key, body, hashlib.sha256).hexdigest()

    def _verify(self, d: dict) -> bool:
        from .model import Receipt as R
        r = R(**{k: d[k] for k in ("receipt_id", "voyage", "carrier", "station",
                                   "cycle", "direction", "bag_fingerprint",
                                   "segment_root", "n_segments", "mac")})
        import hmac
        return hmac.compare_digest(r.mac, self._mac(r))

    # ---- logs ------------------------------------------------------------ #

    def _island_log(self, island, entry):
        p = os.path.join(self._island(island), "ledger.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        entry = dict(entry)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def island_log(self, island) -> list[dict]:
        p = os.path.join(self._island(island), "ledger.jsonl")
        if not os.path.exists(p):
            return []
        return [_r_line(l) for l in open(p, encoding="utf-8") if l.strip()]


def _safe(sid: str) -> str:
    """Reversible filename encoding for a segment id."""
    return quote(sid, safe="")


def _unsafe(name: str) -> str:
    if name.endswith(".seg"):
        name = name[:-4]
    return unquote(name)


def _r_line(line):
    return json.loads(line)


def _composite_fingerprint(opened_bags, manifest, delivered) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(b"exchange|")
    for b in sorted(opened_bags, key=lambda m: m["bag_id"]):
        h.update(b["fingerprint"].encode())
        h.update(b"\x00")
    for sid in delivered:
        h.update(sid.encode())
        h.update(b"\x00")
    return h.hexdigest()
