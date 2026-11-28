"""外借匿名日历：可在另一账户打开、连续区段合并、两套内档不可分辨。"""

import unittest

from caldesk.ical import ICalendarReader, ICalendarWriter
from caldesk.notices import NoticeBoard
from caldesk.scenario import FIXED_DTSTAMP, build_world
from caldesk.time_utils import local


def _private_variant(summary: str, guest: str):
    world = build_world(summary=summary, guest=guest)
    # 做完全相同的占用层面操作，只让私人字段不同
    world.engine.reschedule_instance(
        "north-biweekly", local(2026, 11, 18, 10, 0),
        local(2026, 11, 4, 16, 0), None, moment=local(2026, 11, 1, 9, 0))
    return world


class ExternalCalendarTests(unittest.TestCase):
    def test_another_account_can_open_loan_calendar(self):
        world = build_world()
        ics = world.lender.export()
        # 标准 VCALENDAR 外壳、全 UTC、无 VTIMEZONE 依赖
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertIn("VERSION:2.0\r\n", ics)
        self.assertTrue(ics.rstrip().endswith("END:VCALENDAR"))
        self.assertNotIn("TZID", ics)
        self.assertIn("DTSTART:20261007T", ics)  # UTC 形式
        # 另一账户的读取器能展开成普通事件
        roots = ICalendarReader().parse(ics)
        events = [c for c in ICalendarReader()._all_components(roots)
                  if c["_name"] == "VEVENT"]
        self.assertGreaterEqual(len(events), 5)
        world.lender.assert_leaks_no_private_data(ics)

    def test_back_to_back_occupancy_merges_into_one_segment(self):
        world = build_world()
        # 两个背靠背的临时预订应合并成一段
        slots = [(local(2026, 11, 25, 9, 0), local(2026, 11, 25, 10, 0)),
                 (local(2026, 11, 25, 10, 0), local(2026, 11, 25, 11, 0))]
        world.engine.book_two_slots("当事人丙", "口译员林", slots)
        segments = world.lender.build_segments()
        target = [g for g in segments
                  if g["room"] == "无障碍接待室"
                  and g["start"].strftime("%Y-%m-%d") == "2026-11-25"]
        self.assertEqual(len(target), 1)
        self.assertEqual(target[0]["start"].strftime("%H:%M"), "09:00")
        self.assertEqual(target[0]["end"].strftime("%H:%M"), "11:00")

    def test_private_title_or_guest_swap_is_indistinguishable(self):
        """两套内档只换私人标题/在同一连续占用内互换客人。

        外借件必须逐字节相同；稳定标识、例外条数（外借件恒为 0 个
        RRULE/RECURRENCE-ID）与更新通知也完全一致。
        """
        w1 = _private_variant("隔周面谈（甲）", "当事人甲")
        w2 = _private_variant("隔周面谈（标题不同）", "当事人丁（互换的客人）")

        ics1 = w1.lender.export()
        ics2 = w2.lender.export()
        self.assertEqual(ics1, ics2)

        for ics in (ics1, ics2):
            self.assertNotIn("RRULE", ics)
            self.assertNotIn("RECURRENCE-ID", ics)
            self.assertNotIn("当事人", ics)
            self.assertNotIn("隔周面谈", ics)

        # 再做一次相同的占用变更，通知序列也必须一致
        w1.engine.cancel_instance(
            "north-biweekly", local(2026, 12, 2, 10, 0),
            moment=local(2026, 11, 20, 9, 0))
        w2.engine.cancel_instance(
            "north-biweekly", local(2026, 12, 2, 10, 0),
            moment=local(2026, 11, 20, 9, 0))
        n1 = w1.lender.publish(FIXED_DTSTAMP)
        n2 = w2.lender.publish(FIXED_DTSTAMP)
        self.assertEqual(n1, n2)
        sig1 = [(x.kind, x.segment_uid) for x in w1.board.all()]
        sig2 = [(x.kind, x.segment_uid) for x in w2.board.all()]
        self.assertEqual(sig1, sig2)

    def test_purely_private_edit_emits_no_notice(self):
        world = build_world()
        world.lender.publish(FIXED_DTSTAMP)  # 基线
        before = len(world.board.all())
        series = world.north.get("north-biweekly")
        series.summary = "只改私人标题，占用不变"
        series.guest = "同一连续占用内互换的客人"
        world.lender.publish(FIXED_DTSTAMP)
        self.assertEqual(len(world.board.all()), before)

    def test_stable_uids_derive_only_from_room_and_time(self):
        w1 = build_world()
        w2 = build_world(summary="别的标题", guest="别的客人")
        self.assertEqual(
            [g["uid"] for g in w1.lender.build_segments()],
            [g["uid"] for g in w2.lender.build_segments()])


if __name__ == "__main__":
    unittest.main()
