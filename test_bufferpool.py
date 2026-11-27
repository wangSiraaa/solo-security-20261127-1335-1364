# -*- coding: utf-8 -*-
"""缓冲池端到端测试：六张虚构页 + 本地测试设备，纯标准库 unittest。"""

import unittest

from bufferpool import (
    BufferPool, Maintainer, SimDevice,
    ReqState, PageState, EventType, PAGE_SIZE,
)


def fresh_pool(nr: int = 6) -> tuple[BufferPool, Maintainer]:
    pool = BufferPool(SimDevice(nr))
    for pid in range(nr):
        pool.mark_clean_for_setup(pid)
    pool.events.clear()
    return pool, Maintainer(pool)


class CrossTenantBorrowTests(unittest.TestCase):
    def test_borrow_reads_zero_then_writes_own_bytes(self):
        """给一张页办成跨户借用：乙必须读到全零，再写入自己的值。"""
        pool, m = fresh_pool()
        a = pool.new_tenant("甲")
        b = pool.new_tenant("乙")

        ga = a.borrow(1, contiguous=True)
        pid = ga.pids[0]
        req = a.write_page(pid, {0: b"JIA-DATA", 2: b"secret!!"}, tag="a-data")
        a.flush([req])
        m.drain_page(pid)
        self.assertIn(b"JIA-DATA", pool.device.read(pid))

        a.return_lease(pool.tenants["甲"].lease_ids[0])
        m.recycle([pid])
        self.assertEqual(pool.pages[pid].state, PageState.CLEAN)

        gb = b.borrow(1)
        self.assertEqual(gb.kind, "granted")
        seen = b.read_page(pid)
        self.assertEqual(seen, b"\x00" * PAGE_SIZE, "新租户不得看到上一家的字节")

        req_b = b.write_page(pid, {1: b"YI-VALUE"}, tag="b-data")
        b.flush([req_b])
        m.drain_page(pid)
        self.assertIn(b"YI-VALUE", b.read_page(pid))
        self.assertNotIn(b"JIA-DATA", b.read_page(pid))

    def test_late_stale_write_after_sanitize_blocks_handoff(self):
        """清零之后才送达、仍对原页生效的旧代写：原交接不能照发，页被隔离。"""
        pool, m = fresh_pool()
        a = pool.new_tenant("甲")
        ga = a.borrow(1)
        pid = ga.pids[0]
        old_gen = pool.pages[pid].gen

        req = a.write_page(pid, {0: b"OLDBYTE!"}, tag="old")
        a.flush([req])
        m.drain_page(pid)
        a.return_lease(pool.tenants["甲"].lease_ids[0])
        m.recycle([pid])                       # 代次 +1，页全零
        self.assertEqual(pool.pages[pid].gen, old_gen + 1)

        # 故障注入：维护端迟送一条仍对“原页”生效的旧代次写入
        late = pool.device.inject_late(req)
        self.assertEqual(late.gen, old_gen)
        done = pool.device_complete_next()
        self.assertIs(done, late)
        self.assertEqual(done.state, ReqState.LATE_STALE)
        self.assertEqual(pool.pages[pid].state, PageState.QUARANTINED)
        self.assertEqual(pool.device.read(pid), b"\x00" * PAGE_SIZE,
                         "旧代次内容绝不能落回已净化的页")
        b = pool.new_tenant("乙")
        blocked = b.borrow(1)                  # 其余页仍可租，但隔离那张绝不签出
        self.assertEqual(blocked.kind, "granted")
        self.assertNotIn(pid, blocked.pids)
        self.assertEqual(
            [g for g in pool.guarantees_held()
             if g.tenant == "乙" and pid in g.pids],
            [], "隔离页上不得向任何租户签保障")
        self.assertTrue(
            all(g.tenant != "乙" or pid not in g.pids
                for g in pool.guarantees_held()))

        # 恢复：重新净化后无在途旧写，跨户借用恢复正常
        m.recover(pid)
        self.assertEqual(pool.pages[pid].state, PageState.CLEAN)
        b = pool.new_tenant("乙")
        gb = b.borrow(1)
        self.assertEqual(gb.kind, "granted")
        self.assertEqual(b.read_page(pid), b"\x00" * PAGE_SIZE)

    def test_nonadjacent_pages_cannot_serve_contiguous_order(self):
        """乙改成两张相邻；唯一另一张空页不相邻：单页合格但乙的订单不能成交。"""
        pool, m = fresh_pool()
        a = pool.new_tenant("甲")
        ga = a.borrow(2, contiguous=True)     # 甲占相邻两张 P0、P1
        p0, p1 = ga.pids
        a.return_lease(pool.tenants["甲"].lease_ids[0])
        m.recycle([p0])                        # 只净化 P0；P1 保持脏
        pool.pages[p1].state = PageState.UNKNOWN
        # P2..P4 也置脏，只留下 P5 这张与 P0 不相邻的干净页
        for pid in (2, 3, 4):
            pool.pages[pid].state = PageState.UNKNOWN

        b = pool.new_tenant("乙")
        reply = b.borrow(2, contiguous=True)
        self.assertEqual(reply.kind, "rejected")
        self.assertIn("相邻", reply.message)
        self.assertEqual(
            [g for g in pool.guarantees_held() if g.tenant == "乙"],
            [], "不能成交的订单不得签保障")

        single = b.borrow(1)                   # 单页请求仍然合格
        self.assertEqual(single.kind, "granted")
        self.assertEqual(single.pids, [p0])


class LifecycleTests(unittest.TestCase):
    def test_three_events_are_distinct_and_ordered(self):
        """上家退出、旧代写收尾、清零净化是三个不同事件，且按序留痕。"""
        pool, m = fresh_pool()
        a = pool.new_tenant("甲")
        ga = a.borrow(1)
        pid = ga.pids[0]
        req = a.write_page(pid, {0: b"ABCDabcd"}, tag="x")  # 段长必须恰为 8
        a.flush([req])
        a.return_lease(pool.tenants["甲"].lease_ids[0])
        m.recycle([pid])

        seq = [t for t, _ in pool.events]
        self.assertLess(seq.index(EventType.HANDBACK),
                        seq.index(EventType.OLD_GEN_DRAINED))
        self.assertLess(seq.index(EventType.OLD_GEN_DRAINED),
                        seq.index(EventType.SANITIZED))

    def test_pending_cancel_but_inflight_not(self):
        """受理后尚可撤回；一旦交设备（在途）就不可撤回，只能等回报。"""
        pool, _ = fresh_pool()
        a = pool.new_tenant("甲")
        ga = a.borrow(1)
        pid = ga.pids[0]
        r1 = a.write_page(pid, {0: b"cancel-m"}, tag="p")
        r2 = a.write_page(pid, {1: b"keep-goi"}, tag="i")
        a.cancel(r1)
        self.assertEqual(r1.state, ReqState.CANCELED)
        a.flush([r2])
        with self.assertRaises(RuntimeError):
            a.cancel(r2)

    def test_maintainer_restart_rebuilds_inflight_ledger(self):
        """维护端重启：PENDING 仍可撤回，INFLIGHT 账本按设备状态重建。"""
        pool, m = fresh_pool()
        a = pool.new_tenant("甲")
        ga = a.borrow(1)
        pid = ga.pids[0]
        r_pending = a.write_page(pid, {0: b"pending0"}, tag="p")
        r_inflight = a.write_page(pid, {1: b"inflight"}, tag="i")
        a.flush([r_inflight])
        self.assertEqual(len(pool.pages[pid].inflight), 1)

        state = m.restart()
        self.assertEqual(state, {"inflight": 1, "pending": 1})
        a.cancel(r_pending)                       # 重启后仍可撤回
        self.assertEqual(r_pending.state, ReqState.CANCELED)
        m.drain_page(pid)
        self.assertEqual(r_inflight.state, ReqState.DONE)

    def test_generation_recorded_on_mapping_and_requests(self):
        """映射与写请求都记录有效代次；净化后代次推进，旧请求代次失效。"""
        pool, m = fresh_pool()
        a = pool.new_tenant("甲")
        ga = a.borrow(1)
        pid = ga.pids[0]
        g0 = pool.pages[pid].gen
        map_evt = next(info for t, info in pool.events if t == EventType.MAPPED)
        self.assertEqual(map_evt["gen"], g0)

        req = a.write_page(pid, {0: b"gen-chec"}, tag="g")
        self.assertEqual(req.gen, g0)
        a.flush([req])
        m.drain_page(pid)
        a.return_lease(pool.tenants["甲"].lease_ids[0])
        m.recycle([pid])
        self.assertGreater(pool.pages[pid].gen, req.gen)

    def test_fork_replays_two_outcomes_from_same_state(self):
        """同一批状态深拷贝重放：甲回来 / 甲放弃互不污染。"""
        pool, m = fresh_pool()
        a = pool.new_tenant("甲")
        ga = a.borrow(2, contiguous=True)
        pids = ga.pids
        reqs = [a.write_page(pids[0], {0: b"RESULT-1"}, tag="r1"),
                a.write_page(pids[1], {0: b"RESULT-2"}, tag="r2")]
        a.flush(reqs)
        m.drain_lease(pids)
        lid = pool.tenants["甲"].lease_ids[0]
        a.return_lease(lid)
        m.seal_for_continuation(lid, until_shift=2)

        # 走向 R：甲回来，原内容保留
        r = pool.fork()
        r.tenants["甲"].resume(lid)
        self.assertIn(b"RESULT-1", r.tenants["甲"].read_page(pids[0]))
        self.assertEqual(r.pages[pids[0]].state, PageState.LEASED)

        # 走向 Q：甲放弃，页被净化，原状态不变
        q = pool.fork()
        q.tenants["甲"].renounce(lid)
        Maintainer(q).recycle(pids)
        self.assertEqual(q.device.read(pids[0]), b"\x00" * PAGE_SIZE)
        self.assertEqual(q.pages[pids[0]].state, PageState.CLEAN)
        # 原池与 R 拷贝不受 Q 影响
        self.assertEqual(pool.pages[pids[0]].state, PageState.SEALED)
        self.assertEqual(r.pages[pids[0]].state, PageState.LEASED)


class ReservationTests(unittest.TestCase):
    def test_waiting_promoted_after_sanitize_and_guarantee_fulfilled(self):
        """无库存时只进等候；净化出连续页后自动转正并签保障，保障必须兑现。"""
        pool, m = fresh_pool()
        other = pool.new_tenant("丁")
        a = pool.new_tenant("甲")
        ga = a.borrow(2, contiguous=True)            # 甲占相邻两张
        p0, p1 = ga.pids
        # 其余干净页全部租出，池里再无任何干净库存
        for pid in (pid for pid in range(6) if pid not in (p0, p1)):
            self.assertEqual(other.borrow(1).pids, [pid])
        a.return_lease(pool.tenants["甲"].lease_ids[0])

        b = pool.new_tenant("乙")
        # 两张都脏：未来班次订单进等候，不签保障
        reply = b.borrow(2, contiguous=True, shift=2)
        self.assertEqual(reply.kind, "waiting")
        self.assertIsNone(reply.guarantee)
        self.assertEqual(
            [g for g in pool.guarantees_held() if g.tenant == "乙"], [])

        m.recycle([p0, p1])                       # 空档净化，等候单应自动转正
        res = pool.reservations[reply.reservation]
        self.assertEqual(res.status, "promoted")
        g = next(x for x in pool.guarantees_held() if x.tenant == "乙")
        self.assertEqual(g.pids, [p0, p1])
        self.assertEqual(g.shift, 2)

        # 已签保障必须兑现：班次 2 乙能实际读到全零并写自己的值
        pool.advance_shift()
        self.assertEqual(b.read_page(p0), b"\x00" * PAGE_SIZE)
        req = b.write_page(p0, {0: b"YI-DATA!"}, tag="yi")
        b.flush([req])
        m.drain_page(p0)
        self.assertIn(b"YI-DATA!", b.read_page(p0))

    def test_current_shift_contiguous_failure_is_rejected_not_waited(self):
        """当班连续订单无库存：直接判不能成交，不许空头承诺。"""
        pool, _ = fresh_pool()
        # 把奇数页置脏，干净页只剩互不相邻的偶数页
        for pid in (1, 3, 5):
            pool.pages[pid].state = PageState.UNKNOWN
        b = pool.new_tenant("乙")
        reply = b.borrow(2, contiguous=True)    # 当班、要连续
        self.assertEqual(reply.kind, "rejected")
        self.assertEqual(pool.reservations, {})
        # 单页小单仍然合格
        single = b.borrow(1)
        self.assertEqual(single.kind, "granted")
        self.assertEqual(single.pids, [0])

    def test_past_shift_order_must_reschedule(self):
        """过了首班的订单只能改期：向过去班次下单直接拒收。"""
        pool, _ = fresh_pool()
        pool.advance_shift()                     # 现在是班次 2
        b = pool.new_tenant("乙")
        reply = b.borrow(2, contiguous=True, shift=1)
        self.assertEqual(reply.kind, "rejected")
        self.assertIn("改期", reply.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
