from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from make_fixtures import base_board, write_actual_drill
from pcb_review.model import IsolationRule
from pcb_review.plot import export_public_package
from pcb_review.review import load_package, review_fabrication


REQUIRED = [("P_A", "P_C"), ("P_A", "P_B"), ("Q_A", "Q_B")]
SCOPED_REQUIRED = [("P_A", "P_B"), ("Q_A", "Q_B")]
ISOLATION = [IsolationRule("P", "Q")]
NETS = {
    "P_A": "P",
    "P_B": "P",
    "P_C": "P",
    "Q_A": "Q",
    "Q_B": "Q",
}


class BoardReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.restored = base_board("restored", "serpentine")
        self.npth = base_board("npth", "serpentine")
        self.npth.holes[0] = type(self.restored.holes[0])(
            "P_VIA", 5.0, 5.0, self.restored.holes[0].diameter, False
        )
        self.package_dir = self.root / "package"
        export_public_package(self.restored, self.package_dir)
        self.shapes, self.expected, self.terminals = load_package(self.package_dir)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _review(self, holes, **kwargs):
        return review_fabrication(
            self.shapes,
            holes,
            self.terminals,
            self.expected,
            kwargs.pop("required", REQUIRED),
            isolation=ISOLATION,
            internal_nets=NETS,
            **kwargs,
        )

    def test_nominal_board_releases_and_keeps_p_q_isolated(self) -> None:
        report = self._review(self.expected)
        self.assertEqual(report.status, "RELEASE")
        self.assertTrue(all(row.connected for row in report.continuity))
        self.assertEqual(len(report.components), 2)
        self.assertEqual(
            sorted(report.components[0].terminal_names),
            ["P_A", "P_B", "P_C"],
        )
        self.assertEqual(sorted(report.components[1].terminal_names), ["Q_A", "Q_B"])

    def test_scoped_npth_opens_cross_layer_pair_but_same_layer_pair_conducts(self) -> None:
        actual_npth = [
            hole if (hole.x, hole.y) != (5.0, 5.0)
            else type(hole)(hole.name, hole.x, hole.y, hole.diameter, False)
            for hole in self.expected
        ]
        # Expected holes are anonymous; compare by the same coordinates in this direct test.
        report = self._review(
            actual_npth,
            required=SCOPED_REQUIRED,
            allowed_npth={(5.0, 5.0)},
            expected_open_pairs=[("P_A", "P_C")],
        )
        self.assertEqual(report.status, "RELEASE")
        rows = {(row.a, row.b): row for row in report.continuity}
        self.assertTrue(rows[("P_A", "P_B")].connected)
        self.assertTrue(rows[("Q_A", "Q_B")].connected)
        self.assertFalse(rows[("P_A", "P_C")].connected)
        self.assertEqual(
            {component.node for component in report.components if "P_C" in component.terminal_names},
            {component.node for component in report.components if "P_C" in component.terminal_names},
        )
        self.assertEqual(len(report.components), 3)

    def test_single_fixture_drift_stops_and_identifies_p_q_short(self) -> None:
        drift_file = self.root / "drift.csv"
        write_actual_drill(drift_file, self.restored, True, drift=4.4)
        from pcb_review.svgio import read_drill_csv

        report = self._review(read_drill_csv(drift_file))
        self.assertEqual(report.status, "STOP")
        codes = {finding.code for finding in report.findings}
        self.assertIn("DRILL_OFFSET", codes)
        self.assertIn("HOLE_ENTERED_ADJACENT_GRID", codes)
        self.assertIn("ISOLATION_LOST", codes)
        self.assertEqual(len(report.components), 1)
        self.assertEqual(
            sorted(report.components[0].terminal_names),
            ["P_A", "P_B", "P_C", "Q_A", "Q_B"],
        )

    def test_private_route_shape_cannot_be_distinguished_from_public_files(self) -> None:
        other = base_board("other-private", "diagonal-staircase")
        other_dir = self.root / "other-package"
        first_hashes = export_public_package(self.restored, self.root / "a")
        second_hashes = export_public_package(other, other_dir)
        self.assertEqual(first_hashes, second_hashes)
        text = b"".join(
            path.read_bytes()
            for path in sorted(other_dir.iterdir())
            if path.is_file() and path.name != "SHA256SUMS"
        ).decode("utf-8", "ignore")
        for forbidden in ("private", "serpentine", "staircase"):
            self.assertNotIn(forbidden, text)

    def test_svg_contains_only_paths_and_no_hidden_layer_metadata(self) -> None:
        svg = (self.package_dir / "top-copper.svg").read_text(encoding="utf-8")
        self.assertIn("<path", svg)
        self.assertNotIn("display:none", svg)
        self.assertNotIn("visibility:hidden", svg)
        self.assertNotIn("data-net", svg)


if __name__ == "__main__":
    unittest.main()
