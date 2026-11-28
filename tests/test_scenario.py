"""端到端叙事测试：把题目里的完整流程串成一条可复演的流水线。"""
from __future__ import annotations

import json
import os
import unittest

from glock import bank, exporter, reader, workbench
from glock.analysis import analyze_bar
from glock.chords import enumerate_chords
from glock.tuning import load_temperament
from tests.helpers import (LICENSED_NOTES, TEST_CHORDS, bar_paths, build_once,
                           make_license, temperament_spec)


class EndToEndScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = build_once()
        cls.root = cls.tmp.name

    def test_full_story(self):
        # 1) 培训室拿到授权外借件（两份母库产物不可区分）
        lic = make_license()
        mat = bank.open_loaned_materials(
            os.path.join(self.root, "factoryA"), lic)
        out = os.path.join(self.root, "e2e")
        pkg = exporter.export_both(mat, out)
        self.assertTrue(os.path.exists(os.path.join(out, "route_a",
                                                     "instrument.sfz")))

        # 2) 学员只能选择获准音域内的和弦
        selectable = pkg["route_a"]["chords"]["selectable"]
        authorized = set(lic.notes)
        self.assertTrue(selectable)
        for ch in selectable:
            self.assertTrue(all(t in authorized for t in ch["tones"]))

        # 3) 校音员后交参考音与非等分律目标，工作台出判断与伴奏
        paths = bar_paths(self.root)
        temp = load_temperament(temperament_spec("meantone", 219.5))
        a = analyze_bar(paths["a"], "bar_A")
        b = analyze_bar(paths["b"], "bar_B")
        reports = {}
        for spec in TEST_CHORDS:
            v = workbench.judge_chord(a, b, spec["root"], spec["shape"], temp)
            reports[spec["shape"]] = v
            wav = os.path.join(self.root, "e2e",
                               f"accomp_{spec['shape']}.wav")
            workbench.render_accompaniment(a, b, v, temp, wav)
            sr, data = reader.read_wav(wav)
            self.assertGreater(len(data), 0)

        # 普通 WAV 读器复核：单音频差 + 合响拍频
        rep = reader.reader_report(paths["a"], paths["b"])
        self.assertTrue(rep["ensemble_beat_hz"] > 0)
        self.assertTrue(abs(rep["single_tone"]["f0_delta_cents"] - 12) < 8)

        # 4) 撤掉交界处一层授权：只有该键的过渡换材料，其余许可音照常练
        boundary = LICENSED_NOTES[2]
        revoked = make_license(partial={boundary: [2]})
        mat_r = bank.open_loaned_materials(
            os.path.join(self.root, "factoryA"), revoked)
        pkg_r = exporter.export_route_a(
            mat_r, os.path.join(self.root, "e2e", "revoked"))
        for midi in LICENSED_NOTES:
            if midi == boundary:
                continue
            self.assertEqual(
                pkg["route_a"]["notes"][str(midi)]["layers"],
                pkg_r["notes"][str(midi)]["layers"])
        self.assertNotIn("2", pkg_r["notes"][str(boundary)]["layers"])
        # 撤权后仍有可选和弦（只是集合缩小）
        still = enumerate_chords(set(revoked.notes))
        self.assertTrue(still)
        self.assertLessEqual(len(still), len(selectable))

        # 5) 恢复授权：产物哈希与撤权前完全一致
        mat_back = bank.open_loaned_materials(
            os.path.join(self.root, "factoryA"), make_license())
        pkg_back = exporter.export_route_a(
            mat_back, os.path.join(self.root, "e2e", "restored"))
        self.assertEqual(pkg_back["hashes"], pkg["route_a"]["hashes"])

        # 6) 只把 B 换成第二泛音偏移样本：仅相关和弦判断改变
        b_swap = analyze_bar(paths["b_swap"], "bar_B")
        changed, unchanged = [], []
        for spec in TEST_CHORDS:
            before = workbench.judge_chord(
                a, b, spec["root"], spec["shape"], temp)
            after = workbench.judge_chord(
                a, b_swap, spec["root"], spec["shape"], temp)
            key = (round(before.tune_a_cents, 1),
                   round(before.tune_b_cents, 1),
                   round(before.residual_beat_error, 2),
                   tuple(sorted((c.bar_a_partial, c.bar_b_partial,
                                 c.ratio) for c in before.coincidences)))
            val = (round(after.tune_a_cents, 1),
                   round(after.tune_b_cents, 1),
                   round(after.residual_beat_error, 2),
                   tuple(sorted((c.bar_a_partial, c.bar_b_partial,
                                 c.ratio) for c in after.coincidences)))
            (changed if key != val else unchanged).append(spec["shape"])
        self.assertEqual(set(changed), {"maj", "min"})
        self.assertEqual(set(unchanged), {"aug", "dim"})

    def test_route_b_out_of_range_requires_factory(self):
        lic = make_license(max_shift=20.0)
        mat = bank.open_loaned_materials(
            os.path.join(self.root, "factoryA"), lic)
        pkg = exporter.export_route_b(
            mat, os.path.join(self.root, "e2e", "routeb"))
        first = next(iter(pkg["segments"].values()))["steps"][0]["file"]
        _, data = reader.read_wav(os.path.join(self.root, "e2e",
                                               "routeb", first))
        # 调域内允许；扩大调式/重定参考音高超出 ±20 音分 => 回厂
        exporter.retune_segment(data, 15.0, 20.0)
        with self.assertRaises(PermissionError):
            exporter.retune_segment(data, 25.0, 20.0)


if __name__ == "__main__":
    unittest.main()
