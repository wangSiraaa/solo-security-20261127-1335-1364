"""迁移台单元测试，覆盖题目每条硬性要求。"""
from __future__ import annotations

import unittest
from datetime import date

from custody.hashing import canon, digest, verify_seal
from custody.interpreters import LegacyInterpreter, NewInterpreter
from custody.migration.scenarios import (HORIZONS, RETIRE_ON, STATION_KEY,
                                         build_contracts, build_handovers,
                                         build_orders, extra_guards, run_demo)
from custody.migration.station import Station
from custody.migration.stores import ReceivingStore
from custody.model import Handover, Refusal, StoreLogEntry

LEGACY = LegacyInterpreter()
NEW = NewInterpreter()


class InterpreterTests(unittest.TestCase):
    def setUp(self):
        self.contracts = build_contracts()
        self.orders = build_orders()
        self.handovers = build_handovers()

    def _ev(self, oid):
        o = self.orders[oid]
        c = self.contracts[(o.contract_id, o.contract_version)]
        return (LEGACY.evaluate(o, c, self.handovers[oid], RETIRE_ON),
                NEW.evaluate(o, c, self.handovers[oid], RETIRE_ON))

    def test_blank_term_divergence(self):
        v1, v2 = self._ev("R-OLD-BLANK")
        self.assertEqual(v1.status, "determined")
        self.assertEqual(v1.term_end, date(2025, 6, 30))      # 1/1 + 合同默认 180 天
        self.assertEqual(v2.status, "blank-unresolved")
        self.assertIsNone(v2.term_end)

    def test_explicit_term_agreement(self):
        v1, v2 = self._ev("R-OLD-EXPLICIT")
        self.assertEqual(v1.term_end, v2.term_end)
        self.assertEqual(v1.term_end, date(2025, 4, 1))       # 1/1 + 90 天

    def test_explicit_date_agreement(self):
        v1, v2 = self._ev("R-NEW-EXPLICIT")
        self.assertEqual(v1.term_end, date(2025, 11, 1))
        self.assertEqual(v2.term_end, date(2025, 11, 1))

    def test_pending_transfer_blank_not_expressible(self):
        v1, v2 = self._ev("R-TR-2")
        # 旧版：规则可知（转交后按合同 180 天）但转交未发生；新版：空白一律不推断
        self.assertEqual(v1.status, "pending-transfer")
        self.assertEqual(v2.status, "blank-unresolved")

    def test_grown_contract_snapshot_binding(self):
        # 增长后的 v2 合同不得套用：求值只取签发时绑定的 v1（180 天）
        v1, _ = self._ev("R-GROWN")
        self.assertEqual(v1.term_end, date(2025, 6, 30))
        grown = self.contracts[("C-FRAME", 2)]
        self.assertNotEqual(grown.default_term_days, 180)
        wrong = LEGACY.evaluate(self.orders["R-GROWN"], grown,
                                self.handovers["R-GROWN"], RETIRE_ON)
        self.assertEqual(wrong.term_end, date(2026, 1, 1))   # 若错用新合同会得到的日期


class PermitTests(unittest.TestCase):
    def setUp(self):
        self.station = Station(STATION_KEY, HORIZONS)
        self.contracts = build_contracts()
        self.orders = build_orders()
        self.handovers = build_handovers()
        for c in self.contracts.values():
            self.station.register_contract(c)
        for oid, hs in self.handovers.items():
            self.station.register_handovers(oid, hs, RETIRE_ON)
        self.station.retire_interpreter(list(self.orders.values())[:6], RETIRE_ON)

    def test_blank_order_disposal_frozen_but_retained(self):
        p = self.station.permits["R-OLD-BLANK"]
        self.assertTrue(p.ratification_required)
        self.assertIn("自动处置", p.dropped_automation[0])
        self.assertEqual([o.op for o in p.operations], ["retain-until"])
        self.assertEqual(p.operations[0].arg, "2025-06-30")

    def test_explicit_order_permit_allows_offline_disposal(self):
        p = self.station.permits["R-OLD-EXPLICIT"]
        self.assertFalse(p.ratification_required)
        self.assertEqual({o.op for o in p.operations},
                         {"retain-until", "dispose-after"})

    def test_pending_transfer_blank_automation_dropped(self):
        p = self.station.permits["R-TR-2"]
        self.assertTrue(p.ratification_required)
        self.assertEqual(p.operations, ())
        self.assertTrue(any("转交" in d for d in p.dropped_automation))

    def test_pending_transfer_explicit_keeps_formula(self):
        p = self.station.permits["R-TR-1"]
        self.assertFalse(p.ratification_required)
        ops = {o.op: o.arg for o in p.operations}
        self.assertEqual(ops["accept-transfer-in"], "transfer_on+90D")
        self.assertEqual(ops["retain-until"], "transfer_on+90D")

    def test_seal_rejects_cross_order_reuse(self):
        store = ReceivingStore("T", None)
        p = self.station.permits["R-OLD-EXPLICIT"]
        other = self.orders["R-OLD-EXPLICIT"]
        r = store.receive(RETIRE_ON, "R-OTHER", other.seal, p, STATION_KEY)
        self.assertIsInstance(r, Refusal)
        self.assertIn("不绑定", r.reason)

    def test_seal_rejects_tampered_permit(self):
        from custody.model import PermitOperation, PortablePermit
        p = self.station.permits["R-OLD-EXPLICIT"]
        tampered = PortablePermit(
            p.permit_id, p.order_id, p.order_seal, p.evidence_id, p.issued_on,
            tuple(list(p.operations) +
                  [PermitOperation("dispose-after", "2000-01-01", ("伪造",))]),
            p.ratification_required, p.dropped_automation, p.scope_note, p.station_seal)
        store = ReceivingStore("T", None)
        r = store.receive(RETIRE_ON, "R-OLD-EXPLICIT",
                          self.orders["R-OLD-EXPLICIT"].seal, tampered, STATION_KEY)
        self.assertIsInstance(r, Refusal)
        self.assertIn("封印", r.reason)

    def test_evidence_bundle_sealed_and_divergent(self):
        p = self.station.permits["R-OLD-BLANK"]
        ev = self.station.evidence[p.evidence_id]
        self.assertTrue(verify_seal(STATION_KEY, ev.payload, ev.station_seal))
        self.assertTrue(ev.payload["divergence"])


class StoreAndScenarioTests(unittest.TestCase):
    def setUp(self):
        self.station = run_demo(verbose=False)

    def test_full_scenario_runs_and_chain_valid(self):
        for sid, audit in self.station.audit_stores().items():
            self.assertTrue(audit["chain_valid"], sid)

    def test_horizon_refusal_issues_proposal_not_silent_truncation(self):
        proposals = [p for p in self.station.proposals.values()
                     if p.order_id == "R-GROWN"]
        self.assertTrue(proposals)
        self.assertEqual(proposals[0].kind, "store-swap")
        b_log = self.station.audit_stores()["STORE-B"]["entries"]
        self.assertTrue(any(e["action"] == "refuse" for e in b_log))
        # 义务仍成立：换库后 STORE-A 正常入库，到期日仍是 2025-07-01
        a = [e for e in self.station.audit_stores()["STORE-A"]["entries"]
             if e["order_id"] == "R-GROWN" and e["action"] == "receive"]
        self.assertEqual(a[0]["detail"]["retain_until"], "2025-06-30")

    def test_offline_early_dispose_refused_and_late_dispose_executed(self):
        entries = self.station.audit_stores()["STORE-A"]["entries"]
        refuses = [e for e in entries if e["order_id"] == "R-OLD-EXPLICIT"
                   and e["action"] == "refuse"]
        disposes = [e for e in entries if e["order_id"] == "R-OLD-EXPLICIT"
                    and e["action"] == "dispose"]
        self.assertTrue(refuses)
        self.assertEqual(disposes[0]["on"], "2025-04-10")

    def test_blank_order_offline_dispose_never_approved(self):
        entries = self.station.audit_stores()["STORE-A"]["entries"]
        self.assertFalse(any(e["order_id"] == "R-OLD-BLANK"
                             and e["action"] == "dispose" for e in entries))
        self.assertTrue(any(e["order_id"] == "R-OLD-BLANK"
                            and e["action"] == "refuse" for e in entries))

    def test_unexpected_transfer_drill(self):
        entries = self.station.audit_stores()["STORE-A"]["entries"]
        t1 = [e for e in entries if e["order_id"] == "R-TR-1"
              and e["action"] == "transfer-in"]
        self.assertEqual(t1[0]["detail"]["derived_until"], "2026-01-03")
        # R-TR-2 转交被冻结并新开追认案；演示中批准的是退场时的原案件，
        # 转交冻结案必须保持 open，不能在离线状态下被自动消化
        self.assertTrue(any(e["order_id"] == "R-TR-2" and e["action"] == "refuse"
                            for e in entries))
        frozen = next(c for c in self.station.cases.values()
                      if c.order_id == "R-TR-2" and "转交被冻结" in c.trigger)
        self.assertEqual(frozen.status, "open")
        approved = next(c for c in self.station.cases.values()
                        if c.order_id == "R-TR-2" and c.status == "approved")
        self.assertIsNotNone(approved.resolution)
        # 追认许可的到期日 = 转交日 2025-10-05 + 合同默认 180 天
        new_permit = self.station.permits["R-TR-2"]
        self.assertEqual(new_permit.operations[0].arg, "2026-04-03")
        self.assertFalse(new_permit.ratification_required)
        self.assertTrue(any("客户追认" in cond
                            for cond in new_permit.operations[0].conditions))

    def test_renewal_intake_and_audit(self):
        p = self.station.permits["R-NRN"]
        self.assertFalse(p.ratification_required)
        # 生效日 2025-12-01 + 180 天 = 2026-05-30
        self.assertEqual(p.operations[0].arg, "2026-05-30")
        c_entries = self.station.audit_stores()["STORE-C"]["entries"]
        self.assertTrue(any(e["order_id"] == "R-NRN" and e["action"] == "receive"
                            for e in c_entries))


class ReplayTests(unittest.TestCase):
    def test_journal_replay_matches_live(self):
        import os
        import tempfile
        live = run_demo(verbose=False)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "journal.json")
            live.save_journal(path)
            contracts, orders = build_contracts(), build_orders()
            replayed = Station.replay(
                path, lambda oid: orders[oid],
                lambda payload: contracts[(payload["contract_id"], payload["version"])])
        self.assertEqual(replayed.root_hash, live.root_hash)
        self.assertEqual(canon(replayed.report()), canon(live.report()))

    def test_extra_guards_pass(self):
        self.assertTrue(all(extra_guards(verbose=False).values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
