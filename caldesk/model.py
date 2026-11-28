"""馆内档案模型。

每个馆（firm）各自保存当事人地址、主题、与谁约见等私人信息；
预约台只能通过 :class:`~caldesk.booking.BookingEngine` 取得房间占用，
以及对“自己名下”的预约行使改约权。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .time_utils import expand_weekly_rrule


@dataclass(frozen=True)
class Room:
    name: str
    accessible: bool = False

    def __str__(self) -> str:  # pragma: no cover - 调试便利
        return self.name


@dataclass
class Series:
    """一条周期预约（RRULE 主事件）。"""

    uid: str
    room: Room
    owner: str                 # 受托预约台名下标识（改约权据此判定）
    summary: str               # 私人标题
    guest: str                 # 当事人（私人信息）
    start: datetime            # 本地挂钟时间
    end: datetime
    rrule: dict[str, str]
    sequence: int = 0
    overrides: list["Override"] = field(default_factory=list)
    exdates: set[datetime] = field(default_factory=set)

    def instance_key(self, occurrence: datetime) -> datetime:
        """实例的稳定标识 = 其原始排期起点（本地挂钟时间）。"""
        return occurrence

    def expand(self, horizon: datetime | None = None) -> list[datetime]:
        return expand_weekly_rrule(self.start, self.rrule, horizon=horizon)

    def override_for(self, key: datetime) -> "Override | None":
        for ov in self.overrides:
            if ov.recurrence_id == key:
                return ov
        return None

    def effective_occurrences(self, horizon: datetime | None = None):
        """展开为真正占用房间的 (起点, 终点, 来源覆盖项或 None)。

        - 被取消的原实例 / EXDATE 不占房；
        - 改时实例在新时间占房，并保留与原实例的关联（RECURRENCE-ID）。
        """
        duration = self.end - self.start
        out: list[tuple[datetime, datetime, "Override | None"]] = []
        for occ in self.expand(horizon):
            if occ in self.exdates:
                continue
            ov = self.override_for(occ)
            if ov is not None and ov.cancelled:
                continue
            if ov is not None and ov.new_start is not None:
                out.append((ov.new_start, ov.new_start + duration, ov))
            else:
                out.append((occ, occ + duration, ov))
        return out


@dataclass
class Override:
    """按“原实例”做的例外（改时或取消）。"""

    recurrence_id: datetime    # 原实例的原始起点
    new_start: datetime | None = None
    cancelled: bool = False
    summary: str | None = None
    guest: str | None = None
    sequence: int = 0

    @property
    def is_reschedule(self) -> bool:
        return self.new_start is not None and not self.cancelled


@dataclass(frozen=True)
class Receipt:
    """具名改约的独立经办回执（留在馆内，不进外借日历）。"""

    receipt_id: str
    uid: str
    recurrence_id: datetime
    action: str                # "RESCHEDULE" | "CANCEL"
    actor: str
    guest: str
    old_start: datetime
    new_start: datetime | None
    note: str = ""


@dataclass
class InternalArchive:
    """一个馆的内档：房间是合用的，记录是各自的。"""

    firm: str
    series: list[Series] = field(default_factory=list)
    receipts: list[Receipt] = field(default_factory=list)

    def add_series(self, s: Series) -> None:
        self.series.append(s)

    def get(self, uid: str) -> Series:
        for s in self.series:
            if s.uid == uid:
                return s
        raise KeyError(f"内档中没有 UID={uid} 的周期事件")

    def all_occupancy(self, horizon: datetime | None = None):
        """本档全部有效占用 (room, start, end, series, override)。"""
        out = []
        for s in self.series:
            for start, end, ov in s.effective_occurrences(horizon):
                out.append((s.room, start, end, s, ov))
        return out
