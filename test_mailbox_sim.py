#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for mailbox_sim.py, runnable via ``python3 -m unittest``."""

import unittest

import mailbox_sim as m


ALL = m.CASES


class CorridorIndistinguishability(unittest.TestCase):
    """Corridor times and sizes must be identical across all four cases."""

    def _assert_identical(self, report):
        self.assertTrue(
            report.corridor_identical,
            msg=f"corridors differ: {report.corridors}",
        )
        corridors = {tuple(r) for r in report.corridors.values()}
        self.assertEqual(len(corridors), 1, msg=str(corridors))
        # Exactly the plan's signature, nothing more or less.
        self.assertEqual(next(iter(corridors)), report.plan_signature)

    def test_standard_b_plan_identical(self):
        self._assert_identical(m.replay_plan(m.plan_shift(m.RECIPIENT_B).plan))

    def test_standard_a_plan_identical(self):
        self._assert_identical(m.replay_plan(m.plan_shift(m.RECIPIENT_A).plan))

    def test_alternate_plan_identical(self):
        self._assert_identical(m.alternate_session().replay())

    def test_corridor_never_shows_zero(self):
        # No synthetic "empty beat" entries: only real bags are recorded.
        rep = m.alternate_session().replay()
        for corr in rep.corridors.values():
            for _beat, size in corr:
                self.assertGreater(size, 0)

    def test_contents_do_not_affect_corridor(self):
        # Same plan, different contents (blank fillers vs letters) -> same view.
        plan = m.plan_shift(m.RECIPIENT_B).plan
        sim = m.Simulator()
        res_none = sim.run(plan, frozenset())
        res_both = sim.run(plan, frozenset({m.RECIPIENT_A, m.RECIPIENT_B}))
        self.assertEqual(res_none.corridor, res_both.corridor)


class TwoDeadlines(unittest.TestCase):
    """The standard shift cannot meet both; must honour the chosen one."""

    def test_single_bag_rule(self):
        plan = m.plan_shift(m.RECIPIENT_B).plan
        self.assertEqual(len(plan.bags), 1)

    def test_protect_b(self):
        pr = m.plan_shift(m.RECIPIENT_B)
        self.assertTrue(pr.target_met)
        # Report the other recipient honestly: A arrives late in the both-case.
        case = frozenset({m.RECIPIENT_A, m.RECIPIENT_B})
        rep = m.replay_plan(pr.plan)
        a = rep.outcome(case, m.RECIPIENT_A)
        self.assertTrue(a.delivered)
        self.assertEqual(a.kind, "late")
        self.assertEqual(pr.other_outcome.recipient, m.RECIPIENT_A)
        self.assertEqual(pr.other_outcome.kind, "late")

    def test_protect_a(self):
        pr = m.plan_shift(m.RECIPIENT_A)
        self.assertTrue(pr.target_met)
        case = frozenset({m.RECIPIENT_A, m.RECIPIENT_B})
        rep = m.replay_plan(pr.plan)
        a = rep.outcome(case, m.RECIPIENT_A)
        self.assertEqual(a.kind, "on_time")
        b = rep.outcome(case, m.RECIPIENT_B)
        self.assertEqual(b.kind, "missed")

    def test_cannot_claim_both_in_standard(self):
        # Whatever the standard one-bag plan is, both deadlines never hold.
        for target in (m.RECIPIENT_A, m.RECIPIENT_B):
            rep = m.replay_plan(m.plan_shift(target).plan)
            case = frozenset({m.RECIPIENT_A, m.RECIPIENT_B})
            a_ok = rep.outcome(case, m.RECIPIENT_A).deadline_met
            b_ok = rep.outcome(case, m.RECIPIENT_B).deadline_met
            self.assertFalse(a_ok and b_ok)

    def test_no_empty_bags(self):
        # Never send an all-blank night: every plan actually releases letters
        # in the both-letters case.
        rep = m.replay_plan(m.plan_shift(m.RECIPIENT_B).plan)
        case = frozenset({m.RECIPIENT_A, m.RECIPIENT_B})
        self.assertTrue(any(bag.contents for bag in rep.case_results[case].bags))

    def test_honest_reporting_per_case(self):
        # Protect B: in every case where A sent a letter, A is honestly
        # reported late (delivered), never as on time or silently dropped.
        rep = m.replay_plan(m.plan_shift(m.RECIPIENT_B).plan)
        for case in m.CASES:
            if m.RECIPIENT_A in case:
                a = rep.outcome(case, m.RECIPIENT_A)
                self.assertTrue(a.arrived)
                self.assertTrue(a.delivered)
                self.assertEqual(a.kind, "late")
                self.assertFalse(a.deadline_met)

    def test_no_spurious_rejections(self):
        # A valid plan should never have a letter refused at signing time.
        for plan in (
            m.plan_shift(m.RECIPIENT_A).plan,
            m.plan_shift(m.RECIPIENT_B).plan,
        ):
            for case in m.CASES:
                self.assertEqual(m.simulate(plan, case).rejected, ())

    def test_filler_size_is_fixed(self):
        # Regardless of contents, the corridor slot count equals the planned
        # size (blank fillers make up the difference).
        plan = m.plan_shift(m.RECIPIENT_B).plan
        expected = plan.corridor_signature()
        for case in m.CASES:
            self.assertEqual(m.simulate(plan, case).corridor, expected)


class AlternateShift(unittest.TestCase):
    """Two windows of different beats/types can serve both recipients."""

    def test_both_on_time(self):
        rep = m.alternate_session().replay()
        case = frozenset({m.RECIPIENT_A, m.RECIPIENT_B})
        self.assertTrue(rep.outcome(case, m.RECIPIENT_A).deadline_met)
        self.assertTrue(rep.outcome(case, m.RECIPIENT_B).deadline_met)

    def test_utilities_use_distinct_bag_types(self):
        s = m.alternate_session()
        sizes = {(w.beat, w.label, w.bag_size) for w in s.windows}
        self.assertEqual(len({w.bag_size for w in s.windows}), 2)
        self.assertEqual(len(sizes), 2)

    def test_cancel_early_moves_a(self):
        s = m.alternate_session()
        c = m.Cancellation(2, "early-small", "corridor work")
        self.assertEqual(s.affected_by(c), (m.RECIPIENT_A,))

    def test_cancel_late_moves_b(self):
        s = m.alternate_session()
        c = m.Cancellation(3, "late-large", "staffing")
        self.assertEqual(s.affected_by(c), (m.RECIPIENT_B,))

    def test_reason_is_public_and_irrelevant(self):
        s = m.alternate_session()
        c1 = m.Cancellation(2, "early-small", "reason one")
        c2 = m.Cancellation(2, "early-small", "reason two")
        self.assertEqual(s.affected_by(c1), s.affected_by(c2))
        self.assertEqual(s.plan((c1,)), s.plan((c2,)))


class BOnlyShift(unittest.TestCase):
    def test_b_on_time(self):
        res = m.simulate(m.b_only_session().plan(), {m.RECIPIENT_B},
                         shift_id="b-only")
        self.assertTrue(res.outcome(m.RECIPIENT_B).deadline_met)


class CustodyReceipts(unittest.TestCase):
    """A receipt may only vouch for the bag/round/letter it really binds."""

    def setUp(self):
        self.plan = m.b_only_session().plan()
        self.sim = m.Simulator(shift_id="b-only")
        self.res = self.sim.run(self.plan, {m.RECIPIENT_B})
        self.bag = self.res.bags[-1]
        self.receipt = self.res.receipts[-1]
        self.ledger = m.CustodyLedger(current_shift="b-only")
        self.ledger.register(self.bag)
        self.b = m.Letter(m.RECIPIENT_B, "letter-for-B", m.ARRIVAL_B)

    def test_valid_receipt_signs_b(self):
        self.assertTrue(self.ledger.received(self.receipt, self.bag, self.b))

    def test_loading_before_letter_exists_invalid(self):
        # Bag genuinely holds B, but we claim it was loaded on beat 2, before
        # B was produced at beat 3.  The receipt must not certify that.
        early = m.Bag(
            shift_id=self.bag.shift_id, serial=self.bag.serial, beat=self.bag.beat,
            size=self.bag.size, capacity=self.bag.capacity,
            contents=self.bag.contents, loaded_beat=2,
        )
        early_receipt = m.BagReceipt(
            shift_id=early.shift_id, serial=early.serial, beat=early.beat,
            load_beat=2, digests=early.digests,
        )
        self.assertFalse(self.ledger.received(early_receipt, early, self.b))

    def test_previous_shifts_bag_rejected(self):
        # B really is inside this shift's beat-4 bag, but the dormitory
        # signed off the *previous* shift's beat-4 bag (same round, other
        # shift/serial).  A previous-shift receipt must not report B received.
        prev_shift_bag = m.Bag(
            shift_id="b-only-previous", serial="b-only-previous-b4", beat=4,
            size=2, capacity=2, contents=(self.b,), loaded_beat=4,
        )
        prev_shift_receipt = m.BagReceipt.from_bag(prev_shift_bag)
        self.assertFalse(
            self.ledger.received(prev_shift_receipt, prev_shift_bag, self.b)
        )
        # Even presenting *this* shift's valid receipt against the previous
        # shift's bag must fail (serial/shift mismatch).
        self.assertFalse(self.ledger.received(self.receipt, prev_shift_bag, self.b))

    def test_same_round_other_bag_rejected(self):
        # A bag of the right round but not the receipt's bag.
        other = m.Bag(
            shift_id=self.bag.shift_id, serial="other-b4", beat=4, size=2,
            capacity=2, contents=(self.b,), loaded_beat=4,
        )
        self.assertFalse(self.ledger.received(self.receipt, other, self.b))

    def test_letter_not_in_bag_rejected(self):
        stranger = m.Letter("X", "other", 1)
        self.assertFalse(self.ledger.received(self.receipt, self.bag, stranger))

    def test_receipt_cannot_vouch_for_absent_letter(self):
        # Receipt issued for the bag, but sign for a letter not in its digests.
        c_letter = m.Letter(m.RECIPIENT_A, "letter-for-A", m.ARRIVAL_A)
        self.assertFalse(self.ledger.received(self.receipt, self.bag, c_letter))


class CorridorLogIsAppendOnly(unittest.TestCase):
    def test_erase_forbidden(self):
        log = m.CorridorLog([(4, 2)])
        with self.assertRaises(PermissionError):
            log.erase()

    def test_records_persist(self):
        res = m.simulate(m.plan_shift(m.RECIPIENT_B).plan,
                         {m.RECIPIENT_A, m.RECIPIENT_B})
        log = res.corridor_log()
        self.assertEqual(log.records(), ((4, 2),))
        with self.assertRaises(PermissionError):
            log.erase()
        self.assertEqual(log.records(), ((4, 2),))


class HandshakeIndistinguishable(unittest.TestCase):
    def test_simulator_has_no_handshake_leak(self):
        # The Simulator exposes only corridor and outcomes; nothing that
        # varies with the mail case beyond the home view.
        sim = m.Simulator()
        plan = m.plan_shift(m.RECIPIENT_B).plan
        none = sim.run(plan, frozenset())
        both = sim.run(plan, frozenset({m.RECIPIENT_A, m.RECIPIENT_B}))
        self.assertEqual(none.corridor, both.corridor)
        # And the two views really are distinct objects.
        self.assertNotEqual(len(none.outcomes), len(both.outcomes))


class HelpRequestsArePrivate(unittest.TestCase):
    """Who asked for help in the first two beats never reaches the corridor."""

    def test_help_requests_do_not_change_corridor(self):
        plan = m.plan_shift(m.RECIPIENT_B).plan
        base = m.simulate(plan, {m.RECIPIENT_A})
        with_help = m.simulate(
            plan, {m.RECIPIENT_A},
            help_requests=[(m.RECIPIENT_A, 1), (m.RECIPIENT_B, 2)],
        )
        self.assertEqual(base.corridor, with_help.corridor)
        # ...but the home view remembers who asked, when.
        self.assertEqual(
            with_help.help_requests,
            ((m.RECIPIENT_A, 1), (m.RECIPIENT_B, 2)),
        )
        self.assertEqual(base.help_requests, ())

    def test_identical_corridor_across_all_request_patterns(self):
        plan = m.alternate_session().plan()
        patterns = [
            [],
            [(m.RECIPIENT_A, 1)],
            [(m.RECIPIENT_B, 2)],
            [(m.RECIPIENT_A, 1), (m.RECIPIENT_B, 2)],
        ]
        corridors = {
            m.simulate(plan, {m.RECIPIENT_A, m.RECIPIENT_B},
                       help_requests=p).corridor
            for p in patterns
        }
        self.assertEqual(len(corridors), 1, msg=str(corridors))


if __name__ == "__main__":
    unittest.main(verbosity=2)
