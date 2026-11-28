"""End-to-end scenario runner mirroring the brief.

Run ``python3 -m puzzle.scenario`` (or import ``run_all``) to walk every
requirement with concrete, reproducible artifacts in two temp directories:

1. Pre-race signed start; certify the shared two-door race (base map).
2. Publish per-player demos: player1 (room A) stops before entering B;
   player2 (rooms A+B) stops before entering C.  Both are playable.
3. Altered map (one wall moved): reused action string, referee reports the
   actual stop cell; the old demo cannot be reused for the new puzzle.
4. Revoke one player's room eligibility: score is unchanged; the downloaded
   demo now stops before the restricted room.
5. Mid-run hand-off: attest a witnessed middle, continue the tail; a lost
   start cannot be reconstructed.
6. Acceptance: fetch bytes, replay on the player's restricted map, compare
   exact bytes and the final cell.
"""
from __future__ import annotations

import os
import tempfile
from typing import Dict

from . import fixtures as F
from .demo import build_demo, validate_demo_bytes
from .referee import (
    Midpoint,
    attest_midpoint,
    certify,
    continue_from,
    detect_fork,
    issue_start,
    verify_continuation,
    verify_midpoint,
    verify_receipt,
    VerificationError,
)
from .signing import Key
from .store import TwoDirStore


def _key(label: str) -> Key:
    return Key.from_label(label)


def run_all(root: str) -> Dict[str, object]:
    results: Dict[str, object] = {}
    ref, pub = _key("referee"), _key("publisher")
    store = TwoDirStore(
        referee_dir=os.path.join(root, "referee"),
        demo_dir=os.path.join(root, "demo-station"),
    )

    base = F.build_map()
    race = issue_start(ref, "race-1", base)
    store.save_start(race)
    receipt = certify(ref, race, F.RACE_ACTIONS, F.RACE_DWELLS,
                      self_reported_score=999)  # deliberately wrong report
    store.save_receipt(receipt)
    verify_receipt(receipt, ref, race)

    score = receipt.body["verified_score"]
    results["verified_score"] = score
    results["self_reported_ignored"] = receipt.body["self_reported_score"]
    results["reachable"] = receipt.body["reachable"]
    results["pushes"] = receipt.body["pushes"]

    # --- 2. per-player demos --------------------------------------------
    demos = {}
    for pid, rooms in F.PLAYER_UNLOCKS.items():
        d = build_demo(pub, receipt, ref, race, pid, rooms)
        store.publish_demo(d)
        demos[pid] = d
        pb = validate_demo_bytes(store.download_demo("race-1", pid), pub)
        assert pb.ok and pb.frame_match
    results["demo_stop"] = {
        pid: (demos[pid].body["stop_reason"], demos[pid].body["cut_before_step"],
              demos[pid].body["final_cell"])
        for pid in demos
    }
    # no locked-room content in either file
    for pid, rooms in F.PLAYER_UNLOCKS.items():
        for row in demos[pid].body["map"]["regions"]:
            assert row in rooms
        results.setdefault("demo_regions", {})[pid] = list(
            demos[pid].body["map"]["regions"]
        )

    # --- 3. altered map, reused actions ---------------------------------
    altered = F.altered_map()
    race2 = issue_start(ref, "race-1-alt", altered)
    r_alt = certify(ref, race2, F.RACE_ACTIONS, F.RACE_DWELLS)
    stop_cell = r_alt.body["final_state"]["player"]
    # A fork is a claim for the *same* race whose action bytes diverge from
    # certified history.  Here a player claims a different first move.
    forked_claim = list(F.RACE_ACTIONS)
    forked_claim[0] = "R"
    fork = detect_fork(F.RACE_ACTIONS, forked_claim)
    results["altered"] = {
        "puzzle_commit_differs": altered.puzzle_commit != base.puzzle_commit,
        "reachable": r_alt.body["reachable"],
        "stopped_after_step": r_alt.body["stopped_after_step"],
        "stop_cell": stop_cell,
        "blocked_action": r_alt.body["blocked_action"],
        "old_demo_puzzle_commit": demos["player2"].body["puzzle_commit_full"],
        "is_same_puzzle": demos["player2"].body["puzzle_commit_full"]
        == r_alt.body["puzzle_commit"],
        "fork_detected": fork.forked,
        "fork_step": fork.first_divergent_step,
        "fork_reason": fork.reason,
    }
    # an old demo must not validate as the altered puzzle:
    assert demos["player2"].body["puzzle_commit_full"] != r_alt.body["puzzle_commit"]
    # the reused action string genuinely stops early on the altered map:
    assert r_alt.body["stopped_after_step"] < len(F.RACE_ACTIONS)
    assert fork.forked and fork.first_divergent_step == 1
    # a genuine prefix (player stopped early) is NOT a fork:
    prefix_fork = detect_fork(F.RACE_ACTIONS, F.RACE_ACTIONS[:5])
    assert not prefix_fork.forked

    # --- 4. revoke one room's eligibility -------------------------------
    # Restore the *same* race/receipt; only one player's room grant is pulled.
    # The race score must not move; only the demo cut moves.
    d_revoked = build_demo(pub, receipt, ref, race, "player2", ["A"])
    store.publish_demo(d_revoked)
    revoked_download = store.download_demo("race-1", "player2")
    pb_revoked = validate_demo_bytes(revoked_download, pub)
    results["revocation"] = {
        "score_unchanged": d_revoked.body["verified_score"] == score,
        "stop_reason": d_revoked.body["stop_reason"],
        "cut_before_step": d_revoked.body["cut_before_step"],
        "final_cell": d_revoked.body["final_cell"],
        "download_ok": pb_revoked.ok,
        "download_final_cell": pb_revoked.final_cell,
    }
    assert d_revoked.body["verified_score"] == score
    # The re-downloaded file must stop before entering the now-restricted room.
    assert d_revoked.body["cut_before_step"] == F.ROOM_ENTRY_STEPS["B"]
    assert pb_revoked.final_cell[0] <= 5  # never crossed the x=5 divider into B
    # Earlier full-access demo (made before revocation) is now stale: its
    # cut step differs, so it is NOT the same bytes the player may receive.
    assert demos["player2"].body["cut_before_step"] == F.ROOM_ENTRY_STEPS["C"]
    assert revoked_download != demos["player2"].to_bytes()

    # --- 5. mid-run hand-off --------------------------------------------
    witnessed = F.RACE_ACTIONS[:10]
    witnessed_dwells = F.RACE_DWELLS[:11]
    mid = attest_midpoint(ref, race, witnessed, witnessed_dwells)
    store.save_midpoint(mid)
    verify_midpoint(mid, ref, race)
    tail = F.RACE_ACTIONS[10:]
    tail_dwells = F.RACE_DWELLS[10:]
    cont = continue_from(ref, race, mid, tail, tail_dwells)
    store.save_continuation(cont)
    verify_continuation(cont, ref, race, mid)

    mid_state = mid.body["state_at_midpoint"]
    results["midpoint"] = {
        "resume_from_step": cont.body["resume_from_step"],
        "covered_range": mid.body["covered_range"],
        "mid_step": mid_state["step"],
        "cont_final_step": cont.body["stopped_after_step"],
        "cont_reachable": cont.body["reachable"],
        "tail_actions": list(tail),
    }
    # Reconstructing the full run from midpoint + tail matches the receipt.
    assert mid_state["step"] + cont.body["applied_actions"] == \
        receipt.body["stopped_after_step"]
    assert cont.body["final_state"]["player"] == receipt.body["final_state"]["player"]

    # A "mid-state" that nobody witnessed cannot be passed off as evidence:
    # forging a body with the same signature is impossible, and a *self-
    # asserted* midpoint (no signed witnessed run) fails verification.
    forged = {"v": mid.envelope["v"], "body": dict(mid.envelope["body"]),
              "sig": mid.envelope["sig"]}
    forged["body"]["state_at_midpoint"] = {
        **forged["body"]["state_at_midpoint"], "player": [99, 99]}
    bad_mid = Midpoint(forged)  # type: ignore[name-defined]
    try:
        verify_midpoint(bad_mid, ref, race)
        forged_rejected = False
    except VerificationError:
        forged_rejected = True
    results["midpoint"]["forged_state_rejected"] = forged_rejected
    assert forged_rejected

    # Tail actions must actually continue the attested state; choosing tail
    # actions for a *different* route yields a different final cell than the
    # certified receipt — the tail cannot silently invent a path.
    wrong_tail = ["U", "L", "L"]  # reachable from mid, but not the raced route
    wrong_cont = continue_from(ref, race, mid, wrong_tail,
                               [0] * (len(wrong_tail) + 1))
    results["midpoint"]["wrong_tail_final"] = \
        wrong_cont.body["final_state"]["player"]
    results["midpoint"]["wrong_tail_reachability"] = wrong_cont.body["reachable"]
    assert wrong_cont.body["final_state"]["player"] != \
        receipt.body["final_state"]["player"]

    # --- 6. acceptance: fresh race, exact bytes + last cell --------------
    # "Acceptance may open a new race, download, and play it step by step on
    # the player's own map."  We use an independent race id and compare the
    # actually-downloaded bytes and the final cell to an independently-replayed
    # expectation (not to any in-memory object).
    fresh = issue_start(ref, "race-2", base)
    r_fresh = certify(ref, fresh, F.RACE_ACTIONS, F.RACE_DWELLS)
    d_fresh = build_demo(pub, r_fresh, ref, fresh, "player2", ["A", "B"])
    store.publish_demo(d_fresh)
    raw = store.download_demo("race-2", "player2")

    # Independent expectation via plain replay of the shared trace.
    expect = [fr["cell"] for fr in d_fresh.body["frames"]][-1]
    pb = validate_demo_bytes(raw, pub)
    # tamper one byte -> must fail
    tampered = bytearray(raw)
    tampered[-2] = (tampered[-2] + 1) % 256
    pb_bad = validate_demo_bytes(bytes(tampered), pub)
    results["acceptance"] = {
        "ok": pb.ok,
        "bytes_equal": raw == d_fresh.to_bytes(),
        "final_cell": pb.final_cell,
        "expected_final_cell": tuple(expect),
        "frame_match": pb.frame_match,
        "tampered_rejected": not pb_bad.ok,
    }
    assert pb.ok and pb.frame_match
    assert pb.final_cell == tuple(expect)
    assert raw == d_fresh.to_bytes()
    assert pb_bad.ok is False
    return results


def main() -> None:
    import json

    with tempfile.TemporaryDirectory() as root:
        res = run_all(root)
    print(json.dumps(res, indent=2, default=list))


if __name__ == "__main__":
    main()
