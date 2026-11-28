"""iCalendar (RFC 5545 子集) 读取与写出。

支持本馆需要的能力：

* ``RRULE:FREQ=WEEKLY;INTERVAL=...;BYDAY=...;COUNT|UNTIL=...`` 周期事件
* ``RECURRENCE-ID`` 的改时例外（同一 UID 的额外 VEVENT）
* ``STATUS:CANCELLED`` 取消项与 ``EXDATE``
* ``VTIMEZONE`` 写出、``TZID``/UTC/浮动时间读入
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .model import Override, Room, Series
from .time_utils import NY, as_utc, fold_line, vtimezone_block


def _escape_text(value: str) -> str:
    return (value.replace("\\", "\\\\").replace(";", "\\;")
                 .replace(",", "\\,").replace("\n", "\\n"))


def _unescape_text(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "N": "\n"}.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

class ICalendarReader:
    def parse(self, text: str) -> list[dict]:
        """把 iCalendar 文本解析为组件树。

        组件形如 ``{"_name", "_props": [(name, params, value)], "_children": []}``。
        """
        lines = self._unfold(text.replace("\r\n", "\n").split("\n"))
        roots: list[dict] = []
        stack: list[dict] = []
        for line in lines:
            if not line.strip():
                continue
            if line.startswith("BEGIN:"):
                comp = {"_name": line[6:], "_props": [], "_children": []}
                if stack:
                    stack[-1]["_children"].append(comp)
                else:
                    roots.append(comp)
                stack.append(comp)
            elif line.startswith("END:"):
                if stack and stack[-1]["_name"] == line[4:]:
                    stack.pop()
            else:
                name, params, value = self._parse_property(line)
                stack[-1]["_props"].append((name, params, value))
        return roots

    @staticmethod
    def _unfold(lines: list[str]) -> list[str]:
        out: list[str] = []
        for line in lines:
            if line.startswith((" ", "\t")) and out:
                out[-1] += line[1:]
            else:
                out.append(line)
        return out

    @staticmethod
    def _parse_property(line: str):
        head, _, value = line.partition(":")
        bits = head.split(";")
        name = bits[0].upper()
        params: dict[str, str] = {}
        for bit in bits[1:]:
            k, _, v = bit.partition("=")
            params[k.upper()] = v
        return name, params, value

    @staticmethod
    def parse_datetime(value: str, params: dict) -> datetime:
        value = value.strip()
        tzid = params.get("TZID")
        if value.endswith(("Z", "z")):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=ZoneInfo("UTC"))
        dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
        if tzid:
            return dt.replace(tzinfo=ZoneInfo(tzid))
        return dt  # 浮动时间，由调用方决定时区

    def load_series(self, text: str, rooms: dict[str, Room],
                    owner: str, default_tz=NY) -> list[Series]:
        """把一份馆内 iCalendar 读成 :class:`Series`（含改时例外与取消项）。"""
        roots = self.parse(text)
        events = [c for c in self._all_components(roots) if c["_name"] == "VEVENT"]
        masters: dict[str, Series] = {}
        detached: list[dict] = []
        for ev in events:
            props = self._prop_map(ev)
            if "RECURRENCE-ID" not in props and "RRULE" in props:
                masters[props["UID"][2]] = self._build_series(
                    props, rooms, owner, default_tz)
            else:
                detached.append(ev)

        for ev in detached:
            props = self._prop_map(ev)
            series = masters[props["UID"][2]]
            rid = self._dt(props, "RECURRENCE-ID", default_tz)
            sequence = int(props["SEQUENCE"][2]) if "SEQUENCE" in props else 0
            status = props["STATUS"][2].upper() if "STATUS" in props else ""
            if status == "CANCELLED":
                series.overrides.append(
                    Override(recurrence_id=rid, cancelled=True,
                             sequence=sequence))
                series.exdates.add(rid)
            else:
                new_start = self._dt(props, "DTSTART", default_tz)
                ov = Override(recurrence_id=rid, new_start=new_start,
                              sequence=sequence)
                if "SUMMARY" in props:
                    ov.summary = _unescape_text(props["SUMMARY"][2])
                series.overrides.append(ov)

        # 主事件自带的 EXDATE
        for ev in events:
            props = self._prop_map(ev)
            if "EXDATE" not in props or "RRULE" not in props:
                continue
            _, params, value = props["EXDATE"]
            for piece in value.split(","):
                dt = self.parse_datetime(piece.strip(), params)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=default_tz)
                masters[props["UID"][2]].exdates.add(dt)
        return list(masters.values())

    def _dt(self, props: dict, key: str, default_tz) -> datetime:
        _, params, value = props[key]
        dt = self.parse_datetime(value, params)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=default_tz)
        return dt

    def _build_series(self, props, rooms, owner, default_tz) -> Series:
        start = self._dt(props, "DTSTART", default_tz)
        end = self._dt(props, "DTEND", default_tz)
        rrule = {}
        for part in props["RRULE"][2].split(";"):
            k, _, v = part.partition("=")
            rrule[k.upper()] = v
        room = rooms[props["LOCATION"][2]]
        guest = ""
        if "ATTENDEE" in props:
            guest = _unescape_text(
                props["ATTENDEE"][2].removeprefix("mailto:"))
        return Series(
            uid=props["UID"][2],
            room=room,
            owner=owner,
            summary=_unescape_text(props.get("SUMMARY", ("", {}, ""))[2]),
            guest=guest,
            start=start,
            end=end,
            rrule=rrule,
            sequence=int(props["SEQUENCE"][2]) if "SEQUENCE" in props else 0,
        )

    def _all_components(self, nodes):
        for node in nodes:
            yield node
            yield from self._all_components(node["_children"])

    @staticmethod
    def _prop_map(comp: dict) -> dict[str, tuple[str, dict, str]]:
        return {name: (name, params, value)
                for name, params, value in comp["_props"]}


# ---------------------------------------------------------------------------
# 写出
# ---------------------------------------------------------------------------

class ICalendarWriter:
    def __init__(self, dtstamp: datetime | None = None):
        # 注入固定 DTSTAMP，便于逐字节比较两份外借件
        self.dtstamp = dtstamp or as_utc(datetime.now()).replace(microsecond=0)

    def _line(self, text: str) -> str:
        return fold_line(text) + "\r\n"

    @staticmethod
    def _dt_utc(dt: datetime) -> str:
        return "DTSTART:" + as_utc(dt).strftime("%Y%m%dT%H%M%SZ")

    @staticmethod
    def _dt_local(dt: datetime) -> tuple[str, str]:
        tzname = dt.tzinfo.key if hasattr(dt.tzinfo, "key") else "America/New_York"
        stamp = dt.strftime("%Y%m%dT%H%M%S")
        return tzname, f"TZID={tzname}:{stamp}"

    def write_internal(self, series_list: list[Series]) -> str:
        """完整馆内 iCalendar：周期 + 改时例外 + 取消项 + EXDATE。"""
        tznames = {s.start.tzinfo.key for s in series_list
                   if hasattr(s.start.tzinfo, "key")}
        out = ["BEGIN:VCALENDAR\r\n",
               "VERSION:2.0\r\n",
               "PRODID:-//Caldesk//Internal Archive//CN\r\n",
               "CALSCALE:GREGORIAN\r\n",
               "METHOD:PUBLISH\r\n"]
        for tzname in sorted(tznames):
            block = vtimezone_block(tzname)
            if block:
                out.append(block)
        for s in series_list:
            out.append(self._write_master(s))
            for ov in s.overrides:
                out.append(self._write_override(s, ov))
        out.append("END:VCALENDAR\r\n")
        return "".join(out)

    def _write_master(self, s: Series) -> str:
        _, start_spec = self._dt_local(s.start)
        _, end_spec = self._dt_local(s.end)
        rrule = "RRULE:" + ";".join(f"{k}={v}" for k, v in s.rrule.items())
        out = ["BEGIN:VEVENT\r\n"]
        out.append(self._line(f"UID:{s.uid}"))
        out.append(self._line(f"DTSTAMP:{as_utc(self.dtstamp):%Y%m%dT%H%M%SZ}"))
        out.append(self._line(f"DTSTART;{start_spec}"))
        out.append(self._line(f"DTEND;{end_spec}"))
        out.append(self._line(rrule))
        out.append(self._line(f"SUMMARY:{_escape_text(s.summary)}"))
        out.append(self._line(f"LOCATION:{_escape_text(s.room.name)}"))
        out.append(self._line(
            f"ATTENDEE;CN={_escape_text(s.guest)}:mailto:{_escape_text(s.guest)}"))
        out.append(self._line(f"SEQUENCE:{s.sequence}"))
        for ex in sorted(s.exdates):
            _, ex_spec = self._dt_local(ex)
            out.append(self._line(f"EXDATE;{ex_spec}"))
        out.append("END:VEVENT\r\n")
        return "".join(out)

    def _write_override(self, s: Series, ov: Override) -> str:
        duration = s.end - s.start
        out = ["BEGIN:VEVENT\r\n"]
        out.append(self._line(f"UID:{s.uid}"))
        out.append(self._line(f"DTSTAMP:{as_utc(self.dtstamp):%Y%m%dT%H%M%SZ}"))
        _, rid_spec = self._dt_local(ov.recurrence_id)
        out.append(self._line(f"RECURRENCE-ID;{rid_spec}"))
        out.append(self._line(f"SEQUENCE:{ov.sequence}"))
        if ov.cancelled:
            out.append(self._line("STATUS:CANCELLED"))
            out.append(self._line(
                f"SUMMARY:{_escape_text('已取消: ' + s.summary)}"))
            _, start_spec = self._dt_local(ov.recurrence_id)
            _, end_spec = self._dt_local(ov.recurrence_id + duration)
            out.append(self._line(f"DTSTART;{start_spec}"))
            out.append(self._line(f"DTEND;{end_spec}"))
        else:
            assert ov.new_start is not None
            _, start_spec = self._dt_local(ov.new_start)
            _, end_spec = self._dt_local(ov.new_start + duration)
            out.append(self._line(f"DTSTART;{start_spec}"))
            out.append(self._line(f"DTEND;{end_spec}"))
            summary = ov.summary or s.summary
            out.append(self._line(f"SUMMARY:{_escape_text(summary)}"))
            out.append(self._line(f"LOCATION:{_escape_text(s.room.name)}"))
        out.append("END:VEVENT\r\n")
        return "".join(out)

    def write_external(self, segments: list[dict], calendar_name: str) -> str:
        """外借匿名日历：每个连续占用区段一个普通 VEVENT，全 UTC。

        不含 RRULE、不含例外、不含任何当事人/主题信息；UID 仅由
        房间与区段起止推导，不引用馆内 UID。
        """
        out = ["BEGIN:VCALENDAR\r\n",
               "VERSION:2.0\r\n",
               "PRODID:-//Caldesk//External Loan//CN\r\n",
               f"X-WR-CALNAME:{_escape_text(calendar_name)}\r\n",
               "METHOD:PUBLISH\r\n"]
        for seg in segments:
            out.append("BEGIN:VEVENT\r\n")
            out.append(self._line(f"UID:{seg['uid']}"))
            out.append(self._line(
                f"DTSTAMP:{as_utc(self.dtstamp):%Y%m%dT%H%M%SZ}"))
            out.append(self._line(self._dt_utc(seg["start"])))
            out.append(self._line(
                "DTEND:" + as_utc(seg["end"]).strftime("%Y%m%dT%H%M%SZ")))
            out.append(self._line("SUMMARY:占用"))
            out.append(self._line(f"LOCATION:{_escape_text(seg['room'])}"))
            out.append(self._line("TRANSP:OPAQUE"))
            out.append("END:VEVENT\r\n")
        out.append("END:VCALENDAR\r\n")
        return "".join(out)
