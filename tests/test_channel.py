import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diag import (
    Console,
    LegacyAdapter,
    Outcome,
    PublicEvent,
    Status,
    StepTable,
    TrustedRelay,
    TypedEmitter,
    encode_frame,
    encode_internal,
    interleave_as_pipe,
    split_internal,
)
from diag.assembly import Assembler, split_records
from diag.model import TypedEvent
from diag.wire import decode_frame, encode_fragments


STEPS = ["accept", "label", "sort", "dispatch"]


def make_console(table=STEPS):
    c = Console(StepTable())
    c.table.register_in_order(list(table))
    return c


def internal(receipt, stream, step, outcome, marker=""):
    return encode_internal(receipt, stream, step, outcome, marker)


# ---------------------------------------------------------------------------
# 1. Written in four parts: reassembly by stream identity, not adjacency.
# ---------------------------------------------------------------------------


def test_four_parts_reassemble_correctly():
    # Failure at the first stage: a lone failure is a *definite* failure.
    raw = internal("R1", "s1", "accept", Outcome.FAILURE, marker="DRILL-甲")
    frags = split_internal(raw, "s1")
    assert len(frags) == 4  # written out in four parts

    console = make_console()
    relay = TrustedRelay(console)
    pipe = interleave_as_pipe([frags])
    forwarded = relay.push_pipe(pipe)

    assert len(forwarded) == 1
    assert forwarded[0] == PublicEvent("R1", "accept", Outcome.FAILURE)
    assert console.report("R1").status is Status.FAILED
    assert console.report("R1").step == "accept"


# ---------------------------------------------------------------------------
# 2. Two components interleave, with byte-adjacent half-writes.
# ---------------------------------------------------------------------------


def test_interleaved_components_and_adjacent_halves():
    r1 = internal("R1", "a", "accept", Outcome.SUCCESS, marker="DRILL-A")
    r2a = internal("R2", "b", "accept", Outcome.SUCCESS, marker="DRILL-B0")
    r2 = internal("R2", "c", "label", Outcome.FAILURE, marker="DRILL-B")
    g1 = split_internal(r1, "a", boundaries=[5, 17, 30])
    g2a = split_internal(r2a, "b", boundaries=[6, 18, 31])
    g2 = split_internal(r2, "c", boundaries=[7, 19, 33])

    console = make_console()
    relay = TrustedRelay(console)
    relay.push_pipe(interleave_as_pipe([g1, g2a, g2]))

    assert console.observed("R1")["accept"].outcome is Outcome.SUCCESS
    assert console.observed("R2")["label"].outcome is Outcome.FAILURE
    # With its precursor confirmed, the failure receipt is located precisely.
    assert console.report("R2").status is Status.FAILED
    assert console.report("R2").step == "label"


def test_adjacent_fragments_do_not_fuse():
    """Two different streams' halves sit directly next to each other."""

    r1 = internal("R1", "x", "accept", Outcome.SUCCESS, marker="m")
    r2 = internal("R2", "y", "sort", Outcome.FAILURE, marker="n")
    g1 = split_internal(r1, "x", boundaries=[10])
    g2 = split_internal(r2, "y", boundaries=[12])

    # second half of R1 immediately followed by first half of R2
    pipe = encode_fragments([g1[1], g2[0], g1[0], g2[1]])

    console = make_console()
    relay = TrustedRelay(console)
    relay.push_pipe(pipe)

    assert set(console.receipts()) == {"R1", "R2"}
    assert console.observed("R1")["accept"].outcome is Outcome.SUCCESS
    assert console.observed("R2")["sort"].outcome is Outcome.FAILURE


# ---------------------------------------------------------------------------
# 3. Marker may straddle two writes; legacy component bridge.
# ---------------------------------------------------------------------------


def test_legacy_marker_straddles_two_writes():
    raw = internal("R9", "leg", "dispatch", Outcome.FAILURE, marker="金属印章不可外泄")
    # Choose a boundary that lands strictly inside the marker bytes.
    marker_start = raw.index("金属印章不可外泄".encode("utf-8"))
    cut = marker_start + 4
    frags = split_internal(raw, "leg", boundaries=[cut])
    assert len(frags) == 2

    console = make_console()
    # Precursor stages already confirmed public (arrived earlier, normally).
    for step in ("accept", "label", "sort"):
        console.deliver_frame(encode_frame(PublicEvent("R9", step, Outcome.SUCCESS)))

    adapter = LegacyAdapter(console)
    # Feed the *second* half first: reassembly is offset-based, order-agnostic.
    adapter.write("leg", frags[1].total, frags[1].offset, frags[1].payload)
    assert "dispatch" not in console.observed("R9")  # not forwarded yet
    adapter.write("leg", frags[0].total, frags[0].offset, frags[0].payload)

    assert adapter.forwarded() == 1
    assert console.report("R9").status is Status.FAILED
    assert console.report("R9").step == "dispatch"


def test_unprovable_fragment_stays_inside():
    console = make_console()
    adapter = LegacyAdapter(console)
    frags = split_internal(internal("R0", "leg2", "sort", Outcome.FAILURE), "leg2")
    adapter.write("leg2", frags[0].total, frags[0].offset, frags[0].payload)
    adapter.stall("leg2")  # 断流: never completes
    assert console.receipts() == []
    assert adapter.kept_inside() == 0  # nothing was ever promoted
    assert adapter.forwarded() == 0


# ---------------------------------------------------------------------------
# 4. Non-interference: marker content/split invisible; failure step visible.
# ---------------------------------------------------------------------------


def _observed_public_view(console, receipt):
    return (
        console.delivered_bytes(receipt),
        console.delivered_frame_lengths(receipt),
        console.merge_relations(receipt),
    )


def test_marker_content_and_split_do_not_change_outer_view():
    def run(marker, boundaries):
        raw = internal("R1", "s", "sort", Outcome.FAILURE, marker=marker)
        frags = split_internal(raw, "s", boundaries=boundaries)
        console = make_console()
        relay = TrustedRelay(console)
        relay.push_pipe(interleave_as_pipe([frags]))
        return _observed_public_view(console, "R1"), console.report("R1")

    base_view, base_report = run("DRILL-AAA", [6, 15, 24])
    for marker, bounds in [
        ("演练标记完全不同-乙", [3, 9, 11]),
        ("", [1, 2, 3]),
        ("x" * 80, [20, 40, 60]),
    ]:
        view, report = run(marker, bounds)
        assert view[0] == base_view[0]  # delivered bytes identical
        assert view[1] == base_view[1]  # packet lengths identical
        assert view[2] == base_view[2]  # merge relations identical
        assert report == base_report


def test_changing_real_failure_step_changes_report():
    def run(failure_step):
        console = make_console()
        relay = TrustedRelay(console)
        # Confirm everything upstream of the failing step, then deliver failure.
        idx = STEPS.index(failure_step)
        for step in STEPS[:idx]:
            relay.push_pipe(
                interleave_as_pipe(
                    [split_internal(internal("R1", f"ok-{step}", step, Outcome.SUCCESS), f"ok-{step}")]
                )
            )
        raw = internal("R1", "s", failure_step, Outcome.FAILURE, marker="same")
        relay.push_pipe(interleave_as_pipe([split_internal(raw, "s")]))
        return console.report("R1")

    a = run("label")
    b = run("sort")
    assert a.status is b.status is Status.FAILED
    assert a.step == "label" and b.step == "sort"
    assert a != b


def test_marker_never_appears_in_delivered_bytes():
    secret = "绝密演练标记-ZZ-42"
    console = make_console()
    relay = TrustedRelay(console)
    raw = internal("R1", "s", "sort", Outcome.FAILURE, marker=secret)
    relay.push_pipe(interleave_as_pipe([split_internal(raw, "s")]))
    wire = console.delivered_bytes("R1")
    assert secret.encode("utf-8") not in wire
    assert b"DRILL" not in wire


# ---------------------------------------------------------------------------
# 5. Out-of-order: failure receipt overtakes its precursor -> 依据尚缺.
# ---------------------------------------------------------------------------


def test_failure_overtakes_precursor_then_resolves():
    console = make_console()
    relay = TrustedRelay(console)

    # Shipment R7: 'sort' (step idx 2) fails, delivered before 'label' (idx 1).
    # 'accept' (idx 0) is already confirmed, so the only gap is 'label'.
    relay.push_pipe(
        interleave_as_pipe(
            [split_internal(internal("R7", "z", "accept", Outcome.SUCCESS, marker="drill"), "z")]
        )
    )
    fail_raw = internal("R7", "a", "sort", Outcome.FAILURE, marker="drill")
    relay.push_pipe(interleave_as_pipe([split_internal(fail_raw, "a")]))
    report = console.report("R7")
    assert report.status is Status.AWAITING_BASIS  # 依据尚缺
    assert report.step == "label"                   # the missing precursor
    assert report.observed_failure == "sort"

    # Duty officer supplies the late public receipt for the precursor.
    late = PublicEvent("R7", "label", Outcome.SUCCESS)
    console.deliver_frame(encode_frame(late))

    resolved = console.report("R7")  # fetched by the original receipt id
    assert resolved.status is Status.FAILED
    assert resolved.step == "sort"


def test_console_off_inner_program_can_be_fed_late_receipt():
    """The late receipt is supplied by the operator, not by the inner program."""

    console = make_console()  # no relay at all: inner program is "off"
    console.deliver_frame(encode_frame(PublicEvent("R8", "sort", Outcome.FAILURE)))
    assert console.report("R8").status is Status.AWAITING_BASIS
    console.deliver_frame(encode_frame(PublicEvent("R8", "accept", Outcome.SUCCESS)))
    assert console.report("R8").status is Status.AWAITING_BASIS  # still need label
    console.deliver_frame(encode_frame(PublicEvent("R8", "label", Outcome.SUCCESS)))
    r = console.report("R8")
    assert r.status is Status.FAILED and r.step == "sort"


# ---------------------------------------------------------------------------
# 6. Ordinary shipments show done.
# ---------------------------------------------------------------------------


def test_ordinary_shipment_shows_done():
    console = make_console()
    for step in STEPS:
        console.deliver_frame(encode_frame(PublicEvent("OK1", step, Outcome.SUCCESS)))
    r = console.report("OK1")
    assert r.status is Status.DONE
    assert r.describe().endswith("done")


def test_partial_success_shows_waiting_at_specific_step():
    console = make_console()
    console.deliver_frame(encode_frame(PublicEvent("OK2", "accept", Outcome.SUCCESS)))
    r = console.report("OK2")
    assert r.status is Status.WAITING
    assert r.step == "label"


# ---------------------------------------------------------------------------
# 7. Batch restore: withhold one precursor, the other shipment still delivers.
# ---------------------------------------------------------------------------


def test_withhold_one_precursor_other_shipment_still_delivers():
    console = make_console()
    # Shipment A: complete failure at dispatch -> determinate.
    for step, out in [
        ("accept", Outcome.SUCCESS),
        ("label", Outcome.SUCCESS),
        ("sort", Outcome.SUCCESS),
        ("dispatch", Outcome.FAILURE),
    ]:
        console.deliver_frame(encode_frame(PublicEvent("A", step, out)))
    # Shipment B: only its failing 'sort' delivered; 'label' held back.
    console.deliver_frame(encode_frame(PublicEvent("B", "accept", Outcome.SUCCESS)))
    console.deliver_frame(encode_frame(PublicEvent("B", "sort", Outcome.FAILURE)))

    assert console.report("A").status is Status.FAILED
    assert console.report("A").step == "dispatch"
    assert console.report("B").status is Status.AWAITING_BASIS
    assert console.report("B").step == "label"

    # Supply B's held precursor; A was unaffected throughout.
    console.deliver_frame(encode_frame(PublicEvent("B", "label", Outcome.SUCCESS)))
    assert console.report("B").status is Status.FAILED
    assert console.report("B").step == "sort"
    assert console.report("A").status is Status.FAILED


# ---------------------------------------------------------------------------
# 8. Operator registers an unfamiliar step name and its order.
# ---------------------------------------------------------------------------


def test_operator_registers_unfamiliar_step():
    console = Console(StepTable())
    console.table.register_in_order(["accept", "label"])
    console.deliver_frame(encode_frame(PublicEvent("Z", "accept", Outcome.SUCCESS)))
    console.deliver_frame(encode_frame(PublicEvent("Z", "label", Outcome.SUCCESS)))
    # A stranger step appears that the console cannot yet place.
    console.deliver_frame(encode_frame(PublicEvent("Z", "quarantine", Outcome.FAILURE)))
    assert console.report("Z").status is Status.AWAITING_BASIS

    # Duty officer registers the new step and its position.
    console.table.register("quarantine", rank=2)
    r = console.report("Z")
    assert r.status is Status.FAILED
    assert r.step == "quarantine"


# ---------------------------------------------------------------------------
# 9. Route B: typed emission never leaks the marker.
# ---------------------------------------------------------------------------


def test_typed_emitter_leaks_nothing_of_marker():
    console = make_console()
    emitter = TypedEmitter(console)
    secret = "内部演练-不可见"
    emitter.emit(TypedEvent("T1", "stream", "accept", Outcome.SUCCESS))
    emitter.emit(TypedEvent("T1", "stream", "label", Outcome.SUCCESS))
    emitter.emit(TypedEvent("T1", "stream", "sort", Outcome.FAILURE, marker=secret))
    assert secret.encode("utf-8") not in console.delivered_bytes("T1")
    assert console.report("T1").step == "sort"


# ---------------------------------------------------------------------------
# 10. Wire integrity: tampering / partial frames are rejected.
# ---------------------------------------------------------------------------


def test_decode_frame_rejects_tampering():
    frame = encode_frame(PublicEvent("R1", "sort", Outcome.FAILURE))
    bad = bytearray(frame)
    bad[-5] ^= 0xFF
    with pytest.raises(ValueError):
        decode_frame(bytes(bad))


def test_assembler_demuxes_by_stream_identity():
    a = split_internal(internal("R1", "a", "accept", Outcome.SUCCESS), "a")
    b = split_internal(internal("R2", "b", "label", Outcome.FAILURE), "b")
    asm = Assembler()
    completed = asm.feed_bytes(interleave_as_pipe([a, b]))
    assert set(completed) == {"a", "b"}
    assert asm.assemble("a").public == PublicEvent("R1", "accept", Outcome.SUCCESS)
    assert asm.assemble("b").public == PublicEvent("R2", "label", Outcome.FAILURE)


def test_split_records_handles_adjacent_records():
    a = split_internal(internal("R1", "a", "accept", Outcome.SUCCESS), "a", [8])
    records = [f.encode() for f in a]
    assert split_records(b"".join(records)) == records


def test_complete_but_corrupt_stream_stays_inside():
    """A structurally complete message whose public check fails is not forwarded."""

    raw = bytearray(internal("R1", "s", "sort", Outcome.FAILURE, marker="m"))
    # Corrupt a public field after the checksum was computed.
    idx = raw.index(b"sort")
    raw[idx] ^= 0x01
    frags = split_internal(bytes(raw), "s")

    console = make_console()
    relay = TrustedRelay(console)
    relay.push_pipe(interleave_as_pipe([frags]))

    assert console.receipts() == []          # nothing crossed the boundary
    assert relay.stats.kept_inside == 1
    assert "cannot prove public" in relay.kept_inside()[0].kept_inside_reason


def test_console_rejects_malformed_frame():
    console = make_console()
    with pytest.raises(ValueError):
        console.deliver_frame(b"DX1\x00\x00\x00\x02hi")
    assert console.receipts() == []


def test_order_comes_from_table_not_arrival():
    """A later success delivered first must not mask an earlier failure."""

    console = make_console()
    # Deliver out of order: later success, then earlier failure.
    console.deliver_frame(encode_frame(PublicEvent("R3", "sort", Outcome.SUCCESS)))
    assert console.report("R3").status is Status.WAITING
    assert console.report("R3").step == "accept"

    console.deliver_frame(encode_frame(PublicEvent("R3", "accept", Outcome.SUCCESS)))
    assert console.report("R3").step == "label"

    console.deliver_frame(encode_frame(PublicEvent("R3", "label", Outcome.FAILURE)))
    r = console.report("R3")
    assert r.status is Status.FAILED and r.step == "label"
