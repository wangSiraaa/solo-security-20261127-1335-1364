"""Tests for the offline reading-room reader.

Run with:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reading_room import reader, routing
from reading_room.assets import (
    PUBLIC,
    SUBSCRIBER,
    UNKNOWN,
    classify_url,
    is_shared_public_url,
)
from reading_room.edition import ISSUE_OWNER, load_edition, seal_issue
from reading_room.fixtures import ensure_fixtures, promo_inline_bytes, write_station
from reading_room.supply import SupplyStation
from reading_room.wire import load_mailbox

ROOT = Path(__file__).resolve().parent.parent
ensure_fixtures(ROOT)
FIXTURES = ROOT / "fixtures"
STATION_SRC = ROOT / "supply_station" / "assets"


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rr-test-"))
        self.station_root = self.tmp / "station"
        shutil.copytree(STATION_SRC, self.station_root)
        self.log_path = self.tmp / "requests.log"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seal(self, fixture="issue.json", name="edition"):
        with SupplyStation(self.station_root, self.log_path) as station:
            mb = load_mailbox(FIXTURES / fixture)
            edition = seal_issue(mb, self.tmp / name, base_url=station.base_url)
            log = station.log.entries()
        return edition, log


# ---------------------------------------------------------------------------
# Asset classification
# ---------------------------------------------------------------------------

class ClassificationTests(unittest.TestCase):
    def test_public_and_subscriber_namespaces(self):
        self.assertEqual(classify_url("/public/issue1420/x.png"), PUBLIC)
        self.assertEqual(classify_url("/sub/reader_b/promo.png"), SUBSCRIBER)
        self.assertEqual(classify_url("/other/x.png"), UNKNOWN)
        self.assertEqual(classify_url(""), UNKNOWN)

    def test_only_public_is_shared(self):
        self.assertTrue(is_shared_public_url("/public/issue1420/x.png"))
        self.assertFalse(is_shared_public_url("/sub/reader_b/promo.png"))
        self.assertFalse(is_shared_public_url("/tmp/x.png"))

    def test_query_string_does_not_change_namespace(self):
        self.assertEqual(classify_url("/public/i/x.png?v=2"), PUBLIC)
        self.assertEqual(classify_url("/sub/r/p.png?t=1"), SUBSCRIBER)


# ---------------------------------------------------------------------------
# Sealing / privacy
# ---------------------------------------------------------------------------

class SealTests(TempCase):
    def test_subscriber_addresses_never_requested(self):
        _, log = self.seal()
        paths = [e["path"] for e in log]
        self.assertFalse(
            [p for p in paths if p.startswith("/sub/")],
            f"subscriber namespace was requested: {paths}",
        )

    def test_shared_public_illustration_fetched_once(self):
        _, log = self.seal()
        shared = [e for e in log if e["path"].endswith("/shared_illu.png")]
        # Both letters reference the same shared illustration; seal dedupes.
        self.assertEqual(len(shared), 1)

    def test_public_only_requests(self):
        _, log = self.seal()
        for e in log:
            self.assertTrue(e["path"].startswith("/public/"), e)

    def test_missing_public_is_substituted_and_disclosed(self):
        edition, log = self.seal()
        lost = [e for e in log if e["path"].endswith("/lost.png")]
        self.assertEqual(len(lost), 1)
        self.assertEqual(lost[0]["status"], 404)
        b = edition.letter("letter_b")
        lost_asset = edition.asset(f"letter_b::lost")
        self.assertTrue(lost_asset.substituted)
        self.assertIn(lost_asset.key, b.assets)
        self.assertTrue(lost_asset.label)

    def test_subscriber_asset_is_labelled_substitute(self):
        edition, _ = self.seal()
        promo_b = edition.asset("letter_b::promo_b")
        self.assertTrue(promo_b.substituted)
        self.assertEqual(promo_b.kind, SUBSCRIBER)
        self.assertTrue(promo_b.label)

    def test_inline_asset_used_without_request(self):
        edition, _ = self.seal()
        promo_a = edition.asset("letter_a::promo_a")
        self.assertEqual(promo_a.provenance, "inline")
        self.assertFalse(promo_a.substituted)
        self.assertEqual(edition.local_path(promo_a.key).read_bytes(), promo_inline_bytes())

    def test_same_look_different_ownership(self):
        edition, _ = self.seal()
        promo_a = edition.asset("letter_a::promo_a")
        promo_b = edition.asset("letter_b::promo_b")
        # Ownership differs...
        self.assertNotEqual(promo_a.owner, promo_b.owner)
        self.assertEqual(promo_a.provenance, "inline")
        self.assertEqual(promo_b.provenance, "substituted")
        # ...and cross-reading is refused.
        a = edition.letter("letter_a")
        with self.assertRaises(PermissionError):
            edition.assert_visible(a, promo_b.key)

    def test_issue_has_three_letters(self):
        edition, _ = self.seal()
        self.assertEqual(
            [l.letter_id for l in edition.letters],
            ["letter_a", "letter_b", "letter_c"],
        )

    def test_third_letter_subscriber_asset_withheld(self):
        edition, log = self.seal()
        self.assertFalse(
            [e for e in log if e["path"].startswith("/sub/")],
            "no subscriber address may be requested, including reader C's",
        )
        promo_c = edition.asset("letter_c::promo_c")
        self.assertTrue(promo_c.substituted)

    def test_third_letter_routes_step_free(self):
        edition, _ = self.seal()
        c = edition.letter("letter_c")
        route = reader.plan(edition, c, "C-303", mode=routing.STEP_FREE)
        self.assertFalse(route.has_stairs)
        self.assertIn(routing.LETTER_TEXT, route.sources)

    def test_issue_map_is_visible_to_all(self):
        edition, _ = self.seal()
        a = edition.letter("letter_a")
        b = edition.letter("letter_b")
        cm = edition.asset(edition.common_map_key)
        self.assertEqual(cm.owner, ISSUE_OWNER)
        self.assertIs(edition.assert_visible(a, cm.key), cm)
        self.assertIs(edition.assert_visible(b, cm.key), cm)

    def test_sealed_edition_round_trips(self):
        edition, _ = self.seal()
        reloaded = load_edition(self.tmp / "edition")
        self.assertEqual(reloaded.issue, edition.issue)
        self.assertEqual(
            sorted(reloaded.assets), sorted(edition.assets)
        )
        for key, asset in edition.assets.items():
            self.assertEqual(reloaded.assets[key].owner, asset.owner)
            self.assertEqual(reloaded.assets[key].provenance, asset.provenance)
        self.assertEqual(len(reloaded.substitutions), len(edition.substitutions))


# ---------------------------------------------------------------------------
# Offline reads add nothing to the station log
# ---------------------------------------------------------------------------

class OfflineTests(TempCase):
    def _snapshot(self):
        return [(e["method"], e["path"], e["status"]) for e in SupplyStation(
            self.station_root, self.log_path).log.entries()]

    def test_two_reading_orders_identical_log(self):
        edition, seal_log = self.seal()
        baseline = self._snapshot()

        a = edition.letter("letter_a")
        b = edition.letter("letter_b")

        # Order 1
        reader.render_letter_text(edition, a, "A-101")
        reader.render_letter_text(edition, b, "B-202")
        reader.render_guidance_text(edition, a, "A-101", mode=routing.STEP_FREE)
        reader.render_letter_html(edition, a)
        reader.render_guidance_html(edition, b, "B-202")
        reader.render_guidance_text(edition, b, "B-202", mode=routing.STANDARD)
        after1 = self._snapshot()

        # Order 2 (different order, repeated switches, re-prints)
        reader.render_letter_html(edition, b)
        reader.render_letter_text(edition, a, "A-101")
        reader.render_guidance_text(edition, b, "B-202", mode=routing.STEP_FREE)
        reader.render_letter_text(edition, b)
        reader.render_guidance_text(edition, a, "A-101", mode=routing.STANDARD)
        reader.render_letter_html(edition, a)
        after2 = self._snapshot()

        self.assertEqual(after1, baseline)
        self.assertEqual(after2, baseline)
        self.assertEqual(after1, after2)

    def test_render_does_not_need_network(self):
        edition, _ = self.seal()
        # Stop any station; render must still work from local files.
        a = edition.letter("letter_a")
        text = reader.render_letter_text(edition, a)
        self.assertIn("A-101", text)
        html = reader.render_letter_html(edition, a)
        self.assertIn("<img", html)


# ---------------------------------------------------------------------------
# Routing: pointers from evidence only
# ---------------------------------------------------------------------------

class RoutingTests(TempCase):
    def setUp(self):
        super().setUp()
        self.edition, _ = self.seal()
        self.a = self.edition.letter("letter_a")
        self.b = self.edition.letter("letter_b")

    def test_common_map_stair_shortcut_used_in_standard(self):
        route = reader.plan(self.edition, self.a, "A-101", mode=routing.STANDARD)
        self.assertTrue(route.has_stairs)
        self.assertIn(routing.COMMON_MAP, route.sources)

    def test_step_free_never_uses_stairs(self):
        for letter, ev in [(self.a, "A-101"), (self.b, "B-202")]:
            route = reader.plan(self.edition, letter, ev, mode=routing.STEP_FREE)
            self.assertFalse(route.has_stairs)

    def test_letter_a_step_free_uses_map_detour(self):
        route = reader.plan(self.edition, self.a, "A-101", mode=routing.STEP_FREE)
        self.assertIn(routing.LETTER_MAP, route.sources)
        nodes = {route.node_label(s.edge.v) for s in route.steps}
        self.assertIn("西廊", nodes)

    def test_letter_b_step_free_uses_written_directions(self):
        route = reader.plan(self.edition, self.b, "B-202", mode=routing.STEP_FREE)
        self.assertIn(routing.LETTER_TEXT, route.sources)
        nodes = {route.node_label(s.edge.v) for s in route.steps}
        self.assertIn("东廊", nodes)

    def test_reliable_text_route_usable_for_both_letters(self):
        for letter, ev in [(self.a, "A-101"), (self.b, "B-202")]:
            for mode in routing.MODES:
                route = reader.plan(
                    self.edition, letter, ev, mode=mode, basis=routing.LETTER_TEXT
                )
                self.assertFalse(route.has_stairs)

    def test_no_invented_route_off_gate(self):
        """A start node with no delivered edges cannot be signed."""
        self.edition.node_labels["nowhere"] = "无通道节点"
        with self.assertRaises(routing.RouteError):
            routing.plan_route(
                [(routing.LETTER_TEXT, _desc_of(self.a))],
                "nowhere", "venue", labels=self.edition.node_labels,
            )

    def test_unknown_basis_rejected(self):
        with self.assertRaises(reader.ReaderError):
            reader.plan(self.edition, self.b, "B-202", basis=routing.LETTER_MAP)

    def test_event_lookup_by_text_number(self):
        self.assertEqual(self.a.event("A-101")["title"], "城市步行与无障碍设计")
        with self.assertRaises(KeyError):
            self.a.event("nope")


def _desc_of(letter):
    from reading_room.wire import RouteDescription

    return RouteDescription.from_dict(letter.directions, label="letter_text")


# ---------------------------------------------------------------------------
# Revised-map variant
# ---------------------------------------------------------------------------

class RevisedMapTests(TempCase):
    def test_only_letter_a_map_changes(self):
        base = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))
        rev = json.loads(
            (FIXTURES / "issue_revised_map.json").read_text(encoding="utf-8")
        )
        b0 = [l for l in base["letters"] if l["letter_id"] == "letter_b"][0]
        b1 = [l for l in rev["letters"] if l["letter_id"] == "letter_b"][0]
        self.assertEqual(b0, b1)
        # Letter A differs only in its map block.
        a0 = [l for l in base["letters"] if l["letter_id"] == "letter_a"][0]
        a1 = [l for l in rev["letters"] if l["letter_id"] == "letter_a"][0]
        for key in ("body_text", "directions", "events", "assets"):
            self.assertEqual(a0[key], a1[key], key)
        self.assertNotEqual(a0["letter_map"], a1["letter_map"])

    def test_revised_oneway_forces_detour(self):
        edition, _ = self.seal(fixture="issue_revised_map.json")
        a = edition.letter("letter_a")
        route = reader.plan(edition, a, "A-101", mode=routing.STEP_FREE)
        self.assertFalse(route.has_stairs)
        pairs = {(s.edge.u, s.edge.v) for s in route.steps}
        # Cannot go lobby -> west (now one-way west -> lobby).
        self.assertNotIn(("lobby", "west_corridor"), pairs)
        # Must approach venue via the east corridor instead.
        self.assertIn(("east_corridor", "venue"), pairs)

    def test_base_map_allows_west_detour(self):
        edition, _ = self.seal(fixture="issue.json")
        a = edition.letter("letter_a")
        route = reader.plan(edition, a, "A-101", mode=routing.STEP_FREE)
        pairs = {(s.edge.u, s.edge.v) for s in route.steps}
        self.assertIn(("west_corridor", "venue"), pairs)

    def test_revised_wheelchair_guidance_text_has_no_stairs_section(self):
        edition, _ = self.seal(fixture="issue_revised_map.json")
        a = edition.letter("letter_a")
        text = reader.render_guidance_text(edition, a, "A-101", mode=routing.STEP_FREE)
        self.assertIn("本路线全程无楼梯", text)
        # No step line may be marked as stairs.
        self.assertNotIn("[楼梯]", text)

    def test_letter_b_unaffected_by_revised_a(self):
        edition, _ = self.seal(fixture="issue_revised_map.json")
        b = edition.letter("letter_b")
        route = reader.plan(edition, b, "B-202", mode=routing.STEP_FREE)
        self.assertFalse(route.has_stairs)
        self.assertIn(routing.LETTER_TEXT, route.sources)


# ---------------------------------------------------------------------------
# One-way semantics
# ---------------------------------------------------------------------------

class OneWayTests(unittest.TestCase):
    def _desc(self, edges):
        from reading_room.wire import RouteDescription

        return RouteDescription.from_dict({"edges": edges})

    def test_one_way_edge_not_traversable_backwards(self):
        desc = self._desc([
            {"u": "a", "v": "b", "access": "corridor", "one_way": True},
            {"u": "b", "v": "c", "access": "corridor"},
        ])
        route = routing.plan_route(
            [(routing.LETTER_TEXT, desc)], "a", "c", labels={}
        )
        self.assertEqual([s.edge.v for s in route.steps], ["b", "c"])
        with self.assertRaises(routing.RouteError):
            routing.plan_route(
                [(routing.LETTER_TEXT, desc)], "c", "a", labels={}
            )

    def test_step_free_drops_stairs_entirely(self):
        desc = self._desc([
            {"u": "a", "v": "b", "access": "stairs"},
        ])
        with self.assertRaises(routing.RouteError):
            routing.plan_route(
                [(routing.LETTER_TEXT, desc)], "a", "b",
                mode=routing.STEP_FREE, labels={},
            )


class BundleTests(TempCase):
    def test_bundle_links_and_local_images(self):
        import re

        from reading_room.browse import build_bundle

        edition, _ = self.seal()
        index = build_bundle(edition, self.tmp / "bundle")
        self.assertTrue(index.exists())
        page = (self.tmp / "bundle" / "letter_letter_a.html").read_text(encoding="utf-8")
        # Every <img src> must resolve to a real local file.
        for src in re.findall(r'src="([^"]+)"', page):
            self.assertTrue((self.tmp / "bundle" / src).exists(), src)
        # At least one guidance page exists per event/mode.
        for ev in ("A-101", "B-202", "C-303"):
            for mode in ("standard", "step_free"):
                matches = list((self.tmp / "bundle").glob(f"guidance_*_{ev}_{mode}.html"))
                self.assertEqual(len(matches), 1, (ev, mode))

    def test_bundle_makes_no_requests(self):
        from reading_room.browse import build_bundle

        edition, _ = self.seal()
        before = len(SupplyStation(self.station_root, self.log_path).log.entries())
        build_bundle(edition, self.tmp / "bundle")
        after = len(SupplyStation(self.station_root, self.log_path).log.entries())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
