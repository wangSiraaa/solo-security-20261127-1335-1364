#!/usr/bin/env python3
"""Executable walkthrough of the exercise narrative.

Run: ``python3 demo.py``

Scenario order follows the brief:
1. A message written out in four parts, reassembled by stream identity.
2. Two components interleave; one shipment's failure receipt overtakes its
   precursor -> console shows 依据尚缺; the operator supplies the late public
   receipt (inner program is off) and the failure step becomes confirmable.
3. Change only the drill marker's content/split -> outer bytes/lengths/merge
   unchanged. Change only the real failure step -> report changes.
4. Restore the same inner batch; withhold one precursor -> the other shipment
   still delivers its determinate failure.
"""

from __future__ import annotations

from diag import (
    Console,
    Outcome,
    PublicEvent,
    Status,
    StepTable,
    TrustedRelay,
    encode_frame,
    encode_internal,
    interleave_as_pipe,
    split_internal,
)

STEPS = ["accept", "label", "sort", "dispatch"]


def fresh_console() -> Console:
    c = Console(StepTable())
    c.table.register_in_order(list(STEPS))
    return c


def internal(receipt, stream, step, outcome, marker=""):
    return encode_internal(receipt, stream, step, outcome, marker)


def send(console, relay, receipt, stream, step, outcome, marker=""):
    raw = internal(receipt, stream, step, outcome, marker)
    relay.push_pipe(interleave_as_pipe([split_internal(raw, stream)]))


def banner(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main() -> None:
    banner("1. One message in four parts -> reassembled by stream identity")
    console = fresh_console()
    relay = TrustedRelay(console)
    raw = internal("R1", "s1", "accept", Outcome.FAILURE, marker="DRILL-甲")
    frags = split_internal(raw, "s1")
    print(f"wrote {len(frags)} fragments; pushing interleaved pipe...")
    relay.push_pipe(interleave_as_pipe([frags]))
    print("report:", console.report("R1").describe())
    assert console.report("R1").status is Status.FAILED

    banner("2. Interleaved components; failure overtakes precursor -> 依据尚缺")
    console = fresh_console()
    relay = TrustedRelay(console)
    # R7: accept already confirmed; sort fails but arrives before label.
    send(console, relay, "R7", "a", "accept", Outcome.SUCCESS, marker="m1")
    send(console, relay, "R7", "b", "sort", Outcome.FAILURE, marker="m2")
    r = console.report("R7")
    print("before late receipt:", r.describe())
    assert r.status is Status.AWAITING_BASIS and r.step == "label"

    # Inner program is now OFF; duty officer supplies the late public receipt.
    console.deliver_frame(encode_frame(PublicEvent("R7", "label", Outcome.SUCCESS)))
    r = console.report("R7")
    print("after supply, fetched by original id:", r.describe())
    assert r.status is Status.FAILED and r.step == "sort"

    banner("3a. Change only the marker -> outer view unchanged")
    def outer_view(marker, bounds):
        c = fresh_console()
        rl = TrustedRelay(c)
        raw = internal("N1", "s", "accept", Outcome.FAILURE, marker=marker)
        rl.push_pipe(interleave_as_pipe([split_internal(raw, "s", boundaries=bounds)]))
        return (
            c.delivered_bytes("N1"),
            c.delivered_frame_lengths("N1"),
            c.merge_relations("N1"),
        )

    base = outer_view("DRILL-AAA", [6, 15, 24])
    other = outer_view("演练标记截然不同-乙", [3, 9, 11])
    print("bytes equal:", base[0] == other[0])
    print("lengths equal:", base[1] == other[1])
    print("merge equal:", base[2] == other[2])
    assert base == other

    banner("3b. Change only the real failure step -> report changes")
    def step_report(failure_step):
        c = fresh_console()
        rl = TrustedRelay(c)
        for step in STEPS[: STEPS.index(failure_step)]:
            send(c, rl, "N2", f"ok-{step}", step, Outcome.SUCCESS)
        send(c, rl, "N2", "s", failure_step, Outcome.FAILURE, marker="same")
        return c.report("N2")

    print("label:", step_report("label").describe())
    print("sort:", step_report("sort").describe())
    assert step_report("accept") != step_report("label")
    assert step_report("label").step == "label"
    assert step_report("sort").step == "sort"

    banner("4. Restore batch; withhold one precursor -> other shipment delivers")
    console = fresh_console()
    relay = TrustedRelay(console)
    # Shipment A: full sequence, dispatch fails -> determinate.
    for step in STEPS[:-1]:
        send(console, relay, "A", f"a-{step}", step, Outcome.SUCCESS)
    send(console, relay, "A", "a-dispatch", "dispatch", Outcome.FAILURE)
    # Shipment B: accept ok, sort fails, label withheld.
    send(console, relay, "B", "b-accept", "accept", Outcome.SUCCESS)
    send(console, relay, "B", "b-sort", "sort", Outcome.FAILURE)
    print("A:", console.report("A").describe())
    print("B:", console.report("B").describe())
    assert console.report("A").status is Status.FAILED
    assert console.report("B").status is Status.AWAITING_BASIS

    banner("All narrative assertions held.")


if __name__ == "__main__":
    main()
