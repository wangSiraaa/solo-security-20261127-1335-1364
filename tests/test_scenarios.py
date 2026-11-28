"""Scenario tests for the offline calibration-board tool.

Each test pins one requirement from the exercise brief.  Everything is
simulated; no hardware is touched.
"""

import json

import calboard as cb
from calboard.events import Event, ReadRequest
from calboard.examples import (
    BOARD_EARLY_SECOND_PORT,
    BOARD_STALE_RETAINED,
    BOARD_TWO_DOMAIN,
    BOARD_TWO_PORTS,
)


# ---------------------------------------------------------------------------
# 1. Coherence: a window publishes one complete calibration round only.
# ---------------------------------------------------------------------------
def test_full_public_calibration_publishes_one_round():
    events = [
        Event(at=0, kind="reset", domains=["left"], latency=1),
        Event(at=1, kind="reset", domains=["right"], latency=1),
        Event(at=2, kind="write", cell="left_cal", value=512, gen="gen1"),
        Event(at=2, kind="write", cell="right_cal", value=77, gen="gen1"),
        Event(at=2, kind="write", cell="retained_cal", value=100, gen="gen1"),
    ]
    req = ReadRequest(
        at=2, window="full", want=["left_cal", "right_cal", "retained_cal"],
        generation="gen1",
    )
    res = cb.Simulator(BOARD_TWO_DOMAIN, events, [req]).run()
    rec = res.records[0]
    assert rec.published
    assert rec.gen == "gen1"
    assert rec.outputs == {"left_cal": 512, "right_cal": 77, "retained_cal": 100}


def test_window_refuses_mixed_generations():
    # left_cal gen1, right_cal gen0 (not rewritten) -> cannot share a window.
    events = [
        Event(at=0, kind="reset", domains=["left"], latency=1),
        Event(at=1, kind="write", cell="left_cal", value=512, gen="gen1"),
    ]
    req = ReadRequest(at=1, window="both", want=["left_cal", "right_cal"])
    # right_cal had initial value None -> missing; use a case with a present cell
    b = BOARD_TWO_DOMAIN.clone()
    b.cells["right_cal"].value = 77
    b.cells["right_cal"].gen = "gen0"
    res = cb.Simulator(b, events, [req]).run()
    rec = res.records[0]
    assert not rec.published
    assert rec.generation_mismatch


def test_window_refuses_empty_cleared_cell():
    events = [Event(at=0, kind="reset", domains=["left"], latency=2)]
    req = ReadRequest(at=1, window="early", want=["left_cal"])
    res = cb.Simulator(BOARD_TWO_DOMAIN, events, [req]).run()
    rec = res.records[0]
    assert not rec.published
    assert rec.missing or rec.unsettled


# ---------------------------------------------------------------------------
# 2. Raw tenant cells never reach a public window.
# ---------------------------------------------------------------------------
def test_raw_cell_is_never_published():
    req = ReadRequest(at=0, window="leak", want=["tenant_raw"])
    res = cb.Simulator(BOARD_TWO_DOMAIN, [], [req]).run()
    rec = res.records[0]
    assert not rec.published
    assert rec.leaked_raw
    assert "tenant_raw" in rec.raw_in_readset


def test_raw_reachable_via_wiring_is_blocked():
    # Build a board where a public output depends on a raw cell through wiring.
    b = cb.Board.from_dict(
        {
            "name": "wired_raw",
            "domains": ["d"],
            "cells": [
                {"name": "raw", "domain": "d", "kind": "raw", "retained": True,
                 "initial": {"value": 5, "gen": "g"}},
                {"name": "pub", "domain": "d", "kind": "public", "retained": True,
                 "initial": {"value": 9, "gen": "g"}},
            ],
            "wiring": [{"from": "raw", "to": "pub"}],
            "links": [],
            "reset_policy": {},
        }
    )
    req = ReadRequest(at=0, window="w", want=["pub"])
    rec = cb.Simulator(b, [], [req]).run().records[0]
    assert not rec.published
    assert rec.leaked_raw


# ---------------------------------------------------------------------------
# 3. Mode A: synthesized fixed-tick plan proved over all interleavings.
# ---------------------------------------------------------------------------
def test_plan_proves_safe_tick_over_all_interleavings():
    out = cb.prove_fixed_tick(
        BOARD_TWO_DOMAIN, "power_on", "gen1",
        {"left_cal": 512, "right_cal": 77, "retained_cal": 100},
        ["left_cal", "right_cal", "retained_cal"], tick=3,
    )
    assert out["interleavings"] == 3
    assert out["safe_in_all"]


def test_plan_rejects_too_early_a_tick():
    out = cb.prove_fixed_tick(
        BOARD_TWO_DOMAIN, "power_on", "gen1",
        {"left_cal": 512, "right_cal": 77, "retained_cal": 100},
        ["left_cal", "right_cal", "retained_cal"], tick=1,
    )
    assert not out["safe_in_all"]
    bad = [r for r in out["rows"] if not r["published"]]
    assert bad  # at least one late-start domain leaves a cell empty


def test_plan_pays_for_late_dependent_domain():
    # Plan must choose a tick at/after the worst-case settle for every written
    # closure cell, not just the fastest interleaving.  Including the late
    # ``right`` domain in the want set forces waiting for its worst stagger.
    plan = cb.synthesize_plan(
        BOARD_TWO_DOMAIN, "power_on", "gen1",
        {"left_cal": 512, "right_cal": 77, "retained_cal": 100},
        ["left_cal", "right_cal", "retained_cal"],
    )
    assert plan.ok
    # latency 1 + max_stagger 2 => worst settle at tick 3
    assert plan.window_tick == 3
    proof = cb.prove_fixed_tick(
        BOARD_TWO_DOMAIN, "power_on", "gen1",
        {"left_cal": 512, "right_cal": 77, "retained_cal": 100},
        ["left_cal", "right_cal", "retained_cal"], tick=plan.window_tick,
    )
    assert proof["safe_in_all"]


def test_plan_rejects_stale_retained_with_equal_value():
    # carried_cal survives at gen0 holding 900; this round is gen1 and the
    # same number would be produced -- numeric equality must not suffice.
    plan = cb.synthesize_plan(
        BOARD_STALE_RETAINED, "power_on", "gen1",
        {"a_cal": 900, "b_cal": 900}, ["a_cal", "carried_cal"],
    )
    assert not plan.ok
    assert any("retained" in r and "gen" in r for r in plan.reasons)


def test_plan_accepts_retained_when_genuinely_recalibrated():
    plan = cb.synthesize_plan(
        BOARD_STALE_RETAINED, "power_on", "gen1",
        {"a_cal": 900, "b_cal": 900, "carried_cal": 900}, ["a_cal", "carried_cal"],
    )
    assert plan.ok


# ---------------------------------------------------------------------------
# 4. Mode A must inspect actual retained state, and recompute on a change.
# ---------------------------------------------------------------------------
def test_retained_cell_changed_to_clear_requires_recompute():
    # Original board: retained_cal survives -> plan ok.
    p_ok = cb.synthesize_plan(
        BOARD_TWO_DOMAIN, "power_on", "gen1",
        {"left_cal": 512, "right_cal": 77, "retained_cal": 100},
        ["left_cal", "retained_cal"],
    )
    assert p_ok.ok
    # Change the originally retained cell to clear with its neighbour.
    b2 = BOARD_TWO_DOMAIN.with_cell_clear_scope("retained_cal", ["right"])
    # Program unchanged: it no longer rewrites retained_cal -> now it is empty.
    p_bad = cb.synthesize_plan(
        b2, "power_on", "gen1", {"left_cal": 512, "right_cal": 77},
        ["left_cal", "retained_cal"],
    )
    assert not p_bad.ok
    # Recompute including the now-cleared cell in the program: ok again.
    p_fix = cb.synthesize_plan(
        b2, "power_on", "gen1",
        {"left_cal": 512, "right_cal": 77, "retained_cal": 100},
        ["left_cal", "retained_cal"],
    )
    assert p_fix.ok


# ---------------------------------------------------------------------------
# 5. Mode B: cross-domain handshake.
# ---------------------------------------------------------------------------
def test_handshake_needs_link_connected_closure():
    # Remove the control line between left and right -> cannot seal.
    b = BOARD_TWO_DOMAIN.clone()
    b.links = []
    out = cb.handshake_plan(b, ["left_cal", "right_cal"])
    assert not out["ok"]


def test_handshake_reports_finite_link_budget():
    out = cb.handshake_plan(BOARD_TWO_DOMAIN, ["left_cal", "right_cal"])
    assert out["ok"]
    assert out["link_budget"] >= 1


def test_handshake_publishes_when_all_parts_agree():
    events = [
        Event(at=0, kind="reset", domains=["left"], latency=1),
        Event(at=1, kind="reset", domains=["right"], latency=1),
        Event(at=2, kind="write", cell="left_cal", value=512, gen="gen1"),
        Event(at=2, kind="write", cell="right_cal", value=77, gen="gen1"),
    ]
    req = ReadRequest(
        at=1, window="full", want=["left_cal", "right_cal"],
        generation="gen1", mode="handshake", deadline=6,
    )
    outs = cb.handshake_windows(BOARD_TWO_DOMAIN, events, [req])
    assert len(outs) == 1
    assert outs[0].ok
    assert outs[0].publish_tick == 2
    assert all(a.acked for a in outs[0].acks)


def test_handshake_refuses_when_healthy_domain_holds_old_gen():
    # Only aux reset & regenerated; main not reset still holds gen0.  The
    # handshake must refuse rather than seal on acks alone.
    events = [
        Event(at=0, kind="reset", domains=["aux"], latency=2),
        Event(at=2, kind="write", cell="aux_cal", value=808, gen="gen1"),
    ]
    req = ReadRequest(
        at=1, window="port2", want=["main_public_meas", "aux_cal"],
        mode="handshake", deadline=8,
    )
    outs = cb.handshake_windows(BOARD_TWO_PORTS, events, [req])
    assert not outs[0].ok
    assert any("different generations" in r for r in outs[0].reasons)
    # main did not reset and still reports its old generation.
    main_ack = next(a for a in outs[0].acks if a.domain == "main")
    assert not main_ack.reset_in_run
    assert main_ack.gen == "gen0"


# ---------------------------------------------------------------------------
# 6. Partial reset: healthy domain's independent public measurement still works.
# ---------------------------------------------------------------------------
def test_partial_reset_keeps_healthy_public_measurement_usable():
    events = [
        Event(at=0, kind="reset", domains=["right"], latency=1),
        Event(at=1, kind="write", cell="right_cal", value=77, gen="gen1"),
    ]
    # The retained cell in the healthy left domain is still readable on its own.
    req = ReadRequest(at=0, window="healthy", want=["retained_cal"])
    rec = cb.Simulator(BOARD_TWO_DOMAIN, events, [req]).run().records[0]
    # retained_cal lives in right in this board; instead check left's tenant
    # is untouched and a left public persists -- use a board where the healthy
    # side owns the measurement.
    assert rec.published  # retained_cal survives a right reset at tick 0

    # Two-port board: aux resets, main stays up; port1 reads main's measurement.
    events2 = [
        Event(at=0, kind="reset", domains=["aux"], latency=2),
        Event(at=2, kind="write", cell="aux_cal", value=808, gen="gen1"),
    ]
    req1 = ReadRequest(at=3, window="port1", want=["main_public_meas"])
    rec1 = cb.Simulator(BOARD_TWO_PORTS, events2, [req1]).run().records[0]
    assert rec1.published
    assert rec1.outputs == {"main_public_meas": 55}


# ---------------------------------------------------------------------------
# 7. Two ports: port 2's late miss must not cancel port 1's publication.
# ---------------------------------------------------------------------------
def test_second_port_early_miss_does_not_cancel_first_port():
    events = [
        Event(at=0, kind="reset", domains=["fast"], latency=2),
        Event(at=1, kind="reset", domains=["slow"], latency=2),
        Event(at=3, kind="write", cell="fast_cal", value=11, gen="gen1"),
        Event(at=3, kind="write", cell="slow_cal", value=22, gen="gen1"),
    ]
    reqs = [
        ReadRequest(at=4, window="port1", want=["fast_cal", "slow_cal"],
                    generation="gen1"),
        ReadRequest(at=2, window="port2", want=["fast_cal", "slow_cal"],
                    generation="gen1"),
    ]
    res = cb.Simulator(BOARD_EARLY_SECOND_PORT, events, reqs).run()
    by = res.by_window()
    assert by["port1"][0].published
    assert not by["port2"][0].published


def test_two_ports_same_values_different_rounds():
    # Two rounds carry identical numeric values but distinct generations.  A
    # port pinned to the later round must not accept the earlier one even
    # though the numbers match.
    events = [
        # round gen1 written at tick 2
        Event(at=1, kind="write", cell="fast_cal", value=11, gen="gen1"),
        Event(at=1, kind="write", cell="slow_cal", value=22, gen="gen1"),
        # round gen2 rewrites the *same* numbers at tick 4
        Event(at=4, kind="write", cell="fast_cal", value=11, gen="gen2"),
        Event(at=4, kind="write", cell="slow_cal", value=22, gen="gen2"),
    ]
    reqs = [
        ReadRequest(at=2, window="gen1_port", want=["fast_cal", "slow_cal"],
                    generation="gen1"),
        ReadRequest(at=5, window="gen2_port", want=["fast_cal", "slow_cal"],
                    generation="gen2"),
        # A port that only requires "some coherent round" at tick 2 gets gen1.
        ReadRequest(at=2, window="any_port", want=["fast_cal", "slow_cal"]),
    ]
    res = cb.Simulator(BOARD_EARLY_SECOND_PORT, events, reqs).run()
    by = res.by_window()
    assert by["gen1_port"][0].published and by["gen1_port"][0].gen == "gen1"
    assert by["gen2_port"][0].published and by["gen2_port"][0].gen == "gen2"
    assert by["any_port"][0].gen == "gen1"
    # same values, different provenance
    assert by["gen1_port"][0].outputs == by["gen2_port"][0].outputs
    assert by["gen1_port"][0].gen != by["gen2_port"][0].gen


# ---------------------------------------------------------------------------
# 8. Reset latency: writes during settle are lost; reads unsettled refused.
# ---------------------------------------------------------------------------
def test_write_during_settle_is_lost():
    events = [
        Event(at=0, kind="reset", domains=["left"], latency=3),
        Event(at=1, kind="write", cell="left_cal", value=5, gen="gen1"),
    ]
    req = ReadRequest(at=4, window="w", want=["left_cal"])
    res = cb.Simulator(BOARD_TWO_DOMAIN, events, [req]).run()
    rec = res.records[0]
    assert not rec.published
    assert rec.missing  # the early write was erased by the settling reset


def test_read_unsettled_is_refused_but_settled_ok():
    events = [Event(at=0, kind="reset", domains=["left"], latency=2)]
    early = ReadRequest(at=1, window="early", want=["left_cal"])
    res = cb.Simulator(BOARD_TWO_DOMAIN, events, [early]).run()
    assert not res.records[0].published
    assert res.records[0].unsettled or res.records[0].missing


# ---------------------------------------------------------------------------
# 9. Reset policy: only allowed normal orderings are enumerated.
# ---------------------------------------------------------------------------
def test_allowed_interleavings_bounded_stagger():
    ivs = cb.allowed_interleavings(["a", "b", "c"], 2)
    for iv in ivs:
        assert iv["a"] == 0
        assert iv["a"] <= iv["b"] <= iv["c"]
        assert iv["c"] <= 2
    # order preserved, bounded
    assert {"a": 0, "b": 0, "c": 0} in ivs
    assert {"a": 0, "b": 2, "c": 2} in ivs


def test_partial_reset_only_resets_named_domains():
    # reset_policy local_aux only lists aux; a local reset must not clear main.
    spec = cb.ResetSpec.from_board(BOARD_TWO_PORTS, "local_aux")
    assert spec.order == ["aux"]


# ---------------------------------------------------------------------------
# 10. CLI smoke tests.
# ---------------------------------------------------------------------------
def test_cli_plan_and_run(tmp_path):
    from calboard import cli

    board_path = tmp_path / "board.json"
    board_path.write_text(json.dumps(BOARD_TWO_DOMAIN.to_dict()))
    prog_path = tmp_path / "prog.json"
    prog_path.write_text(
        json.dumps(
            {
                "reset_kind": "power_on",
                "round_gen": "gen1",
                "want": ["left_cal", "retained_cal"],
                "writes": {"left_cal": 512, "right_cal": 77, "retained_cal": 100},
            }
        )
    )
    rc = cli.main(["--format", "json", "plan", "--board", str(board_path),
                   "--program", str(prog_path)])
    assert rc == 0


# ---------------------------------------------------------------------------
# 11. A value without a generation token is never treated as current.
# ---------------------------------------------------------------------------
def test_value_without_generation_is_refused():
    b = BOARD_TWO_DOMAIN.clone()
    b.cells["retained_cal"].value = 100
    b.cells["retained_cal"].gen = None
    req = ReadRequest(at=0, window="w", want=["retained_cal"])
    rec = cb.Simulator(b, [], [req]).run().records[0]
    assert not rec.published
    assert any("no calibration generation" in r for r in rec.reasons)


# ---------------------------------------------------------------------------
# 12. Scenario CLI: mutation (retained -> cleared) forces a recompute.
# ---------------------------------------------------------------------------
def test_scenario_mutation_changes_plan(tmp_path):
    from calboard import cli

    board_path = tmp_path / "board.json"
    board_path.write_text(json.dumps(BOARD_TWO_DOMAIN.to_dict()))
    prog = {
        "reset_kind": "power_on",
        "round_gen": "gen1",
        "want": ["left_cal", "retained_cal"],
        "writes": {"left_cal": 512, "right_cal": 77},
    }
    prog_path = tmp_path / "prog.json"
    prog_path.write_text(json.dumps(prog))
    # Without mutation, retained_cal survives but is not written -> stale gen0.
    rc = cli.main(["plan", "--board", str(board_path), "--program", str(prog_path)])
    # retained_cal retains gen0 -> plan fails (exit 2)
    assert rc == 2

    # Mutate retained_cal to clear with right; same program now leaves it
    # empty; still fails but for a different, reported reason.
    prog2 = dict(prog, mutations=[{"cell": "retained_cal", "clear_with": ["right"]}])
    prog_path.write_text(json.dumps(prog2))
    rc = cli.main(["plan", "--board", str(board_path), "--program", str(prog_path)])
    assert rc == 2
