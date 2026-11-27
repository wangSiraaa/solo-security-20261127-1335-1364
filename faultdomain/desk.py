"""The rehearsal desk (演练台).

The desk judges whether announcing may continue, from exactly:

* the **proofs / evidence received** (whichever disclosure approach was chosen),
* the **two ports actually connected** for the pair being announced over,
* the **fault domain cut in this drill**.

Two kinds of facts are kept separate on purpose:

* **Paperwork** – ``Evidence``: does the chosen approach establish that the two
  paths are on disjoint fault domains, and does it bind the same access ports
  the station believes it wired?  Perhaps signed (approach B), perhaps the
  station's own label-level assembly (approach A).
* **Physical reality** – an optional ``probe``: actually send a throwaway ticket
  over a named path.  This is the ground truth for "is this port really
  attached" and "does this path really avoid the cut".  Paperwork can say the
  pair is isolated; only a real probe shows the wire is connected.

A good isolation proof therefore cannot rescue a detached backup port: the port
binding check and the probe both fail.  A cut at a fictional domain that no real
path uses cannot block a genuinely working path: the probe succeeds.  But a cut
that both paths really cross makes every probe fail, so the desk refuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

# probe(index) -> (ok, reason)
Probe = Callable[[int], Tuple[bool, str]]


@dataclass
class Evidence:
    """Approach-agnostic summary of what the received proofs establish."""

    source: str                       # "export" or "verifier"
    disjoint_ok: bool                 # establishes disjoint fault domains
    path_ports: List[Tuple[str, str]]  # per path: (ingress, egress) ports bound
    path_survives: Optional[List[bool]] = None  # per path: avoids the cut (paper)
    # Approach B specifics; None => not applicable (approach A recomputes fresh).
    fresh: Optional[bool] = None
    contract_active: Optional[bool] = None
    signature_ok: Optional[bool] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class ActuallyConnected:
    """One path's real wiring, as the station sees it on its own ports."""

    src_port: str
    dst_port: Optional[str]  # None => destination access port is detached


class Desk:
    """Stateless decision procedure; every step is reported in the trace."""

    def decide(
        self,
        evidence: Evidence,
        actually_connected: List[ActuallyConnected],
        cut_domain: Optional[str],
        probe: Optional[Probe] = None,
    ) -> Tuple[bool, List[str]]:
        trace: List[str] = []

        # (0) Approach-B proofs must be authentic, fresh, and in-contract.  The
        # station cannot mint a replacement itself, so a stale proof is a hard
        # stop until the verifier re-issues one.
        if evidence.signature_ok is False:
            trace.append("REJECT: proof signature invalid")
            return False, trace
        if evidence.fresh is False:
            trace.append(
                "REJECT: proof is not fresh for the current epoch; the station "
                "cannot renew it by guessing -- it must be re-issued"
            )
            return False, trace
        if evidence.contract_active is False:
            trace.append("REJECT: contract window does not cover the current epoch")
            return False, trace

        # (1) Paperwork: disjoint fault domains.
        if not evidence.disjoint_ok:
            trace.append(
                f"REJECT: {evidence.source} evidence does not establish disjoint "
                "fault domains"
            )
            return False, trace
        trace.append(f"{evidence.source} evidence: disjoint fault domains")

        # (2) Every path's evidence must cover the real wiring count.
        if len(evidence.path_ports) != len(actually_connected):
            trace.append("REJECT: evidence does not cover every path")
            return False, trace

        # (3) Each path must be physically made, and ports must match evidence.
        for idx, ((e_src, e_dst), conn) in enumerate(
            zip(evidence.path_ports, actually_connected)
        ):
            if conn.dst_port is None:
                trace.append(
                    f"REJECT: path {idx} destination port is detached -- a valid "
                    "isolation proof cannot make a non-existent connection carry "
                    "traffic"
                )
                return False, trace
            if e_src != conn.src_port or e_dst != conn.dst_port:
                trace.append(
                    f"REJECT: path {idx} evidence binds {e_src}->{e_dst} but the "
                    f"actually connected ports are {conn.src_port}->{conn.dst_port}"
                )
                return False, trace
            trace.append(
                f"path {idx}: evidence binds {conn.src_port}->{conn.dst_port} "
                "(matches actual wiring)"
            )

        # (4) Cut survival, from physical probes when available, else paperwork.
        if probe is not None:
            survivors: List[int] = []
            reasons: List[str] = []
            for idx in range(len(actually_connected)):
                ok, reason = probe(idx)
                if ok:
                    survivors.append(idx)
                else:
                    reasons.append(f"path {idx}: {reason}")
            if not survivors:
                label = cut_domain if cut_domain is not None else "(no cut)"
                trace.append(
                    f"REJECT: no path survives {label}: " + "; ".join(reasons)
                )
                return False, trace
            if cut_domain is None:
                trace.append(f"live probe: path(s) {survivors} deliver")
            else:
                trace.append(
                    f"live probe: path(s) {survivors} deliver under cut '{cut_domain}'"
                )
        elif evidence.path_survives is not None:
            survivors = [i for i, s in enumerate(evidence.path_survives) if s]
            if not survivors:
                trace.append(
                    f"REJECT: paperwork says every path crosses cut '{cut_domain}'"
                )
                return False, trace
            trace.append(
                f"paperwork: path(s) {survivors} avoid cut '{cut_domain}'"
            )
        else:
            trace.append("note: no probe and no survival claim; cut survival unverified")

        trace.append("ACCEPT: an isolated, actually connected path may carry the announcement")
        return True, trace
