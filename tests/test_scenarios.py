"""Scenario tests: offline windows, exclusive final authority, transfer, loss
ordering, one-person-one-vote, and the final re-countable / verifiable report.

Maps to the brief's acceptance narrative:

* 甲 (A) only goes east, and only during the outage; east *pre-granted*
  independent final authority -> A's service must complete (FINAL receipt while
  offline), and the loss the desk takes meanwhile must be shown as *pending*
  until the hall hands off.
* 乙 (B) decides on the spot which hall to use:
    - transfer card: both halls may take it first (暂存); counts only if the
      hand-off clears before the deadline; no final promise while offline.
    - exclusive-to-east: if B arrives east during the outage -> FINAL at once;
      if B arrives west -> wrong hall / cannot finish.
* 先挂失 vs 先收妥 must produce observably different outcomes.
* 每人一票 and 意见保密 must hold throughout.
"""

from __future__ import annotations

import json
import os
import unittest

from harness import WorldTestCase

from ballotbox import (
    BALLOT_COUNTED, BALLOT_PENDING, RECEIPT_FINAL, RECEIPT_TEMPORARY,
    build_report, verify_report,
)


class OfflineExclusiveTests(WorldTestCase):
    def test_A_who_only_goes_east_offline_completes(self):
        """甲已预订且东厅可独立接收的服务须能办成: FINAL receipt while offline."""
        tok = self.w.desk.issue("A", "EV-2024")
        # desk pre-grants *exclusive* final authority to east before the outage
        self.w.desk.grant_final_authority(tok.token_id, "east")
        # outage window begins on east before A arrives
        self.w.desk.set_hall_online("east", False)
        self.w.clock.tick(10)
        res = self.w.east.submit(tok.slip, "yes", self.w.event)
        self.assertTrue(res.ok)
        self.assertEqual(res.receipt.kind, RECEIPT_FINAL)
        self.assertEqual(self.w.east.recount(), {"yes": 1})

    def test_no_exclusive_authority_offline_cannot_finish(self):
        """Without pre-granted authority an offline hall may not promise FINAL."""
        tok = self.w.desk.issue("B", "EV-2024")
        self.w.desk.keep_transfer(tok.token_id)
        self.w.desk.set_hall_online("east", False)
        res = self.w.east.submit(tok.slip, "yes", self.w.event)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "PENDING_CONFIRM")
        self.assertEqual(self.w.east.recount(), {})

    def test_transfer_card_delivered_while_online_is_temporary(self):
        """流转证两边都可先收: 暂存回条, counts only after hand-off."""
        tok = self.w.desk.issue("B", "EV-2024")
        self.w.desk.keep_transfer(tok.token_id)
        res = self.w.west.submit(tok.slip, "yes", self.w.event)
        self.assertTrue(res.ok)
        self.assertEqual(res.receipt.kind, RECEIPT_TEMPORARY)
        # provisional -> not counted yet
        self.assertEqual(self.w.west.recount(), {})
        # hand-off before deadline -> counted
        self.w.desk.commit_handoff(tok.token_id, "west")
        self.assertEqual(self.w.west.recount(), {"yes": 1})

    def test_transfer_offline_deferred_not_promised(self):
        """不能在仍失联时许诺最终结果."""
        tok = self.w.desk.issue("B", "EV-2024")
        self.w.desk.keep_transfer(tok.token_id)
        self.w.desk.set_hall_online("west", False)
        res = self.w.west.submit(tok.slip, "yes", self.w.event)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "PENDING_CONFIRM")

    def test_exclusive_to_east_west_rejects(self):
        """乙若去了另一厅: 旧厅来不及交回的接收权会让她错过收件."""
        tok = self.w.desk.issue("B", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        res = self.w.west.submit(tok.slip, "yes", self.w.event)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "WRONG_HALL")

    def test_exclusive_to_east_east_final(self):
        tok = self.w.desk.issue("B", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        res = self.w.east.submit(tok.slip, "yes", self.w.event)
        self.assertTrue(res.ok)
        self.assertEqual(res.receipt.kind, RECEIPT_FINAL)

    def test_no_authority_yet_is_pending_confirm(self):
        """恢复本场凭条，东厅若尚未取得其最终接收权，相同投递只能待确认."""
        tok = self.w.desk.issue("A", "EV-2024")
        res = self.w.east.submit(tok.slip, "yes", self.w.event)  # no grant
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "PENDING_CONFIRM")


class LossOrderingTests(WorldTestCase):
    def test_loss_then_delivery_via_transfer_voids(self):
        """先挂失: transfer card loss is immediate; later delivery is void."""
        tok = self.w.desk.issue("C", "EV-2024")
        self.w.desk.keep_transfer(tok.token_id)
        lr = self.w.desk.report_loss(tok.token_id)
        self.assertTrue(lr.effective)
        res = self.w.east.submit(tok.slip, "no", self.w.event)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "VOIDED")

    def test_delivery_then_loss_keeps_ballot(self):
        """先收妥: exclusive east counts; a later loss cannot undo the vote."""
        tok = self.w.desk.issue("C", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        self.assertTrue(self.w.east.submit(tok.slip, "no", self.w.event).ok)
        lr = self.w.desk.report_loss(tok.token_id)
        self.assertFalse(lr.effective)
        self.assertEqual(lr.state, "CONSUMED")
        # one vote remains; a re-delivery is idempotent (still just one vote)
        self.assertEqual(self.w.east.recount(), {"no": 1})
        again = self.w.east.submit(tok.slip, "yes", self.w.event)
        self.assertEqual(again.reason, "idempotent")
        self.assertEqual(self.w.east.recount(), {"no": 1})

    def test_loss_pending_while_authority_hall_offline(self):
        """若一厅独占接收权且仍离线，会员台只能明示挂失待其交接."""
        tok = self.w.desk.issue("A", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        self.w.desk.set_hall_online("east", False)
        lr = self.w.desk.report_loss(tok.token_id)
        self.assertFalse(lr.effective)
        self.assertTrue(lr.pending)
        # desk must not claim it is already in force
        self.assertEqual(lr.state, "PENDING")

    def test_pending_loss_before_offline_vote_still_counts(self):
        """A arrives east offline after loss was filed: vote before hand-off stands,
        and the loss becomes effective only on hand-off."""
        tok = self.w.desk.issue("A", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        self.w.desk.set_hall_online("east", False)
        # filed while hall offline -> pending
        lr = self.w.desk.report_loss(tok.token_id)
        self.assertTrue(lr.pending)
        # A casts before the desk can tell east (no revoke reached it) -> final
        res = self.w.east.submit(tok.slip, "yes", self.w.event)
        self.assertTrue(res.ok)
        self.assertEqual(res.receipt.kind, RECEIPT_FINAL)
        # reconnect: hall syncs; vote counted -> loss now concluded, vote stands
        self.w.desk.set_hall_online("east", True)
        self.w.east.sync()
        concl = self.w.desk.loss_conclusion(tok.token_id)
        self.assertTrue(concl["finalized"])
        self.assertEqual(concl["finalized_hall"], "east")
        self.assertEqual(self.w.east.recount(), {"yes": 1})

    def test_pending_loss_after_offline_sync_no_vote_effective(self):
        tok = self.w.desk.issue("B", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        self.w.desk.set_hall_online("east", False)
        self.assertTrue(self.w.desk.report_loss(tok.token_id).pending)
        # no vote happened; reconnect and sync -> loss takes effect
        self.w.desk.set_hall_online("east", True)
        self.w.east.sync()
        concl = self.w.desk.loss_conclusion(tok.token_id)
        self.assertEqual(concl["loss_state"], "EFFECTIVE")
        # east now refuses the token
        res = self.w.east.submit(tok.slip, "yes", self.w.event)
        self.assertFalse(res.ok)

    def test_revoked_before_outage_rejected_offline(self):
        """An effective loss pushed *before* the outage is honoured offline."""
        tok = self.w.desk.issue("D", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        # effective now (hall online) -> revoke pushed to east
        self.assertTrue(self.w.desk.report_loss(tok.token_id).effective)
        self.w.desk.set_hall_online("east", False)
        res = self.w.east.submit(tok.slip, "no", self.w.event)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "VOIDED")


class OneVoteTests(WorldTestCase):
    def test_transfer_accepted_in_both_halls_counts_once(self):
        """流转证两边都可先收, 但每人一票: only the hand-off winner counts."""
        tok = self.w.desk.issue("B", "EV-2024")
        self.w.desk.keep_transfer(tok.token_id)
        r_east = self.w.east.submit(tok.slip, "yes", self.w.event)
        r_west = self.w.west.submit(tok.slip, "no", self.w.event)
        self.assertEqual(r_east.receipt.kind, RECEIPT_TEMPORARY)
        self.assertEqual(r_west.receipt.kind, RECEIPT_TEMPORARY)
        # choose west's hand-off
        self.w.desk.commit_handoff(tok.token_id, "west")
        self.assertEqual(self.w.west.recount(), {"no": 1})
        self.assertEqual(self.w.east.recount(), {})  # east provisional voided

    def test_replay_to_same_hall_is_idempotent(self):
        tok = self.w.desk.issue("A", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        r1 = self.w.east.submit(tok.slip, "yes", self.w.event)
        r2 = self.w.east.submit(tok.slip, "yes", self.w.event)
        self.assertTrue(r2.ok)
        self.assertEqual(r2.reason, "idempotent")
        self.assertEqual(r1.receipt.receipt_id, r2.receipt.receipt_id)
        self.assertEqual(self.w.east.recount(), {"yes": 1})

    def test_second_ballot_after_final_refused(self):
        tok = self.w.desk.issue("A", "EV-2024")
        self.w.desk.grant_final_authority(tok.token_id, "east")
        self.w.east.submit(tok.slip, "yes", self.w.event)
        # try the same card at the other hall -> already final
        res = self.w.west.submit(tok.slip, "no", self.w.event)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "ALREADY_FINAL")


class ReportTests(WorldTestCase):
    def _populate(self):
        # A: east exclusive final
        a = self.w.desk.issue("A", "EV-2024")
        self.w.desk.grant_final_authority(a.token_id, "east")
        self.w.east.submit(a.slip, "yes", self.w.event)
        # B: transfer, accepted west then handed off
        b = self.w.desk.issue("B", "EV-2024")
        self.w.desk.keep_transfer(b.token_id)
        self.w.west.submit(b.slip, "yes", self.w.event)
        self.w.desk.commit_handoff(b.token_id, "west")
        # C: delivered then loss (vote stands)
        c = self.w.desk.issue("C", "EV-2024")
        self.w.desk.grant_final_authority(c.token_id, "east")
        self.w.east.submit(c.slip, "no", self.w.event)
        self.w.desk.report_loss(c.token_id)
        return a, b, c

    def test_report_is_recountable_and_verifiable(self):
        self._populate()
        out = os.path.join(self.w.root, "report")
        paths = build_report(self.w.desk, out)
        self.assertTrue(all(os.path.exists(p) for p in paths.values()))

        with open(os.path.join(out, "tally.json"), encoding="utf-8") as fh:
            tally = json.load(fh)
        self.assertEqual(tally["by_choice"], {"yes": 2, "no": 1})

        verdict = verify_report(self.w.desk, out)
        self.assertTrue(verdict["ok"], verdict["problems"])
        self.assertEqual(verdict["recount"], {"yes": 2, "no": 1})

    def test_identity_and_choice_never_share_a_table(self):
        self._populate()
        out = os.path.join(self.w.root, "report")
        build_report(self.w.desk, out)
        with open(os.path.join(out, "identity.json"), encoding="utf-8") as fh:
            ident = json.load(fh)
        for row in ident["records"]:
            for bad in ("choice", "selection", "vote"):
                self.assertNotIn(bad, row)
        with open(os.path.join(out, "ballots.json"), encoding="utf-8") as fh:
            ballots = json.load(fh)
        for row in ballots["ballots"]:
            self.assertNotIn("member_id", row)
            self.assertNotIn("token_id", row)

    def test_loss_conclusions_in_report(self):
        a, b, c = self._populate()
        out = os.path.join(self.w.root, "report")
        build_report(self.w.desk, out)
        with open(os.path.join(out, "loss.json"), encoding="utf-8") as fh:
            loss = {r["token_id"]: r for r in json.load(fh)["conclusions"]}
        # C: delivered first -> loss consumed, vote stands
        self.assertEqual(loss[c.token_id]["loss_state"], "CONSUMED")
        self.assertTrue(loss[c.token_id]["finalized"])
        # A and B have no loss
        self.assertEqual(loss[a.token_id]["loss_state"], "NONE")

    def test_tamper_breaks_verification(self):
        self._populate()
        out = os.path.join(self.w.root, "report")
        build_report(self.w.desk, out)
        # corrupt a saved tally -> verify must notice the mismatch
        p = os.path.join(out, "tally.json")
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        data["by_choice"]["yes"] = 99
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.assertFalse(verify_report(self.w.desk, out)["ok"])


if __name__ == "__main__":
    unittest.main()
