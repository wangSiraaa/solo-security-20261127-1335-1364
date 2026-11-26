#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封存柜端到端测试：直接驱动 CLI，不依赖第三方包。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "seal_cabinet.py"


class SealCabinetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)
        self.home = self.work / "cabinet"
        self.env = os.environ.copy()
        self.env["SEALCAB_HOME"] = str(self.home)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def cli(self, *args: str, expected_rc: int = 0):
        p = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(
            p.returncode,
            expected_rc,
            msg=f"args={args}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}",
        )
        try:
            data = json.loads(p.stdout)
        except json.JSONDecodeError:
            data = None
        return p, data

    def init_old_volume(self) -> str:
        _, data = self.cli("init", "--json")
        self.assertEqual(data["receipt_kind_zh"] if "receipt_kind_zh" in data else "complete", "complete")
        return data["old_seal_id"]

    def test_boot_old_volume_restart_and_fetch(self) -> None:
        seal7 = self.init_old_volume()
        self.cli("verify", seal7, "--json")
        self.cli("restart", "--json")
        out = self.work / "fetched7"
        _, data = self.cli("fetch", seal7, "--out", str(out), "--json")
        self.assertEqual(len(data["retrieved"]), 4)
        self.assertTrue((out / "A-catalog.txt").is_file())

    def test_gen8_complete_independent_bundle(self) -> None:
        self.init_old_volume()
        self.cli("scenario", "gen8")
        _, order = self.cli(
            "order", "submit", "--generation", "8", "--all",
            "--code", "T-G8", "--json",
        )
        oid = order["order_id"]
        self.cli("copy", "receive", oid, "A", "--from-source", "--json")
        self.cli("copy", "receive", oid, "B", "--from-source", "--json")
        _, sealed = self.cli("seal", oid, "--json")
        seal8 = sealed["seal_id"]
        bundle = self.work / "bundle8"
        self.cli("export-bundle", seal8, "--out", str(bundle), "--json")

        retrieve = self.work / "reviewer8"
        _, report = self.cli(
            "verify", "--bundle", str(bundle), seal8,
            "--retrieve-to", str(retrieve), "--json",
        )
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["verified_blocks"], ["A", "B", "C", "D"])
        self.assertIn("A8", (retrieve / "A-catalog.txt").read_text(encoding="utf-8"))
        self.assertIn("B8", (retrieve / "B-signoff.txt").read_text(encoding="utf-8"))

        # 独立复核包不应依赖柜内状态：换一个不存在的 HOME 也能验。
        env_backup = self.env["SEALCAB_HOME"]
        self.env["SEALCAB_HOME"] = str(self.work / "never-created-home")
        try:
            _, independent = self.cli("verify", "--bundle", str(bundle), seal8, "--json")
            self.assertEqual(independent["status"], "complete")
            self.assertFalse(Path(self.env["SEALCAB_HOME"]).exists())
        finally:
            self.env["SEALCAB_HOME"] = env_backup

    def test_gen9_pair_is_partial_fragment(self) -> None:
        self.init_old_volume()
        self.cli("scenario", "gen9")
        _, order = self.cli(
            "order", "submit", "--generation", "9", "--blocks", "B,C", "--json",
        )
        oid = order["order_id"]
        self.cli("copy", "receive", oid, "B", "--from-source", "--json")
        self.cli("copy", "receive", oid, "C", "--from-source", "--json")
        _, sealed = self.cli("seal", oid, "--json")
        seal9 = sealed["seal_id"]
        bundle = self.work / "bundle9"
        self.cli("deliver", seal9, "--to", "reviewer", "--bundle-out", str(bundle), "--json")

        retrieve = self.work / "reviewer9"
        # deliver 默认把 receipt 放在 bundle 父目录；本测试显式指定 bundle 路径。
        receipt = bundle.parent / "delivery-receipt.json"
        _, report = self.cli(
            "verify", "--bundle", str(bundle), seal9,
            "--receipt", str(receipt), "--retrieve-to", str(retrieve), "--json",
        )
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["verified_blocks"], ["B", "C"])
        self.assertTrue((retrieve / "B-signoff.txt").is_file())
        self.assertTrue((retrieve / "C-handover.txt").is_file())
        self.assertFalse((retrieve / "A-catalog.txt").exists())
        self.assertFalse((bundle / "replica" / "A.bin").exists())

    def test_cross_generation_witness_fails_then_narrow_b_succeeds(self) -> None:
        seal7 = self.init_old_volume()
        good7 = self.work / "good7"
        self.cli("export-bundle", seal7, "--out", str(good7), "--json")
        snapshot = self.work / "src7"
        self.cli("source", "snapshot", "--out", str(snapshot), "--json")
        self.cli("scenario", "gen8")

        bad_witness = self.work / "bad-witness"
        self.cli(
            "demo-witness-swap", "--bundle", str(good7), "--out", str(bad_witness), "--json",
        )
        _, bad_report = self.cli(
            "verify", "--bundle", str(bad_witness), seal7, "--json", expected_rc=3,
        )
        self.assertEqual(bad_report["status"], "invalid")

        bad_mix = self.work / "bad-mix"
        _, mix = self.cli(
            "demo-mix", "--bundle", str(good7), "--out", str(bad_mix), "--json",
        )
        _, mix_report = self.cli(
            "verify", "--bundle", str(bad_mix), mix["forged_seal_number"], "--json", expected_rc=3,
        )
        self.assertTrue(mix_report["block_self_hashes_valid"])
        self.assertTrue(any(c["code"] == "cross_generation_conflict" for c in mix_report["conflicts"]))

        self.cli("source", "restore", "--input", str(snapshot), "--yes", "--json")
        _, order = self.cli(
            "order", "submit", "--generation", "7", "--blocks", "B",
            "--code", "B-ONLY-TEST", "--json",
        )
        oid = order["order_id"]
        self.cli("copy", "receive", oid, "B", "--from-source", "--json")
        _, sealed = self.cli("seal", oid, "--json")
        seal_b = sealed["seal_id"]
        bundle_b = self.work / "b-only"
        self.cli("export-bundle", seal_b, "--out", str(bundle_b), "--json")
        retrieve = self.work / "b-retrieve"
        _, report = self.cli(
            "verify", "--bundle", str(bundle_b), seal_b,
            "--retrieve-to", str(retrieve), "--json",
        )
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["verified_blocks"], ["B"])
        self.assertFalse((bundle_b / "replica" / "A.bin").exists())

    def test_half_finished_copy_switching_generation_fails(self) -> None:
        self.init_old_volume()
        self.cli("scenario", "gen8")
        _, order = self.cli("order", "submit", "--generation", "8", "--all", "--json")
        oid = order["order_id"]

        # 镜像拷到一半：A8 已入柜，随后源端换到第九代，B 才拷完。
        self.cli("copy", "receive", oid, "A", "--from-source", "--json")
        self.cli("scenario", "gen9")
        self.cli("copy", "receive", oid, "B", "--from-source", "--json")

        p, error = self.cli("seal", oid, "--json", expected_rc=3)
        self.assertFalse(error["ok"])
        self.assertEqual(error["error"], "CROSS_GENERATION_PROOF_FAILED")
        codes = [c["code"] for c in error["details"]["conflicts"]]
        self.assertIn("cross_generation_conflict", codes)
        _, current_order = self.cli("order", "show", oid, "--json")
        self.assertIsNone(current_order.get("seal_id"))

    def test_two_worm_slots_do_not_overwrite(self) -> None:
        self.init_old_volume()
        self.cli("scenario", "gen8")
        _, order = self.cli("order", "submit", "--generation", "8", "--all", "--json")
        oid = order["order_id"]
        self.cli("copy", "receive", oid, "A", "--from-source", "--json")
        self.cli("copy", "receive", oid, "B", "--from-source", "--json")

        # 同一位收到不同内容必须拒绝。
        _, current_b = self.cli("source", "block", "B")
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "copy", "receive", oid, "B", "different B8 content", "--json"],
            env=self.env, text=True, capture_output=True,
        )
        self.assertNotEqual(p.returncode, 0)

        self.cli("scenario", "gen9")
        _, order9 = self.cli("order", "submit", "--generation", "9", "--blocks", "B,C", "--json")
        # 两个新位置已被 A8/B8 占用，B9 不能覆盖。
        self.cli("copy", "receive", order9["order_id"], "B", "--from-source", "--json", expected_rc=4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
