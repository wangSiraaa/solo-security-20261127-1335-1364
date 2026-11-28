"""Tick-based power + flush simulation (the server process).

The engine owns the store, decides what to flush under the measured power
envelope, and mints receipts.  It is killed at a sync point and must never be
trusted afterwards -- recovery reads only disk.

Power model (one tick == one scheduling round); units are watts / watt-ticks:
  * each feed (支路) has its own breaker capacity
  * powered cabinets draw base load on whichever feed serves them
  * cabinets cold-starting this tick also draw their startup peak
  * dual-fed cabinets are assigned to the feed minimising the worst-case load
  * a per-feed overload may be shaved by the battery up to DISCHARGE_RATE for
    one tick, otherwise a chassis must be shed
  * sustained load above total feed capacity drains the battery each tick
  * chassis inside a paid minimum-service window are never shed

Flush / energy model:
  * 每拍承载  ``flushes_per_tick`` : how many write points can land per tick
  * before a feed is lost, disk writes are carried by the utility spare head-
    room (free with respect to the battery)
  * after a feed is lost the disk subsystem is isolated onto the battery bus,
    so every flush draws ``cost`` from the battery -- this is what bounds the
    real recovery window
  * cross-volume (cross-book reference) write points are prioritised so that,
    while residual energy lasts, a reference order is completed first
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple

from . import power as pw
from .model import KIND_RANK, Cabinet, DrillConfig, Event, Op, Submission
from .receipts import sign_receipt
from .storage import Store

DISCHARGE_RATE = 60.0


def ops_for(sub: Submission) -> List[Op]:
    if sub.ops:
        return sub.ops
    ops: List[Op] = []
    data_ids: List[str] = []
    for p in sub.pages:
        if p.kind != "data":
            continue
        oid = f"{sub.txid}.d{p.page_no}"
        data_ids.append(oid)
        ops.append(Op(
            op_id=oid, txid=sub.txid, classroom=sub.classroom,
            assignment=sub.assignment, cabinet=sub.cabinet, kind="data",
            vol=p.vol, page_no=p.page_no, content=p.content,
            cost=0.0, deps=(), seq=len(ops), cross_volume=sub.cross_volume))
    for p in sub.pages:
        if p.kind != "index":
            continue
        oid = f"{sub.txid}.x{p.page_no}"
        ops.append(Op(
            op_id=oid, txid=sub.txid, classroom=sub.classroom,
            assignment=sub.assignment, cabinet=sub.cabinet, kind="index",
            vol=p.vol, page_no=p.page_no, content=p.content,
            cost=0.0, deps=tuple(data_ids), seq=len(ops),
            cross_volume=sub.cross_volume))
    commit_deps = tuple(o.op_id for o in ops)
    ops.append(Op(
        op_id=f"{sub.txid}.c", txid=sub.txid, classroom=sub.classroom,
        assignment=sub.assignment, cabinet=sub.cabinet, kind="commit",
        vol=None, page_no=None, content="commit", cost=0.0,
        deps=commit_deps, seq=len(ops), cross_volume=sub.cross_volume))
    sub.ops = ops
    return ops


def _fill_costs(sub: Submission, cabinet: Cabinet) -> None:
    for op in ops_for(sub):
        op.cost = cabinet.flush_cost


class Engine:
    def __init__(self, cfg: DrillConfig, store: Store, mode: str = "online") -> None:
        self.cfg = cfg
        self.store = store
        self.mode = mode
        self.tick = 0
        self.battery = float(cfg.battery_energy)
        self.events: List[Event] = []
        self.telemetry: List[Dict[str, Any]] = []
        self.receipts: List[Dict[str, Any]] = []
        self.aborted: Dict[str, str] = {}
        self._flush_order = {n: i for i, n in enumerate(cfg.chassis_flush_order)}
        self._shed_order = {n: i for i, n in enumerate(cfg.shed_order)}
        self._started: Set[str] = set()
        self._shed: Set[str] = set()
        self._accepted: Set[str] = set()
        self._promised_tx: Set[str] = set()
        self._pending_issued: Set[str] = set()
        self._promisable_cabs: Optional[Set[str]] = None
        self._inflight: List[Tuple[int, Op]] = []   # (complete_tick, op)
        self._dispatched: Set[str] = set()
        for sub in cfg.submissions:
            _fill_costs(sub, cfg.topology.cabinet(sub.cabinet))
        self._apply_delay_injection()

    # -- timeline -------------------------------------------------------
    def _feeds_present(self, tick: int) -> frozenset:
        if tick < self.cfg.feed_loss_tick:
            return frozenset(self.cfg.all_feeds)
        return frozenset(self.cfg.present_feeds_after_loss)

    def _post_loss(self, tick: int) -> bool:
        return tick >= self.cfg.feed_loss_tick

    def _protected(self, tick: int) -> Set[str]:
        ms = self.cfg.min_service
        if ms and tick <= ms["ticks"]:
            return {ms["cabinet"]}
        return set()

    def _apply_delay_injection(self) -> None:
        di = self.cfg.delay_index
        if not di:
            return
        for sub in self.cfg.submissions:
            if sub.classroom == di["classroom"] and sub.assignment == di["assignment"]:
                for op in ops_for(sub):
                    if op.kind == "index":
                        op.deferred_once = True
                        self._log("inject_delay_index",
                                  {"txid": sub.txid, "op_id": op.op_id})
                        break

    def _log(self, kind: str, detail: Dict[str, Any]) -> None:
        self.events.append(Event(self.tick, kind, detail))

    # -- power ----------------------------------------------------------
    def _candidates(self, tick: int, feeds: frozenset) -> Set[str]:
        cand: Set[str] = set()
        for cab in self.cfg.topology.cabinets:
            if not cab.powered_by(feeds):
                continue
            if cab.name in self._shed:
                continue
            if tick < self.cfg.startup_schedule.get(cab.name, 0):
                continue
            cand.add(cab.name)
        if self.mode == "graded" and self._promisable_cabs is not None:
            cand &= set(self._promisable_cabs) | self._protected(tick)
        return cand

    def _evaluate(self, powered: Set[str], starting: Set[str], feeds: frozenset
                  ) -> Tuple[Dict[str, float], float, float, float]:
        present = tuple(sorted(feeds))
        _, loads = pw.best_assignment(self.cfg.topology, feeds,
                                      sorted(powered), starting)
        total_cap = sum(self.cfg.topology.feed(f).capacity for f in present)
        total_load = sum(loads.values())
        sustained = max(0.0, total_load - total_cap)
        overload = pw.feed_overload(loads, self.cfg.topology, present)
        headroom = pw.spare_headroom(loads, self.cfg.topology, present)
        return loads, sustained, overload, headroom

    def _settle_power(self, tick: int, feeds: frozenset
                      ) -> Tuple[Set[str], Set[str], Dict[str, float], float]:
        powered = self._candidates(tick, feeds)
        starting = {n for n in powered if n not in self._started}
        protected = self._protected(tick)
        order = sorted(powered, key=lambda n: self._shed_order.get(n, 10**6),
                       reverse=True)
        while True:
            loads, sustained, overload, _ = self._evaluate(powered, starting, feeds)
            over_rate = overload > min(DISCHARGE_RATE, self.battery) + 1e-9
            over_energy = sustained > self.battery + 1e-9
            if not (over_rate or over_energy):
                break
            shedable = [n for n in order if n in powered and n not in protected]
            if not shedable:
                break
            victim = shedable[0]
            powered.discard(victim)
            starting.discard(victim)
            self._shed.add(victim)
            self._log("shed", {"cabinet": victim, "tick": tick,
                               "overload": round(overload, 2),
                               "sustained": round(sustained, 2)})
        loads, sustained, overload, headroom = self._evaluate(
            powered, starting, feeds)
        if sustained:
            self.battery = max(0.0, self.battery - sustained)
        if sustained > 0 and self.battery <= 0 or overload > DISCHARGE_RATE:
            self._log("brownout", {"tick": tick, "overload": round(overload, 2),
                                   "sustained": round(sustained, 2)})
            return powered, starting, loads, 0.0
        self._started |= starting
        return powered, starting, loads, headroom

    # -- flush ----------------------------------------------------------
    def _flush_priority(self, ops: List[Op]) -> List[Op]:
        return sorted(ops, key=lambda o: (
            0 if o.cross_volume else 1,
            self._flush_order.get(o.cabinet, 10**6),
            KIND_RANK[o.kind],
            o.seq,
        ))

    def _eligible(self, op: Op, flushed: Set[str], tick: int) -> bool:
        return op.not_before_tick <= tick and all(d in flushed for d in op.deps)

    def _present_ops(self, powered: Set[str]) -> List[Op]:
        ops: List[Op] = []
        for sub in self.cfg.submissions:
            if sub.txid in self.aborted or sub.txid not in self._accepted:
                continue
            if sub.cabinet not in powered:
                continue
            for o in ops_for(sub):
                if not o.flushed and o.op_id not in self._dispatched:
                    ops.append(o)
        return ops

    def _advance_inflight(self, tick: int) -> List[str]:
        """Complete write points whose 磁盘落盘时间 has elapsed."""
        due = [(ct, op) for (ct, op) in self._inflight if ct <= tick]
        self._inflight = [(ct, op) for (ct, op) in self._inflight if ct > tick]
        done: List[str] = []
        for _, op in sorted(due, key=lambda i: i[1].seq):
            self.store.flush(op, tick)
            done.append(op.op_id)
        return done

    def _flush_round(self, tick: int, powered: Set[str], headroom: float) -> List[str]:
        candidates = self._present_ops(powered)
        flushed_total = {o.op_id for sub in self.cfg.submissions
                         for o in ops_for(sub) if o.flushed}
        done: List[str] = []
        post = self._post_loss(tick)
        # 每拍承载: number of write points that can land this tick
        if post:
            lanes = self.cfg.flushes_per_tick
        else:
            unit = max(1e-9, min((o.cost for o in candidates), default=1.0))
            lanes = max(0, min(self.cfg.flushes_per_tick,
                               int(max(0.0, headroom) // unit)))
        progressed = True
        while progressed and lanes > 0:
            progressed = False
            for op in self._flush_priority(candidates):
                if op.flushed or op.op_id in self._dispatched:
                    continue
                if not self._eligible(op, flushed_total, tick):
                    continue
                if op.deferred_once and not op.defer_used:
                    op.defer_used = True
                    op.not_before_tick = tick + 1     # slip exactly one tick
                    self._log("defer_index", {"op_id": op.op_id, "tick": tick,
                                              "to_tick": tick + 1})
                    continue
                if lanes <= 0:
                    break
                if post:
                    if self.battery < op.cost - 1e-9:
                        continue
                else:
                    if headroom + 1e-9 < op.cost:
                        continue
                # charge energy at start; land on disk after flush_ticks ticks
                if post:
                    self.battery -= op.cost
                else:
                    headroom -= op.cost
                if self.cfg.flush_ticks <= 1:
                    self.store.flush(op, tick)
                    flushed_total.add(op.op_id)
                    done.append(op.op_id)
                else:
                    op.not_before_tick = tick            # already dispatched
                    self._dispatched.add(op.op_id)
                    self._inflight.append(
                        (tick + self.cfg.flush_ticks - 1, op))
                    done.append(op.op_id + "@inflight")
                lanes -= 1
                progressed = True
        return done

    # -- graded planning -------------------------------------------------
    def _plan_promisable(self) -> Set[str]:
        feeds = frozenset(self.cfg.present_feeds_after_loss)
        budget = self.battery
        chosen: Set[str] = set()
        cabs: Set[str] = set()
        for sub in sorted(self.cfg.submissions,
                          key=lambda s: (not s.cross_volume, s.submitted_tick)):
            need = sum(o.cost for o in ops_for(sub))
            if need <= budget:
                budget -= need
                chosen.add(sub.txid)
                cabs.add(sub.cabinet)
        for sub in self.cfg.submissions:
            if sub.txid not in chosen:
                self.aborted[sub.txid] = "graded: outside safe combo"
        return cabs

    # -- tick loop ------------------------------------------------------
    def run(self) -> None:
        if self.mode == "graded":
            self._promisable_cabs = self._plan_promisable()
        for tick in range(self.cfg.kill_tick + 1):
            self.tick = tick
            self._step(tick)
        self._finish()

    def _accept_submissions(self, tick: int) -> None:
        for sub in self.cfg.submissions:
            if sub.submitted_tick != tick or sub.txid in self.aborted:
                continue
            for op in ops_for(sub):
                self.store.accept(op)
            self._accepted.add(sub.txid)
            self._log("accept", {"txid": sub.txid, "cabinet": sub.cabinet,
                                 "cross_volume": sub.cross_volume})
            if self.cfg.receipt_policy == "cache":
                self._mint(sub, tick, policy="cache")  # UNSAFE: pre-durable

    def _step(self, tick: int) -> None:
        feeds = self._feeds_present(tick)
        self._accept_submissions(tick)
        powered, starting, loads, headroom = self._settle_power(tick, feeds)
        landed = self._advance_inflight(tick)
        flushes = landed + self._flush_round(tick, powered, headroom)
        total_cap = sum(self.cfg.topology.feed(f).capacity for f in sorted(feeds))
        self.telemetry.append({
            "tick": tick,
            "feeds_present": sorted(feeds),
            "powered": sorted(powered),
            "starting": sorted(starting),
            "feed_loads": {k: round(v, 3) for k, v in sorted(loads.items())},
            "total_capacity": round(total_cap, 3),
            "battery_after": round(self.battery, 3),
            "headroom": round(headroom, 3),
            "flushes": flushes,
            "post_loss": self._post_loss(tick),
        })
        if self.cfg.receipt_policy != "cache":
            self._confirm_durable(tick, powered)

    def _confirm_durable(self, tick: int, powered: Set[str]) -> None:
        for sub in self.cfg.submissions:
            if (sub.txid in self._promised_tx or sub.txid in self.aborted
                    or sub.txid not in self._accepted):
                continue
            if sub.cabinet not in powered:
                continue     # cabinet unpowered: cannot have produced a receipt
            ops = ops_for(sub)
            if all(o.flushed for o in ops):
                self._mint(sub, tick, policy="durable")
                continue
            remaining = sum(o.cost for o in ops if not o.flushed)
            if (self._post_loss(tick) and self.battery <= remaining
                    and sub.txid not in self._pending_issued):
                self._mint(sub, tick, policy="pending")
                self._pending_issued.add(sub.txid)

    def _mint(self, sub: Submission, tick: int, policy: str) -> None:
        ops = ops_for(sub)
        payload = {
            "txid": sub.txid,
            "receipt_number": f"{sub.classroom}-{sub.assignment}-{sub.txid}",
            "classroom": sub.classroom,
            "assignment": sub.assignment,
            "cabinet": sub.cabinet,
            "cross_volume": sub.cross_volume,
            "policy": policy,
            "confirmed_tick": tick,
            "residual_energy": round(self.battery, 3),
            "service_set": sorted(self._feeds_present(tick)),
            "pages": [
                {"vol": o.vol, "page_no": o.page_no, "kind": o.kind,
                 "content": o.content}
                for o in ops if o.kind != "commit"
            ],
            "flush_order": [o.op_id for o in ops],
            "deps": {o.op_id: list(o.deps) for o in ops},
        }
        receipt = sign_receipt(payload, self.cfg.classroom_keys[sub.classroom])
        receipt["status"] = "confirmed" if policy == "durable" else policy
        self.receipts.append(receipt)
        if policy == "durable":
            self._promised_tx.add(sub.txid)
            sub.promised = True
            sub.receipt = receipt
            self._log("confirm", {"txid": sub.txid, "tick": tick,
                                  "residual_energy": payload["residual_energy"]})
        elif policy == "pending":
            sub.pending_receipt = receipt
            self._log("pending", {"txid": sub.txid, "tick": tick})

    def _finish(self) -> None:
        self.store.freeze_cache()
        self.store.write_power({
            "feeds_present_final": sorted(self._feeds_present(self.cfg.kill_tick)),
            "battery_final": round(self.battery, 3),
            "kill_tick": self.cfg.kill_tick,
            "min_service": self.cfg.min_service,
            "telemetry": self.telemetry,
            "events": [e.to_dict() for e in self.events],
        })
        held = os.path.join(os.path.dirname(self.store.root.rstrip("/")), "held")
        os.makedirs(held, exist_ok=True)
        for old in os.listdir(held):
            if old.endswith(".json"):
                os.remove(os.path.join(held, old))
        for i, r in enumerate(self.receipts):
            with open(os.path.join(held, f"receipt_{i:03d}.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(r, fh, sort_keys=True, ensure_ascii=False, indent=2)
