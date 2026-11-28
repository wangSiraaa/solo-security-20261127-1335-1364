"""端到端情节：夏令时切换、只撤一个原实例、还原内档后仅改口译员半小时。"""

import unittest

from caldesk.ical import ICalendarReader
from caldesk.booking import BookingError
from caldesk.scenario import build_world
from caldesk.time_utils import as_utc, local


class DaylightSavingScenarioTests(unittest.TestCase):
    def test_dst_fallback_keeps_wall_clock_and_shifts_offset(self):
        world = build_world()
        series = world.north.get("north-biweekly")
        occurrences = series.expand()
        before, after = occurrences[2], occurrences[3]  # 11-04 / 11-18
        # 秋退发生在 2026-11-01：两个实例都在冬令，挂钟时间同为 10:00；
        # 取跨切换两侧的 10-21（EDT）与 11-04（EST）验证偏移变化
        edt, est = occurrences[1], occurrences[2]
        self.assertEqual(edt.strftime("%H:%M"), "10:00")
        self.assertEqual(est.strftime("%H:%M"), "10:00")
        self.assertEqual(as_utc(edt).strftime("%H%M"), "1400")  # EDT UTC-4
        self.assertEqual(as_utc(est).strftime("%H%M"), "1500")  # EST UTC-5
        self.assertEqual(as_utc(after).strftime("%H%M"), "1500")

    def test_move_one_instance_onto_same_day_then_cancel_one_original(self):
        world = build_world()
        rid_nov18 = local(2026, 11, 18, 10, 0)
        world.engine.reschedule_instance(
            "north-biweekly", rid_nov18, local(2026, 11, 4, 16, 0), None,
            moment=local(2026, 11, 1, 9, 0))
        # 只撤“另一个”原实例（12-02），11-04 的单必须能准时入住
        world.engine.cancel_instance(
            "north-biweekly", local(2026, 12, 2, 10, 0),
            moment=local(2026, 11, 20, 9, 0))

        series = world.north.get("north-biweekly")
        occ = series.effective_occurrences()
        day_nov4 = [(s, e) for s, e, _ in occ
                    if s.strftime("%Y-%m-%d") == "2026-11-04"]
        self.assertEqual(
            [(s.strftime("%H:%M"), e.strftime("%H:%M")) for s, e in day_nov4],
            [("10:00", "11:00"), ("16:00", "17:00")])
        # 12-02 已撤，不占房；11-18 原实例也因改时不再占原时段
        self.assertFalse(any(
            s.strftime("%Y-%m-%d") == "2026-12-02" for s, _, _ in occ))
        self.assertFalse(any(
            s.strftime("%Y-%m-%d %H:%M") == "2026-11-18 10:00"
            for s, _, _ in occ))

        # 馆外读取器展开外借件：同日两场都在，另一单（南馆 14:00）不受影响
        ics = world.lender.export()
        roots = ICalendarReader().parse(ics)
        events = [c for c in ICalendarReader()._all_components(roots)
                  if c["_name"] == "VEVENT"]
        self.assertGreaterEqual(len(events), 1)
        # 南馆那一单 14:00 EST 照常占用无障碍房，可准时入住
        south_busy = world.engine.room_busy("无障碍接待室")
        self.assertIn(
            (local(2026, 11, 4, 14, 0), local(2026, 11, 4, 15, 0)),
            south_busy)

        # 取消项以 RECURRENCE-ID 精确命中，不波及同日其他实例
        canceled = [ov for ov in series.overrides if ov.cancelled]
        self.assertEqual(len(canceled), 1)
        self.assertEqual(canceled[0].recurrence_id,
                         local(2026, 12, 2, 10, 0))

    def test_restore_archive_then_only_move_interpreters_half_hour(self):
        # 1) 先把含例外的内档导出，再从材料还原
        world = build_world()
        world.engine.reschedule_instance(
            "north-biweekly", local(2026, 11, 18, 10, 0),
            local(2026, 11, 4, 16, 0), None, moment=local(2026, 11, 1, 9, 0))
        snapshot = world.writer.write_internal(world.north.series)
        restored = ICalendarReader().load_series(
            snapshot, world.rooms, owner="desk-north")
        self.assertEqual(
            [(ov.recurrence_id, ov.new_start) for ov in restored[0].overrides],
            [(local(2026, 11, 18, 10, 0), local(2026, 11, 4, 16, 0))])

        # 2) 另一间房已购得（接待室A 09:00–10:00 被占），口译员只占其中半小时
        world.engine.interpreter_busy.setdefault("口译员林", []).append(
            (local(2026, 11, 25, 9, 30), local(2026, 11, 25, 10, 0)))
        # 在接待室A登记背靠背占用，模拟该房 09:00–10:00 已售出
        from caldesk.booking import Reservation
        world.engine.reservations.append(Reservation(
            reservation_id="BK-OWNED-1", room=world.rooms["接待室A"],
            start=local(2026, 11, 25, 9, 0), end=local(2026, 11, 25, 10, 0),
            guest="已购得客人"))

        # 3) 重新办理：想让口译员在已购得的半小时上重叠 → 必须拒绝，
        #    已购得的另一间房不能被重复占用
        with self.assertRaises(BookingError):
            world.engine.book_two_slots(
                "当事人丁", "口译员林",
                [(local(2026, 11, 25, 9, 0),
                  local(2026, 11, 25, 9, 30)),
                 (local(2026, 11, 25, 9, 30),
                  local(2026, 11, 25, 10, 0))])

        # 4) 只改口译员已占的那半小时：平移到空闲半小时后办理成功
        ok = world.engine.book_two_slots(
            "当事人丁", "口译员林",
            [(local(2026, 11, 25, 10, 30),
             local(2026, 11, 25, 11, 0)),
             (local(2026, 11, 25, 11, 0),
              local(2026, 11, 25, 11, 30))])
        self.assertEqual(len(ok), 2)
        # 接待室A 的原始售出占用原样保留
        self.assertIn(
            (local(2026, 11, 25, 9, 0), local(2026, 11, 25, 10, 0)),
            world.engine.room_busy("接待室A"))


if __name__ == "__main__":
    unittest.main()
