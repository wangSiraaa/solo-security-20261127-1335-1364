#!/usr/bin/env python3
"""Walk the exercise end to end and print the reconciliation.

Run:  python3 demo.py
"""

from privacydesk.board import DisplayBoard, PublicationIndex
from privacydesk.catalog import CheckinCatalog
from privacydesk.core import Budget, BudgetExceeded, Mechanism
from privacydesk.query_desk import QueryDesk

DAY_1 = [(1, "d1-morning"), (1, "d1-evening")]
DAY_2 = [(1, "d2-morning"), (1, "d2-evening")]
DAY_3 = [(1, "d3-morning"), (1, "d3-evening")]


def catalog():
    cat = CheckinCatalog()
    cat.register_slot(1, "d1-morning", ["h1", "h2"])
    cat.register_slot(1, "d1-evening", ["h1", "h3"])
    cat.register_slot(1, "d2-morning", ["h1", "h4"])
    cat.register_slot(1, "d2-evening", ["h2", "h5"])
    cat.register_slot(1, "d3-morning", ["h2", "h6"])
    cat.register_slot(1, "d3-evening", ["h3", "h4"])
    return cat


def line(title):
    print(f"\n=== {title} ===")


def main():
    cat = catalog()

    line("Direct route: three-day total, then a two-day follow-up")
    budget = Budget(default=4.0)
    desk = QueryDesk(budget, Mechanism(), cost=1.0)
    board = DisplayBoard(current_period=1)
    index = PublicationIndex()

    total = desk.ask_detailed("q-3day", cat.slots(DAY_1 + DAY_2 + DAY_3))
    desk.ack(total.record.release_id)
    sub = desk.ask_detailed("q-day1-2", cat.slots(DAY_1 + DAY_2))
    desk.ack(sub.record.release_id)
    board.mount(desk.deliver_exhibit("q-3day", index))
    board.mount(desk.deliver_exhibit("q-day1-2", index))
    print("spend per household:", budget.snapshot())
    print(board.describe())
    stmt = board.trend_statement([total.record.release_id, sub.record.release_id])
    print("signed:", stmt.signed, "| independent:", stmt.independent_observations)

    line("Lost acknowledgement, then an honest retry")
    budget2 = Budget(default=4.0)
    desk2 = QueryDesk(budget2, Mechanism(), cost=1.0)
    signed = desk2.ask_detailed("q-day1", cat.slots(DAY_1))
    print("figure born once:", signed.record.public_value, "id:", signed.record.release_id)
    print("reserved spend while ack in flight:", budget2.snapshot())
    desk2.retry("q-day1")  # ack lost -> refund
    print("after lost ack:", budget2.snapshot())
    again = desk2.ask_detailed("q-day1", cat.slots(DAY_1))
    desk2.ack(again.record.release_id)
    print("retry reused figure:", again.record.public_value,
          "same id:", again.record.release_id == signed.record.release_id)
    print("final spend:", budget2.snapshot())

    line("Busy household exhausts its cap first")
    budget3 = Budget(caps={"h1": 1.0}, default=3.0)
    desk3 = QueryDesk(budget3, Mechanism(), cost=1.0)
    desk3.ack(desk3.ask_detailed("q-first", cat.slots(DAY_1)).record.release_id)
    try:
        desk3.ask_detailed("q-second", cat.slots(DAY_1 + DAY_2))
        print("unexpectedly allowed")
    except BudgetExceeded as exc:
        print("refused new spend:", exc)

    line("Period rollover: last period's exhibit cannot be hung")
    cat2 = CheckinCatalog()
    cat2.register_slot(1, "p1", ["h1", "h2"])
    cat2.register_slot(2, "p2", ["h1", "h2"])
    desk4 = QueryDesk(Budget(default=3.0), Mechanism(), cost=1.0)
    idx = PublicationIndex()
    desk4.ack(desk4.ask_detailed("q-p1", cat2.slots([(1, "p1")])).record.release_id)
    desk4.ack(desk4.ask_detailed("q-p2", cat2.slots([(2, "p2")])).record.release_id)
    board4 = DisplayBoard(current_period=2, index=idx)
    print("hang last period:", board4.try_mount(desk4.deliver_exhibit("q-p1", idx)))
    print("hang current period:", board4.try_mount(desk4.deliver_exhibit("q-p2", idx)))

    line("Published table: recompose slices for free, one shared observation")
    budget5 = Budget(default=4.0)
    desk5 = QueryDesk(budget5, Mechanism(), cost=1.0)
    table = desk5.ask_table("q-window", cat.slots(DAY_1 + DAY_2 + DAY_3))
    spent = budget5.snapshot()
    whole = table.compose(DAY_1 + DAY_2 + DAY_3)
    part = table.compose(DAY_1 + DAY_2)
    board5 = DisplayBoard(current_period=1)
    board5.mount(whole)
    board5.mount(part)
    print("spend unchanged after recompose:", budget5.snapshot() == spent)
    stmt5 = board5.trend_statement([whole.exhibit_id, part.exhibit_id])
    print("signed:", stmt5.signed, "| independent:", stmt5.independent_observations)


if __name__ == "__main__":
    main()
