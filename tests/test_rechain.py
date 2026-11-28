# -*- coding: utf-8 -*-
"""rechain 的单元测试：python -m pytest tests/ -q 或 python tests/test_rechain.py"""
import json
import os
import shutil
import tempfile
import unittest

from rechain import table as T
from rechain import lang, sealing, recompute
from rechain.engine import Engine


def make_table():
    return T.Table(
        name="t", columns=["month", "region", "e", "w"],
        rows=[["m1", "S", 420000.0, 910.0], ["m2", "S", 435000.0, 925.0],
              ["m1", "E", 500000.0, 1020.0]],
        units={"month": None, "region": None, "e": "Wh", "w": "m3"})


class TableTests(unittest.TestCase):
    def test_filter_groupby_and_units(self):
        t = make_table()
        south = t.filter("s", lambda r: r["region"] == "S")
        self.assertEqual(len(south.rows), 2)
        agg = south.groupby("g", ["region"], [("e_sum", "e", "sum"),
                                              ("n", "month", "count")])
        self.assertEqual(agg.rows[0][agg.col_index("e_sum")], 855000.0)
        self.assertEqual(agg.units["e_sum"], "Wh")
        self.assertIsNone(agg.units["n"])

    def test_unit_conversion_and_dimension_guard(self):
        t = make_table()
        t.units["e"] = "kWh"
        base = T.to_base_units(t)
        self.assertEqual(base.rows[0][base.col_index("e")], 420000.0 * 1000)
        self.assertEqual(base.units["e"], "Wh")
        with self.assertRaises(ValueError):
            t.add_column("bad", "x", lambda r: r["e"] + r["w"], "Wh",
                         dim_check=(t.unit_dim("e"), t.unit_dim("w")))

    def test_local_update_same_dimension_only(self):
        t = make_table()
        out = t.update_column("u", "e", [1, 2, 3], "kWh")  # 同 energy 维度
        self.assertEqual(out.units["e"], "kWh")
        with self.assertRaises(ValueError):
            t.update_column("bad", "e", [1, 2, 3], "m3")   # 跨维度拒绝

    def test_content_hash_stable(self):
        self.assertEqual(make_table().content_hash(), make_table().content_hash())

    def test_join_drops_unmatched(self):
        a = make_table()
        b = T.Table("b", ["month", "region", "x"],
                    [["m1", "S", 7.0]], {"month": None, "region": None, "x": None})
        j = a.join("j", b, ["month", "region"])
        self.assertEqual(len(j.rows), 1)
        self.assertIn("x", j.columns)


class LangTests(unittest.TestCase):
    def test_safe_eval_blocks_calls(self):
        t = make_table()
        self.assertEqual(lang.evaluate("e > 420000 and region == 'S'", t._dicts()[1]), True)
        with self.assertRaises(ValueError):
            lang.evaluate("__import__('os')", {})

    def test_dimension_inference(self):
        t = make_table()
        self.assertEqual(lang.infer_dimension("e / w", t), "energy/volume")
        with self.assertRaises(ValueError):
            lang.infer_dimension("e + w", t)

    def test_parse_program(self):
        prog = lang.parse("FILTER t -> s WHERE region == 'S' // 注释\n\nSAVE s TO x.csv")
        self.assertEqual([s.verb for s in prog.statements], ["FILTER", "SAVE"])


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.nb_dir = os.path.join(self.tmp, "nb")
        os.makedirs(os.path.join(self.nb_dir, "data"))
        raw = T.Table("elec", ["month", "region", "e"],
                      [["m1", "S", 420.0], ["m2", "S", 435.0]],
                      {"month": None, "region": None, "e": "kWh"},
                      meta={"version": "elec/v1"})
        T.save_csv(raw, os.path.join(self.nb_dir, "data", "elec.csv"))
        self.nb = {
            "id": "NB-TEST", "declared_outputs": [
                {"rel_path": "out/t.csv", "kind": "table"}],
            "cells": [{"id": "c1", "code":
                       "LOAD data/elec.csv AS e\n"
                       "FILTER e -> s WHERE region == 'S'\n"
                       "SAVE s TO out/t.csv"}]}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rerun_overwrite_history_and_manifest(self):
        eng = Engine(notebook_dir=self.nb_dir)
        eng.run_notebook(self.nb)
        eng.rerun_cell(self.nb, "c1")
        man = eng.manifest("NB-TEST", "test", self.nb)
        self.assertEqual(len(man["variables"]["s"]["overwrite_history"]), 2)
        self.assertEqual(man["execution_order"][0]["inputs"], [])
        self.assertTrue(man["outputs"]["files"]["out/t.csv"]["exists"])

    def test_erratum_changes_one_column_unit(self):
        eng = Engine(notebook_dir=self.nb_dir,
                     errata={"data/elec.csv": {"e": "Wh"}})
        eng.run_notebook(self.nb)
        tbl = eng.tables["s"]
        self.assertEqual(tbl.rows[0][tbl.col_index("e")], 420.0)  # Wh 不再 ×1000
        self.assertEqual(tbl.meta["erratum_applied"], {"e": "Wh"})

    def test_missing_dependency_fails_clean(self):
        nb = dict(self.nb)
        nb["cells"] = [{"id": "c1", "code": "LOAD tmp/ghost.csv AS g"}]
        eng = Engine(notebook_dir=self.nb_dir)
        with self.assertRaises(FileNotFoundError):
            eng.run_notebook(nb)


class SealingRecomputeTests(unittest.TestCase):
    def test_not_sealable_and_recompute_failure_then_remediation(self):
        # 直接用交付项目里的 B 封存包验证端到端结论
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "archive", "seal_manifest_B.json")) as f:
            seal_b = json.load(f)
        self.assertFalse(seal_b["sealable"])
        self.assertTrue(any("tmp/" in m["rel_path"]
                            for m in seal_b["missing_dependencies"]))
        with open(os.path.join(root, "archive", "round2_result_B.json")) as f:
            r_b = json.load(f)
        self.assertFalse(r_b["signed"])
        self.assertEqual(r_b["failure"]["type"], "FileNotFoundError")
        with open(os.path.join(root, "archive", "round2_result_A.json")) as f:
            r_a = json.load(f)
        self.assertTrue(r_a["signed"])
        self.assertTrue(all(c["match"] for c in r_a["input_verification"]
                            if c["raw_identity_required"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
