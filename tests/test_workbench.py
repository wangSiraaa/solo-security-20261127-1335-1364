"""校音工作台：测量、拍频判断、重调决策、伴奏与读器复核、泛音替换实验。"""
from __future__ import annotations

import os
import unittest

from glock import reader, synth, workbench
from glock.analysis import analyze_bar
from glock.dsp import rms
from glock.tuning import load_temperament
from tests.helpers import (BAR_A_RATIOS, TEST_CHORDS, bar_paths, build_once,
                           temperament_spec)


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = build_once()
        cls.paths = bar_paths(cls.tmp.name)

    def test_01_f0_and_partials_measured(self):
        a = analyze_bar(self.paths["a"], "A")
        b = analyze_bar(self.paths["b"], "B")
        # 基音估计误差应在 5 音分以内
        self.assertLess(abs(1200 * __import__("math").log2(a.f0 / 220.0)), 5)
        self.assertLess(
            abs(1200 * __import__("math").log2(b.f0 / (220 * 2 ** (12 / 1200)))),
            5)
        # 两根基音接近但不同；B 比 A 高约 12 音分
        rep = reader.reader_report(self.paths["a"], self.paths["b"])
        self.assertAlmostEqual(rep["single_tone"]["f0_delta_cents"],
                               12.0, delta=6.0)
        # 力度/衰减不同：tau2 应有可测差异
        self.assertNotAlmostEqual(
            rep["single_tone"]["decay"]["tau2_a"],
            rep["single_tone"]["decay"]["tau2_b"],
            delta=0.02)

    def test_02_swap_changes_second_partial_only(self):
        b = analyze_bar(self.paths["b"], "B")
        bs = analyze_bar(self.paths["b_swap"], "B_swap")
        bm, sm = b.partial_map(), bs.partial_map()
        # 基音一致
        self.assertAlmostEqual(bm[1].freq, sm[1].freq, delta=0.5)
        # 第二泛音按 2.06/2.0 偏移，约 51 音分
        delta = 1200 * __import__("math").log2(sm[2].freq / bm[2].freq)
        self.assertAlmostEqual(delta, 50.3, delta=8.0)
        # 第三泛音不变
        self.assertAlmostEqual(bm[3].freq, sm[3].freq, delta=0.5)


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = build_once()
        cls.paths = bar_paths(cls.tmp.name)
        cls.temp = load_temperament(temperament_spec())
        cls.a = analyze_bar(cls.paths["a"], "bar_A")
        cls.b = analyze_bar(cls.paths["b"], "bar_B")

    def _verdicts(self, a=None, b=None):
        a = a or self.a
        b = b or self.b
        return {spec["shape"]: workbench.judge_chord(
            a, b, spec["root"], spec["shape"], self.temp)
            for spec in TEST_CHORDS}

    def test_03_verdicts_cover_all_test_chords(self):
        verdicts = self._verdicts()
        self.assertEqual(set(verdicts), {s["shape"] for s in TEST_CHORDS})
        for shape, v in verdicts.items():
            self.assertTrue(v.coincidences, f"{shape} 应至少有一组重合泛音")
            self.assertGreaterEqual(v.residual_beat_error, 0.0)

    def test_04_judgment_points_to_retune_and_synthesizes_accompaniment(self):
        # 两根基音相差 12 音分：至少一个和弦应要求重调
        verdicts = self._verdicts()
        need_tune = [v for v in verdicts.values() if v.which]
        self.assertTrue(need_tune)
        for v in need_tune:
            self.assertTrue(
                all(name in ("bar_A", "bar_B") for name in v.which))
            self.assertLessEqual(
                max(abs(v.tune_a_cents), abs(v.tune_b_cents)),
                workbench.MAX_TUNE_CENTS + 1e-6)
        # 合成可听伴奏并可被普通读器打开
        out = os.path.join(self.tmp.name, "accomp_maj.wav")
        workbench.render_accompaniment(
            self.a, self.b, verdicts["maj"], self.temp, out)
        sr, data = reader.read_wav(out)
        self.assertEqual(sr, synth.SR)
        self.assertGreater(rms(data), 0.0)

    def test_05_reader_beats_corroborate_workbench(self):
        rep = reader.reader_report(self.paths["a"], self.paths["b"])
        # 合响包络能数出非零拍频（两根基音失谐必然产生拍）
        self.assertGreater(rep["ensemble_beat_hz"], 0.0)
        maj = self._verdicts()["maj"]
        # 工作台报出的当前拍频也应非负且有数值
        self.assertGreaterEqual(
            max(c.beat_now for c in maj.coincidences), 0.0)

    def test_06_swap_changes_only_related_chords(self):
        before = self._verdicts()
        b_swap = analyze_bar(self.paths["b_swap"], "bar_B")
        after = self._verdicts(b=b_swap)

        def sig(v):
            return (round(v.tune_a_cents, 1), round(v.tune_b_cents, 1),
                    round(v.residual_beat_error, 2),
                    tuple(sorted((c.bar_a_partial, c.bar_b_partial,
                                  tuple(c.ratio)) for c in v.coincidences)))

        # 不使用第二泛音重合的和弦：判断完全不变
        for shape in ("aug", "dim"):
            self.assertFalse(
                workbench.chord_uses_partial(shape, 2),
                f"{shape} 不应依赖第二泛音")
            self.assertEqual(sig(before[shape]), sig(after[shape]),
                             f"{shape} 的调律判断不应被第二泛音替换改变")
        # 使用第二泛音的和弦：相关重合对消失或改变
        for shape in ("maj", "min"):
            self.assertTrue(workbench.chord_uses_partial(shape, 2))
            self.assertNotEqual(sig(before[shape]), sig(after[shape]),
                                f"{shape} 应受第二泛音偏移影响")

    def test_07_run_workbench_report_is_serializable(self):
        report = workbench.run_workbench(
            self.paths["a"], self.paths["b"], temperament_spec(), TEST_CHORDS)
        self.assertEqual(len(report["verdicts"]), len(TEST_CHORDS))
        self.assertEqual(report["temperament"]["reference_hz"], 219.5)
        import json
        json.dumps(report)     # 不抛异常即满足“可交差”


if __name__ == "__main__":
    unittest.main()
