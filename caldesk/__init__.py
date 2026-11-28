"""caldesk —— 两家咨询室合用接待室的预约与外借日历工具。

模块分工：

* ``time_utils``  : 本地时间、UTC、VTIMEZONE（含夏令时）
* ``ical``        : iCalendar 的读取与写出（RRULE/RECURRENCE-ID/取消项）
* ``model``       : 馆内档案：房间、周期事件、改约例外、经办回执
* ``booking``     : 预约台：占用冲突、无障碍/口译约束、原子预订
* ``external``    : 外借匿名日历：只含连续占用区段，可在另一账户打开
* ``policy``      : 提前安排周期与临时改约的代价/报价策略
* ``notices``     : 订阅更新通知
"""

from .time_utils import NY, utc, local, as_utc, now_utc
from .model import Room, Series, Override, Receipt, InternalArchive
from .booking import BookingEngine, BookingError, Quote
from .external import ExternalLender
from .ical import ICalendarReader, ICalendarWriter

__all__ = [
    "NY",
    "utc",
    "local",
    "as_utc",
    "now_utc",
    "Room",
    "Series",
    "Override",
    "Receipt",
    "InternalArchive",
    "BookingEngine",
    "BookingError",
    "Quote",
    "ExternalLender",
    "ICalendarReader",
    "ICalendarWriter",
]
