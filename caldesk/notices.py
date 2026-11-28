"""更新通知板。

外借日历的订阅者（另一账户）只收到 *占用层面* 的更新通知；
馆内私人标题/客人互换不产生任何通知。通知内容刻意只含区段稳定标识，
不含馆内 UID、当事人或改约原因。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Notice:
    calendar: str
    kind: str            # "PUBLISH"（整份刷新）/ "SEGMENT-ADDED" 等
    segment_uid: str
    published_at: datetime
    detail: str = ""


class NoticeBoard:
    def __init__(self) -> None:
        self._notices: list[Notice] = []

    def publish(self, calendar: str, segment_uid: str, kind: str,
                published_at: datetime, detail: str = "") -> Notice:
        n = Notice(calendar=calendar, kind=kind, segment_uid=segment_uid,
                   published_at=published_at, detail=detail)
        self._notices.append(n)
        return n

    def for_calendar(self, calendar: str) -> list[Notice]:
        return [n for n in self._notices if n.calendar == calendar]

    def all(self) -> list[Notice]:
        return list(self._notices)

    def reset(self) -> None:
        self._notices.clear()
