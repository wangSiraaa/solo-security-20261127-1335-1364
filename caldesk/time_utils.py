"""时间与夏令时辅助。

所有内部记录用 *挂钟时间 + 固定时区* 保存（例如 ``America/New_York``），
写出时再换算。这样跨一次夏令时切换后，周期事件的本地时间保持不变，
而 UTC 偏移自动变化（夏令 -04:00、冬令 -05:00）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """构造一个 UTC 时间。"""
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def local(year: int, month: int, day: int, hour: int, minute: int,
          tz=NY) -> datetime:
    """构造一个带时区的本地挂钟时间。"""
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def as_utc(dt: datetime) -> datetime:
    """带时区时间转 UTC；朴素时间按 UTC 处理。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# RRULE 展开（仅实现本馆需要的 WEEKLY + INTERVAL/BYDAY/COUNT/UNTIL）
# ---------------------------------------------------------------------------

_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_WEEKDAY_NAMES = {v: k for k, v in _WEEKDAYS.items()}


def _weekday_set(byday: str) -> set[int]:
    days = {part.strip().upper() for part in byday.split(",") if part.strip()}
    if not days:
        return set()
    return {_WEEKDAYS[d] for d in days}


def expand_weekly_rrule(start: datetime, rrule: dict,
                        horizon: datetime | None = None,
                        max_count: int = 500) -> list[datetime]:
    """按 WEEKLY 规则展开本地挂钟时间。

    返回的每个时间都与 ``start`` 同时区，夏令时切换时挂钟时间不变、
    偏移量自动调整。
    """
    freq = (rrule.get("FREQ") or "").upper()
    if freq != "WEEKLY":
        raise ValueError(f"仅支持 WEEKLY，收到: {freq!r}")

    interval = int(rrule.get("INTERVAL", "1"))
    byday = _weekday_set(rrule.get("BYDAY", _WEEKDAY_NAMES[start.weekday()]))
    count = int(rrule["COUNT"]) if "COUNT" in rrule else None
    until = rrule.get("UNTIL")
    if horizon is None:
        horizon = start + timedelta(weeks=104)

    results: list[datetime] = []
    week0 = start - timedelta(days=start.weekday())  # 所在周周一（保留时间分量无关）
    week = 0
    while len(results) < max_count:
        week_start = week0 + timedelta(weeks=week * interval)
        for wd in sorted(byday):
            candidate = week_start + timedelta(days=wd)
            candidate = start.replace(year=candidate.year, month=candidate.month,
                                      day=candidate.day)
            if candidate < start:
                continue
            if count is not None and len(results) >= count:
                return results
            if until is not None and candidate > until:
                return results
            if candidate > horizon:
                return results
            results.append(candidate)
        week += 1
        if count is None and week_start > horizon:
            break
    return results


# ---------------------------------------------------------------------------
# VTIMEZONE 生成（规则化夏令时，供 iCalendar 读取器在任意账户展开）
# ---------------------------------------------------------------------------

def _vtimezone_new_york() -> str:
    # 美国 2007 年起：三月第二个周日 02:00 春进，十一月第一个周日 02:00 秋退。
    return (
        "BEGIN:VTIMEZONE\r\n"
        "TZID:America/New_York\r\n"
        "X-LIC-LOCATION:America/New_York\r\n"
        "BEGIN:DAYLIGHT\r\n"
        "TZOFFSETFROM:-0500\r\n"
        "TZOFFSETTO:-0400\r\n"
        "TZNAME:EDT\r\n"
        "DTSTART:19700308T020000\r\n"
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r\n"
        "END:DAYLIGHT\r\n"
        "BEGIN:STANDARD\r\n"
        "TZOFFSETFROM:-0400\r\n"
        "TZOFFSETTO:-0500\r\n"
        "TZNAME:EST\r\n"
        "DTSTART:19701101T020000\r\n"
        "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r\n"
        "END:STANDARD\r\n"
        "END:VTIMEZONE\r\n"
    )


_VTIMEZONES = {
    "America/New_York": _vtimezone_new_york,
}


def vtimezone_block(tzname: str) -> str:
    """返回某时区的 VTIMEZONE 文本；未知时区返回空串（届时改用 UTC）。"""
    factory = _VTIMEZONES.get(tzname)
    return factory() if factory else ""


def fold_line(line: str) -> str:
    """RFC 5545 折行：按 UTF-8 字节不超过 75 octet，续行以单个空格开头。"""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts: list[bytes] = []
    first = True
    while raw:
        limit = 75 if first else 74
        chunk = raw[:limit]
        # 避免把多字节字符截断
        while True:
            try:
                chunk.decode("utf-8")
                break
            except UnicodeDecodeError:
                chunk = chunk[:-1]
        parts.append(chunk)
        raw = raw[len(chunk):]
        first = False
    out = b"\r\n ".join(parts)
    return out.decode("utf-8")
