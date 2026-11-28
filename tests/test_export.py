"""导出程序：不可区分性、撤权过渡换材料、B 路限域移频、和弦可选。"""
from __future__ import annotations

import json
import os
import unittest

from glock import bank, exporter
from glock.chords import enumerate_chords
from glock.dsp import read_wav
from tests.helpers import (LICENSED_NOTES, build_once, make_license)


def _hashes(manifest: dict) -> dict:
    return manifest["hashes"]


class ExportRouteATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = build_once()
        cls.root = cls.tmp.name
        cls.out = os.path.join(cls.root, "exports")

    def test_01_two_factories_produce_identical_loan_package(self):
        lic = make_license()
        out_a = os.path.join(self.out, "a_factoryA")
        out_b = os.path.join(self.out, "a_factoryB")
        m1 = exporter.export_route_a(
            bank.open_loaned_materials(
                os.path.join(self.root, "factoryA"), lic), out_a)
        m2 = exporter.export_route_a(
            bank.open_loaned_materials(
                os.path.join(self.root, "factoryB"), lic), out_b)
        # 哈希清单一致 => 下家无法区分两份外借材料
        self.assertEqual(_hashes(m1), _hashes(m2))
        self.assertEqual(
            {n for n in m1["notes"]},
            {str(m) for m in LICENSED_NOTES})
        # 留厂尾音绝不出现在产物中
        for rel in _hashes(m1):
            self.assertNotIn("tail", rel)

    def test_02_only_authorized_operations_present(self):
        lic = make_license()
        manifest = exporter.export_route_a(
            bank.open_loaned_materials(
                os.path.join(self.root, "factoryA"), lic),
            os.path.join(self.out, "a_ops"))
        for midi, entry in manifest["notes"].items():
            self.assertEqual(
                set(entry["layers"]), set(map(str, lic.notes[int(midi)])))
            # 重制的过渡只可能夹在相邻且都授权的层之间
            for br in entry["bridges"]:
                l0, l1 = br["between"]
                self.assertEqual(l1, l0 + 1)
                self.assertIn(l0, lic.notes[int(midi)])
                self.assertIn(l1, lic.notes[int(midi)])

    def test_03_revoke_boundary_layer_swaps_only_nearby_transition(self):
        boundary_note = LICENSED_NOTES[2]
        full = make_license()
        revoked = make_license(partial={boundary_note: [1]})
        m_full = exporter.export_route_a(
            bank.open_loaned_materials(
                os.path.join(self.root, "factoryA"), full),
            os.path.join(self.out, "a_full"))
        m_rev = exporter.export_route_a(
            bank.open_loaned_materials(
                os.path.join(self.root, "factoryA"), revoked),
            os.path.join(self.out, "a_revoked"))

        # 其它许可音：材料完全不变
        for midi in LICENSED_NOTES:
            if midi == boundary_note:
                continue
            self.assertEqual(
                m_full["notes"][str(midi)]["layers"],
                m_rev["notes"][str(midi)]["layers"])
        # 交界处：层 1 与两条相邻过渡都被移除/换材料，层 0、2 仍可练
        entry = m_rev["notes"][str(boundary_note)]
        self.assertNotIn("1", entry["layers"])
        self.assertIn("0", entry["layers"])
        self.assertIn("2", entry["layers"])
        bridge_between = [tuple(br["between"]) for br in entry["bridges"]]
        self.assertNotIn((0, 1), bridge_between)
        self.assertNotIn((1, 2), bridge_between)

    def test_04_selectable_chords_stay_inside_license(self):
        lic = make_license()
        manifest = exporter.export_route_a(
            bank.open_loaned_materials(
                os.path.join(self.root, "factoryA"), lic),
            os.path.join(self.out, "a_chords"))
        selectable = manifest["chords"]["selectable"]
        self.assertTrue(selectable)
        authorized = set(lic.notes)
        for ch in selectable:
            self.assertTrue(all(t in authorized for t in ch["tones"]))
        # 直接用和弦模块枚举的结果必须与清单一致
        self.assertEqual(
            {(c["root"], c["shape"]) for c in selectable},
            {(c["root"], c["shape"])
             for c in enumerate_chords(authorized)})


class ExportRouteBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = build_once()
        cls.root = cls.tmp.name

    def test_05_route_b_segments_and_cents_limit(self):
        lic = make_license(max_shift=30.0)
        out = os.path.join(self.root, "exports", "b_pkg")
        manifest = exporter.export_route_b(
            bank.open_loaned_materials(
                os.path.join(self.root, "factoryA"), lic), out)
        midi = LICENSED_NOTES[0]
        seg = manifest["segments"][str(midi)]
        self.assertEqual(len(seg["steps"]), len(exporter.B_VELOCITY_GRID))
        sr, data = read_wav(os.path.join(out, seg["steps"][2]["file"]))

        # 限域内移频：升高 25 音分 => 播放速率变大 => 时长按 2^(-25/1200) 缩短
        shifted = exporter.retune_segment(data, 25.0, lic.max_cents_shift)
        self.assertAlmostEqual(len(shifted) / len(data),
                               2 ** (-25 / 1200), delta=0.01)
        # 超出合约调域：拒绝并要求回厂
        with self.assertRaises(PermissionError):
            exporter.retune_segment(data, 31.0, lic.max_cents_shift)

    def test_06_route_b_also_lists_only_authorized_chords(self):
        lic = make_license()
        out = os.path.join(self.root, "exports", "b_pkg2")
        manifest = exporter.export_route_b(
            bank.open_loaned_materials(
                os.path.join(self.root, "factoryA"), lic), out)
        authorized = set(lic.notes)
        for ch in manifest["chords"]["selectable"]:
            self.assertTrue(all(t in authorized for t in ch["tones"]))


if __name__ == "__main__":
    unittest.main()
