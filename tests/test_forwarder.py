"""
Security-property regression tests for the privacy-screen forwarder.

Each test pins one invariant from the brief.  Run with:

    python3 -m pytest tests/ -q
    # or, stdlib only:
    python3 tests/test_forwarder.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from privacy_forwarder.engine import (
    REDACTION_COLOR,
    SOURCE_BASE,
    SOURCE_PRIVACY,
    SOURCE_WINDOW,
    Endpoint,
    Forwarder,
    Operation,
    Rect,
    Region,
    assess_view,
    check_click,
    decide,
    default_regions,
    forwarder_from_dict,
    forwarder_to_dict,
    tile_key,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def fresh(versions=("甲单", "甲单")) -> Forwarder:
    fw = Forwarder(default_regions(), policy="partitioned")
    fw.composer.set_base("L", versions[0], "#e8eef7")
    fw.composer.set_base("R", versions[1], "#e8eef7")
    fw.delivery.mode = {"L": "deliver", "R": "deliver"}
    return fw


def auth(decisions, oid):
    return next(d["authorized"] for d in decisions if d["oid"] == oid)


def _all_tiles(report):
    return list(report["tiles"].values())


def run_test(name, fn):
    try:
        fn()
        print(f"PASS {name}")
        return True
    except AssertionError as e:
        print(f"FAIL {name}: {e}")
        return False


# --------------------------------------------------------------------------- #
# 1. privacy is fail-closed and does not leak
# --------------------------------------------------------------------------- #

def test_privacy_redaction_is_fail_closed():
    fw = fresh()
    dest = fw.regions["R"].tile_rect(*fw.regions["R"].dest_tile())
    fw.composer.set_privacy("dm", dest, color="#00ff00")  # a *visible* raw colour
    frame = fw.composer.compose()
    key = tile_key("R", *fw.regions["R"].dest_tile())
    t = frame[key]
    assert t["source"] == SOURCE_PRIVACY
    # the privacy layer's own colour must NOT be emitted
    assert t["color"] == REDACTION_COLOR
    assert t["color"] != "#00ff00"
    # no label, no base version underneath
    assert t["label"] is None
    assert t["ver"] is None


def test_privacy_does_not_reveal_base_underneath():
    # B (hidden) base must never surface on a privacy-covered tile, so the
    # receiver cannot guess/split what was underneath.
    fw = fresh()
    dest = fw.regions["R"].tile_rect(*fw.regions["R"].dest_tile())
    fw.composer.set_privacy("dm", dest)
    fw.tick([])
    rt = fw.endpoint.report(fw.tick_no)
    key = tile_key("R", *fw.regions["R"].dest_tile())
    tile = rt["tiles"][key]
    assert tile["source"] == SOURCE_PRIVACY
    assert tile["color"] == REDACTION_COLOR
    assert tile.get("ver") is None
    assert tile.get("label") is None


def test_privacy_within_tile_leaks_nothing_partial():
    # a privacy rect that covers only PART of a tile still redacts the whole
    # tile (strictly >= true secret; never a partial reveal).
    fw = fresh()
    region = fw.regions["R"]
    trect = region.tile_rect(1, 1)
    partial = Rect(trect.x + 5, trect.y + 5, 10, 10)
    fw.composer.set_privacy("dm", partial)
    frame = fw.composer.compose()
    key = tile_key("R", 1, 1)
    assert frame[key]["source"] == SOURCE_PRIVACY
    assert frame[key]["color"] == REDACTION_COLOR


# --------------------------------------------------------------------------- #
# 2. authorization reads only the actual displayed view
# --------------------------------------------------------------------------- #

def test_cross_requires_same_version_both_visible():
    fw = fresh(("乙单", "乙单"))
    r = fw.tick([Operation("x", "cross", "乙单")])
    assert auth(r["decisions"], "x") is True


def test_cross_refused_when_versions_differ():
    fw = fresh(("乙单", "甲单"))
    r = fw.tick([Operation("x", "cross", "乙单")])
    assert auth(r["decisions"], "x") is False
    # two regions visibly disagreeing => the screen is contradictory: void
    assert next(d["reason"] for d in r["decisions"] if d["oid"] == "x") == "cross_region_disagree"
    assert next(d["outcome"] for d in r["decisions"] if d["oid"] == "x") == "void"


def test_cross_refused_when_right_redacted():
    fw = fresh(("甲单", "甲单"))
    dest = fw.regions["R"].tile_rect(*fw.regions["R"].dest_tile())
    fw.composer.set_privacy("dm", dest)
    r = fw.tick([Operation("x", "cross", "甲单")])
    assert auth(r["decisions"], "x") is False


def test_left_op_ignores_right_state():
    # a left-only op is provable even if R is redacted, as long as L is clean
    fw = fresh(("乙单", "甲单"))
    dest = fw.regions["R"].tile_rect(*fw.regions["R"].dest_tile())
    fw.composer.set_privacy("dm", dest)
    r = fw.tick([Operation("l", "left", "乙单")])
    assert auth(r["decisions"], "l") is True


def test_stale_right_block_blocks_cross():
    # R receipt frozen on old 甲单 while desktop is now 乙/乙
    fw = fresh(("甲单", "甲单"))
    fw.tick([])  # terminal shows 甲/甲
    fw.composer.set_base("L", "乙单", "#fdecef")
    fw.composer.set_base("R", "乙单", "#fdecef")
    fw.delivery.mode = {"L": "deliver", "R": "drop"}
    r = fw.tick([Operation("x", "cross", "乙单"),
                 Operation("l", "left", "乙单")])
    assert auth(r["decisions"], "x") is False
    assert auth(r["decisions"], "l") is True
    assert r["report"]["regions"]["R"]["stale"] > 0


def test_public_window_hiding_confirm_blocks_cross():
    fw = fresh(("甲单", "甲单"))
    rdest = fw.regions["R"].tile_rect(*fw.regions["R"].dest_tile())
    fw.composer.set_window("renew", rdest, "#fff3cd", "续费")
    r = fw.tick([Operation("x", "cross", "甲单"),
                 Operation("l", "left", "甲单")])
    # public pixels shipped (no redaction) and versions agree ...
    assert r["report"]["regions"]["R"]["redactions"] == 0
    assert r["report"]["regions"]["R"]["version_seen"] == "甲单"
    # ... but the confirm affordance is hidden => cross refused
    assert auth(r["decisions"], "x") is False
    assert next(d["reason"] for d in r["decisions"] if d["oid"] == "x") == "right_confirm_hidden"
    assert auth(r["decisions"], "l") is True


# --------------------------------------------------------------------------- #
# 3. torn / ambiguous views are voided, not guessed
# --------------------------------------------------------------------------- #

def test_torn_region_is_ambiguous_and_voided():
    # Build a view where R holds a mix of 甲单 and 乙单 base tiles.
    fw = fresh(("乙单", "乙单"))
    fw.tick([])   # all 乙单, fresh
    # force half of R's tiles to an old 甲单 block directly (simulating a
    # half-arrived refresh); Endpoint API is the only way pixels change.
    region = fw.regions["R"]
    for cj in range(region.tiles_y):
        for ci in range(region.tiles_x):
            if ci % 2 == 0:
                key = tile_key("R", ci, cj)
                fw.endpoint.receive(key, {
                    "source": SOURCE_BASE, "color": "#e8eef7",
                    "label": "甲单" if (ci, cj) == (0, 0) else None,
                    "ver": "甲单", "hash": "forced",
                }, fw.tick_no)
    rep = fw.endpoint.report(fw.tick_no)
    assert len(rep["regions"]["R"]["versions_visible"]) == 2
    d = decide(Operation("x", "cross", "乙单"), rep, fw.regions, fw.tick_no)
    assert d["authorized"] is False
    assert d["reason"] == "right_view_ambiguous"


# --------------------------------------------------------------------------- #
# 4. endpoint cannot roll back to history / cannot be fed history to sign
# --------------------------------------------------------------------------- #

def test_endpoint_rejects_older_tick_write():
    fw = fresh()
    fw.tick([])   # tick 1 writes fresh pixels
    region = fw.regions["L"]
    key = tile_key("L", 0, 0)
    ok = fw.endpoint.receive(key, {"source": SOURCE_BASE, "color": "#000",
                                   "label": None, "ver": "旧",
                                   "hash": "old"}, 0)  # older tick
    assert ok is False
    # unchanged: still the fresh tick-1 content
    assert fw.endpoint.view()[key]["tick"] == fw.tick_no


def test_no_history_leaks_into_view():
    # history is recorded for audit but must never become the signing view.
    fw = fresh(("甲单", "甲单"))
    fw.tick([Operation("a", "cross", "甲单")])
    assert fw.history  # recorded
    # a brand-new endpoint (fresh terminal) still shows nothing until shipped
    ep = Endpoint(fw.regions)
    rep = ep.report(fw.tick_no)
    assert rep["regions"]["L"]["missing"] > 0
    assert rep["regions"]["L"]["version_seen"] is None


# --------------------------------------------------------------------------- #
# 5. delivery modes: late / drop / hold
# --------------------------------------------------------------------------- #

def test_late_block_arrives_next_tick():
    fw = fresh(("乙单", "乙单"))
    fw.delivery.mode = {"L": "late", "R": "deliver"}
    r1 = fw.tick([])
    # L was deferred -> terminal does not yet show it
    assert r1["report"]["regions"]["L"]["missing"] > 0
    fw.delivery.mode = {"L": "deliver", "R": "deliver"}
    r2 = fw.tick([])
    # the deferred block arrived at the start of tick 2
    assert r2["actions"]["arrived_late"] > 0
    assert r2["report"]["regions"]["L"]["version_seen"] == "乙单"


def test_hold_means_terminal_does_not_update():
    fw = fresh(("甲单", "甲单"))
    fw.tick([])
    fw.composer.set_base("L", "乙单", "#fdecef")
    fw.delivery.mode = {"L": "hold", "R": "deliver"}
    r = fw.tick([])
    assert r["report"]["regions"]["L"]["version_seen"] == "甲单"
    assert r["report"]["regions"]["L"]["stale"] > 0


# --------------------------------------------------------------------------- #
# 6. atomic handoff withholds the whole frame on any dirty tile
# --------------------------------------------------------------------------- #

def test_atomic_withholds_all_when_dirty():
    fw = fresh(("甲单", "甲单"))
    fw.tick([])
    fw.composer.set_base("L", "乙单", "#fdecef")
    dest = fw.regions["R"].tile_rect(*fw.regions["R"].dest_tile())
    fw.composer.set_privacy("dm", dest)
    fw.policy = "atomic"
    r = fw.tick([Operation("x", "cross", "甲单"),
                 Operation("l", "left", "乙单")])
    assert r["dirty"] is True
    assert not r["actions"]["sent"]
    # terminal keeps old 甲/甲 -> cross(甲) provable, left(乙) not
    assert r["report"]["regions"]["L"]["version_seen"] == "甲单"
    assert auth(r["decisions"], "x") is True
    assert auth(r["decisions"], "l") is False


# --------------------------------------------------------------------------- #
# 7. click is only accepted on actually-displayed, non-redacted pixels
# --------------------------------------------------------------------------- #

def test_click_on_redacted_tile_refused():
    fw = fresh(("甲单", "甲单"))
    dest = fw.regions["R"].tile_rect(*fw.regions["R"].dest_tile())
    fw.composer.set_privacy("dm", dest)
    fw.tick([])
    rep = fw.endpoint.report(fw.tick_no)
    d = fw.regions["R"].dest_tile()
    trect = fw.regions["R"].tile_rect(*d)
    res = check_click(trect.x + 5, trect.y + 5, rep, fw.regions)
    assert res["accepted"] is False
    assert res["reason"] == "redacted"


def test_click_on_visible_base_accepted():
    fw = fresh(("甲单", "甲单"))
    fw.tick([])
    rep = fw.endpoint.report(fw.tick_no)
    d = fw.regions["L"].dest_tile()
    trect = fw.regions["L"].tile_rect(*d)
    res = check_click(trect.x + 5, trect.y + 5, rep, fw.regions)
    assert res["accepted"] is True
    assert res["label"] == "前台"


# --------------------------------------------------------------------------- #
# 8. serialization round-trip preserves the signing semantics
# --------------------------------------------------------------------------- #

def test_consistent_stale_view_is_still_signable():
    # Whole-frame (atomic) handoff withheld the tick-2 frame, so the terminal
    # keeps a *consistent* old 甲/甲 view.  The brief says the customer may keep
    # seeing/querying the same-version old 甲单, so cross(甲) is honestly
    # signable against that actual view -- staleness alone must NOT veto it.
    fw = fresh(("甲单", "甲单"))
    fw.tick([])                     # tick 1: real 甲/甲
    fw.composer.set_base("L", "乙单", "#fdecef")
    dest = fw.regions["R"].tile_rect(*fw.regions["R"].dest_tile())
    fw.composer.set_privacy("dm", dest)
    fw.policy = "atomic"
    r = fw.tick([Operation("x", "cross", "甲单")])
    assert r["report"]["regions"]["L"]["stale"] > 0   # it IS stale ...
    assert r["report"]["regions"]["R"]["stale"] > 0
    assert auth(r["decisions"], "x") is True           # ... yet consistently 甲


def test_assess_view_flags_void_vs_incomplete():
    fw = fresh(("甲单", "甲单"))
    fw.tick([])
    inv = fw.tick([])["invalidation"]
    assert inv["void_regions"] == []
    assert inv["incomplete_regions"] == []
    assert inv["consistent"] is True
    assert inv["cross_region_same"] is True

    # cover part of R with privacy -> R incomplete, not void
    dest = fw.regions["R"].tile_rect(*fw.regions["R"].dest_tile())
    fw.composer.set_privacy("dm", dest)
    inv = fw.tick([])["invalidation"]
    assert "R" in inv["incomplete_regions"]
    assert inv["void_regions"] == []


def test_assess_view_flags_cross_region_disagreement():
    # L on 乙单, R still showing coherent old 甲单 -> not intra-region void,
    # but cross_region_same is False and the mismatch is surfaced as such.
    fw = fresh(("乙单", "甲单"))
    inv = fw.tick([])["invalidation"]
    assert inv["void_regions"] == []
    assert inv["cross_region_same"] is False
    assert inv["consistent"] is False
    assert set(inv["disagreeing_regions"]) == {"L", "R"}


def test_late_block_carries_intended_version_and_resolves_torn_state():
    # tick2: desktop advanced to 乙/乙 but L was deferred, R delivered -> the
    # terminal genuinely shows a torn L=甲/R=乙.  That is cross_region_disagree
    # (void), NOT something to sign.  tick3 the late block arrives -> 乙/乙,
    # cross(乙) becomes provable.
    fw = fresh(("甲单", "甲单"))
    fw.tick([])                         # tick1 甲/甲
    fw.composer.set_base("L", "乙单", "#fdecef")
    fw.composer.set_base("R", "乙单", "#fdecef")
    fw.delivery.mode = {"L": "late", "R": "deliver"}
    r2 = fw.tick([Operation("x", "cross", "乙单")])
    assert r2["report"]["regions"]["L"]["version_seen"] == "甲单"
    assert r2["report"]["regions"]["R"]["version_seen"] == "乙单"
    d2 = r2["decisions"][0]
    assert d2["authorized"] is False and d2["outcome"] == "void"
    assert d2["reason"] == "cross_region_disagree"

    fw.delivery.mode = {"L": "deliver", "R": "deliver"}
    r3 = fw.tick([Operation("y", "cross", "乙单")])
    assert r3["actions"]["arrived_late"] > 0
    assert r3["report"]["regions"]["L"]["version_seen"] == "乙单"
    assert auth(r3["decisions"], "y") is True


def test_state_roundtrip_preserves_view_and_decisions():
    fw = fresh(("乙单", "乙单"))
    fw.tick([Operation("x", "cross", "乙单")])
    d = forwarder_to_dict(fw)
    fw2 = forwarder_from_dict(d)
    rep1 = fw.endpoint.report(fw.tick_no)
    rep2 = fw2.endpoint.report(fw2.tick_no)
    assert rep1["regions"]["L"]["version_seen"] == rep2["regions"]["L"]["version_seen"]
    d2 = fw2.tick([Operation("y", "cross", "乙单")])
    assert auth(d2["decisions"], "y") is True


def test_http_api_smoke():
    import json
    import threading
    import tempfile
    import urllib.request
    from http.server import HTTPServer
    from privacy_forwarder import cli

    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    os.unlink(tmp.name)
    state = tmp.name

    # build the same handler the CLI's serve command uses, on an ephemeral port
    handler = cli._make_handler(state)
    srv = HTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    def post(path, obj):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(obj).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
            return r.status, json.loads(r.read())

    try:
        assert get("/health") == (200, {"ok": True})
        post("/draw", {"base": [
            {"rid": "L", "version": "甲单", "color": "#e8eef7"},
            {"rid": "R", "version": "甲单", "color": "#e8eef7"}]})
        _, res = post("/send", {"ops": [
            {"oid": "x", "kind": "cross", "version": "甲单"}]})
        assert res["decisions"][0]["authorized"] is True

        # cover R confirm with privacy; cross(乙) must wait, left(乙) blocked too
        post("/draw", {
            "base": [{"rid": "L", "version": "乙单", "color": "#fdecef"}],
            "privacy": [{"pid": "dm", "rect": [840, 320, 60, 60]}]})
        _, res = post("/send", {"ops": [
            {"oid": "e", "kind": "cross", "version": "乙单"},
            {"oid": "l", "kind": "left", "version": "乙单"}]})
        d = {x["oid"]: x for x in res["decisions"]}
        assert d["e"]["authorized"] is False
        assert d["l"]["authorized"] is True
        assert "R" in res["invalidation"]["incomplete_regions"]

        # GET /show must work (regression: it used to be POST-only)
        st, show = get("/show")
        assert st == 200 and "regions" in show
        assert show["regions"]["R"]["redactions"] > 0

        # click on redacted R confirm -> 409
        st, _ = post("/click", {"x": 845, "y": 325})
        assert st == 409
        # click on visible L 前台 -> 200
        st, _ = post("/click", {"x": 455, "y": 170})
        assert st == 200
    finally:
        srv.shutdown()
        if os.path.exists(state):
            os.unlink(state)


# --------------------------------------------------------------------------- #

ALL = [
    ("privacy_redaction_is_fail_closed", test_privacy_redaction_is_fail_closed),
    ("privacy_does_not_reveal_base_underneath", test_privacy_does_not_reveal_base_underneath),
    ("privacy_within_tile_leaks_nothing_partial", test_privacy_within_tile_leaks_nothing_partial),
    ("cross_requires_same_version_both_visible", test_cross_requires_same_version_both_visible),
    ("cross_refused_when_versions_differ", test_cross_refused_when_versions_differ),
    ("cross_refused_when_right_redacted", test_cross_refused_when_right_redacted),
    ("left_op_ignores_right_state", test_left_op_ignores_right_state),
    ("stale_right_block_blocks_cross", test_stale_right_block_blocks_cross),
    ("public_window_hiding_confirm_blocks_cross", test_public_window_hiding_confirm_blocks_cross),
    ("torn_region_is_ambiguous_and_voided", test_torn_region_is_ambiguous_and_voided),
    ("endpoint_rejects_older_tick_write", test_endpoint_rejects_older_tick_write),
    ("no_history_leaks_into_view", test_no_history_leaks_into_view),
    ("late_block_arrives_next_tick", test_late_block_arrives_next_tick),
    ("hold_means_terminal_does_not_update", test_hold_means_terminal_does_not_update),
    ("atomic_withholds_all_when_dirty", test_atomic_withholds_all_when_dirty),
    ("click_on_redacted_tile_refused", test_click_on_redacted_tile_refused),
    ("click_on_visible_base_accepted", test_click_on_visible_base_accepted),
    ("consistent_stale_view_is_still_signable", test_consistent_stale_view_is_still_signable),
    ("assess_view_flags_void_vs_incomplete", test_assess_view_flags_void_vs_incomplete),
    ("assess_view_flags_cross_region_disagreement", test_assess_view_flags_cross_region_disagreement),
    ("late_block_carries_intended_version_and_resolves_torn_state", test_late_block_carries_intended_version_and_resolves_torn_state),
    ("state_roundtrip_preserves_view_and_decisions", test_state_roundtrip_preserves_view_and_decisions),
    ("http_api_smoke", test_http_api_smoke),
]


def main() -> int:
    fails = 0
    for name, fn in ALL:
        if not run_test(name, fn):
            fails += 1
    print()
    print(f"{len(ALL) - fails}/{len(ALL)} tests passed")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
