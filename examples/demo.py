"""Minimal, readable demo of the bench's core promise.

Run:  python3 examples/demo.py

Shows that (a) literal name differences do not create sources, (b) a shared
pool collapses capacity, (c) the combiner needs a genuine head, and (d) a
re-claim by order number is idempotent.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bench.content import ChassisDeclaration, DeclarationBook, Feed  # noqa: E402
from bench.engine import IssuanceEngine, IssuanceError  # noqa: E402
from bench.reserve import ReserveBook  # noqa: E402

L = 32


def line(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def case_names_do_not_matter():
    line("1) Two literal names, one box -> ONE source")
    rb = ReserveBook()
    rb.add("box", 1, 2 * L)
    decls = DeclarationBook()
    decls.set(ChassisDeclaration("rack-1", (Feed("upstream-alpha", "box", 1),)))
    decls.set(ChassisDeclaration("rack-2", (Feed("upstream-beta", "box", 1),)))
    eng = IssuanceEngine(rb, decls, method="partition")
    print("max independent orders:", eng.max_independent_orders(L),
          "(2*L bytes in ONE box -> 2, not 4)")
    eng.issue(L, "A")
    eng.issue(L, "B")
    print("served A and B from the single box; now empty.  "
          "A 3rd order fails:")
    try:
        eng.issue(L, "C")
    except IssuanceError as e:
        print("  refused:", e)


def case_shared_collapses():
    line("2) Collapse capacity by declaring both racks onto one box")
    rb = ReserveBook()
    for p in ("A", "B", "C"):
        rb.add(p, 1, 2 * L)
    decls = DeclarationBook()
    decls.set(ChassisDeclaration("rack-1", (Feed("a", "A", 1), Feed("b", "B", 1))))
    decls.set(ChassisDeclaration("rack-2", (Feed("c", "C", 1),)))
    eng = IssuanceEngine(rb, decls, method="partition")
    print("separate boxes -> orders of L:", eng.max_independent_orders(L))
    # Re-declare both racks onto box A only (names differ).
    decls.set(ChassisDeclaration("rack-1", (Feed("a", "A", 1),)))
    decls.set(ChassisDeclaration("rack-2", (Feed("a-dup", "A", 1),)))
    eng.replace_declarations(decls)
    print("both racks name box A -> orders of L:", eng.max_independent_orders(L))


def case_combiner():
    line("3) Combiner: needs a genuine independent head")
    rb = ReserveBook()
    rb.add("H", 1, 2 * L)   # accepted head
    rb.add("D", 1, 2 * L)   # synthetic-only -> dependent volume
    decls = DeclarationBook()
    decls.set(ChassisDeclaration("rack-1", (Feed("h", "H", 1),)))
    decls.set(ChassisDeclaration("synth", (Feed("d", "D", 1, synthetic=True),)))
    eng = IssuanceEngine(rb, decls, method="combiner")
    issued = eng.issue(L, "CMB-1")
    print("combined output basis:", [f"{p}@{e}" for p, e in issued.basis])
    print("receipt:", issued.receipt)
    # Pause the head; the remaining dependent volume cannot mint independence.
    eng.pause_pool(("H", 1))
    try:
        eng.issue(L, "CMB-2")
    except IssuanceError as e:
        print("after pausing the head, second order refused:", e)


def case_idempotent():
    line("4) Lost reply: re-claim by order number is idempotent")
    rb = ReserveBook()
    rb.add("A", 1, 4 * L)
    decls = DeclarationBook()
    decls.set(ChassisDeclaration("rack-1", (Feed("a", "A", 1),)))
    eng = IssuanceEngine(rb, decls, method="partition")
    first = eng.issue(L, "ORD-7")
    live = eng.independent_bytes()
    again = eng.refetch("ORD-7")
    print("same receipt:", first.receipt == again.receipt)
    print("same material:", first.material_hex == again.material_hex)
    print("live bytes unchanged:", live == eng.independent_bytes(), f"({live})")


if __name__ == "__main__":
    case_names_do_not_matter()
    case_shared_collapses()
    case_combiner()
    case_idempotent()
