"""馆内 iCalendar：周期事件导入、按原实例改时例外、取消项。"""

import unittest
from datetime import timedelta

from caldesk.ical import ICalendarReader, ICalendarWriter
from caldesk.model import Override
from caldesk.scenario import FIXED_DTSTAMP, build_world
from caldesk.time_utils import NY, local


class InternalICalendarTests(unittest.TestCase):
    def setUp(self):
        self.world = build_world()
        self.writer = ICalendarWriter(dtstamp=FIXED_DTSTAMP)

    def test_roundtrip_rrule_overrides_and_cancellation(self):
        series = self.world.north.get("north-biweekly")
        rid = local(2026, 11, 18, 10, 0)
        self.world.engine.reschedule_instance(
            "north-biweekly", rid, local(2026, 11, 4, 16, 0), None,
            moment=local(2026, 11, 1, 9, 0))
        self.world.engine.cancel_instance(
            "north-biweekly", local(2026, 12, 2, 10, 0),
            moment=local(2026, 11, 20, 9, 0))

        ics = self.writer.write_internal(self.world.north.series)
        self.assertIn("RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=WE;COUNT=5", ics)
        self.assertIn("RECURRENCE-ID;TZID=America/New_York:20261118T100000",
                      ics)
        self.assertIn("DTSTART;TZID=America/New_York:20261104T160000", ics)
        self.assertIn("STATUS:CANCELLED", ics)
        self.assertIn("EXDATE;TZID=America/New_York:20261202T100000", ics)

        # 重新导入：主事件、改时例外、取消例外都要还原
        reloaded = ICalendarReader().load_series(
            ics, self.world.rooms, owner="desk-north")
        self.assertEqual(len(reloaded), 1)
        s2 = reloaded[0]
        self.assertEqual(s2.uid, "north-biweekly")
        self.assertEqual(s2.rrule["INTERVAL"], "2")
        kinds = sorted((ov.cancelled, ov.new_start) for ov in s2.overrides)
        self.assertEqual(len(kinds), 2)
        cancel = next(ov for ov in s2.overrides if ov.cancelled)
        self.assertEqual(cancel.recurrence_id, local(2026, 12, 2, 10, 0))
        moved = next(ov for ov in s2.overrides if ov.is_reschedule)
        self.assertEqual(moved.recurrence_id, rid)
        self.assertEqual(moved.new_start, local(2026, 11, 4, 16, 0))
        self.assertIn(local(2026, 12, 2, 10, 0), s2.exdates)

    def test_occurrences_honor_overrides(self):
        series = self.world.north.get("north-biweekly")
        base = series.expand()
        self.assertEqual([o.strftime("%Y-%m-%d") for o in base],
                         ["2026-10-07", "2026-10-21", "2026-11-04",
                          "2026-11-18", "2026-12-02"])
        series.overrides.append(Override(
            recurrence_id=local(2026, 11, 18, 10, 0),
            new_start=local(2026, 11, 4, 16, 0)))
        series.overrides.append(Override(
            recurrence_id=local(2026, 12, 2, 10, 0), cancelled=True))
        series.exdates.add(local(2026, 12, 2, 10, 0))
        occ = series.effective_occurrences()
        starts = [(s.strftime("%Y-%m-%d %H:%M"), e - s) for s, e, _ in occ]
        self.assertEqual(starts, [
            ("2026-10-07 10:00", timedelta(hours=1)),
            ("2026-10-21 10:00", timedelta(hours=1)),
            ("2026-11-04 10:00", timedelta(hours=1)),
            ("2026-11-04 16:00", timedelta(hours=1)),  # 同日挪来的实例
            # 12-02 已取消，不再占房
        ])

    def test_folding_and_escaping(self):
        long_summary = "主题" * 40
        world = build_world(summary=long_summary)
        ics = self.writer.write_internal(world.north.series)
        for physical in ics.split("\r\n"):
            self.assertLessEqual(len(physical.encode("utf-8")), 75)
        # 折行能被读回
        reloaded = ICalendarReader().load_series(
            ics, world.rooms, owner="desk-north")
        self.assertEqual(reloaded[0].summary, long_summary)


if __name__ == "__main__":
    unittest.main()
