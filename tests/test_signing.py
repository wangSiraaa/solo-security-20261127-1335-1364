import unittest

from privacydesk.catalog import CheckinCatalog
from privacydesk.core import (
    Budget,
    BudgetExceeded,
    Mechanism,
)
from privacydesk.query_desk import QueryDesk


def make_catalog():
    """A few households over three days (slots) — no other fixtures."""
    cat = CheckinCatalog()
    # day 1: households a, b     (busy family = "hot" household a)
    cat.register_slot(1, "d1-morning", ["h1", "h2"])
    cat.register_slot(1, "d1-evening", ["h1", "h3"])
    # day 2: households a, c
    cat.register_slot(1, "d2-morning", ["h1", "h4"])
    cat.register_slot(1, "d2-evening", ["h2", "h5"])
    # day 3: households b, d
    cat.register_slot(1, "d3-morning", ["h2", "h6"])
    cat.register_slot(1, "d3-evening", ["h3", "h4"])
    return cat


class SigningTests(unittest.TestCase):
    def setUp(self):
        self.catalog = make_catalog()
        self.budget = Budget(default=3.0)
        self.desk = QueryDesk(self.budget, Mechanism(), cost=1.0)

    def test_new_release_charges_affected_households_once(self):
        slots = self.catalog.slots([(1, "d1-morning"), (1, "d1-evening")])
        signed = self.desk.ask_detailed("q-d1", slots)
        # households h1,h2 from morning; h1,h3 from evening -> h1 charged once
        self.assertEqual(set(signed.charges), {"h1", "h2", "h3"})
        # h1 appears in both slots but is charged once for this release
        self.assertEqual(self.budget.spent("h1"), 1.0)
        self.assertEqual(len(signed.charges), 3)

        self.desk.ack(signed.record.release_id)
        self.assertEqual(self.budget.spent("h1"), 1.0)

    def test_exact_resend_charges_nothing(self):
        slots = self.catalog.slots([(1, "d1-morning"), (1, "d1-evening")])
        first = self.desk.ask_detailed("q-d1", slots)
        self.desk.ack(first.record.release_id)
        spent = self.budget.snapshot()

        again = self.desk.ask_detailed("q-d1", slots)
        self.assertEqual(again.charges, {})
        self.assertEqual(again.record.release_id, first.record.release_id)
        self.assertEqual(self.budget.snapshot(), spent)

    def test_lost_ack_refunds_and_retry_reuses_same_draw(self):
        slots = self.catalog.slots([(1, "d1-morning"), (1, "d1-evening")])
        signed = self.desk.ask_detailed("q-d1", slots)
        release_id = signed.record.release_id
        public = signed.record.public_value
        self.assertEqual(self.budget.spent("h1"), 1.0)  # reserved then lost

        # acknowledgement never arrives -> rollback refunds everything
        self.desk.retry("q-d1")
        self.assertEqual(self.budget.spent("h1"), 0.0)

        # honest retry with the same key uses the same perturbation
        retry = self.desk.ask_detailed("q-d1", slots)
        self.assertEqual(retry.record.release_id, release_id)
        self.assertEqual(retry.record.public_value, public)
        self.desk.ack(retry.record.release_id)
        self.assertEqual(self.budget.spent("h1"), 1.0)  # exactly once

    def test_hot_household_runs_out_first(self):
        # h1 is in every day-1/2 slot; a cap of 2 lets two releases through
        budget = Budget(caps={"h1": 2.0}, default=3.0)
        desk = QueryDesk(budget, Mechanism(), cost=1.0)
        s1 = desk.ask_detailed("q1", self.catalog.slots([(1, "d1-morning"), (1, "d1-evening")]))
        desk.ack(s1.record.release_id)
        s2 = desk.ask_detailed("q2", self.catalog.slots([(1, "d2-morning")]))
        desk.ack(s2.record.release_id)
        self.assertEqual(budget.spent("h1"), 2.0)
        # a third release touching h1 (present in d1-morning) cannot be signed
        with self.assertRaises(BudgetExceeded) as ctx:
            desk.ask_detailed("q3", self.catalog.slots([(1, "d1-morning")]))
        self.assertEqual(ctx.exception.household, "h1")
        # and the failed sign reserved nothing
        self.assertEqual(budget.spent("h1"), 2.0)

    def test_display_only_cannot_be_backfilled_with_a_fresh_draw(self):
        # A period's genuine release exists at the stats end, but the board was
        # meant to hang the previous period's exhibit.  We must not re-roll the
        # number to make it look current; the release id is fixed.
        slots = self.catalog.slots([(1, "d1-morning")])
        signed = self.desk.ask_detailed("q-d1", slots)
        self.desk.ack(signed.record.release_id)
        record = self.desk.ledger.existing("q-d1")

        # Re-signing with the same key returns the recorded release, same value.
        same = self.desk.ask_detailed("q-d1", slots)
        self.assertEqual(same.record.release_id, record.release_id)
        self.assertEqual(same.record.public_value, record.public_value)


if __name__ == "__main__":
    unittest.main()
