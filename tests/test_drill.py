"""End-to-end drill tests (stdlib unittest).

Each test drives the real two-process flow: an engine run writes only disk +
held receipts, then a fresh Recovery reads those artifacts.  Nothing in the
recovery path may reach into the live engine/config.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from drill.engine import Engine, ops_for
from drill.model import DrillConfig
from drill.receipts import verify_receipt
from drill.recovery import Recovery, reopen
from drill.credentials import classroom_key as _key
from drill.scenario import build_shared_feed_topology, build_standard
from drill.storage import Store


def _run(cfg: DrillConfig, root: str, mode: str = "online") -> Engine:
    store = Store(root)
    eng = Engine(cfg, store, mode=mode)
    eng.run()
    return eng


class DrillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="drilltest-")
        self.root = os.path.join(self.tmp, "store")
        self.held = os.path.join(self.tmp, "held")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _recover(self) -> dict:
        rec = Recovery(self.root, self.held,
                       {"C101": _key("C101"), "C102": _key("C102")})
        return rec.recover()

    # -- 1. a promised cross-book order keeps both halves ----------------
    def test_confirmed_cross_volume_keeps_both_halves(self) -> None:
        cfg = build_standard()
        eng = _run(cfg, self.root)
        summary = self._recover()
        # txA1 is the cross-volume order that was confirmed while energy lasted
        self.assertIn("txA1", summary["confirmed"])
        rec = next(t for t in summary["recovered"] if t["txid"] == "txA1")
        vols = sorted(p["vol"] for p in rec["pages"])
        self.assertEqual(vols, ["vol1", "vol1", "vol2"])  # both spindles present
        # no index points at a missing page
        self.assertFalse(any("stale index" in i for i in rec["issues"]))

    # -- 2. cache-only work is never counted as backup -------------------
    def test_cache_only_write_not_resurrected(self) -> None:
        cfg = build_standard()
        _run(cfg, self.root)
        summary = self._recover()
        # txB2 was accepted + given a pending receipt, but its pages lived only
        # in the volatile cache when the service ended.
        self.assertIn("txB2", summary["rejected_cache_only"])
        self.assertIn("txB2", summary["phantom"])
        self.assertNotIn("txB2", summary["usable"])

    # -- 3. unsafe cache-policy receipts cannot be honoured --------------
    def test_cache_policy_receipt_becomes_phantom(self) -> None:
        cfg = build_standard()
        cfg.receipt_policy = "cache"
        eng = _run(cfg, self.root)
        # every receipt was minted before durability
        self.assertTrue(all(r["payload"]["policy"] == "cache" for r in eng.receipts))
        summary = self._recover()
        # txB2 (cache-promised, never flushed) must not be honoured
        self.assertIn("txB2", summary["phantom"])
        self.assertNotIn("txB2", summary["usable"])

    # -- 4. receipt numbers that were returned are redeemed -------------
    def test_returned_receipt_numbers_are_redeemed(self) -> None:
        cfg = build_standard()
        eng = _run(cfg, self.root)
        summary = self._recover()
        redeem = {r["payload"]["txid"]: r["payload"]["receipt_number"]
                  for r in eng.receipts}
        out = reopen(summary, redeem)
        for item in out["reopened"]:
            self.assertEqual(item["redeemed_receipt"],
                             redeem[item["txid"]])
            self.assertTrue(item["editing"])

    # -- 5. two classrooms recover independently by their own credentials
    def test_classrooms_recover_independently(self) -> None:
        cfg = build_standard()
        _run(cfg, self.root)
        summary = self._recover()
        by_tx = {t["txid"]: t for t in summary["recovered"]}
        self.assertEqual(by_tx["txA1"]["classroom"], "C101")
        self.assertEqual(by_tx["txB1"]["classroom"], "C102")
        self.assertIn("txA1", summary["confirmed"])
        self.assertIn("txB1", summary["confirmed"])

    # -- 6. forged receipt is rejected ----------------------------------
    def test_forged_receipt_rejected(self) -> None:
        cfg = build_standard()
        _run(cfg, self.root)
        # tamper with one held receipt's MAC
        path = os.path.join(self.held, "receipt_000.json")
        with open(path, "r", encoding="utf-8") as fh:
            r = json.load(fh)
        r["mac"] = "AAAA" + r["mac"][4:]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(r, fh)
        summary = self._recover()
        txid = r["payload"]["txid"]
        self.assertIn(txid, summary["phantom"])

    # -- 7. delayed index slips one tick, no stale index served ---------
    def test_delayed_index_slips_and_stays_consistent(self) -> None:
        cfg = build_standard()
        cfg.delay_index = {"classroom": "C102", "assignment": "HW-A"}
        _run(cfg, self.root)
        with open(os.path.join(self.root, "power.json"), "r", encoding="utf-8") as fh:
            power = json.load(fh)
        defer = [e for e in power["events"] if e["kind"] == "defer_index"]
        self.assertTrue(defer)
        self.assertEqual(defer[0]["to_tick"], defer[0]["tick"] + 1)
        summary = self._recover()
        # txB1's index was slipped; both halves must still be together, or it is
        # honestly downgraded -- never a half-presented confirmed receipt.
        rec = next(t for t in summary["recovered"] if t["txid"] == "txB1")
        if rec["status"] == "confirmed":
            self.assertFalse(any("stale index" in i for i in rec["issues"]))

    # -- 8. min-service chassis is never shed inside its paid window ----
    def test_min_service_not_shed(self) -> None:
        cfg = build_standard()
        _run(cfg, self.root)
        with open(os.path.join(self.root, "power.json"), "r", encoding="utf-8") as fh:
            power = json.load(fh)
        shed = [e for e in power["events"] if e["kind"] == "shed"]
        ms = cfg.min_service
        self.assertFalse(any(e["cabinet"] == ms["cabinet"]
                             and e["tick"] <= ms["ticks"] for e in shed))

    # -- 9. graded plan only promises what fits the safe window --------
    def test_graded_mode_limits_promises(self) -> None:
        cfg = build_standard()
        eng = _run(cfg, self.root, mode="graded")
        summary = self._recover()
        promised = set(eng._promised_tx)
        self.assertIn("txA1", promised)
        # anything not promised was explicitly aborted, never half-served
        for txid, reason in eng.aborted.items():
            self.assertNotIn(txid, summary["confirmed"])

    # -- 10. isolation: recovery ignores cache.json --------------------
    def test_recovery_ignores_cache_files(self) -> None:
        cfg = build_standard()
        _run(cfg, self.root)
        # inject a bogus "durable-looking" page into the cache-only channel
        cache_path = os.path.join(self.root, "vol2", "cache.json")
        with open(cache_path, "r", encoding="utf-8") as fh:
            cache = json.load(fh)
        cache["bogus"] = {"op_id": "bogus", "txid": "txB2",
                          "classroom": "C102", "assignment": "HW-C",
                          "kind": "data", "page_no": 99, "content": "ghost"}
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        summary = self._recover()
        # txB2 still not usable; ghost page never appears
        self.assertNotIn("txB2", summary["usable"])
        rec = next(t for t in summary["recovered"] if t["txid"] == "txB2")
        self.assertFalse(any(p["page_no"] == 99 for p in rec["pages"]))

    # -- 11. shared-feed fault breaks a startup promise that base kept --
    def test_shared_feed_fault_finds_startup_conflict(self) -> None:
        from drill.power import feasible
        base = build_standard().topology
        fault = build_shared_feed_topology()
        self.assertTrue(feasible(base, ["A", "B"], 90.0, ["R2", "R3"]))
        self.assertFalse(feasible(fault, ["A", "B"], 90.0, ["R2", "R3"]))

    # -- 12. a confirmed receipt without its commit is downgraded -------
    def test_missing_commit_downgrades_confirmed(self) -> None:
        cfg = build_standard()
        _run(cfg, self.root)
        # delete txA1's commit line from the durable journal
        jpath = os.path.join(self.root, "journal.log")
        with open(jpath, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        kept = [l for l in lines if json.loads(l)["txid"] != "txA1"]
        with open(jpath, "w", encoding="utf-8") as fh:
            fh.writelines(kept)
        summary = self._recover()
        rec = next(t for t in summary["recovered"] if t["txid"] == "txA1")
        self.assertNotEqual(rec["status"], "confirmed")
        # both halves are still there (pages survived), just not re-promised
        vols = sorted(p["vol"] for p in rec["pages"])
        self.assertEqual(vols, ["vol1", "vol1", "vol2"])

    # -- 13. true two-process run: separate interpreter, disk-only recovery
    def test_separate_processes_end_to_end(self) -> None:
        import subprocess, sys
        run = subprocess.run(
            [sys.executable, "-m", "drill", "run",
             "--root", self.root, "--mode", "online"],
            capture_output=True, text=True, cwd=os.getcwd())
        self.assertEqual(run.returncode, 0, run.stderr)
        rec = subprocess.run(
            [sys.executable, "-m", "drill", "recover",
             "--root", self.root, "--held", self.held, "--out",
             os.path.join(self.tmp, "rec.json")],
            capture_output=True, text=True, cwd=os.getcwd())
        self.assertEqual(rec.returncode, 0, rec.stderr)
        with open(os.path.join(self.tmp, "rec.json"), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        self.assertEqual(sorted(d["summary"]["confirmed"]),
                         ["txA1", "txA2", "txB1"])
        self.assertIn("txB2", d["summary"]["phantom"])

    # -- 14. disk flush duration is honoured ----------------------------
    def test_flush_ticks_pipeline(self) -> None:
        cfg = build_standard()
        cfg.flush_ticks = 2
        _run(cfg, self.root)
        with open(os.path.join(self.root, "power.json"), "r", encoding="utf-8") as fh:
            power = json.load(fh)
        inflight = [op for t in power["telemetry"] for op in t["flushes"]
                    if "@inflight" in op]
        self.assertTrue(inflight)
        summary = self._recover()
        # with slower disks fewer orders complete -> fewer confirms, no phantoms
        # wrongly confirmed
        self.assertNotIn("txB2", summary["confirmed"])
        self.assertIn("txB1", summary["confirmed"])

    # -- 15. when energy is sufficient the cross-book order is fully saved
    def test_sufficient_energy_saves_cross_order(self) -> None:
        cfg = build_standard()
        _run(cfg, self.root)
        summary = self._recover()
        rec = next(t for t in summary["recovered"] if t["txid"] == "txA1")
        self.assertEqual(rec["status"], "confirmed")
        # both data halves (vol1 + vol2) plus its index are all durable
        kinds = sorted((p["vol"], p["kind"]) for p in rec["pages"])
        self.assertIn(("vol1", "data"), kinds)
        self.assertIn(("vol2", "data"), kinds)
        self.assertIn(("vol1", "index"), kinds)

    # -- 16. cross-book order is prioritised over a non-cross competitor
    def test_cross_volume_priority_under_contest(self) -> None:
        cfg = build_standard()
        # reduce battery so post-loss energy is contested between txA1 (cross,
        # on R3) and txB2 (non-cross, also on R3)
        cfg.battery_energy = 38.0
        _run(cfg, self.root)
        with open(os.path.join(self.root, "power.json"), "r", encoding="utf-8") as fh:
            power = json.load(fh)
        first_tick = {}
        for t in power["telemetry"]:
            for op in t["flushes"]:
                base = op.split("@")[0]
                txid = base.split(".")[0]
                first_tick.setdefault(txid, t["tick"])
        # the cross order gets disk time before the non-cross competitor
        self.assertIn("txA1", first_tick)
        self.assertLessEqual(first_tick.get("txA1", 10**9),
                             first_tick.get("txB2", 10**9))


if __name__ == "__main__":
    unittest.main(verbosity=2)
