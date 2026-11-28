"""工厂母库、授权边界与“两份母库外借不可区分”。"""
from __future__ import annotations

import json
import os
import unittest

from glock import bank
from tests.helpers import (BUILD_ROOT, LICENSED_NOTES, RETAINED_NOTES,
                           build_once, make_license)


class FactoryBankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = build_once()
        cls.root = cls.tmp.name

    def test_01_bank_contains_bodies_and_retained_tails(self):
        for midi in range(bank.BANK_MIDI_LO, bank.BANK_MIDI_HI + 1):
            for layer in range(bank.N_LAYERS):
                p = os.path.join(BUILD_ROOT(self.root, "factoryA"),
                                 "body", str(midi), f"{layer}.wav")
                self.assertTrue(os.path.exists(p))
            self.assertTrue(os.path.exists(os.path.join(
                BUILD_ROOT(self.root, "factoryA"), "tail", f"{midi}.wav")))

    def test_02_two_factories_differ_only_on_retained_notes(self):
        ma = bank.load_bank_meta(BUILD_ROOT(self.root, "factoryA"))
        mb = bank.load_bank_meta(BUILD_ROOT(self.root, "factoryB"))
        for midi in range(bank.BANK_MIDI_LO, bank.BANK_MIDI_HI + 1):
            key = str(midi)
            if midi in RETAINED_NOTES:
                self.assertNotEqual(ma["notes"][key]["cents"],
                                    mb["notes"][key]["cents"])
            else:
                self.assertEqual(ma["notes"][key]["cents"],
                                 mb["notes"][key]["cents"])

    def test_03_loaned_materials_expose_only_whitelist(self):
        lic = make_license()
        mat = bank.open_loaned_materials(
            BUILD_ROOT(self.root, "factoryA"), lic)
        self.assertEqual(set(mat.bodies),
                         {(m, li) for m in LICENSED_NOTES
                          for li in range(bank.N_LAYERS)})
        self.assertEqual(mat.tails, {})          # 尾音未授权
        for retained in RETAINED_NOTES:
            self.assertFalse(any(m == retained for (m, _) in mat.bodies))

    def test_04_unauthorized_read_is_denied(self):
        lic = make_license()
        lic.notes[RETAINED_NOTES[0]] = [0]       # 越权请求保留音
        with self.assertRaises(PermissionError):
            bank.open_loaned_materials(
                BUILD_ROOT(self.root, "factoryA"), lic)
        bad = make_license()
        bad.tails = {LICENSED_NOTES[0]}
        # 尾音是留厂资产：授权清单单方面索要也必须被工厂侧拒绝
        with self.assertRaises(PermissionError):
            bank.open_loaned_materials(
                BUILD_ROOT(self.root, "factoryA"), bad)


if __name__ == "__main__":
    unittest.main()
