# -*- coding: utf-8 -*-
"""
多租户模拟缓冲池（只操作模拟字节页）。

核心规则
========
1. 每张物理页分成 SEGMENTS=4 段；设备写入“先受理、后完成”：
   PENDING（尚可撤回） -> INFLIGHT（已交设备，不可撤回） -> DONE。
2. 每张页、每个写请求都带代次（generation）。页被清零净化时代次 +1，
   代次不匹配的迟到写入判为 LATE_STALE，并把该页隔离（QUARANTINED），
   交接给下一家必须等重新净化。
3. 三类事件严格区分、分别留痕：
   - HANDBACK        上家退出/交还（映射是否拆除取决于是否封存）
   - OLD_GEN_DRAINED 旧代次在途写全部收尾
   - SANITIZED       页被覆盖为全零、可跨户借用
4. 已签出的保障（Guarantee）必须兑现，因此实现只在确实能交货时才签保障；
   满足不了的订单进入等候（WAITING，无保障）或直接拒收（REJECTED）。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

SEGMENTS = 4
SEGMENT_SIZE = 8
PAGE_SIZE = SEGMENTS * SEGMENT_SIZE


# --------------------------------------------------------------------------
# 枚举与数据结构
# --------------------------------------------------------------------------
class ReqState(Enum):
    PENDING = "pending"        # 已受理，仍在本池队列，可撤回
    INFLIGHT = "inflight"      # 已交模拟设备，不可撤回，等待完成回报
    DONE = "done"
    CANCELED = "canceled"      # 受理后、交设备前撤回
    LATE_STALE = "late_stale"  # 清零之后才落到本页的旧代次写入


class PageState(Enum):
    UNKNOWN = "unknown"        # 内容不明/脏，不能出租
    LEASED = "leased"          # 已映射给某租户
    SEALED = "sealed"          # 原主专用封存，内容保留到约定班次
    QUARANTINED = "quarantined"  # 收到旧代次迟到写，阻断交接
    CLEAN = "clean"            # 全零、可承诺给任意租户


class EventType(Enum):
    HANDBACK = "handback"                       # 上家退出、交还缓冲
    SEALED = "sealed"                           # 按原主专用封存
    RESUMED = "resumed"                         # 原主开机续跑、领回
    RENOUNCED = "renounced"                     # 原主放弃续跑
    OLD_GEN_DRAINED = "old_gen_drained"         # 旧代写全部收尾
    WRITE_CANCELED = "write_canceled"
    WRITE_COMPLETED = "write_completed"
    LATE_STALE_BLOCKED = "late_stale_blocked"
    ZERO_SUBMITTED = "zero_submitted"
    SANITIZED = "sanitized"                     # 覆盖为零、净化完成
    QUARANTINED = "quarantined"
    RECOVERED = "recovered"
    MAPPED = "mapped"                           # 页映射给租户（记代次）
    LEASED = "leased"
    GUARANTEE_SIGNED = "guarantee_signed"
    RESERVATION_WAITING = "reservation_waiting"
    RESERVATION_PROMOTED = "reservation_promoted"
    RESERVATION_EXPIRED = "reservation_expired"
    RESTART = "restart"


@dataclass
class WriteRequest:
    rid: int
    pid: int
    gen: int                     # 提交时该页的有效代次
    tenant: str
    data: bytes
    tag: str = ""
    state: ReqState = ReqState.PENDING


@dataclass
class Page:
    pid: int
    state: PageState = PageState.UNKNOWN
    owner: Optional[str] = None
    gen: int = 0
    inflight: list[int] = field(default_factory=list)   # 在设备队列中的 rid
    sealed_until: Optional[int] = None

    @property
    def quarantined(self) -> bool:
        return self.state == PageState.QUARANTINED


@dataclass
class Lease:
    lid: int
    tenant: str
    pids: list[int]
    state: str = "active"        # active / sealed / returned


@dataclass
class Reservation:
    rid: int
    tenant: str
    count: int
    contiguous: bool
    deadline_shift: int
    status: str = "waiting"      # waiting / promoted / expired / rejected
    note: str = ""


@dataclass
class Guarantee:
    gid: int
    tenant: str
    pids: list[int]
    shift: int
    reservation: Optional[int] = None


@dataclass
class Reply:
    kind: str                    # granted / waiting / rejected
    tenant: str
    pids: Optional[list[int]] = None
    lease_id: Optional[int] = None
    reservation: Optional[int] = None
    guarantee: Optional[int] = None
    shift: Optional[int] = None
    message: str = ""


# --------------------------------------------------------------------------
# 本地测试设备：只存模拟字节，写入先入队、完成时才真正落字节
# --------------------------------------------------------------------------
class SimDevice:
    def __init__(self, nr_pages: int):
        self.nr_pages = nr_pages
        self._pages = [bytearray(PAGE_SIZE) for _ in range(nr_pages)]
        self._queue: list[WriteRequest] = []

    def submit(self, req: WriteRequest) -> None:
        req.state = ReqState.INFLIGHT
        self._queue.append(req)

    def has_inflight(self, pid: Optional[int] = None) -> bool:
        if pid is None:
            return bool(self._queue)
        return any(r.pid == pid for r in self._queue)

    def complete_oldest_for_page(self, pid: int) -> Optional[WriteRequest]:
        for i, req in enumerate(self._queue):
            if req.pid == pid:
                return self._queue.pop(i)
        return None

    def complete_next(self) -> Optional[WriteRequest]:
        if not self._queue:
            return None
        return self._queue.pop(0)

    def inject_late(self, req: WriteRequest) -> WriteRequest:
        """故障注入：把一条旧代次写请求重新塞回设备队列，且池侧账本无感知。"""
        late = copy.copy(req)
        late.state = ReqState.INFLIGHT
        self._queue.append(late)
        return late

    def apply(self, req: WriteRequest) -> None:
        self._pages[req.pid][:] = req.data

    def read(self, pid: int) -> bytes:
        return bytes(self._pages[pid])


# --------------------------------------------------------------------------
# 缓冲池主体：代次、映射、事件、分配、保障、等候、班次
# --------------------------------------------------------------------------
class BufferPool:
    def __init__(self, device: SimDevice):
        self.device = device
        self.pages: dict[int, Page] = {i: Page(i) for i in range(device.nr_pages)}
        self.requests: dict[int, WriteRequest] = {}
        self.tenants: dict[str, "TenantHandle"] = {}
        self.leases: dict[int, Lease] = {}
        self.reservations: dict[int, Reservation] = {}
        self.guarantees: list[Guarantee] = []
        self.events: list[tuple[EventType, dict]] = []
        self.current_shift = 1
        self._rid = 0
        self._lid = 0
        self._res_id = 0
        self._gid = 0

    # ---- 基础记账 --------------------------------------------------------
    def _log(self, etype: EventType, **info) -> None:
        self.events.append((etype, info))

    def new_tenant(self, name: str) -> "TenantHandle":
        h = TenantHandle(name, self)
        self.tenants[name] = h
        return h

    def mark_clean_for_setup(self, pid: int) -> None:
        """测试/建池辅助：把一张页置为已净化的干净页。"""
        p = self.pages[pid]
        p.state = PageState.CLEAN
        p.owner = None
        p.gen += 1
        self.device._pages[pid][:] = b"\x00" * PAGE_SIZE
        self._log(EventType.SANITIZED, pid=pid, gen=p.gen, setup=True)

    def fork(self) -> "BufferPool":
        """对同一批状态做深拷贝，用于重放不同走向；句柄回指新池。"""
        new = copy.deepcopy(self)
        for handle in new.tenants.values():
            handle.pool = new
        return new

    # ---- 写入受理 --------------------------------------------------------
    def _accept_write(self, tenant: str, pid: int, data: bytes, tag: str) -> WriteRequest:
        page = self.pages[pid]
        lease = self._active_lease_of(tenant, pid)
        if lease is None or page.state != PageState.LEASED or page.owner != tenant:
            raise PermissionError(f"页 {pid} 未映射给租户 {tenant}，写入被拒")
        assert len(data) == PAGE_SIZE
        self._rid += 1
        req = WriteRequest(self._rid, pid, page.gen, tenant, bytes(data), tag)
        self.requests[req.rid] = req
        return req

    def _submit(self, req: WriteRequest) -> None:
        if req.state != ReqState.PENDING:
            raise RuntimeError(f"请求 {req.rid} 状态为 {req.state.value}，不能再提交设备")
        self.device.submit(req)
        self.pages[req.pid].inflight.append(req.rid)

    def _on_device_completion(self, req: WriteRequest) -> WriteRequest:
        page = self.pages[req.pid]
        # 代次防护：清零之后才落到本页的旧代写，绝不允许覆盖新户内容
        if req.gen != page.gen or page.state in (PageState.CLEAN, PageState.QUARANTINED):
            req.state = ReqState.LATE_STALE
            page.state = PageState.QUARANTINED
            self._log(EventType.LATE_STALE_BLOCKED, rid=req.rid, pid=req.pid,
                      req_gen=req.gen, page_gen=page.gen)
            self._log(EventType.QUARANTINED, pid=req.pid)
            return req
        self.device.apply(req)
        req.state = ReqState.DONE
        if req.rid in page.inflight:
            page.inflight.remove(req.rid)
        self._log(EventType.WRITE_COMPLETED, rid=req.rid, pid=req.pid, gen=req.gen)
        return req

    def device_complete_next(self) -> Optional[WriteRequest]:
        req = self.device.complete_next()
        if req is None:
            return None
        tracked = self.requests.get(req.rid)
        if tracked is not None and req.rid in self.pages[req.pid].inflight:
            self.pages[req.pid].inflight.remove(req.rid)
        return self._on_device_completion(req)

    def cancel_pending(self, req: WriteRequest) -> None:
        if req.state != ReqState.PENDING:
            raise RuntimeError(f"请求 {req.rid} 已交设备（{req.state.value}），不可撤回")
        req.state = ReqState.CANCELED
        self._log(EventType.WRITE_CANCELED, rid=req.rid, pid=req.pid)

    # ---- 租约与映射 ------------------------------------------------------
    def _active_lease_of(self, tenant: str, pid: int) -> Optional[Lease]:
        # 以池侧租约账为准（深拷贝重放后句柄上的索引可能缺失，也能正确鉴权）
        for lease in self.leases.values():
            if (lease.tenant == tenant and pid in lease.pids
                    and lease.state in ("active", "sealed")):
                return lease
        return None

    def _map_pages(self, tenant: str, pids: list[int]) -> Lease:
        self._lid += 1
        lease = Lease(self._lid, tenant, list(pids))
        self.leases[lease.lid] = lease
        for pid in pids:
            page = self.pages[pid]
            page.owner = tenant
            page.state = PageState.LEASED
            self._log(EventType.MAPPED, pid=pid, gen=page.gen, tenant=tenant, lid=lease.lid)
        self.tenants[tenant].lease_ids.append(lease.lid)
        return lease

    def read_page(self, tenant: str, pid: int) -> bytes:
        page = self.pages[pid]
        lease = self._active_lease_of(tenant, pid)
        if lease is None or page.owner != tenant:
            raise PermissionError(f"租户 {tenant} 无权读页 {pid}")
        if page.state not in (PageState.LEASED, PageState.SEALED):
            raise PermissionError(f"页 {pid} 状态 {page.state.value}，不可读")
        return self.device.read(pid)

    # ---- 交还 / 封存 / 续跑 / 放弃 ---------------------------------------
    def handback(self, tenant: str, lid: int) -> Lease:
        lease = self.leases[lid]
        assert lease.tenant == tenant and lease.state == "active"
        lease.state = "returned"
        for pid in lease.pids:
            page = self.pages[pid]
            # 交还即拆旧映射：内容不明，必须净化后才能再出租
            page.state = PageState.UNKNOWN
            page.owner = None
            self._log(EventType.HANDBACK, pid=pid, tenant=tenant, gen=page.gen)
        return lease

    def seal(self, lid: int, until_shift: int) -> None:
        """暂按原主专用封存：保留内容、归属与映射，不进入可承诺库存。"""
        lease = self.leases[lid]
        lease.state = "sealed"
        for pid in lease.pids:
            page = self.pages[pid]
            page.state = PageState.SEALED
            page.owner = lease.tenant
            page.sealed_until = until_shift
            self._log(EventType.SEALED, pid=pid, tenant=lease.tenant,
                      gen=page.gen, until_shift=until_shift)

    def resume(self, tenant: str, lid: int) -> Lease:
        lease = self.leases[lid]
        if lease.tenant != tenant or lease.state != "sealed":
            raise PermissionError("只有封存中的原租户可领回")
        lease.state = "active"
        for pid in lease.pids:
            page = self.pages[pid]
            page.state = PageState.LEASED
            page.sealed_until = None
            self._log(EventType.RESUMED, pid=pid, tenant=tenant, gen=page.gen)
        for pid in lease.pids:                       # 全部翻回 LEASED 后再读
            self.tenants[tenant]._shadow[pid] = bytearray(self.read_page(tenant, pid))
        return lease

    def renounce(self, tenant: str, lid: int) -> list[int]:
        """原主放弃续跑：封存租约转为普通交还，返回页号交维护端净化。"""
        lease = self.leases[lid]
        if lease.tenant != tenant or lease.state != "sealed":
            raise PermissionError("只能放弃自己封存中的租约")
        lease.state = "returned"
        for pid in lease.pids:
            page = self.pages[pid]
            page.state = PageState.UNKNOWN
            page.owner = None
            self._log(EventType.RENOUNCED, pid=pid, tenant=tenant, gen=page.gen)
            self._log(EventType.HANDBACK, pid=pid, tenant=tenant, gen=page.gen)
        return list(lease.pids)

    # ---- 净化（清零）与隔离恢复 ------------------------------------------
    def assert_old_generation_drained(self, pid: int) -> None:
        page = self.pages[pid]
        if page.inflight or self.device.has_inflight(pid):
            raise RuntimeError(f"页 {pid} 仍有在途写，不能清零")
        self._log(EventType.OLD_GEN_DRAINED, pid=pid, gen=page.gen)

    def sanitize(self, pid: int) -> None:
        page = self.pages[pid]
        if page.state in (PageState.LEASED, PageState.SEALED):
            raise RuntimeError(f"页 {pid} 仍在租或封存中，不能净化")
        self.assert_old_generation_drained(pid)
        # 清零写同样“先受理、交设备、收回报”，并把代次 +1，使旧代写全部失效。
        # 完成回调用的是新代次，而页代次在净化收尾时才翻，因此这里不走通用
        # 完成回调（那会把零写误判成旧代次），直接落零字节并原子翻代次。
        new_gen = page.gen + 1
        self._rid += 1
        zero_req = WriteRequest(self._rid, pid, new_gen, "_pool",
                                b"\x00" * PAGE_SIZE, tag="zero")
        self.requests[zero_req.rid] = zero_req
        self._submit(zero_req)
        self._log(EventType.ZERO_SUBMITTED, pid=pid, new_gen=new_gen)
        done = self.device.complete_oldest_for_page(pid)
        assert done is zero_req
        page.inflight.remove(zero_req.rid)
        self.device.apply(zero_req)
        zero_req.state = ReqState.DONE
        page.gen = new_gen
        page.owner = None
        page.sealed_until = None
        page.state = PageState.CLEAN
        self._log(EventType.SANITIZED, pid=pid, gen=new_gen)

    def recover_quarantine(self, pid: int) -> None:
        page = self.pages[pid]
        if not page.quarantined:
            raise RuntimeError(f"页 {pid} 未被隔离")
        self.sanitize(pid)                       # sanitize 会把状态翻回 CLEAN
        self._log(EventType.RECOVERED, pid=pid, gen=page.gen)
        self._promote_waiting()

    # ---- 分配、保障、等候、班次 ------------------------------------------
    def _clean_runs(self) -> list[list[int]]:
        clean = sorted(pid for pid, p in self.pages.items() if p.state == PageState.CLEAN)
        runs, cur = [], []
        for pid in clean:
            if cur and pid == cur[-1] + 1:
                cur.append(pid)
            else:
                if cur:
                    runs.append(cur)
                cur = [pid]
        if cur:
            runs.append(cur)
        return runs

    def _alloc(self, count: int, contiguous: bool) -> Optional[list[int]]:
        if contiguous:
            for run in self._clean_runs():
                if len(run) >= count:
                    return run[:count]
            return None
        clean = sorted(pid for pid, p in self.pages.items() if p.state == PageState.CLEAN)
        return clean[:count] if len(clean) >= count else None

    def borrow(self, tenant: str, count: int, contiguous: bool = False,
               shift: Optional[int] = None) -> Reply:
        shift = self.current_shift if shift is None else shift
        if shift < self.current_shift:
            return Reply("rejected", tenant, message="首班已过，该班次订单只能改期", shift=shift)
        pids = self._alloc(count, contiguous)
        if pids is not None:
            lease = self._map_pages(tenant, pids)
            self._gid += 1
            g = Guarantee(self._gid, tenant, list(pids), shift)
            self.guarantees.append(g)
            self._log(EventType.LEASED, tenant=tenant, pids=pids, lid=lease.lid, shift=shift)
            self._log(EventType.GUARANTEE_SIGNED, gid=g.gid, tenant=tenant,
                      pids=pids, shift=shift)
            return Reply("granted", tenant, pids=pids, lease_id=lease.lid,
                         guarantee=g.gid, shift=shift,
                         message=f"已签保障 #{g.gid}，班次 {shift} 交货")
        # 无现货：未来班次进等候（不签保障），当班订单直接判不能成交
        if shift > self.current_shift:
            self._res_id += 1
            r = Reservation(self._res_id, tenant, count, contiguous, shift,
                            note="无连续干净库存，进入等候，不签保障，可改期")
            self.reservations[r.rid] = r
            self._log(EventType.RESERVATION_WAITING, rid=r.rid, tenant=tenant,
                      count=count, contiguous=contiguous, deadline=shift)
            return Reply("waiting", tenant, reservation=r.rid, shift=shift,
                         message="库存不足：仅进入等候，无保障；首班交不了只能改期")
        return Reply("rejected", tenant, shift=shift,
                     message=("无相邻干净页，连续订单不能成交" if contiguous
                              else "干净库存不足，订单不能成交"))

    def _promote_waiting(self) -> None:
        for r in self.reservations.values():
            if r.status != "waiting":
                continue
            pids = self._alloc(r.count, r.contiguous)
            if pids is None:
                continue
            lease = self._map_pages(r.tenant, pids)
            if r.tenant in self.tenants:
                self.tenants[r.tenant].lease_ids.append(lease.lid)
            self._gid += 1
            g = Guarantee(self._gid, r.tenant, list(pids), r.deadline_shift, reservation=r.rid)
            self.guarantees.append(g)
            r.status = "promoted"
            self._log(EventType.RESERVATION_PROMOTED, rid=r.rid, tenant=r.tenant, pids=pids)
            self._log(EventType.GUARANTEE_SIGNED, gid=g.gid, tenant=r.tenant,
                      pids=pids, shift=r.deadline_shift)

    def advance_shift(self) -> None:
        self.current_shift += 1
        for r in self.reservations.values():
            # 到了约定班次仍未转正 = 首班交不了，等候单失效，只能改期
            if r.status == "waiting" and r.deadline_shift <= self.current_shift:
                r.status = "expired"
                self._log(EventType.RESERVATION_EXPIRED, rid=r.rid, tenant=r.tenant,
                          deadline=r.deadline_shift)

    def guarantees_held(self) -> list[Guarantee]:
        return list(self.guarantees)


# --------------------------------------------------------------------------
# 租户句柄：分段写、读、交还、续跑、放弃、借页
# --------------------------------------------------------------------------
class TenantHandle:
    def __init__(self, name: str, pool: BufferPool):
        self.name = name
        self.pool = pool
        self.lease_ids: list[int] = []
        self._shadow: dict[int, bytearray] = {}

    def borrow(self, count: int, contiguous: bool = False,
               shift: Optional[int] = None) -> Reply:
        reply = self.pool.borrow(self.name, count, contiguous, shift)
        if reply.kind == "granted":
            self.lease_ids.append(reply.lease_id)
            for pid in reply.pids:
                self._shadow[pid] = bytearray(self.pool.read_page(self.name, pid))
        return reply

    def write_page(self, pid: int, segments: dict[int, bytes], tag: str = "") -> WriteRequest:
        """在自己已租的页上写若干段（段号 0..3），返回已受理、尚未交设备的写请求。"""
        for seg, data in segments.items():
            if not (0 <= seg < SEGMENTS) or len(data) != SEGMENT_SIZE:
                raise ValueError("段号或段长度非法")
        buf = bytearray(self._shadow.setdefault(pid, bytearray(PAGE_SIZE)))
        for seg, data in segments.items():
            buf[seg * SEGMENT_SIZE:(seg + 1) * SEGMENT_SIZE] = data
        return self.pool._accept_write(self.name, pid, bytes(buf), tag)

    def flush(self, reqs: list[WriteRequest]) -> None:
        for req in reqs:
            assert req.tenant == self.name
            self.pool._submit(req)

    def cancel(self, req: WriteRequest) -> None:
        self.pool.cancel_pending(req)

    def read_page(self, pid: int) -> bytes:
        return self.pool.read_page(self.name, pid)

    def return_lease(self, lid: int) -> Lease:
        return self.pool.handback(self.name, lid)

    def resume(self, lid: int) -> Lease:
        # 池侧 resume 已在页状态翻回 LEASED 后刷新本句柄的影子缓冲
        return self.pool.resume(self.name, lid)

    def renounce(self, lid: int) -> list[int]:
        return self.pool.renounce(self.name, lid)


# --------------------------------------------------------------------------
# 维护端：排空、撤回、净化、隔离恢复、重启
# --------------------------------------------------------------------------
class Maintainer:
    def __init__(self, pool: BufferPool):
        self.pool = pool

    def cancel_pending_of(self, pids: list[int]) -> int:
        n = 0
        for req in list(self.pool.requests.values()):
            if req.pid in pids and req.state == ReqState.PENDING:
                self.pool.cancel_pending(req)
                n += 1
        return n

    def drain_page(self, pid: int) -> int:
        """等待该页所有在途写回报；返回完成条数。"""
        n = 0
        while True:
            req = self.pool.device.complete_oldest_for_page(pid)
            if req is None:
                break
            if req.rid in self.pool.pages[pid].inflight:
                self.pool.pages[pid].inflight.remove(req.rid)
            self.pool._on_device_completion(req)
            n += 1
        return n

    def drain_lease(self, pids: list[int]) -> None:
        for pid in pids:
            self.drain_page(pid)

    def recycle(self, pids: list[int], cancel_pending: bool = True) -> None:
        """完整回收：撤回可撤写 -> 等旧代写完 -> 清零净化。"""
        if cancel_pending:
            self.cancel_pending_of(pids)
        for pid in pids:
            self.drain_page(pid)
            self.pool.assert_old_generation_drained(pid)
            self.pool.sanitize(pid)
        self.pool._promote_waiting()

    def seal_for_continuation(self, lid: int, until_shift: int) -> None:
        lease = self.pool.leases[lid]
        self.drain_lease(lease.pids)   # 封存前先让中间结果落盘
        self.pool.seal(lid, until_shift)

    def recover(self, pid: int) -> None:
        self.pool.recover_quarantine(pid)

    def restart(self) -> dict:
        """模拟维护端重启：按账本重建内存索引并核对在途状态。"""
        rebuilt_inflight = 0
        for page in self.pool.pages.values():
            page.inflight = [rid for rid in page.inflight
                             if self.pool.requests.get(rid)
                             and self.pool.requests[rid].state == ReqState.INFLIGHT]
            rebuilt_inflight += len(page.inflight)
        pending = sum(1 for r in self.pool.requests.values()
                      if r.state == ReqState.PENDING)
        self.pool._log(EventType.RESTART, inflight=rebuilt_inflight, pending=pending)
        return {"inflight": rebuilt_inflight, "pending": pending}


# --------------------------------------------------------------------------
# 场景搭建：六张虚构页
#   P0、P5 ........ 两张不相邻的干净散页（可办零散小单）
#   P2、P3 ........ 甲租下的相邻两页，留有本班唯一中间结果
#   P1、P4 ........ 内容不明的脏页
# --------------------------------------------------------------------------
def build_scenario() -> tuple[BufferPool, Maintainer, TenantHandle, Lease]:
    """六张虚构页的目标布局（测试夹具）：

       P0、P5 ... 两张不相邻的干净散页（可办零散小单，不能凑连续单）
       P2、P3 ... 甲租下的相邻两页，留有本班唯一的中间结果
       P1、P4 ... 内容不明的脏页
    """
    dev = SimDevice(6)
    pool = BufferPool(dev)
    for pid in range(6):
        pool.mark_clean_for_setup(pid)
    pool.events.clear()          # 建池痕迹不计入业务事件流
    m = Maintainer(pool)

    a = pool.new_tenant("甲")
    lease_a = pool._map_pages("甲", [2, 3])
    a.lease_ids.append(lease_a.lid)
    for pid in (2, 3):
        a._shadow[pid] = bytearray(PAGE_SIZE)
    # 甲在最后两页写下本班唯一中间结果，并等其落盘
    reqs = [
        a.write_page(2, {0: b"JIA-RES1", 3: b"tail-002"}, tag="jia-res-1"),
        a.write_page(3, {1: b"JIA-RES2", 2: b"mid--003"}, tag="jia-res-2"),
    ]
    a.flush(reqs)
    m.drain_lease([2, 3])
    # P1、P4 置为内容不明的脏页；P0、P5 保持干净且不相邻
    pool.pages[1].state = PageState.UNKNOWN
    pool.pages[4].state = PageState.UNKNOWN
    return pool, m, a, lease_a


def book_b(pool: BufferPool) -> tuple[TenantHandle, Reply]:
    b = pool.new_tenant("乙")
    reply_b = b.borrow(2, contiguous=True, shift=pool.current_shift + 1)
    return b, reply_b


# --------------------------------------------------------------------------
# 中文演示：同一批状态重放“甲回来 / 甲放弃”
# --------------------------------------------------------------------------
def demo() -> None:
    print("=" * 72)
    print("班次 1：甲交还两页（封存续跑），乙预订班次 2 的两页连续新缓冲")
    print("=" * 72)
    base, m, a, lease_a = build_scenario()
    m.drain_lease(lease_a.pids)
    a.return_lease(lease_a.lid)
    m.seal_for_continuation(lease_a.lid, until_shift=2)
    b, reply_b = book_b(base)
    c = base.new_tenant("丙")
    r_c1 = c.borrow(1)
    r_c2 = c.borrow(1)
    print(f"乙的回复：{reply_b.message}（reservation={reply_b.reservation}）")
    print(f"丙的零散小单：{r_c1.message} -> 页 {r_c1.pids}；{r_c2.message} -> 页 {r_c2.pids}")
    print(f"已签保障：{[(g.gid, g.tenant, g.pids) for g in base.guarantees]}")

    # ---- 走向 R：甲开机后决定续跑，领回 --------------------------------
    r_pool = base.fork()
    r_a = r_pool.tenants["甲"]
    r_m = Maintainer(r_pool)
    r_m.restart()
    r_a.resume(lease_a.lid)
    data2, data3 = r_a.read_page(2), r_a.read_page(3)
    r_pool.advance_shift()   # 进入班次 2，乙的首班
    print("\n[走向 R] 甲回来续跑")
    print(f"  甲读回 P2={data2!r} P3={data3!r}，中间结果完好")
    res_b = r_pool.reservations[reply_b.reservation]
    print(f"  乙的等候单状态：{res_b.status}（首班已过，只能改期；从未签保障，无违约）")

    # ---- 走向 Q：甲放弃续跑，维护端净化，等候单转正 ----------------------
    q_pool = base.fork()
    q_a = q_pool.tenants["甲"]
    q_m = Maintainer(q_pool)
    q_m.restart()
    q_a.renounce(lease_a.lid)
    q_m.recycle([2, 3])
    q_pool.advance_shift()
    q_b = q_pool.tenants["乙"]
    print("\n[走向 Q] 甲放弃续跑")
    kinds = {et.value for et, _ in q_pool.events}
    print(f"  三类事件分别留痕：handback={'handback' in kinds}，"
          f"old_gen_drained={'old_gen_drained' in kinds}，sanitized={'sanitized' in kinds}")
    res_b = q_pool.reservations[reply_b.reservation]
    g = next(g for g in q_pool.guarantees if g.tenant == "乙")
    print(f"  乙的等候单转正：{res_b.status}，签出保障 #{g.gid}，页 {g.pids}，班次 {g.shift} 交货")
    zeros = q_b.read_page(2)
    print(f"  乙实际读到 P2：{zeros!r}（全零，看不到甲的字节）")


if __name__ == "__main__":
    demo()
