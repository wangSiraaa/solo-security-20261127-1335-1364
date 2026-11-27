"""安全不变量单元测试：python -m unittest drill_dispatch.tests.test_safety -v"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from drill_dispatch.codec import read_json
from drill_dispatch.drillmaster import ClaimError, DrillMaster
from drill_dispatch.external import Cabinet, ReinspectStation
from drill_dispatch.router import (
    CRASH_ONCE,
    DomainGuardError,
    ProcessCrash,
    Router,
    TIMEOUT_ONCE,
)
from drill_dispatch.runner import JobRejected, Runner


def make_order(**over):
    order = {
        "order_id": "WO-1",
        "item_id": "ITEM-A",
        "receiver": "GROUP-A",
        "cabinet": "vault-main",
        "station": "reinspect-1",
        "purpose": "测试",
    }
    order.update(over)
    return order


class SafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="dd-test-")
        self.prod_jobs = os.path.join(self.root, "prod", "jobs")
        self.drill_jobs = os.path.join(self.root, "drill", "jobs")
        self.cab = Cabinet(self.root, "prod", "vault-main")
        self.station = ReinspectStation(self.root, "prod", "reinspect-1")
        self.cab.load_items({"ITEM-A": {"allowed_receiver": "GROUP-A"}})
        self.router = Router(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_timeout_retry_emits_single_receipt(self) -> None:
        self.router.set_fault("J1", "check", TIMEOUT_ONCE)
        r = Runner(self.root, self.prod_jobs, "J1", make_order(), "prod", self.router)
        r.run()
        fresh = Cabinet(self.root, "prod", "vault-main")
        self.assertEqual(fresh.state["dispatched"], 1)
        station = ReinspectStation(self.root, "prod", "reinspect-1")
        self.assertEqual(station.state["seq"], 1)  # 超时重传不产生第二序号
        checks = [a for a in station.actions() if a["kind"] == "check"]
        self.assertEqual(len(checks), 1)

    def test_crash_replay_no_double_dispatch(self) -> None:
        self.router.set_fault("J2", "take", CRASH_ONCE)
        r = Runner(self.root, self.prod_jobs, "J2", make_order(), "prod", self.router)
        with self.assertRaises(ProcessCrash):
            r.run()
        mid = Cabinet(self.root, "prod", "vault-main").state["dispatched"]
        self.assertEqual(mid, 1)
        # 重启
        r2 = Runner(self.root, self.prod_jobs, "J2", make_order(), "prod",
                    Router(self.root))
        r2.resume_after_crash()
        self.assertEqual(Cabinet(self.root, "prod", "vault-main").state["dispatched"], 1)
        self.assertEqual(r2.journal.get_state()["status"], "COMPLETED")

    def test_drill_cannot_touch_prod_even_with_cached_name(self) -> None:
        dm = DrillMaster(self.root)
        snap = dm.take_baseline(self.cab)
        dm.build_shadow("shadow")
        dm.seal_production()
        # 空别名表：演练作业缓存里的正式柜名无法解析
        rogue = Router(self.root, domain_aliases={})
        order = make_order(cabinet_shadow="shadow")
        with self.assertRaises(DomainGuardError):
            Runner(self.root, self.drill_jobs, "J3", order, "drill", rogue).run()
        self.assertEqual(
            Cabinet(self.root, "prod", "vault-main").state["dispatched"], 0
        )

    def test_drill_counter_independent_and_replay_idempotent(self) -> None:
        dm = DrillMaster(self.root)
        dm.take_baseline(self.cab)
        dm.build_shadow("shadow")
        ReinspectStation(self.root, "drill", "shadow-reinspect",
                         genesis="g")
        rt = Router(self.root, domain_aliases={
            "vault-main": "shadow", "reinspect-1": "shadow-reinspect"})
        order = make_order(cabinet_shadow="shadow")
        Runner(self.root, self.drill_jobs, "J4", order, "drill", rt).run()
        Runner(self.root, self.drill_jobs, "J4", order, "drill", rt).run()
        shadow = Cabinet(self.root, "drill", "shadow")
        self.assertEqual(shadow.state["dispatched"], 1)
        self.assertEqual(shadow.state["seq"], 1)
        # 正式柜纹丝不动
        self.assertEqual(Cabinet(self.root, "prod", "vault-main").state["seq"], 0)

    def test_old_receipt_cannot_seed_new_order(self) -> None:
        # 正式先跑一笔，拿到历史回条
        r = Runner(self.root, self.prod_jobs, "J5", make_order(), "prod", self.router)
        r.run()
        history = r._receipt_of("take")
        # 退回并拍快照
        Cabinet(self.root, "prod", "vault-main").give_back(
            {"idempotency_key": "rb", "intent_digest": "x",
             "ticket": {"order_id": "WO-1", "item_id": "ITEM-A",
                        "receiver": "GROUP-A"}})
        dm = DrillMaster(self.root)
        dm.take_baseline(Cabinet(self.root, "prod", "vault-main"))
        dm.build_shadow("shadow")
        rt = Router(self.root, domain_aliases={
            "vault-main": "shadow", "reinspect-1": "shadow-reinspect"})
        ReinspectStation(self.root, "drill", "shadow-reinspect")
        moved = make_order(order_id="WO-OTHER", cabinet_shadow="shadow")
        with self.assertRaises(ClaimError):
            dm.start_drill_job(self.drill_jobs, "J6", moved, rt,
                               source_receipt=history)

    def test_receiver_swap_blocks_original_group(self) -> None:
        dm = DrillMaster(self.root)
        dm.take_baseline(self.cab)
        shadow = dm.build_shadow("shadow")
        shadow.replace_receiver("ITEM-A", "GROUP-B")
        rt = Router(self.root, domain_aliases={
            "vault-main": "shadow", "reinspect-1": "shadow-reinspect"})
        ReinspectStation(self.root, "drill", "shadow-reinspect")
        # 原组直接打柜也被拒
        with self.assertRaises(JobRejected):
            Runner(self.root, self.drill_jobs, "J7",
                   make_order(cabinet_shadow="shadow"), "drill", rt).run()
        # 新组办成，回条记名新组
        ok = Runner(self.root, self.drill_jobs, "J8",
                    make_order(receiver="GROUP-B", cabinet_shadow="shadow"),
                    "drill", rt)
        ok.run()
        self.assertEqual(ok._receipt_of("take")["receiver"], "GROUP-B")

    def test_evidence_tamper_is_detected(self) -> None:
        r = Runner(self.root, self.prod_jobs, "J9", make_order(), "prod", self.router)
        r.run()
        dm = DrillMaster(self.root)
        dm.import_evidence(self.prod_jobs, "J9")
        self.assertEqual(dm.verify_evidence(), [])
        target = os.path.join(self.root, "evidence", "J9", "slip.json")
        os.chmod(target, 0o644)
        data = read_json(target)
        data["receiver"] = "GROUP-FORGERY"
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f)
        problems = dm.verify_evidence()
        self.assertTrue(any("哈希不符" in p for p in problems))

    def test_chain_tamper_is_detected(self) -> None:
        Runner(self.root, self.prod_jobs, "J10", make_order(), "prod",
               self.router).run()
        svc = Cabinet(self.root, "prod", "vault-main")
        self.assertEqual(svc.verify(), [])
        ledger = os.path.join(self.root, "prod", "vault-main", "ledger.jsonl")
        with open(ledger, "a", encoding="utf-8") as f:
            f.write('{"seq": 99, "kind": "take"}\n')
        self.assertTrue(svc.verify())


if __name__ == "__main__":
    unittest.main()
