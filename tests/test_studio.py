from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from subtitle_studio import FontStudio, LayoutProfile  # noqa: E402
from subtitle_studio.acceptance import run_acceptance  # noqa: E402
from subtitle_studio.errors import PackageQuotaError, StaleAdmissionError  # noqa: E402

PACKAGES = ROOT / "packages"


class StudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.studio = FontStudio(store_dir=self.tmp.name)
        self.fangyuan = self.studio.import_path(PACKAGES / "fangyuan.json")
        self.gewu = self.studio.import_path(PACKAGES / "gewu.json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_built_in_acceptance_narrative(self) -> None:
        result = run_acceptance(verbose=False)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(len(result["checks"]), 8)

    def test_two_packages_and_persisted_canvas_readback(self) -> None:
        self.assertEqual({p["package_id"] for p in self.studio.list_packages()}, {"fangyuan", "gewu"})
        lock = self.studio.lock_layout("启明学校", package_id="gewu", line_width=16)
        again = self.studio.read_canvas(lock["lock_id"])
        self.assertEqual(again.width, 16)
        self.assertEqual(again.height, 6)
        self.assertEqual(again.matrix, lock["canvas"]["matrix"])
        self.assertTrue(all(g["promised_whole"] for g in lock["glyph_results"]))

        # A new studio process rooted at the same store can read the lock.
        reopened = FontStudio(store_dir=self.tmp.name)
        self.assertEqual(reopened.read_canvas(lock["lock_id"]).matrix, again.matrix)

    def test_self_reference_makes_old_admission_stale(self) -> None:
        old_id = self.fangyuan["admission_id"]
        raw = json.loads((PACKAGES / "fangyuan.json").read_text(encoding="utf-8"))
        mutated = copy.deepcopy(raw)
        mutated["primitives"]["yong"].append({"op": "ref", "ref": "yong"})
        revised = self.studio.import_dict(mutated)

        self.assertIn("永", revised["rejected"])
        with self.assertRaises(StaleAdmissionError):
            self.studio.verify_admission(old_id)

        lock = self.studio.lock_layout("永", package_id="fangyuan", line_width=16)
        glyph = lock["glyph_results"][0]
        self.assertEqual(glyph["outcome"], "substituted")
        self.assertEqual(glyph["requested_attempt"]["failure_reason"], "CycleDetected")
        self.assertEqual(lock["canvas"]["filled_cells"], 6)

        restored = self.studio.import_dict(raw)
        self.assertEqual(restored["content_hash"], self.fangyuan["content_hash"])
        self.assertTrue(self.studio.verify_admission(old_id)["valid"])

    def test_narrow_profile_forces_new_whole_or_fallback_decision(self) -> None:
        wide = self.studio.lock_layout("永", package_id="fangyuan", line_width=16)
        narrow = self.studio.lock_layout("永", package_id="fangyuan", line_width=15)
        self.assertNotEqual(wide["canvas"]["matrix"], narrow["canvas"]["matrix"])
        self.assertEqual(narrow["glyph_results"][0]["outcome"], "substituted")

        whole = self.studio.lock_layout(
            "永",
            package_id="fangyuan",
            line_width=15,
            work_budget=40,
            cell_budget=16,
        )
        self.assertEqual(whole["glyph_results"][0]["outcome"], "committed")
        self.assertEqual(whole["glyph_results"][0]["final_attempt"]["cell_writes"], 13)

    def test_duplicate_condition_records_one_decision(self) -> None:
        pack = {
            "id": "dup",
            "glyphs": {"A": "root"},
            "primitives": {
                "a": [
                    {
                        "op": "branch",
                        "when": {"line_width_lte": 10},
                        "then": [{"op": "square", "x": 0, "y": 0}],
                        "else": [{"op": "square", "x": 0, "y": 4}],
                    }
                ],
                "b": [
                    {
                        "op": "branch",
                        "when": {"max_line_width": 10},
                        "then": [{"op": "square", "x": 2, "y": 0}],
                        "else": [{"op": "square", "x": 2, "y": 4}],
                    }
                ],
                "root": [{"ref": "a"}, {"ref": "b"}],
            },
        }
        self.studio.import_dict(pack)
        lock = self.studio.lock_layout("A", package_id="dup", line_width=10)
        self.assertEqual(len(lock["condition_decisions"]), 1)
        self.assertEqual(lock["condition_decisions"][0]["occurrences"], 2)
        self.assertEqual(lock["condition_decisions"][0]["result"], True)

    def test_classic_cut_is_all_admitted(self) -> None:
        cut = self.studio.classic_cut("fangyuan")
        imported = self.studio.import_dict(cut)
        self.assertEqual(set(imported["classic_glyphs"]), set(cut["glyphs"]))
        lock = self.studio.lock_layout("启明学校", package_id=cut["id"], mode="classic", line_width=16)
        self.assertEqual(lock["promise"], "whole_line_install_time_guaranteed")

    def test_package_byte_quota_checked(self) -> None:
        from subtitle_studio.profiles import ImportLimits

        small = FontStudio(import_limits=ImportLimits(max_package_bytes=128))
        with self.assertRaises(PackageQuotaError):
            small.import_path(PACKAGES / "fangyuan.json")

    def test_profile_rejects_bad_mode(self) -> None:
        with self.assertRaises(ValueError):
            LayoutProfile(text="x", mode="weird")


if __name__ == "__main__":
    unittest.main()
