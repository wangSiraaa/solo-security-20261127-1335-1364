"""End-to-end walkthroughs of the reading-room exercises.

Each test follows one of the narrative beats and reconciles the two things the
exercise asks us to prove: the *real* spend produced, and what the wall ends
up showing.  Tests only use public numbers and the mock mechanism; nothing
estimates privacy strength from samples.
"""

import unittest

from privacydesk.board import DisplayBoard, PeriodMismatch, PublicationIndex
from privacydesk.catalog import CheckinCatalog
from privacydesk.core import Budget, BudgetExceeded, Exhibit, Mechanism
from privacydesk.query_desk import QueryDesk


def three_day_catalog():
    """Six slots over three days; h1 is the busy ("hot") household."""
    cat = CheckinCatalog()
    cat.register_slot(1, "d1-morning", ["h1", "h2"])
    cat.register_slot(1, "d1-evening", ["h1", "h3"])
    cat.register_slot(1, "d2-morning", ["h1", "h4"])
    cat.register_slot(1, "d2-evening", ["h2", "h5"])
    cat.register_slot(1, "d3-morning", ["h2", "h6"])
    cat.register_slot(1, "d3-evening", ["h3", "h4"])
    return cat


DAY_1 = [(1, "d1-morning"), (1, "d1-evening")]
DAY_2 = [(1, "d2-morning"), (1, "d2-evening")]
DAY_3 = [(1, "d3-morning"), (1, "d3-evening")]


class TwoQueryOrdersTests(unittest.TestCase):
    """Ask day-3-total first, then two of those days — and vice versa."""

    def test_total_then_subrange_via_direct_route(self):
        cat = three_day_catalog()
        budget = Budget(default=4.0)
        desk = QueryDesk(budget, Mechanism(), cost=1.0)
        board = DisplayBoard(current_period=1)

        # 1) three-day total, then a two-day sub-range (a genuinely new ask)
        total = desk.ask_detailed("q-3day", cat.slots(DAY_1 + DAY_2 + DAY_3))
        desk.ack(total.record.release_id)
        sub = desk.ask_detailed("q-day1-2", cat.slots(DAY_1 + DAY_2))
        desk.ack(sub.record.release_id)

        board.mount(Exhibit.from_record(total.record))
        board.mount(Exhibit.from_record(sub.record))

        # real spend: each release charged its affected households once
        self.assertEqual(budget.spent("h1"), 2.0)  # in both releases
        total_spend = sum(budget.snapshot().values())
        self.assertGreater(total_spend, 0.0)

        # two distinct paid releases -> two independent observations
        stmt = board.trend_statement([total.record.release_id, sub.record.release_id])
        self.assertEqual(stmt.independent_observations, 2)
        self.assertTrue(stmt.signed)

        # re-send of the exact three-day total adds nothing
        before = budget.snapshot()
        resend = desk.ask_detailed("q-3day", cat.slots(DAY_1 + DAY_2 + DAY_3))
        self.assertEqual(resend.charges, {})
        self.assertEqual(budget.snapshot(), before)

    def test_subrange_then_total_is_same_real_spend(self):
        cat = three_day_catalog()
        budget = Budget(default=4.0)
        desk = QueryDesk(budget, Mechanism(), cost=1.0)

        desk.ack(desk.ask_detailed("q-day1-2", cat.slots(DAY_1 + DAY_2)).record.release_id)
        desk.ack(desk.ask_detailed("q-3day", cat.slots(DAY_1 + DAY_2 + DAY_3)).record.release_id)

        # same set of releases, so the same real spend as the other order
        self.assertEqual(budget.spent("h1"), 2.0)

    def test_published_table_route_has_no_extra_spend_on_new_slices(self):
        cat = three_day_catalog()
        budget = Budget(default=4.0)
        desk = QueryDesk(budget, Mechanism(), cost=1.0)
        board = DisplayBoard(current_period=1)

        table = desk.ask_table("q-summer", cat.slots(DAY_1 + DAY_2 + DAY_3))
        after_table = budget.snapshot()

        whole = table.compose(DAY_1 + DAY_2 + DAY_3)
        part = table.compose(DAY_1 + DAY_2)          # a different slice, free
        board.mount(whole)
        board.mount(part)

        self.assertEqual(budget.snapshot(), after_table)  # recompose costs nothing
        # but both views share one perturbation -> not two independent reads
        stmt = board.trend_statement([whole.exhibit_id, part.exhibit_id])
        self.assertEqual(stmt.independent_observations, 1)
        self.assertFalse(stmt.signed)


class LostAckRetryTests(unittest.TestCase):
    def test_retry_reuses_the_same_figure_and_spends_once(self):
        cat = three_day_catalog()
        budget = Budget(default=4.0)
        desk = QueryDesk(budget, Mechanism(), cost=1.0)
        slots = cat.slots(DAY_1)

        signed = desk.ask_detailed("q-day1", slots)
        figure = signed.record.public_value
        release_id = signed.record.release_id
        # the ack is lost in transit: retry reconciles back to zero spend
        self.assertIsNone(desk.retry("q-day1"))
        self.assertEqual(budget.spent("h1"), 0.0)
        self.assertEqual(sum(budget.snapshot().values()), 0.0)

        # honest retry hands back the *same* figure, then spends exactly once
        again = desk.ask_detailed("q-day1", slots)
        self.assertEqual(again.record.release_id, release_id)
        self.assertEqual(again.record.public_value, figure)
        desk.ack(again.record.release_id)

        # day 1 touches h1,h2,h3; each is charged once, so 3.0 total
        self.assertEqual(budget.spent("h1"), 1.0)
        self.assertEqual(budget.spent("h2"), 1.0)
        self.assertEqual(budget.spent("h3"), 1.0)
        self.assertEqual(sum(budget.snapshot().values()), 3.0)

        board = DisplayBoard(current_period=1)
        board.mount(Exhibit.from_record(again.record))
        self.assertEqual([e.public_value for e in board.exhibits()], [figure])


class BudgetExhaustionTests(unittest.TestCase):
    def test_two_slots_with_exhausted_household_refused_for_new_spend(self):
        cat = three_day_catalog()
        # h1 (in d1 and d2) may only pay for one release
        budget = Budget(caps={"h1": 1.0}, default=3.0)
        desk = QueryDesk(budget, Mechanism(), cost=1.0)

        first = desk.ask_detailed("q-first", cat.slots(DAY_1))
        desk.ack(first.record.release_id)
        self.assertEqual(budget.spent("h1"), 1.0)

        # a *new* base release spanning two slots that both include h1 would
        # need new h1 spend -> must be refused, not silently re-rolled
        with self.assertRaises(BudgetExceeded) as ctx:
            desk.ask_detailed("q-second", cat.slots(DAY_1 + DAY_2))
        self.assertEqual(ctx.exception.household, "h1")
        self.assertEqual(budget.spent("h1"), 1.0)  # nothing reserved

        # re-sending what was already published is still fine (no new spend)
        resend = desk.ask_detailed("q-first", cat.slots(DAY_1))
        self.assertEqual(resend.charges, {})


class PeriodRolloverTests(unittest.TestCase):
    def test_board_rejects_last_period_version_while_stats_has_new_one(self):
        cat = CheckinCatalog()
        cat.register_slot(1, "p1", ["h1", "h2"])
        cat.register_slot(2, "p2", ["h1", "h2"])
        desk = QueryDesk(Budget(default=3.0), Mechanism(), cost=1.0)
        index = PublicationIndex()

        last_period = desk.ask_detailed("q-p1", cat.slots([(1, "p1")]))
        desk.ack(last_period.record.release_id)

        # a legitimate *current* release exists at the stats end
        current = desk.ask_detailed("q-p2", cat.slots([(2, "p2")]))
        desk.ack(current.record.release_id)
        self.assertIsNotNone(desk.ledger.existing("q-p2"))

        # both publications are stamped at the stats end (metadata only)
        old_exhibit = desk.deliver_exhibit("q-p1", index)
        new_exhibit = desk.deliver_exhibit("q-p2", index)

        board = DisplayBoard(current_period=2, index=index)
        # the wall is fed the previous period's version -> cannot hang it
        self.assertFalse(board.try_mount(old_exhibit))
        self.assertEqual(board.exhibits(), [])
        # the genuine current one is accepted
        self.assertTrue(board.try_mount(new_exhibit))
        self.assertEqual([e.period for e in board.exhibits()], [2])

    def test_relabeling_last_period_exhibit_is_refused(self):
        cat = CheckinCatalog()
        cat.register_slot(1, "p1", ["h1", "h2"])
        cat.register_slot(2, "p2", ["h1", "h2"])
        desk = QueryDesk(Budget(default=3.0), Mechanism(), cost=1.0)
        index = PublicationIndex()
        desk.ack(desk.ask_detailed("q-p1", cat.slots([(1, "p1")])).record.release_id)
        old = desk.deliver_exhibit("q-p1", index)

        # forge the period label to pretend it is this period's version
        forged = Exhibit(
            exhibit_id=old.exhibit_id, lineage=old.lineage, period=2,
            slots=old.slots, public_value=old.public_value,
            fingerprint=old.fingerprint,
        )
        board = DisplayBoard(current_period=2, index=index)
        with self.assertRaises(PeriodMismatch):
            board.mount(forged)


class RisingPairTests(unittest.TestCase):
    def test_two_independent_releases_that_both_rise_can_sign(self):
        cat = CheckinCatalog()
        # low traffic on day 1, high traffic on day 3, disjoint households so
        # both fit the cap and the counts genuinely rise.
        cat.register_slot(1, "d1", ["h1"])
        cat.register_slot(1, "d3", ["h2", "h3", "h4", "h5", "h6"])
        budget = Budget(default=3.0)
        desk = QueryDesk(budget, Mechanism(), cost=1.0)

        early = desk.ask_detailed("q-early", cat.slots([(1, "d1")]))
        desk.ack(early.record.release_id)
        late = desk.ask_detailed("q-late", cat.slots([(1, "d3")]))
        desk.ack(late.record.release_id)

        board = DisplayBoard(current_period=1)
        board.mount(Exhibit.from_record(early.record))
        board.mount(Exhibit.from_record(late.record))

        stmt = board.trend_statement([early.record.release_id, late.record.release_id])
        self.assertTrue(stmt.signed)
        self.assertEqual(stmt.independent_observations, 2)
        self.assertEqual(stmt.direction, "rising")
        self.assertIn("rising", stmt.text)

    def test_shared_evidence_pair_cannot_be_signed_even_if_rising(self):
        cat = three_day_catalog()
        desk = QueryDesk(Budget(default=4.0), Mechanism(), cost=1.0)
        table = desk.ask_table("q-window", cat.slots(DAY_1 + DAY_2 + DAY_3))

        # whole window and a sub-window from the same publication
        whole = table.compose(DAY_1 + DAY_2 + DAY_3)
        part = table.compose(DAY_1)
        board = DisplayBoard(current_period=1)
        board.mount(whole)
        board.mount(part)

        stmt = board.trend_statement([whole.exhibit_id, part.exhibit_id])
        self.assertFalse(stmt.signed)
        self.assertEqual(stmt.independent_observations, 1)


class NewBatchEntranceTests(unittest.TestCase):
    def test_new_records_enter_through_the_ordinary_door(self):
        cat = CheckinCatalog()
        cat.register_slot(1, "d1", ["h1", "h2"])
        budget = Budget(default=3.0)
        desk = QueryDesk(budget, Mechanism(), cost=1.0)
        desk.ack(desk.ask_detailed("q-a", cat.slots([(1, "d1")])).record.release_id)

        # a new batch arrives via the normal registration path, then is asked
        # through the same desk with no special handling
        cat.register_batch([(1, "d2", ("h3", "h4"))])
        signed = desk.ask_detailed("q-b", cat.slots([(1, "d2")]))
        desk.ack(signed.record.release_id)

        self.assertEqual(set(signed.charges), {"h3", "h4"})
        self.assertGreater(signed.record.public_value, 0)
        self.assertEqual(budget.spent("h3"), 1.0)

    def test_synthetic_batch_uses_normal_path(self):
        cat = CheckinCatalog()
        created = cat.synthetic_batch(1, ["s1", "s2"], ["h1", "h2", "h3"],
                                      per_slot=2, seed=7)
        self.assertEqual(created, 2)
        desk = QueryDesk(Budget(default=5.0), Mechanism(), cost=1.0)
        signed = desk.ask_detailed("q-syn", cat.slots([(1, "s1"), (1, "s2")]))
        desk.ack(signed.record.release_id)
        self.assertTrue(signed.record.slots)


if __name__ == "__main__":
    unittest.main()
