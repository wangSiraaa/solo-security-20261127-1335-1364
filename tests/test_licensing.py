"""Tests for the read-aloud licensing exercise.

Run:  python3 -m pytest -q      (or: python3 -m unittest discover -s tests)

These assert the *boundaries* the exercise cares about:
  * consumption boundary (one credit per first open, free in-window resume)
  * pause/resume scope
  * per-segment missing honesty
  * preview-only never cached / never offline
  * return proof binding + tamper rejection (no premature reclaim)
  * provisional pending/limited receipts
  * no double spend while a device could still open
  * transfer only after seal; remaining carried, consumed stays spent
  * front-preview-only cannot receive the whole chapter
"""
from __future__ import annotations

import unittest

from licensing.clock import SimClock
from licensing.content import chapter
from licensing.desk import Desk
from licensing.model import (
    LeaseScope,
    RESUME_WINDOW,
    ReturnProof,
    ReturnStatus,
)
from licensing.player import Player
from licensing.reader import ReaderCapability


def fresh():
    clock = SimClock(0)
    desk = Desk(clock, {"ch1": 8, "ch2": 8})
    a = Player("player-A", clock)
    b = Player("player-B", clock)
    a.online = b.online = True
    return clock, desk, a, b


class TestConsumptionBoundary(unittest.TestCase):
    def test_one_credit_per_first_open_and_free_resume(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch1", "player-A", "t", 3, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        a.prefetch(res.lease.lease_id)
        self.assertTrue(a.open(res.lease.lease_id, "ch1.s1"))
        st = a.states[res.lease.lease_id]
        self.assertEqual(st.plays_consumed, 1)
        # Pause + in-window resume: still 1 credit.
        a.pause(res.lease.lease_id)
        clock.advance(RESUME_WINDOW - 1)
        self.assertTrue(a.resume(res.lease.lease_id))
        self.assertEqual(a.states[res.lease.lease_id].plays_consumed, 1)

    def test_resume_after_window_refused_without_reopen(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch1", "player-A", "t", 3, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        a.prefetch(res.lease.lease_id)
        a.open(res.lease.lease_id, "ch1.s1")
        a.pause(res.lease.lease_id)
        clock.advance(RESUME_WINDOW + 1)
        self.assertFalse(a.resume(res.lease.lease_id))
        self.assertEqual(a.states[res.lease.lease_id].plays_consumed, 1)

    def test_credit_exhaustion_refuses_open(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch1", "player-A", "t", 2, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        a.prefetch(res.lease.lease_id)
        self.assertTrue(a.open(res.lease.lease_id, "ch1.s1"))
        self.assertTrue(a.open(res.lease.lease_id, "ch1.s2"))
        self.assertFalse(a.open(res.lease.lease_id, "ch1.s3"))

    def test_expiry_bounds_the_capability(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch1", "player-A", "t", 5, LeaseScope.FULL, ttl=100)
        a.bind(res.lease)
        a.prefetch(res.lease.lease_id)
        self.assertTrue(a.open(res.lease.lease_id, "ch1.s1"))
        clock.advance(101)
        # Time-bounded: expired even though credits remain.
        self.assertFalse(a.open(res.lease.lease_id, "ch1.s2"))
        self.assertFalse(a.play_live(res.lease.lease_id, "ch1.s0"))


class TestPerSegmentDisconnect(unittest.TestCase):
    def test_only_issued_segments_play_offline_rest_missing(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch2", "player-A", "t", 6, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        a.receive_until(res.lease.lease_id, 2)   # only s1, s2
        a.online = False
        self.assertTrue(a.open(res.lease.lease_id, "ch2.s1"))
        self.assertTrue(a.open(res.lease.lease_id, "ch2.s2"))
        self.assertFalse(a.open(res.lease.lease_id, "ch2.s3"))
        missing = a.missing_segments(res.lease.lease_id)
        self.assertEqual(missing, ["ch2.s3", "ch2.s4", "ch2.s5"])


class TestPreviewOnly(unittest.TestCase):
    def test_preview_only_live_but_never_cached_or_offline(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch1", "player-A", "t", 3, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        # Online live works.
        self.assertTrue(a.play_live(res.lease.lease_id, "ch1.s0"))
        # Never cached by live play.
        self.assertNotIn("ch1.s0", a.cached_segments(res.lease.lease_id))
        # Cannot open() a preview-only segment even online (must be live).
        self.assertFalse(a.open(res.lease.lease_id, "ch1.s0"))
        # Offline live refused.
        a.online = False
        self.assertFalse(a.play_live(res.lease.lease_id, "ch1.s0"))

    def test_front_preview_scope_cannot_receive_whole_chapter(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch1", "player-A", "t", 0,
                               LeaseScope.FRONT_PREVIEW, 1000)
        a.bind(res.lease)
        self.assertFalse(a.receive(res.lease.lease_id, "ch1.s1"))
        self.assertFalse(a.open(res.lease.lease_id, "ch1.s1"))
        self.assertTrue(a.play_live(res.lease.lease_id, "ch1.s0"))


class TestReturnProof(unittest.TestCase):
    def test_sealed_proof_settles_and_reclaims_remainder(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch1", "player-A", "t", 4, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        a.prefetch(res.lease.lease_id)
        a.open(res.lease.lease_id, "ch1.s1")
        a.seal(res.lease.lease_id)
        proof = a.return_proof(res.lease.lease_id)
        out = desk.settle_return(proof)
        self.assertTrue(out.ok)
        self.assertEqual(out.receipt.credits_returned, 3)
        self.assertEqual(out.receipt.status, ReturnStatus.SETTLED)

    def test_unsealed_proof_rejected(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch1", "player-A", "t", 4, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        a.open(res.lease.lease_id, "ch1.s1")
        # Forge an unsealed proof object directly.
        proof = ReturnProof(
            lease_id=res.lease.lease_id, chapter_id="ch1", device_id="player-A",
            plays_consumed=1, sealed=False, at=0, play_log=[],
        )
        out = desk.settle_return(proof)
        self.assertFalse(out.ok)

    def test_receipt_swap_to_other_chapter_rejected_no_premature_reclaim(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch1", "player-A", "t", 4, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        a.prefetch(res.lease.lease_id)
        a.open(res.lease.lease_id, "ch1.s1")
        before = desk.pool_status()["ch1"]["available"]
        a.seal(res.lease.lease_id)
        real = a.return_proof(res.lease.lease_id)
        swapped = ReturnProof(**{**real.__dict__, "chapter_id": "ch2"})
        out = desk.settle_return(swapped)
        self.assertFalse(out.ok)
        self.assertIn("binding mismatch", out.message)
        # ch1 pool untouched: no early reclaim.
        self.assertEqual(desk.pool_status()["ch1"]["available"], before)
        # Lease still not revoked, so it was not closed by the bad proof.
        self.assertNotEqual(desk.lease_state(res.lease.lease_id).state.value, "revoked")
        # The genuine proof then settles correctly.
        good = desk.settle_return(real)
        self.assertTrue(good.ok)
        self.assertEqual(good.receipt.credits_returned, 3)

    def test_tampered_signature_rejected(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch1", "player-A", "t", 4, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        a.prefetch(res.lease.lease_id)
        a.open(res.lease.lease_id, "ch1.s1")
        a.seal(res.lease.lease_id)
        real = a.return_proof(res.lease.lease_id)
        # Edit the consumed count, keep the old signature.
        tampered = ReturnProof(**{**real.__dict__, "plays_consumed": 0})
        out = desk.settle_return(tampered)
        self.assertFalse(out.ok)
        self.assertIn("signature", out.message)

    def test_double_return_refused(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch1", "player-A", "t", 4, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        a.seal(res.lease.lease_id)
        proof = a.return_proof(res.lease.lease_id)
        self.assertTrue(desk.settle_return(proof).ok)
        self.assertFalse(desk.settle_return(proof).ok)


class TestProvisionalReceipts(unittest.TestCase):
    def test_offline_no_data_is_pending_nothing_reclaimed(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch2", "player-A", "t", 6, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        before = desk.pool_status()["ch2"]["available"]
        out = desk.provisional_return(res.lease.lease_id, online=False)
        self.assertTrue(out.ok)
        self.assertEqual(out.receipt.status, ReturnStatus.PENDING)
        self.assertEqual(out.receipt.credits_returned, 0)
        self.assertEqual(desk.pool_status()["ch2"]["available"], before)

    def test_offline_unsealed_is_pending_even_with_count(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch2", "player-A", "t", 6, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        # Not sealed: device could still open, so nothing reclaimable.
        out = desk.provisional_return(res.lease.lease_id, online=False,
                                      sealed_report=False, reported_consumed=2)
        self.assertEqual(out.receipt.status, ReturnStatus.PENDING)
        self.assertEqual(out.receipt.credits_returned, 0)

    def test_offline_sealed_is_limited_then_reconciled(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch2", "player-A", "t", 6, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        a.receive_until(res.lease.lease_id, 2)
        a.open(res.lease.lease_id, "ch2.s1")
        a.open(res.lease.lease_id, "ch2.s2")
        a.seal(res.lease.lease_id)
        rep = a.sealed_report(res.lease.lease_id)
        lim = desk.provisional_return(res.lease.lease_id, online=False,
                                      sealed_report=rep["sealed"],
                                      reported_consumed=rep["plays_consumed"])
        self.assertEqual(lim.receipt.status, ReturnStatus.LIMITED)
        self.assertEqual(lim.receipt.credits_returned, 4)  # 6 - 2 consumed
        # Later, the real signed proof arrives (online). It must reconcile to the
        # same final balance, not double-count the provisional release.
        a.online = True
        proof = a.return_proof(res.lease.lease_id)
        settled = desk.settle_return(proof)
        self.assertTrue(settled.ok)
        self.assertEqual(settled.receipt.credits_returned, 4)
        # Pool reflects only the identical 4 credits once.
        self.assertEqual(desk.pool_status()["ch2"]["available"], 8 - 2)


class TestDoubleSpend(unittest.TestCase):
    def test_cannot_grant_more_than_available(self):
        clock, desk, a, _ = fresh()
        res = desk.issue_lease("ch1", "player-A", "t", 5, LeaseScope.FULL, 1000)
        a.bind(res.lease)
        # 3 available now; asking for 4 must be refused while A can still open.
        out = desk.issue_lease("ch1", "player-B", "b", 4, LeaseScope.FULL, 1000)
        self.assertFalse(out.ok)
        self.assertIn("outstanding", out.message)
        # Asking for exactly 3 is fine.
        ok = desk.issue_lease("ch1", "player-B", "b", 3, LeaseScope.FULL, 1000)
        self.assertTrue(ok.ok)

    def test_waiting_borrower_can_use_returned_credits(self):
        clock, desk, a, b = fresh()
        ra = desk.issue_lease("ch1", "player-A", "t", 4, LeaseScope.FULL, 1000)
        a.bind(ra.lease)
        a.prefetch(ra.lease.lease_id)
        a.open(ra.lease.lease_id, "ch1.s1")
        a.seal(ra.lease.lease_id)
        desk.settle_return(a.return_proof(ra.lease.lease_id))
        rb = desk.issue_lease("ch1", "player-B", "waiting", 3, LeaseScope.FULL, 1000)
        self.assertTrue(rb.ok)
        b.bind(rb.lease)
        b.prefetch(rb.lease.lease_id)
        self.assertTrue(b.open(rb.lease.lease_id, "ch1.s1"))
        self.assertTrue(b.open(rb.lease.lease_id, "ch1.s2"))


class TestTransfer(unittest.TestCase):
    def test_transfer_requires_seal_and_carries_remaining(self):
        clock, desk, a, b = fresh()
        ra = desk.issue_lease("ch1", "player-A", "t", 4, LeaseScope.FULL, 1000)
        a.bind(ra.lease)
        a.prefetch(ra.lease.lease_id)
        a.open(ra.lease.lease_id, "ch1.s1")
        a.seal(ra.lease.lease_id)
        proof = a.return_proof(ra.lease.lease_id)
        out = desk.transfer_lease(proof, to_device_id="player-B",
                                  new_borrower_id="transfer-borrower", ttl=1000)
        self.assertTrue(out.ok)
        self.assertEqual(out.lease.plays_total, 3)   # 4 - 1 consumed
        # Source lease is closed.
        self.assertEqual(desk.lease_state(ra.lease.lease_id).state.value, "revoked")
        # B can actually use the carried credits.
        b.bind(out.lease)
        b.prefetch(out.lease.lease_id)
        self.assertTrue(b.open(out.lease.lease_id, "ch1.s2"))
        # The consumed credit did not come back to the pool.
        # ch1: started 8, A spent 1, carried 3 to B (still reserved), so avail=4.
        self.assertEqual(desk.pool_status()["ch1"]["available"], 4)

    def test_transfer_unsealed_refused(self):
        clock, desk, a, _ = fresh()
        ra = desk.issue_lease("ch1", "player-A", "t", 4, LeaseScope.FULL, 1000)
        a.bind(ra.lease)
        a.open(ra.lease.lease_id, "ch1.s1")
        from licensing.model import ReturnProof as RP
        forged = RP(lease_id=ra.lease.lease_id, chapter_id="ch1",
                    device_id="player-A", plays_consumed=1, sealed=False,
                    at=0, play_log=[])
        out = desk.transfer_lease(forged, to_device_id="player-B",
                                  new_borrower_id="x", ttl=1000)
        self.assertFalse(out.ok)


class TestReaderCapability(unittest.TestCase):
    def test_reader_verbs_only(self):
        clock, desk, a, _ = fresh()
        ra = desk.issue_lease("ch1", "player-A", "t", 4, LeaseScope.FULL, 1000)
        a.bind(ra.lease)
        r = ReaderCapability(a, desk)
        # Play a normal segment through the capability.
        self.assertTrue(r.play(ra.lease.lease_id, "ch1.s1"))
        # Preview-only routes to live automatically.
        self.assertTrue(r.play(ra.lease.lease_id, "ch1.s0"))
        # Return via capability (online -> settled).
        out = r.return_(ra.lease.lease_id)
        self.assertTrue(out.ok)
        self.assertEqual(out.receipt.status, ReturnStatus.SETTLED)


def conserved(desk: Desk, chapter_id: str) -> int:
    """total == available + outstanding(active) + spent(all)."""
    total = desk.total[chapter_id]
    avail = desk.pool[chapter_id]
    outstanding = 0
    spent = 0
    for rec in desk.leases.values():
        if rec.lease.chapter_id != chapter_id:
            continue
        spent += rec.plays_consumed
        if rec.state.value == "active":
            outstanding += (
                rec.lease.plays_total - rec.plays_consumed - rec.provisional_released
            )
    return avail + outstanding + spent


class TestConservation(unittest.TestCase):
    def _assert_conserved(self, desk, ch):
        self.assertEqual(
            conserved(desk, ch), desk.total[ch],
            f"credit conservation broken for {ch}: {desk.pool_status()[ch]}",
        )

    def test_conservation_across_full_lifecycle(self):
        clock, desk, a, b = fresh()
        self._assert_conserved(desk, "ch1")
        ra = desk.issue_lease("ch1", "player-A", "t", 4, LeaseScope.FULL, 1000)
        a.bind(ra.lease)
        self._assert_conserved(desk, "ch1")
        a.prefetch(ra.lease.lease_id)
        a.open(ra.lease.lease_id, "ch1.s1")
        self._assert_conserved(desk, "ch1")
        a.seal(ra.lease.lease_id)
        desk.settle_return(a.return_proof(ra.lease.lease_id))
        self._assert_conserved(desk, "ch1")
        rb = desk.issue_lease("ch1", "player-B", "w", 3, LeaseScope.FULL, 1000)
        b.bind(rb.lease)
        self._assert_conserved(desk, "ch1")

    def test_conservation_with_provisional_then_settle(self):
        clock, desk, a, _ = fresh()
        ra = desk.issue_lease("ch2", "player-A", "t", 6, LeaseScope.FULL, 1000)
        a.bind(ra.lease)
        a.receive_until(ra.lease.lease_id, 2)
        a.open(ra.lease.lease_id, "ch2.s1")
        a.open(ra.lease.lease_id, "ch2.s2")
        a.seal(ra.lease.lease_id)
        self._assert_conserved(desk, "ch2")
        rep = a.sealed_report(ra.lease.lease_id)
        desk.provisional_return(ra.lease.lease_id, online=False,
                                sealed_report=rep["sealed"],
                                reported_consumed=rep["plays_consumed"])
        self._assert_conserved(desk, "ch2")
        a.online = True
        desk.settle_return(a.return_proof(ra.lease.lease_id))
        self._assert_conserved(desk, "ch2")

    def test_conservation_with_transfer(self):
        clock, desk, a, b = fresh()
        ra = desk.issue_lease("ch1", "player-A", "t", 4, LeaseScope.FULL, 1000)
        a.bind(ra.lease)
        a.prefetch(ra.lease.lease_id)
        a.open(ra.lease.lease_id, "ch1.s1")
        self._assert_conserved(desk, "ch1")
        a.seal(ra.lease.lease_id)
        out = desk.transfer_lease(a.return_proof(ra.lease.lease_id),
                                  to_device_id="player-B",
                                  new_borrower_id="nb", ttl=1000)
        self.assertTrue(out.ok)
        self._assert_conserved(desk, "ch1")


class TestProvisionalSafety(unittest.TestCase):
    """A device must not be able to over-release by misreporting its counter."""

    def test_under_report_is_reconciled_down_on_settle(self):
        clock, desk, a, _ = fresh()
        ra = desk.issue_lease("ch2", "player-A", "t", 6, LeaseScope.FULL, 1000)
        a.bind(ra.lease)
        a.receive_until(ra.lease.lease_id, 3)
        a.open(ra.lease.lease_id, "ch2.s1")
        a.open(ra.lease.lease_id, "ch2.s2")   # truly consumed 2
        a.seal(ra.lease.lease_id)
        # Device dishonestly shows only 1.
        lim = desk.provisional_return(ra.lease.lease_id, online=False,
                                      sealed_report=True, reported_consumed=1)
        self.assertEqual(lim.receipt.credits_returned, 5)  # over-released
        # Real signed proof says 2; settlement claws the extra back.
        a.online = True
        settled = desk.settle_return(a.return_proof(ra.lease.lease_id))
        self.assertTrue(settled.ok)
        self.assertEqual(settled.receipt.credits_returned, 4)
        self.assertEqual(desk.pool_status()["ch2"]["available"], 8 - 2)

    def test_provisional_cannot_drive_pool_above_total(self):
        clock, desk, a, _ = fresh()
        ra = desk.issue_lease("ch2", "player-A", "t", 6, LeaseScope.FULL, 1000)
        a.bind(ra.lease)
        a.seal(ra.lease.lease_id)
        # Device claims 0 consumed (impossible after real play, but adversarial).
        desk.provisional_return(ra.lease.lease_id, online=False,
                                sealed_report=True, reported_consumed=0)
        self.assertLessEqual(desk.pool_status()["ch2"]["available"], 8)
        # Repeated provisional with the same claim must not release again.
        before = desk.pool_status()["ch2"]["available"]
        desk.provisional_return(ra.lease.lease_id, online=False,
                                sealed_report=True, reported_consumed=0)
        self.assertEqual(desk.pool_status()["ch2"]["available"], before)


if __name__ == "__main__":
    unittest.main()
