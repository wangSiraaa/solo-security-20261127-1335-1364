"""Edge cases: queued grants on reconnect, deadline cut-off, finalized guards."""

from __future__ import annotations

import unittest

from harness import WorldTestCase

from ballotbox import NotAllowed, RECEIPT_FINAL, Rejected


class ReconnectTests(WorldTestCase):
    def test_grant_queued_while_offline_delivered_on_reconnect(self):
        """A grant pushed while the hall is unreachable is applied on reconnect."""
        tok = self.w.desk.issue("A", "EV-2024")
        self.w.desk.set_hall_online("east", False)
        # grant arrives during the outage -> queued, hall cannot see it yet
        self.w.desk.grant_final_authority(tok.token_id, "east")
        res = self.w.east.submit(tok.slip, "yes", self.w.event)
        self.assertFalse(res.ok)          # not known offline
        self.assertEqual(res.reason, "PENDING_CONFIRM")
        # reconnect flushes the queued grant; now east may finalize
        self.w.desk.set_hall_online("east", True)
        res2 = self.w.east.submit(tok.slip, "yes", self.w.event)
        self.assertTrue(res2.ok)
        self.assertEqual(res2.receipt.kind, RECEIPT_FINAL)

    def test_handoff_after_deadline_rejected(self):
        """断线前收妥留；截止后交接不再计入."""
        tok = self.w.desk.issue("B", "EV-2024")
        self.w.desk.keep_transfer(tok.token_id)
        self.w.west.submit(tok.slip, "yes", self.w.event)  # temp, before deadline
        self.w.desk.close_event("EV-2024")                 # deadline passed
        with self.assertRaises(Rejected):
            self.w.desk.commit_handoff(tok.token_id, "west")
        # provisional never counts
        self.assertEqual(self.w.west.recount(), {})

    def test_grant_after_final_refused(self):
        tok = self.w.desk.issue("A", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        self.w.east.submit(tok.slip, "yes", self.w.event)
        with self.assertRaises(NotAllowed):
            self.w.desk.grant_final_authority(tok.token_id, "west")

    def test_wrong_hall_during_outage_knows_from_preoutage_push(self):
        """A card exclusively granted to east: west learnt this before the
        outage, so even offline it correctly refuses with WRONG_HALL (it cannot
        and need not finalize a card it does not own)."""
        tok = self.w.desk.issue("B", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")  # pushed to west
        self.w.desk.set_hall_online("west", False)
        res = self.w.west.submit(tok.slip, "yes", self.w.event)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "WRONG_HALL")

    def test_closed_event_rejects_delivery(self):
        tok = self.w.desk.issue("A", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        self.w.desk.close_event("EV-2024")
        res = self.w.east.submit(tok.slip, "yes", self.w.event)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "EVENT_CLOSED")

    def test_reconnect_without_vote_voids_after_pending_loss(self):
        tok = self.w.desk.issue("C", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        self.w.desk.set_hall_online("east", False)
        self.assertTrue(self.w.desk.report_loss(tok.token_id).pending)
        self.w.desk.set_hall_online("east", True)
        self.w.east.sync()
        # loss now effective; a delivery is voided, nothing counted
        res = self.w.east.submit(tok.slip, "yes", self.w.event)
        self.assertFalse(res.ok)
        self.assertEqual(self.w.east.recount(), {})


if __name__ == "__main__":
    unittest.main()
