"""预约台引擎：房间占用、改约权、冲突与原子预订。

预约台 *只能* 取得：

1. 房间维度的忙闲（看不到当事人地址、主题等私人字段）；
2. 对 owner 为自己的预约行使改约/取消权。

跨馆的占用通过两个馆的 :class:`~caldesk.model.InternalArchive` 汇总，
但引擎从不把对方馆的标题/客人暴露给调用方。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .model import InternalArchive, Override, Receipt, Room, Series
from .policy import Quote, reschedule_fee


class BookingError(Exception):
    """占用冲突、权限不足或约束不满足。"""


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    room: Room
    start: datetime
    end: datetime
    guest: str
    interpreter: str | None = None
    accessible_required: bool = False


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


class BookingEngine:
    def __init__(self, desk_id: str, rooms: list[Room],
                 archives: list[InternalArchive],
                 horizon: datetime | None = None) -> None:
        self.desk_id = desk_id
        self.rooms = {r.name: r for r in rooms}
        self.archives = archives
        self.horizon = horizon
        self.reservations: list[Reservation] = []
        self.interpreter_busy: dict[str, list[tuple[datetime, datetime]]] = {}
        self.holds: list[Quote] = []
        self._receipt_seq = 0

    # ------------------------------------------------------------------
    # 只读忙闲：预约台可见的全部信息
    # ------------------------------------------------------------------
    def room_busy(self, room_name: str) -> list[tuple[datetime, datetime]]:
        """某房间的全部占用区间（来自两馆周期档案 + 本台预订 + 报价锁定）。"""
        out: list[tuple[datetime, datetime]] = []
        for archive in self.archives:
            for room, start, end, _s, _ov in archive.all_occupancy(self.horizon):
                if room.name == room_name:
                    out.append((start, end))
        for r in self.reservations:
            if r.room.name == room_name:
                out.append((r.start, r.end))
        for q in self.holds:
            if q.room == room_name:
                out.append((q.start, q.end))
        return sorted(out)

    def is_room_free(self, room: Room, start: datetime, end: datetime) -> bool:
        return not any(_overlaps(start, end, bs, be)
                       for bs, be in self.room_busy(room.name))

    def available_rooms(self, start: datetime, end: datetime,
                        accessible: bool = False) -> list[Room]:
        candidates = [r for r in self.rooms.values()
                      if not accessible or r.accessible]
        return [r for r in candidates if self.is_room_free(r, start, end)]

    def is_interpreter_free(self, interpreter: str,
                            start: datetime, end: datetime) -> bool:
        return not any(_overlaps(start, end, bs, be)
                       for bs, be in self.interpreter_busy.get(interpreter, []))

    # ------------------------------------------------------------------
    # 报价锁定：有效期内余量必须兑现
    # ------------------------------------------------------------------
    def place_hold(self, quote: Quote) -> None:
        room = self.rooms[quote.room]
        if not self.is_room_free(room, quote.start, quote.end):
            raise BookingError(f"房间 {quote.room} 在该时段已占用，无法报价锁定")
        self.holds.append(quote)

    def release_expired_holds(self, moment: datetime) -> None:
        self.holds = [q for q in self.holds if q.is_valid(moment)]

    # ------------------------------------------------------------------
    # 改约权：仅 owner 为本台的周期实例
    # ------------------------------------------------------------------
    def _owned_series(self, uid: str) -> tuple[Series, InternalArchive]:
        for archive in self.archives:
            for s in archive.series:
                if s.uid == uid:
                    if s.owner != self.desk_id:
                        raise BookingError(
                            f"预约台 {self.desk_id} 无权改约 {uid}（属 {s.owner}）")
                    return s, archive
        raise BookingError(f"找不到周期事件 {uid}")

    def reschedule_instance(self, uid: str, recurrence_id: datetime,
                            new_start: datetime, new_room_name: str | None,
                            interpreter: str | None = None,
                            moment: datetime | None = None) -> Receipt:
        """把一个原实例改到新时间/新房间；冲突即拒，不产生半成品。"""
        series, archive = self._owned_series(uid)
        if recurrence_id not in series.expand(self.horizon):
            raise BookingError("RECURRENCE-ID 不是该周期的真实实例")
        target_room = self.rooms[new_room_name] if new_room_name else series.room
        new_end = new_start + (series.end - series.start)

        if not self.is_room_free(target_room, new_start, new_end):
            raise BookingError(
                f"目标房间 {target_room.name} 在 {new_start:%Y-%m-%d %H:%M} 已被占用")
        if interpreter and not self.is_interpreter_free(
                interpreter, new_start, new_end):
            raise BookingError(f"口译员 {interpreter} 该时段已有占用")

        fee, reason = (reschedule_fee(moment, recurrence_id)
                       if moment else (0.0, "未计费（未提供当前时刻）"))
        ov = Override(recurrence_id=recurrence_id, new_start=new_start,
                      sequence=series.sequence + 1)
        series.overrides.append(ov)
        series.sequence += 1
        if interpreter:
            self.interpreter_busy.setdefault(interpreter, []).append(
                (new_start, new_end))
        receipt = self._receipt(series, recurrence_id, "RESCHEDULE",
                                new_start, reason, fee)
        archive.receipts.append(receipt)
        return receipt

    def cancel_instance(self, uid: str, recurrence_id: datetime,
                        moment: datetime | None = None) -> Receipt:
        series, archive = self._owned_series(uid)
        if recurrence_id not in series.expand(self.horizon):
            raise BookingError("RECURRENCE-ID 不是该周期的真实实例")
        fee, reason = (reschedule_fee(moment, recurrence_id)
                       if moment else (0.0, "未计费（未提供当前时刻）"))
        series.overrides.append(Override(recurrence_id=recurrence_id,
                                         cancelled=True,
                                         sequence=series.sequence + 1))
        series.exdates.add(recurrence_id)
        series.sequence += 1
        receipt = self._receipt(series, recurrence_id, "CANCEL", None,
                                reason, fee)
        archive.receipts.append(receipt)
        return receipt

    def _receipt(self, series: Series, recurrence_id: datetime, action: str,
                 new_start, reason: str, fee: float) -> Receipt:
        self._receipt_seq += 1
        return Receipt(
            receipt_id=f"R-{self.desk_id}-{self._receipt_seq:04d}",
            uid=series.uid, recurrence_id=recurrence_id, action=action,
            actor=self.desk_id, guest=series.guest,
            old_start=recurrence_id, new_start=new_start,
            note=f"{reason}；费用 {fee:.0f}")

    # ------------------------------------------------------------------
    # 新请求：无障碍房 + 连续两场同一口译员，原子确认
    # ------------------------------------------------------------------
    def book_two_slots(self, guest: str, interpreter: str,
                       slots: list[tuple[datetime, datetime]],
                       accessible: bool = True) -> list[Reservation]:
        """两个时段都真正订妥才返回；任一失败则全部不落地。"""
        if len(slots) != 2:
            raise BookingError("该请求必须恰好包含两个时段")
        staged: list[Reservation] = []
        try:
            for idx, (start, end) in enumerate(slots, start=1):
                if not self.is_interpreter_free(interpreter, start, end):
                    raise BookingError(
                        f"口译员 {interpreter} 在 {start:%H:%M} 已有占用")
                room = next((r for r in self.available_rooms(start, end,
                                                             accessible)),
                            None)
                if room is None:
                    kind = "无障碍" if accessible else ""
                    raise BookingError(f"第 {idx} 场没有可用的{kind}房间")
                res = Reservation(
                    reservation_id=f"BK-{self.desk_id}-{len(self.reservations) + idx:04d}",
                    room=room, start=start, end=end, guest=guest,
                    interpreter=interpreter,
                    accessible_required=accessible)
                # 先登记口译员占用，保证第二场不会重复订到同一口译员
                self.interpreter_busy.setdefault(interpreter, []).append(
                    (start, end))
                staged.append(res)
        except BookingError:
            # 回滚口译员占用
            for res in staged:
                self.interpreter_busy[res.interpreter].remove(
                    (res.start, res.end))
            raise
        # 原地扩展，保证外借日历等持有同一列表引用的组件能看到新预订
        self.reservations.extend(staged)
        return staged
