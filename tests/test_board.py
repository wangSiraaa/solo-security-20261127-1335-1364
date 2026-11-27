import unittest

from privacydesk.board import DisplayBoard, PeriodMismatch, PublicationIndex
from privacydesk.catalog import CheckinCatalog
from privacydesk.core import Budget, Exhibit, Mechanism
from privacydesk.query_desk import QueryDesk


def make_catalog():
    cat = CheckinCatalog()
    cat.register_slot(1, "d1-morning", ["h1", "h2"])
    cat.register_slot(1, "d1-evening", ["h1", "h3"])
    cat.register_slot(1, "d2-morning", ["h1", "h4"])
    cat.register_slot(1, "d2-evening", ["h2", "h5"])
    cat.register_slot(1, "d3-morning", ["h2", "h6"])
    cat.register_slot(1, "d3-evening", ["h3", "h4"])
    return cat


class BoardPeriodTests(unittest.TestCase):
    def test_previous_period_exhibit_cannot_hang_as_current(self):
        cat = CheckinCatalog()
        cat.register_slot(1, "p1-slot", ["h1", "h2"])
        cat.register_slot(2, "p2-slot", ["h1", "h2"])
        budget = Budget(default=3.0)
        desk = QueryDesk(budget, Mechanism(), cost=1.0)

        # a genuine release for the *previous* period
        old = desk.ask_detailed("q-p1", cat.slots([(1, "p1-slot")]))
        desk.ack(old.record.release_id)
        old_exhibit = Exhibit.from_record(old.record)
        self.assertEqual(old_exhibit.period, 1)

        board = DisplayBoard(current_period=2)
        self.assertFalse(board.try_mount(old_exhibit))
        self.assertEqual(board.exhibits(), [])

        # the stats end can still make a legitimate current-period release...
        new = desk.ask_detailed("q-p2", cat.slots([(2, "p2-slot")]))
        desk.ack(new.record.release_id)
        # ...but only that one may be hung for period 2
        self.assertTrue(board.try_mount(Exhibit.from_record(new.record)))
        self.assertEqual([e.period for e in board.exhibits()], [2])

    def test_cannot_hang_old_release_under_new_period_claim(self):
        cat = CheckinCatalog()
        cat.register_slot(1, "p1-slot", ["h1", "h2"])
        budget = Budget(default=3.0)
        desk = QueryDesk(budget, Mechanism(), cost=1.0)
        old = desk.ask_detailed("q-p1", cat.slots([(1, "p1-slot")]))
        desk.ack(old.record.release_id)

        index = PublicationIndex()
        desk.deliver_exhibit("q-p1", index)  # stamps period=1 for this release

        # try to relabel the old exhibit as period 2
        forged = Exhibit(
            exhibit_id=old.record.release_id,
            lineage=old.record.release_id,
            period=2,
            slots=old.record.slots,
            public_value=old.record.public_value,
            fingerprint=old.record.fingerprint,
        )
        board = DisplayBoard(current_period=2, index=index)
        with self.assertRaises(PeriodMismatch):
            board.mount(forged)


class IndependenceTests(unittest.TestCase):
    def test_same_publication_referenced_twice_is_one_observation(self):
        cat = make_catalog()
        table = QueryDesk(Budget(default=5.0), Mechanism(), cost=1.0).ask_table(
            "q-window",
            cat.slots([(1, "d1-morning"), (1, "d2-morning"), (1, "d3-morning")]),
        )
        # a three-day span and an overlapping two-day span, both cut from the
        # one published table -> same perturbation evidence
        three = table.compose([(1, "d1-morning"), (1, "d2-morning"), (1, "d3-morning")])
        two = table.compose([(1, "d1-morning"), (1, "d2-morning")])

        board = DisplayBoard(current_period=1)
        board.mount(three)
        board.mount(two)

        stmt = board.trend_statement([three.exhibit_id, two.exhibit_id])
        self.assertEqual(stmt.independent_observations, 1)
        self.assertFalse(stmt.signed)
        self.assertIn("single observation", stmt.text)

    def test_two_separate_paid_releases_are_two_observations(self):
        cat = make_catalog()
        desk = QueryDesk(Budget(default=5.0), Mechanism(), cost=1.0)
        a = desk.ask_detailed("q-a", cat.slots([(1, "d1-morning")]))
        desk.ack(a.record.release_id)
        b = desk.ask_detailed("q-b", cat.slots([(1, "d2-morning")]))
        desk.ack(b.record.release_id)

        board = DisplayBoard(current_period=1)
        ea = Exhibit.from_record(a.record)
        eb = Exhibit.from_record(b.record)
        board.mount(ea)
        board.mount(eb)
        stmt = board.trend_statement([ea.exhibit_id, eb.exhibit_id])
        self.assertEqual(stmt.independent_observations, 2)
        self.assertTrue(stmt.signed)
        self.assertIn("two independent observations", stmt.text)

    def test_resend_of_one_release_is_not_a_second_observation(self):
        cat = make_catalog()
        desk = QueryDesk(Budget(default=5.0), Mechanism(), cost=1.0)
        a = desk.ask_detailed("q-a", cat.slots([(1, "d1-morning")]))
        desk.ack(a.record.release_id)

        # the exact same release handed over twice is still one publication
        ea = Exhibit.from_record(a.record)
        eb = Exhibit.from_record(a.record)
        board = DisplayBoard(current_period=1)
        board.mount(ea)
        board.mount(eb)  # idempotent re-hang by exhibit_id
        self.assertEqual(len(board.exhibits()), 1)
        with self.assertRaises(Exception):
            board.trend_statement([ea.exhibit_id, eb.exhibit_id])


class TwoRouteTests(unittest.TestCase):
    def test_direct_route_new_subrange_keeps_charging(self):
        cat = make_catalog()
        budget = Budget(default=5.0)
        desk = QueryDesk(budget, Mechanism(), cost=1.0)
        d1 = desk.ask_detailed("q-3day", cat.slots(
            [(1, "d1-morning"), (1, "d2-morning"), (1, "d3-morning")]))
        desk.ack(d1.record.release_id)
        spent_after_first = budget.snapshot()

        d2 = desk.ask_detailed("q-2day", cat.slots(
            [(1, "d1-morning"), (1, "d2-morning")]))
        desk.ack(d2.record.release_id)
        # a genuine new release -> real extra spend
        self.assertGreaterEqual(sum(budget.snapshot().values()),
                                sum(spent_after_first.values()))

    def test_published_table_recomposes_without_extra_spend(self):
        cat = make_catalog()
        budget = Budget(default=5.0)
        desk = QueryDesk(budget, Mechanism(), cost=1.0)
        table = desk.ask_table("q-summer-table", cat.slots(
            [(1, "d1-morning"), (1, "d2-morning"), (1, "d3-morning")]))
        spent = budget.snapshot()

        board = DisplayBoard(current_period=1)
        whole = table.compose([(1, "d1-morning"), (1, "d2-morning"), (1, "d3-morning")])
        part = table.compose([(1, "d1-morning"), (1, "d2-morning")])
        board.mount(whole)
        board.mount(part)

        # recomposing costs nothing extra
        self.assertEqual(budget.snapshot(), spent)
        # and the two views share one perturbation -> one observation
        stmt = board.trend_statement([whole.exhibit_id, part.exhibit_id])
        self.assertEqual(stmt.independent_observations, 1)
        self.assertFalse(stmt.signed)

    def test_published_table_cannot_show_unsupported_dimension(self):
        cat = make_catalog()
        desk = QueryDesk(Budget(default=5.0), Mechanism(), cost=1.0)
        table = desk.ask_table("q-table", cat.slots([(1, "d1-morning"), (1, "d2-morning")]))
        with self.assertRaises(Exception):
            table.compose([(1, "d1-morning"), (1, "d3-morning")])  # d3 not in table


if __name__ == "__main__":
    unittest.main()
