"""Slip authentication, event scoping, isolation and confidentiality."""

from __future__ import annotations

import json
import os
import unittest

from harness import WorldTestCase

from ballotbox import (
    AlreadyIssued, EventMismatch, ForeignPost, InvalidSignature, Rejected,
    tokens,
)


class TokenTests(WorldTestCase):
    def test_slip_has_no_member_id(self):
        tok = self.w.desk.issue("A", "EV-2024")
        self.assertNotIn(":A:", tok.slip)
        self.assertEqual(self.w.desk.tokens[tok.token_id].member_id, "A")

    def test_forged_slip_rejected_before_recording(self):
        tok = self.w.desk.issue("A", "EV-2024")
        forged = tok.slip[:-4] + "dead"  # tamper the MAC
        before = len(self.w.east.ledger)
        res = self.w.east.submit(forged, "yes", self.w.event)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "InvalidSignature")
        self.assertFalse(any(r["type"] == "ballot"
                             for r in self.w.east.ledger))
        self.assertEqual(len(self.w.east.ledger), before + 1)  # only a reject

    def test_previous_event_slip_rejected_before_delivery(self):
        """把基准凭条的活动改成上一场，应在投递前发现证不符."""
        self.w.desk.register_event("EV-2023", choices=("yes", "no"))
        old_tok = self.w.desk.issue("A", "EV-2023")
        res = self.w.east.submit(old_tok.slip, "yes", self.w.event)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "EventMismatch")
        self.assertFalse(any(r["type"] == "ballot"
                             for r in self.w.east.ledger))

    def test_slip_verifies_against_its_own_event(self):
        old = self.w.desk.register_event("EV-2023", choices=("yes", "no"))
        tok = self.w.desk.issue("A", "EV-2023")
        self.assertEqual(tokens.verify_slip(tok.slip, old).token_id, tok.token_id)
        with self.assertRaises(EventMismatch):
            tokens.verify_slip(tok.slip, self.w.event)

    def test_unknown_key_slip_fails(self):
        tok = self.w.desk.issue("A", "EV-2024")
        parts = tok.slip.split(":")
        forged = ":".join(parts[:4] + ["f" * 64])
        with self.assertRaises(InvalidSignature):
            tokens.verify_slip(forged, self.w.event)

    def test_clerk_cannot_read_other_hall_post(self):
        with self.assertRaises(ForeignPost):
            self.w.west.read_post(
                os.path.join(self.w.east.directory, "hall.jsonl"))
        # own post is readable
        self.w.west.read_post(os.path.join(self.w.west.directory, "hall.jsonl"))

    def test_hall_ballot_table_has_no_member_or_token(self):
        tok = self.w.desk.issue("A", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        self.w.east.submit(tok.slip, "yes", self.w.event)
        for b in self.w.east.ballots():
            self.assertNotIn("member_id", b)
            self.assertNotIn("token_id", b)
            self.assertIn("choice", b)
            self.assertIn("ballot_id", b)

    def test_second_card_for_same_member_refused(self):
        self.w.desk.issue("A", "EV-2024")
        with self.assertRaises(AlreadyIssued):
            self.w.desk.issue("A", "EV-2024")

    def test_ineligible_member_cannot_get_card(self):
        with self.assertRaises(Rejected):
            self.w.desk.issue("Z", "EV-2024")

    def test_desk_ledger_never_contains_choice(self):
        tok = self.w.desk.issue("A", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        self.w.east.submit(tok.slip, "yes", self.w.event)
        # The event itself legitimately lists its choice *labels* once; ignore
        # that setup record and audit every per-vote record.
        for rec in self.w.desk.ledger:
            if rec["type"] == "event":
                continue
            blob = json.dumps(rec["data"])
            self.assertNotIn('"yes"', blob)
            self.assertNotIn("choice", blob)


if __name__ == "__main__":
    unittest.main()
