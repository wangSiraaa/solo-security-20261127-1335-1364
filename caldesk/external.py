"""外借匿名日历（方案一）。

按预约期把 *所有* 占用重建为只含匿名占用的连续区段：

* 背靠背（前一段结束 == 下一段开始）的占用合并为同一段；
* 每段是一个普通 VEVENT（无 RRULE、无 RECURRENCE-ID），所以外借件里
  根本不存在“例外条数”；具名改约只走馆内经办回执；
* 区段 UID 由房间名 + 起止时间的哈希稳定推导，不含任何馆内 UID 或人名；
* 标题一律为“占用”，时间全 UTC，可在另一账户直接导入。

不变量：只要房间占用集合相同，无论内档是换了私人标题还是在同一段内
互换客人，导出的外借件逐字节一致、通知序列也一致。
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from .ical import ICalendarWriter
from .model import InternalArchive
from .notices import NoticeBoard
from .time_utils import as_utc


class ExternalLender:
    SUMMARY = "占用"

    def __init__(self, calendar_name: str, archives: list[InternalArchive],
                 reservations=None, board: NoticeBoard | None = None,
                 writer: ICalendarWriter | None = None) -> None:
        self.calendar_name = calendar_name
        self.archives = archives
        # 显式判断而非 `or []`：空列表也是合法的共享引用，必须保留，
        # 这样引擎后续原地 extend 时外借器才能看到新预订
        self.reservations = reservations if reservations is not None else []
        self.board = board or NoticeBoard()
        self.writer = writer
        self._last_segments: list[dict] = []

    # ------------------------------------------------------------------
    def _raw_intervals(self, horizon):
        intervals: dict[str, list[tuple[datetime, datetime]]] = {}
        for archive in self.archives:
            for room, start, end, _s, _ov in archive.all_occupancy(horizon):
                intervals.setdefault(room.name, []).append((start, end))
        for r in self.reservations:
            intervals.setdefault(r.room.name, []).append((r.start, r.end))
        return intervals

    @staticmethod
    def _merge(intervals: list[tuple[datetime, datetime]]
               ) -> list[tuple[datetime, datetime]]:
        """合并相交或背靠背的区间为连续区段。"""
        if not intervals:
            return []
        ordered = sorted(intervals)
        merged = [list(ordered[0])]
        for start, end in ordered[1:]:
            if start <= merged[-1][1]:  # 相交或背靠背（== 也合并）
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return [(a, b) for a, b in merged]

    def build_segments(self, horizon=None) -> list[dict]:
        segments: list[dict] = []
        for room_name in sorted(self._raw_intervals(horizon)):
            for start, end in self._merge(self._raw_intervals(horizon)[room_name]):
                segments.append({
                    "room": room_name,
                    "start": start,
                    "end": end,
                    "uid": self._stable_uid(room_name, start, end),
                })
        segments.sort(key=lambda g: (g["room"], g["start"]))
        return segments

    @staticmethod
    def _stable_uid(room_name: str, start: datetime, end: datetime) -> str:
        raw = f"{room_name}|{as_utc(start):%Y%m%dT%H%M%SZ}|{as_utc(end):%Y%m%dT%H%M%SZ}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
        return f"loan-{digest}@caldesk.local"

    # ------------------------------------------------------------------
    def export(self, horizon=None) -> str:
        writer = self.writer or ICalendarWriter()
        self._last_segments = self.build_segments(horizon)
        return writer.write_external(self._last_segments, self.calendar_name)

    def publish(self, published_at: datetime, horizon=None) -> str:
        """导出并按区段差异发更新通知；纯私人改动不会产生任何通知。"""
        new_segments = self.build_segments(horizon)
        old_uids = {g["uid"] for g in self._last_segments}
        new_uids = {g["uid"] for g in new_segments}
        for uid in sorted(new_uids - old_uids):
            self.board.publish(self.calendar_name, uid, "SEGMENT-ADDED",
                               published_at, "新增匿名占用区段")
        for uid in sorted(old_uids - new_uids):
            self.board.publish(self.calendar_name, uid, "SEGMENT-REMOVED",
                               published_at, "匿名占用区段解除")
        if old_uids and old_uids != new_uids:
            self.board.publish(self.calendar_name, "-", "PUBLISH",
                               published_at, "外借日历整份刷新")
        self._last_segments = new_segments
        writer = self.writer or ICalendarWriter()
        return writer.write_external(new_segments, self.calendar_name)

    def assert_leaks_no_private_data(self, text: str) -> None:
        """自检：外借件不得出现任何馆内私人字段。"""
        for archive in self.archives:
            for s in archive.series:
                for secret in (s.summary, s.guest):
                    if secret and secret in text:
                        raise AssertionError(f"外借日历泄露私人信息: {secret!r}")
        for forbidden in ("RECURRENCE-ID", "RRULE", "STATUS:CANCELLED",
                          "ATTENDEE"):
            if forbidden in text:
                raise AssertionError(f"外借日历含禁止成分: {forbidden}")
