"""Probe-ticket delivery.

A probe ticket is sent from a source terminal to a destination terminal along a
concrete installed lease.  Delivery is *physical*: it succeeds only if

1. every middle domain the lease traverses is not cut,
2. every interchange the lease crosses is not cut,
3. the source access port the lease is installed on is attached, and
4. the destination access port the lease is installed on is attached.

On success each real domain that handled the ticket (each operator, each
interchange, and the destination terminal) appends a ``HandlingRecord`` to its
own log.  Those per-domain logs are the ground truth the exercise checks
"really received" against -- a fictional domain has no logs, and a detached
port means the ticket never arrives no matter how good the paperwork is.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import List, Optional

from .model import Lease
from .world import HandlingRecord, World


@dataclass
class DeliveryResult:
    ok: bool
    ticket_id: str
    reason: str = ""
    records: List[HandlingRecord] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.records is None:
            self.records = []


def new_ticket_id() -> str:
    return "probe-" + secrets.token_hex(8)


class DeliveryEngine:
    def __init__(self, world: World) -> None:
        self.world = world

    def deliver(
        self,
        ticket_id: str,
        source_terminal: str,
        dest_terminal: str,
        lease: Lease,
        src_port: str,
        dst_port: str,
        announce: Optional[str] = None,
    ) -> DeliveryResult:
        w = self.world

        # (3) source access port must be attached.
        if not w.is_attached(src_port):
            w.log(f"deliver {ticket_id}: source port {src_port} detached")
            return DeliveryResult(False, ticket_id, f"source port {src_port} is detached")

        view = w.resolve(lease)

        # (1)+(2) no domain/interchange on the path may be cut.
        for dom in view.domains:
            if dom in w.cuts:
                w.log(f"deliver {ticket_id}: domain {dom} cut")
                return DeliveryResult(False, ticket_id, f"domain {dom} is cut")

        # (4) destination access port must be attached.
        if not w.is_attached(dst_port):
            w.log(f"deliver {ticket_id}: dest port {dst_port} detached")
            return DeliveryResult(False, ticket_id, f"destination port {dst_port} is detached")

        # Delivery succeeds: record handling at every real domain in order.
        records: List[HandlingRecord] = []
        seen_domains: List[str] = []
        for hop in lease.hops:
            dom = w.dispatcher.domain_of_operator(hop.operator)
            if dom not in seen_domains:
                seen_domains.append(dom)
        # interchanges interleaved by crossing order; include them too.
        path_domains: List[str] = []
        for hop in lease.hops:
            d = w.dispatcher.domain_of_operator(hop.operator)
            if d not in path_domains:
                path_domains.append(d)
        for ic in view.interchanges:
            if ic not in path_domains:
                path_domains.append(ic)

        summary = announce or f"ticket {ticket_id} for {dest_terminal}"
        for dom in path_domains:
            role = "interchange" if dom in view.interchanges else "operator"
            rec = HandlingRecord(
                ticket_id=ticket_id,
                domain=dom,
                role=role,
                detail=f"{summary} :: {source_terminal}->{dest_terminal}",
                epoch=w.epoch,
            )
            w._record(rec)
            records.append(rec)

        # The destination terminal itself records receipt.
        drec = HandlingRecord(
            ticket_id=ticket_id,
            domain=dest_terminal,
            role="terminal",
            detail=summary,
            epoch=w.epoch,
        )
        w._record(drec)
        records.append(drec)

        w.log(f"deliver {ticket_id}: OK via {lease.lease_id}")
        return DeliveryResult(True, ticket_id, "", records)
