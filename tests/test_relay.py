import tempfile
import unittest
from pathlib import Path

from relay_cert.certify import (
    band_distance,
    certify,
    enumerate_combinations,
    pair_cameras,
)
from relay_cert.codebook import design_codebook, distinguish_masked
from relay_cert.model import PALETTE
from relay_cert.pipeline import certify_scene
from relay_cert.scenarios import build_scene
from relay_cert.score import decide
from relay_cert.vision import crop_edge_cells, extract_traces, load_frame
from relay_cert.witness import apply_witness, choose_section


class EndToEndTests(unittest.TestCase):
    def test_clean_scene_unique_combo_and_rankings(self):
        with tempfile.TemporaryDirectory() as d:
            scene = build_scene("clean")
            result, notes = certify_scene(scene, Path(d))
        self.assertEqual(len(result.combinations), 1)
        self.assertEqual(result.disputed, [])
        self.assertTrue(all(r["team_certified"] for r in result.certified_rankings))
        self.assertTrue(result.line_check["within_tolerance"])
        for frame in notes["frames"].values():
            self.assertFalse(frame["detected_flip"])

    def test_ambiguous_scene_keeps_multiple_combos_no_guess(self):
        with tempfile.TemporaryDirectory() as d:
            scene = build_scene("ambiguous")
            result, _ = certify_scene(scene, Path(d))
        self.assertGreaterEqual(len(result.combinations), 2)
        self.assertTrue(result.disputed)
        # 存在同队互斥/不可重复用杆的证据，而不是把局部最像者直接判掉。
        found_exclusion = any(
            e.exclusions for e in result.events if e.event.event_id in result.disputed
        ) or any(e.exclusions for e in result.events)
        self.assertTrue(found_exclusion)
        # 共同成立的名次仍可署名。
        fixed = [r for r in result.certified_rankings if r["team_certified"]]
        self.assertTrue(fixed)
        # 第一名“事件”在所有组合下都可署给甲队；队级总冠军待核，
        # 两队都进加赛但编排不中断。
        first = next(r for r in result.certified_rankings if r["place"] == 1)
        self.assertEqual(first["team_certified"], "甲")
        roster = decide(result.to_dict(), {"甲": 0.0, "乙": 0.0}, quota=1)
        self.assertIn("甲", roster["runoff_teams"])
        self.assertIn("乙", roster["runoff_teams"])
        self.assertTrue(any(e["kind"] == "pending" for e in roster["roster"]))
        # 只改一个接棒罚秒：第一名事件归属不变，名单 id 改变但编排继续。
        roster2 = decide(result.to_dict(), {"甲": 0.0, "乙": 0.5}, quota=1)
        first2 = next(r for r in result.certified_rankings if r["place"] == 1)
        self.assertEqual(first2["team_certified"], "甲")
        self.assertIn("甲", roster2["runoff_teams"])
        self.assertNotEqual(roster["roster_id"], roster2["roster_id"])

    def test_witness_section_resolves_and_is_replayable(self):
        with tempfile.TemporaryDirectory() as d:
            scene = build_scene("ambiguous")
            result, _ = certify_scene(scene, Path(d))
            section = choose_section(result)
            self.assertIsNotNone(section)
            event_id, cell = section["event"], section["band_cell"]
            if section["level"] == "full":
                for color in section["colors_offered"]:
                    outcome = apply_witness(result, event_id, cell, color)
                    self.assertTrue(outcome["resolves"], color)
                    self.assertEqual(outcome["combinations_after"], 1)
            else:
                # 部分收窄截面也必须真实减少组合数，且整个过程可复读。
                before = len(result.combinations)
                outcome = apply_witness(result, event_id, cell,
                                        section["colors_offered"][0])
                self.assertLess(outcome["combinations_after"], before)
    def test_flip_is_detected_and_restored_for_brush_pair(self):
        with tempfile.TemporaryDirectory() as d:
            scene = build_scene("flip")
            result, notes = certify_scene(scene, Path(d))
        cam_b = notes["frames"]["B"]
        self.assertTrue(cam_b["detected_flip"])
        self.assertTrue(cam_b["restored"])
        # 翻转还原后仍能配对并认证，事件数不丢。
        self.assertEqual(len(result.events), 8)
        self.assertTrue(result.line_check["within_tolerance"])

    def test_external_crop_one_edge_cell(self):
        with tempfile.TemporaryDirectory() as d:
            scene = build_scene("crop")
            result, notes = certify_scene(scene, Path(d), crop=("right", 1))
        self.assertIsNotNone(notes["crop"])
        # 裁边后系统拒绝猜色，但仍能完成认证并标出争议。
        self.assertEqual(len(result.events), 8)
        self.assertGreaterEqual(len(result.combinations), 1)

    def test_codebook_two_camera_and_budget(self):
        plan = design_codebook(list(PALETTE.keys())[:5], 5, need=8,
                               level="two_camera", spare_per_selected=1)
        self.assertEqual(len(plan.selected), 8)
        self.assertEqual(plan.budget["temporary_spare_poles"], 8)
        # 同等级内任意两码全码可见时不得零错配。
        for i, a in enumerate(plan.selected):
            for b in plan.selected[i + 1:]:
                self.assertNotEqual(band_distance(list(a), list(b)), 0)
        stronger = design_codebook(list(PALETTE.keys())[:6], 6, need=8,
                                   level="rotation_mask")
        self.assertEqual(len(stronger.selected), 8)
        for i, a in enumerate(stronger.selected):
            for b in stronger.selected[i + 1:]:
                self.assertTrue(distinguish_masked(tuple(a), tuple(b)))

    def test_png_roundtrip_and_crop_file(self):
        with tempfile.TemporaryDirectory() as d:
            scene = build_scene("clean")
            paths = certify_scene  # import guard
            from relay_cert.render import render_scene
            paths = render_scene(scene, Path(d))
            frame = load_frame(paths["A"], "A")
            self.assertEqual(frame.width, scene.width)
            cropped = Path(d) / "cropped.png"
            crop_edge_cells(paths["A"], cropped, "left", 1)
            frame2 = load_frame(cropped, "A")
            self.assertEqual(frame2.width, scene.width - 1)
            traces = extract_traces(frame2)
            self.assertTrue(traces)

    def test_tie_and_runoff_ranking(self):
        with tempfile.TemporaryDirectory() as d:
            scene = build_scene("clean")
            result, _ = certify_scene(scene, Path(d))
        # 清晰场只有一种组合；构造罚秒让两队有效成绩相同，名额两人都给。
        roster = decide(result.to_dict(), {"甲": 0.0, "乙": 0.0}, quota=2)
        self.assertEqual(len(roster["qualified"]), 2)
        kinds = {e["kind"] for e in roster["roster"]}
        self.assertNotIn("pending", kinds)


if __name__ == "__main__":
    unittest.main()
