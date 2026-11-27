"""断言隔离、共享实块、退款合同与回收重开的性质。"""

import unittest

from plant import Plant, PlantError, swatch_bytes, sha_hex
from client import TenantClient


def fresh(capacity=1 << 20, grace=60):
    p = Plant(physical_capacity=capacity, grace_seconds=grace)
    for t in ("甲", "乙", "丙"):
        p.register_tenant(t, 100000)
    return p, TenantClient(p, "甲"), TenantClient(p, "乙"), TenantClient(p, "丙")


SPECS_A = [(1, "red"), (2, "blue"), (3, "green")]
SPECS_B = [(1, "red"), (2, "yellow")]


class IsolationTest(unittest.TestCase):
    def setUp(self):
        self.p, self.a, self.b, self.c = fresh()
        self.ra = self.a.reserve("vol-a", SPECS_A)
        self.a.upload_all(self.ra, SPECS_A)
        self.rb = self.b.reserve("vol-b", SPECS_B)
        self.b.upload_all(self.rb, SPECS_B)

    def test_tenant_only_sees_own_pages(self):
        aa = {x["page_no"]: x["data"] for x in self.a.restore("vol-a")["pages"]}
        bb = {x["page_no"]: x["data"] for x in self.b.restore("vol-b")["pages"]}
        self.assertEqual(set(aa), {1, 2, 3})
        self.assertEqual(set(bb), {1, 2})
        # 相同的 red 页两侧字节相等
        self.assertEqual(aa[1], bb[1])
        self.assertEqual(aa[1], swatch_bytes("red"))

    def test_cross_tenant_no_hint_and_full_price(self):
        # 乙的红页与甲同内容，但回单不提示“已有”，且全价计费
        # 乙 committed 应为两页 800（不因甲已有而打折）
        self.assertEqual(self.b.balance()["committed"], 800)
        # 甲的 committed 不受乙影响
        self.assertEqual(self.a.balance()["committed"], 1200)

    def test_delete_coincident_page_only_hits_tenant(self):
        before = self.b.restore("vol-b")
        before_bytes = {x["page_no"]: x["data"] for x in before["pages"]}
        before_bal = self.b.balance()
        self.a.delete_page("vol-a", 1)  # 删 red（甲侧）
        after = self.b.restore("vol-b")
        after_bytes = {x["page_no"]: x["data"] for x in after["pages"]}
        self.assertEqual(before_bytes, after_bytes)
        self.assertEqual(before_bal, self.b.balance())
        # red 物理块仍 live（乙在用）
        inv = self.p.admin_inventory()
        red = sha_hex(swatch_bytes("red"))
        blob = [x for x in inv["blobs"] if x["sha"] == red][0]
        self.assertEqual(blob["state"], "live")
        self.assertGreaterEqual(blob["refcount"], 1)

    def test_physical_saving_separate_from_logical(self):
        inv = self.p.admin_inventory()
        # red 被乙共用：物理节省 400，但逻辑合计 = 甲1200 + 乙800
        self.assertEqual(inv["physical_savings_total"], 400)
        self.assertEqual(inv["logical_committed_total"], 2000)
        self.assertEqual(inv["physical_used"], 1600)  # 4 distinct blobs

    def test_client_cannot_read_other_tenant(self):
        # 甲在自己的作用域里看不到乙的卷
        with self.assertRaises(PlantError) as cm:
            self.a.restore("vol-b")
        self.assertEqual(cm.exception.code, "volume_not_recoverable")
        # 甲在自己的作用域里 release 一个同名卷，不会触碰乙的真实卷
        self.p.release_retention("甲", "vol-b")
        self.assertTrue(self.b.volume_state("vol-b")["restorable"])
        self.assertFalse(self.b.volume_state("vol-b")["contract_released"])
        self.assertEqual([x["page_no"] for x in self.b.restore("vol-b")["pages"]],
                         [1, 2])


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.p, self.a, self.b, self.c = fresh(grace=60)
        rb = self.b.reserve("vol-b", SPECS_B)
        self.b.upload_all(rb, SPECS_B)

    def test_grace_period_restore_then_expire(self):
        self.b.release_retention("vol-b")
        # 宽限期内仍可还原，账不变
        self.assertEqual([x["page_no"] for x in self.b.restore("vol-b")["pages"]], [1, 2])
        self.assertEqual(self.b.balance()["committed"], 800)
        with self.assertRaises(PlantError) as cm:
            self.b.settle_release("vol-b")
        self.assertEqual(cm.exception.code, "grace_not_elapsed")
        # 过宽限
        self.p.tick(61)
        with self.assertRaises(PlantError) as cm:
            self.b.restore("vol-b")
        self.assertEqual(cm.exception.code, "retention_expired")
        res = self.b.settle_release("vol-b")
        self.assertEqual(res["refunded_bytes"], 800)
        self.assertEqual(self.b.balance()["committed"], 0)
        self.assertEqual(self.b.balance()["refundable_bytes"], 100000)

    def test_release_keeps_pages_then_settles_real_contract(self):
        self.b.release_retention("vol-b")
        self.p.tick(61)
        self.b.settle_release("vol-b")
        # 合同释放后不可恢复，但撤销前数据一直在（宽限期内）
        self.assertFalse(self.b.volume_state("vol-b")["restorable"])


class ReclaimReopenTest(unittest.TestCase):
    def setUp(self):
        self.p, self.a, self.b, self.c = fresh()
        ra = self.a.reserve("vol-a", SPECS_A)
        self.a.upload_all(ra, SPECS_A)
        self.ra_id = ra["request_id"]

    def test_incomplete_reclaim_reopen_and_query(self):
        self.a.delete_volume("vol-a")
        self.p.make_reclaim_job("job-a")
        step = self.p.reclaim_step("job-a", count=1)  # 未完成
        self.assertGreater(step["remaining"], 0)
        self.assertEqual(self.p.reclaim_job_state("job-a")["state"], "open")
        q1 = self.p.pages_available_to_next_tenant(self.ra_id)
        # 只释放了 1 个，仍标记有未释放
        self.assertEqual(q1["freed_count"], 1)
        self.assertGreater(q1["not_yet_freed"], 0)
        # 重开
        self.p.reopen_reclaim("job-a")
        self.assertEqual(self.p.reclaim_job_state("job-a")["state"], "done")
        q2 = self.p.pages_available_to_next_tenant(self.ra_id)
        self.assertEqual(q2["freed_count"], 3)
        self.assertEqual(q2["not_yet_freed"], 0)
        self.assertEqual(q2["request_id"], self.ra_id)
        # 全部已给下家 -> 物理待回收清零
        self.assertEqual(self.p.admin_inventory()["pending_reclaim_bytes"], 0)

    def test_pending_capacity_then_retry(self):
        # 先占满容量
        p, a, b, c = fresh(capacity=800, grace=60)
        ra = a.reserve("va", [(1, "x"), (2, "y")])
        a.upload_all(ra, [(1, "x"), (2, "y")])  # 800 = full
        rc = c.reserve("vc", [(1, "z"), (2, "w")])
        st = [c.upload(rc["request_id"], pn, s)["state"]
              for pn, s in [(1, "z"), (2, "w")]]
        self.assertTrue(all(x == "pending_capacity" for x in st))
        # pending 不扣 committed，冻结保留
        self.assertEqual(c.balance()["committed"], 0)
        self.assertEqual(c.balance()["reserved"], 800)
        # 释放空间
        a.delete_volume("va")
        p.make_reclaim_job("j"); p.reopen_reclaim("j")
        retried = c.retry_pending(rc["request_id"])
        self.assertTrue(any(x["state"] == "received" for x in retried))
        self.assertEqual([x["page_no"] for x in c.restore("vc")["pages"]], [1, 2])
        self.assertEqual(c.balance()["reserved"], 0)


class IdempotencyTest(unittest.TestCase):
    def test_reupload_same_page_no_double_bill(self):
        p, a, b, c = fresh()
        specs = [(1, "red")]
        ra = a.reserve("v", specs)
        r1 = a.upload(ra["request_id"], 1, "red")
        r2 = a.upload(ra["request_id"], 1, "red")
        self.assertEqual(r1["state"], "received")
        self.assertEqual(r2["state"], "received")
        self.assertEqual(a.balance()["committed"], 400)

    def test_checksum_mismatch_rejected(self):
        p, a, b, c = fresh()
        ra = a.reserve("v", [(1, "red")])
        from plant import sha_hex
        with self.assertRaises(PlantError) as cm:
            p.receive(ra["request_id"], 1, b"not the right bytes")
        self.assertEqual(cm.exception.code, "page_mismatch")


class IntraVolumeDuplicateTest(unittest.TestCase):
    def test_duplicate_page_in_one_volume_billed_once(self):
        p, a, b, c = fresh()
        specs = [(1, "pink"), (2, "purple"), (3, "pink")]  # page 1 & 3 identical
        ra = a.reserve("v", specs)
        a.upload_all(ra, specs)
        # 逻辑只计 pink 一次 + purple 一次 = 800，而非 1200
        self.assertEqual(a.balance()["committed"], 800)
        # 冻结额度按声明 1200 收，结算后剩余退回
        self.assertEqual(a.balance()["reserved"], 0)
        # 册仍按有序页保存 3 页，重复页可还原
        restored = a.restore("v")
        self.assertEqual([x["page_no"] for x in restored["pages"]], [1, 2, 3])
        data = {x["page_no"]: x["data"] for x in restored["pages"]}
        self.assertEqual(data[1], data[3])
        self.assertEqual(data[1], swatch_bytes("pink"))
        # 同租户内共享实块：物理只剩 2 个块
        inv = p.admin_inventory()
        self.assertEqual(inv["physical_used"], 800)
        self.assertEqual(inv["physical_savings_total"], 400)

    def test_receipt_shows_within_tenant_repeat_only(self):
        p, a, b, c = fresh()
        a.upload_all(a.reserve("va", [(1, "red")]), [(1, "red")])
        # 乙的预约页与甲同内容：回单不提示
        rb = b.reserve("vb", [(1, "red"), (2, "red")])
        r1 = b.upload(rb["request_id"], 1, "red")
        r2 = b.upload(rb["request_id"], 2, "red")
        self.assertFalse(r1["already_in_this_tenant"])   # 甲有不算
        self.assertTrue(r2["already_in_this_tenant"])    # 乙本户第二份才算


if __name__ == "__main__":
    unittest.main(verbosity=2)
