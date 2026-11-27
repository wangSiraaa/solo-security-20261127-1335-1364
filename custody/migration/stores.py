"""只读模拟接收库。

三个库都只有 append（受托站事件重放时调用），对外暴露的是不可变日志视图；
没有任何修改/删除接口，符合「只读接收库」。日志为哈希链，可逐库审计。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

from ..hashing import digest, verify_seal
from ..model import PortablePermit, Refusal, StoreLogEntry

_GENESIS = "0" * 64
_FORMULA = re.compile(r"^transfer_on\+(\d+)D$")


class ReceivingStore:
    def __init__(self, store_id: str, guaranteed_horizon: Optional[date]):
        self.store_id = store_id
        # 该库能保证的最长存期（物理/合同上限）；None 表示无上限
        self.guaranteed_horizon = guaranteed_horizon
        self._log: list[StoreLogEntry] = []
        # 库内对转交起算单的本地派生状态：order_id -> 派生到期日
        self._transfer_horizon: dict[str, date] = {}

    # ---------------------------------------------------------- 哈希链追加
    def _append(self, on: date, action: str, order_id: str, detail: dict) -> StoreLogEntry:
        prev = self._log[-1].entry_hash if self._log else _GENESIS
        seq = len(self._log) + 1
        entry_hash = digest({
            "seq": seq, "store_id": self.store_id, "on": on.isoformat(),
            "action": action, "order_id": order_id, "detail": detail, "prev_hash": prev,
        })
        entry = StoreLogEntry(seq, self.store_id, on, action, order_id, detail, prev, entry_hash)
        self._log.append(entry)
        return entry

    @property
    def log(self) -> tuple[StoreLogEntry, ...]:
        return tuple(self._log)

    def verify_chain(self) -> bool:
        prev = _GENESIS
        for e in self._log:
            if e.prev_hash != prev:
                return False
            if e.entry_hash != digest({
                "seq": e.seq, "store_id": e.store_id, "on": e.on.isoformat(),
                "action": e.action, "order_id": e.order_id, "detail": e.detail,
                "prev_hash": e.prev_hash,
            }):
                return False
            prev = e.entry_hash
        return True

    # ---------------------------------------------------------- 许可校验
    @staticmethod
    def _check_permit(station_key: str, permit: PortablePermit, order_id: str,
                      order_seal: str) -> Optional[str]:
        if permit.order_id != order_id or permit.order_seal != order_seal:
            return "许可与单据号/签章不绑定，禁止跨单复用（同文同章也不行）"
        if not verify_seal(station_key, permit.sealing_payload(), permit.station_seal):
            return "许可封印校验失败"
        return None

    def _horizon_ok(self, end: date) -> bool:
        return self.guaranteed_horizon is None or end <= self.guaranteed_horizon

    # ---------------------------------------------------------- 入库
    def receive(self, on: date, order_id: str, order_seal: str,
                permit: PortablePermit, station_key: str) -> StoreLogEntry | Refusal:
        err = self._check_permit(station_key, permit, order_id, order_seal)
        if err:
            return self._refuse(on, order_id, err)
        retain = next((o for o in permit.operations if o.op == "retain-until"), None)
        if retain is None:
            return self._refuse(on, order_id, "许可未明示可留存义务（自动化已被舍弃），仅可受理已追认动作")
        if _FORMULA.match(retain.arg or ""):
            # 条件式留存：义务成立，到期日待转交事实派生，不触碰库保证存期上限
            return self._append(on, "receive", order_id,
                                {"permit": permit.permit_id,
                                 "retain_until": retain.arg, "conditional": True})
        end = date.fromisoformat(retain.arg)
        if not self._horizon_ok(end):
            return self._refuse(on, order_id,
                                f"库保证存期止于 {self.guaranteed_horizon}，短于义务到期 {end}；"
                                "不得套用新日期或缩短原义务，出换库/改约单",
                                "store-swap")
        return self._append(on, "receive", order_id,
                            {"permit": permit.permit_id, "retain_until": retain.arg})

    def _refuse(self, on, order_id, reason, proposal_kind="amendment") -> Refusal:
        self._append(on, "refuse", order_id,
                     {"reason": reason, "proposal_kind": proposal_kind})
        return Refusal(self.store_id, order_id, reason, proposal_kind)

    # ---------------------------------------------------------- 转交（演练）
    def transfer_in(self, on: date, order_id: str, order_seal: str,
                    permit: PortablePermit, station_key: str) -> StoreLogEntry | Refusal:
        err = self._check_permit(station_key, permit, order_id, order_seal)
        if err:
            return self._refuse(on, order_id, err)
        op = next((o for o in permit.operations if o.op == "accept-transfer-in"), None)
        if op is None:
            return self._refuse(on, order_id,
                                "许可未明示接受转交的可表达条件：该自动化签出时已舍弃，冻结并返回复核")
        m = _FORMULA.match(op.arg or "")
        if not m:
            return self._refuse(on, order_id, f"转交公式无法识别：{op.arg}")
        end = on + timedelta(days=int(m.group(1)))
        if not self._horizon_ok(end):
            return self._refuse(on, order_id,
                                f"转交派生到期 {end} 超出库保证存期 {self.guaranteed_horizon}")
        self._transfer_horizon[order_id] = end
        return self._append(on, "transfer-in", order_id,
                            {"permit": permit.permit_id, "formula": op.arg,
                             "derived_until": end.isoformat()})

    # ---------------------------------------------------------- 到期处置
    def dispose(self, on: date, order_id: str, order_seal: str,
                permit: PortablePermit, station_key: str) -> StoreLogEntry | Refusal:
        err = self._check_permit(station_key, permit, order_id, order_seal)
        if err:
            return self._refuse(on, order_id, err)
        op = next((o for o in permit.operations if o.op == "dispose-after"), None)
        if op is None:
            return self._refuse(on, order_id,
                                "许可无自动处置条款：库房离线时不得自行批准新动作，返回复核/追认")
        earliest = date.fromisoformat(op.arg)
        if on < earliest:
            return self._refuse(on, order_id, f"未到最早处置日 {earliest}")
        return self._append(on, "dispose", order_id,
                            {"permit": permit.permit_id, "disposed_on": on.isoformat()})

    def actions_on(self, order_id: str) -> tuple[StoreLogEntry, ...]:
        return tuple(e for e in self._log if e.order_id == order_id)
