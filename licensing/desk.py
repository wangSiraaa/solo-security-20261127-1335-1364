"""The library issue desk (the central licensing authority).

Responsibilities:
  * Issue signed LEASES drawn from a per-chapter credit pool.
  * Verify sealed RETURN PROOFS (binding + toy device MAC) before reclaiming.
  * Never re-grant a credit while an old device could still open once.
  * Issue honest PENDING / LIMITED receipts for players that are not online.

Why the pool prevents double spending:
  An ACTIVE lease keeps its whole `plays_total` reserved out of the pool. The
  desk only adds back the *unconsumed* credits after a VALID SEALED proof. So a
  waiting borrower can never be granted a credit that a still-open device might
  yet spend. Consumed credits are spent forever -- "heard lines cannot be
  recalled from the listener".
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

from .clock import SimClock
from .crypto import TOY_DESK_SECRET, device_secret, mac, verify
from .model import (
    Lease,
    LeaseScope,
    LeaseState,
    ReturnProof,
    ReturnReceipt,
    ReturnStatus,
)


@dataclass
class LeaseRecord:
    lease: Lease
    plays_consumed: int = 0
    state: LeaseState = LeaseState.ACTIVE
    proof_seen: bool = False
    provisional_released: int = 0     # credits already released provisionally
    returned_receipt: Optional[ReturnReceipt] = None


@dataclass
class DeskResult:
    ok: bool
    message: str
    lease: Optional[Lease] = None
    receipt: Optional[ReturnReceipt] = None


class Desk:
    def __init__(self, clock: SimClock, chapter_credits: Dict[str, int]) -> None:
        self.clock = clock
        self.pool: Dict[str, int] = dict(chapter_credits)
        self.total: Dict[str, int] = dict(chapter_credits)
        self.leases: Dict[str, LeaseRecord] = {}
        self._seq = itertools.count(1)
        self._rseq = itertools.count(1)
        self._audit: List[str] = []

    # ------------------------------------------------------------------ util
    def _next_lease_id(self) -> str:
        return f"lease-{next(self._seq):03d}"

    def _next_receipt_id(self) -> str:
        return f"receipt-{next(self._rseq):03d}"

    def audit(self) -> List[str]:
        return list(self._audit)

    def _log(self, msg: str) -> None:
        self._audit.append(msg)

    def pool_status(self) -> Dict[str, Dict[str, int]]:
        return {
            ch: {
                "total": self.total[ch],
                "available": self.pool[ch],
                "reserved_or_spent": self.total[ch] - self.pool[ch],
            }
            for ch in self.total
        }

    # ----------------------------------------------------------------- issue
    def issue_lease(
        self,
        chapter_id: str,
        device_id: str,
        borrower_id: str,
        plays: int,
        scope: LeaseScope,
        ttl: int,
    ) -> DeskResult:
        if chapter_id not in self.pool:
            return DeskResult(False, f"unknown chapter {chapter_id!r}")

        if scope == LeaseScope.FRONT_PREVIEW:
            # Preview-only borrowers consume NO normal credits and get no cache.
            plays = 0
        else:
            if plays <= 0:
                return DeskResult(False, "a full lease needs at least one play")
            available = self.pool[chapter_id]
            if plays > available:
                # Loudest double-spend guard: credits still reserved to another
                # (unsealed) device, so we will not grant them twice.
                self._log(
                    f"REFUSE issue ch={chapter_id} plays={plays} > available={available}"
                )
                return DeskResult(
                    False,
                    f"refused: {plays} play(s) requested for {chapter_id!r} but only "
                    f"{available} available; an outstanding lease may still open",
                )

        now = self.clock.now()
        lease = Lease(
            lease_id=self._next_lease_id(),
            chapter_id=chapter_id,
            device_id=device_id,
            borrower_id=borrower_id,
            plays_total=plays,
            scope=scope,
            issued_at=now,
            expires_at=now + ttl,
        )
        lease = replace(lease, signature=mac(TOY_DESK_SECRET, lease.payload()))
        self.pool[chapter_id] -= plays
        self.leases[lease.lease_id] = LeaseRecord(lease=lease)
        self._log(
            f"ISSUE {lease.lease_id} ch={chapter_id} dev={device_id} "
            f"borrower={borrower_id} plays={plays} scope={scope.value} "
            f"pool_after={self.pool[chapter_id]}"
        )
        return DeskResult(True, "lease issued", lease=lease)

    # ---------------------------------------------------------------- return
    def _bind_check(self, proof: ReturnProof) -> Tuple[bool, str]:
        rec = self.leases.get(proof.lease_id)
        if rec is None:
            return False, "no such lease on the desk"
        lease = rec.lease
        if proof.chapter_id != lease.chapter_id:
            # Receipt-swap tamper: a proof retargeted to another chapter.
            return False, (
                f"binding mismatch: proof chapter {proof.chapter_id!r} != "
                f"lease chapter {lease.chapter_id!r}"
            )
        if proof.device_id != lease.device_id:
            return False, "binding mismatch: proof device != lease device"
        if not proof.sealed:
            return False, "proof is not sealed; device may still open again"
        if not (0 <= proof.plays_consumed <= lease.plays_total):
            return False, "impossible consumed count for this lease"
        if not verify(device_secret(proof.device_id), proof.payload(), proof.signature):
            return False, "device signature invalid (tampered proof)"
        return True, "ok"

    def settle_return(self, proof: ReturnProof) -> DeskResult:
        """Verify a sealed proof and, if valid, reconcile credits."""
        rec = self.leases.get(proof.lease_id)
        if rec is None:
            return DeskResult(False, "refused: no such lease")

        ok, why = self._bind_check(proof)
        if not ok:
            # No premature reclaim: pool untouched, lease stays active.
            self._log(f"REJECT return {proof.lease_id}: {why}")
            return DeskResult(False, f"refused: {why}")

        if rec.state == LeaseState.REVOKED:
            return DeskResult(False, "already fully returned; double return refused")

        consumed = proof.plays_consumed
        final_credits_returned = rec.lease.plays_total - consumed
        # Reconcile against anything already released provisionally.
        delta = final_credits_returned - rec.provisional_released
        self.pool[rec.lease.chapter_id] += delta

        rec.plays_consumed = consumed
        rec.proof_seen = True
        rec.state = LeaseState.REVOKED
        if final_credits_returned != rec.provisional_released:
            recon = (
                f"reconciled {delta:+d} vs provisional {rec.provisional_released}"
            )
        else:
            recon = "matches provisional release"
        rec.provisional_released = final_credits_returned

        receipt = ReturnReceipt(
            receipt_id=self._next_receipt_id(),
            lease_id=proof.lease_id,
            chapter_id=rec.lease.chapter_id,
            device_id=proof.device_id,
            plays_total=rec.lease.plays_total,
            plays_consumed=consumed,
            credits_returned=final_credits_returned,
            status=ReturnStatus.SETTLED,
            note=f"sealed proof verified; {recon}",
            at=self.clock.now(),
        )
        rec.returned_receipt = receipt
        self._log(
            f"SETTLE {proof.lease_id} consumed={consumed}/{rec.lease.plays_total} "
            f"returned={final_credits_returned} pool_after="
            f"{self.pool[rec.lease.chapter_id]}"
        )
        return DeskResult(True, "return settled", receipt=receipt)

    def provisional_return(
        self,
        lease_id: str,
        online: bool,
        sealed_report: bool = False,
        reported_consumed: Optional[int] = None,
    ) -> DeskResult:
        """Receipt for a player that is not (yet) fully online.

        * offline, no count                      -> PENDING ("to be checked"),
                                                    reclaim NOTHING.
        * offline, sealed + reported count       -> LIMITED, reclaim the
                                                    provable remainder
                                                    provisionally.
        * offline, count but NOT sealed          -> PENDING (cannot safely
                                                    reclaim; device might open).
        * online                                 -> must present a sealed proof
                                                    to SETTLE instead.

        Provisional releases are tracked and reconciled by settle_return(), so
        the desk never double-counts when the real proof finally arrives.
        """
        rec = self.leases.get(lease_id)
        if rec is None:
            return DeskResult(False, "refused: no such lease")
        if rec.state == LeaseState.REVOKED:
            return DeskResult(False, "already fully returned")

        if online:
            return DeskResult(
                False, "player is online; present a sealed proof to settle"
            )

        if reported_consumed is None or not sealed_report:
            why = (
                "no consumption data"
                if reported_consumed is None
                else "device not sealed; it could still open, so nothing is reclaimable"
            )
            receipt = ReturnReceipt(
                receipt_id=self._next_receipt_id(),
                lease_id=lease_id,
                chapter_id=rec.lease.chapter_id,
                device_id=rec.lease.device_id,
                plays_total=rec.lease.plays_total,
                plays_consumed=0,
                credits_returned=0,
                status=ReturnStatus.PENDING,
                note=(
                    f"PENDING / to-be-checked ({why}). NO credits reclaimed; the "
                    "whole balance stays reserved until a sealed proof arrives."
                ),
                at=self.clock.now(),
            )
            rec.returned_receipt = receipt
            self._log(f"PENDING return {lease_id}: {why}; nothing reclaimed")
            return DeskResult(True, "pending (to-be-checked) receipt issued", receipt=receipt)

        # LIMITED: device is sealed (cannot open again) and reports a count.
        # Treat the device's own sealed counter as a LOWER BOUND on spend: more
        # spend only ever means fewer credits returned, so a low report can at
        # worst over-release. We therefore clamp defensively so the pool can
        # never go negative and never exceed the chapter total.
        k = max(0, min(reported_consumed, rec.lease.plays_total))
        prov = rec.lease.plays_total - k - rec.provisional_released
        prov = max(0, prov)
        # Hard cap: never let the pool exceed the chapter total (defence in depth
        # against a misbehaving device under-reporting its consumed count).
        prov = min(prov, max(0, self.total[rec.lease.chapter_id] - self.pool[rec.lease.chapter_id]))
        self.pool[rec.lease.chapter_id] += prov
        rec.provisional_released += prov
        receipt = ReturnReceipt(
            receipt_id=self._next_receipt_id(),
            lease_id=lease_id,
            chapter_id=rec.lease.chapter_id,
            device_id=rec.lease.device_id,
            plays_total=rec.lease.plays_total,
            plays_consumed=k,
            credits_returned=prov,
            status=ReturnStatus.LIMITED,
            note=(
                f"LIMITED provisional receipt: device sealed; {prov} credit(s) "
                f"released against reported consumed={k}. Balance may be adjusted "
                "when the full signed proof arrives."
            ),
            at=self.clock.now(),
        )
        rec.returned_receipt = receipt
        self._log(
            f"LIMITED return {lease_id}: reported_consumed={k} provisional_release={prov} "
            f"pool_after={self.pool[rec.lease.chapter_id]}"
        )
        return DeskResult(True, "limited (provisional) receipt issued", receipt=receipt)

    # ----------------------------------------------------------------- query
    def lease_state(self, lease_id: str) -> Optional[LeaseRecord]:
        return self.leases.get(lease_id)

    # -------------------------------------------------------------- transfer
    def transfer_lease(
        self,
        proof: ReturnProof,
        to_device_id: str,
        new_borrower_id: str,
        ttl: int,
    ) -> DeskResult:
        """Re-bind a sealed lease's REMAINING credits to another device.

        This is the "转机" (transfer-to-another-machine) path. It is legal only
        when the source device is SEALED -- i.e. it can no longer open once --
        which is exactly the rule that stops the old machine and the new machine
        from both spending the same credit. Consumed credits stay spent; the
        unconsumed remainder is re-reserved to the target WITHOUT returning to
        the pool (so no third party can grab it in between).
        """
        rec = self.leases.get(proof.lease_id)
        if rec is None:
            return DeskResult(False, "refused: no such lease")
        ok, why = self._bind_check(proof)
        if not ok:
            self._log(f"REJECT transfer {proof.lease_id}: {why}")
            return DeskResult(False, f"refused: {why}")
        if rec.state == LeaseState.REVOKED:
            return DeskResult(False, "refused: lease already closed")
        if rec.lease.scope == LeaseScope.FRONT_PREVIEW:
            return DeskResult(False, "refused: nothing to transfer from a preview-only lease")

        consumed = proof.plays_consumed
        remaining = rec.lease.plays_total - consumed
        if remaining <= 0:
            # Nothing left; this is effectively a normal (zero-return) settle.
            return self.settle_return(proof)

        now = self.clock.now()
        # Close the source; its credits do not go back to the pool, they are
        # carried straight to the target so no one else can take them.
        rec.plays_consumed = consumed
        rec.proof_seen = True
        rec.state = LeaseState.REVOKED

        new_lease = Lease(
            lease_id=self._next_lease_id(),
            chapter_id=rec.lease.chapter_id,
            device_id=to_device_id,
            borrower_id=new_borrower_id,
            plays_total=remaining,
            scope=LeaseScope.FULL,
            issued_at=now,
            expires_at=now + ttl,
        )
        new_lease = replace(new_lease, signature=mac(TOY_DESK_SECRET, new_lease.payload()))
        self.leases[new_lease.lease_id] = LeaseRecord(lease=new_lease)
        self._log(
            f"TRANSFER {proof.lease_id}->{new_lease.lease_id} ch={new_lease.chapter_id} "
            f"from={proof.device_id} to={to_device_id} consumed={consumed} "
            f"carried={remaining} pool_after={self.pool[new_lease.chapter_id]}"
        )
        receipt = ReturnReceipt(
            receipt_id=self._next_receipt_id(),
            lease_id=proof.lease_id,
            chapter_id=rec.lease.chapter_id,
            device_id=proof.device_id,
            plays_total=rec.lease.plays_total,
            plays_consumed=consumed,
            credits_returned=remaining,
            status=ReturnStatus.SETTLED,
            note=(
                f"transferred {remaining} remaining credit(s) to {to_device_id} as "
                f"{new_lease.lease_id}; source sealed so it cannot open again"
            ),
            at=now,
        )
        rec.returned_receipt = receipt
        return DeskResult(
            True, f"lease transferred to {to_device_id} as {new_lease.lease_id}",
            lease=new_lease, receipt=receipt,
        )
