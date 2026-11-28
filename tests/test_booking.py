"""预约台权限、报价锁定与原子预订。"""

import unittest
from datetime import timedelta

from caldesk.booking import BookingError
from caldesk.policy import (QUOTE_TTL, adhoc_fee, make_quote,
                            reschedule_fee)
from caldesk.scenario import build_world
from caldesk.time_utils import local


class BookingEngineTests(unittest.TestCase):
    def test_desk_cannot_reschedule_other_desk_series(self):
        world = build_world()
        with self.assertRaises(BookingError):
            world.engine.reschedule_instance(
                "south-accessible", local(2026, 11, 4, 14, 0),
                local(2026, 11, 5, 14, 0), None)

    def test_reschedule_into_occupied_room_is_rejected(self):
        world = build_world()
        # 11/4 14:00 无障碍房已被南馆占用，且接待室B 10:00 也有本周期实例
        with self.assertRaises(BookingError):
            world.engine.reschedule_instance(
                "north-biweekly", local(2026, 11, 18, 10, 0),
                local(2026, 11, 4, 10, 0), "接待室B",
                moment=local(2026, 11, 1, 9, 0))

    def test_quote_hold_is_honored_until_expiry(self):
        world = build_world()
        created = local(2026, 11, 1, 9, 0)
        quote = make_quote("Q-1", "接待室A",
                           local(2026, 11, 10, 9, 0),
                           local(2026, 11, 10, 10, 0),
                           created, "ADHOC", 600.0)
        world.engine.place_hold(quote)
        # 锁定期间该余量对别人不可用——既有服务必须兑现
        self.assertFalse(world.engine.is_room_free(
            world.rooms["接待室A"],
            local(2026, 11, 10, 9, 30),
            local(2026, 11, 10, 10, 30)))
        self.assertTrue(quote.is_valid(created + QUOTE_TTL))
        # 越过有效期边界后释放余量
        world.engine.release_expired_holds(created + QUOTE_TTL
                                           + timedelta(minutes=1))
        self.assertTrue(world.engine.is_room_free(
            world.rooms["接待室A"],
            local(2026, 11, 10, 9, 30),
            local(2026, 11, 10, 10, 30)))

    def test_two_slots_are_atomic_and_require_accessible_room(self):
        world = build_world()
        slots = [(local(2026, 11, 25, 9, 0), local(2026, 11, 25, 10, 0)),
                 (local(2026, 11, 25, 10, 0), local(2026, 11, 25, 11, 0))]
        reservations = world.engine.book_two_slots(
            "当事人丙", "口译员林", slots)
        self.assertEqual(len(reservations), 2)
        self.assertTrue(all(r.room.accessible for r in reservations))
        self.assertEqual({r.room.name for r in reservations},
                         {"无障碍接待室"})

        # 两场都订妥后才通知：通知里出现的是合并后的一个连续区段
        ics = world.lender.publish(local(2026, 11, 1, 9, 0))
        self.assertIn("DTSTART:20261125T140000Z", ics)  # 09:00 EST = 14:00Z
        self.assertIn("DTEND:20261125T160000Z", ics)    # 背靠背合并到 11:00

    def test_second_slot_failure_rolls_back_first(self):
        world = build_world()
        # 先占掉无障碍房第二场，制造“第一场能订、第二场订不到”
        good_slots = [(local(2026, 11, 26, 9, 0), local(2026, 11, 26, 10, 0)),
                      (local(2026, 11, 26, 10, 0),
                       local(2026, 11, 26, 11, 0))]
        world.engine.book_two_slots("当事人丙", "口译员林", good_slots)
        busy_count = len(world.engine.reservations)
        with self.assertRaises(BookingError):
            world.engine.book_two_slots(
                "当事人戊", "口译员赵", good_slots)
        self.assertEqual(len(world.engine.reservations), busy_count)
        # 口译员占用也回滚，半小时后可以再用
        self.assertTrue(world.engine.is_interpreter_free(
            "口译员赵", local(2026, 11, 26, 9, 0),
            local(2026, 11, 26, 10, 0)))

    def test_interpreter_half_hour_cannot_be_double_booked(self):
        world = build_world()
        # 口译员已占 09:30–10:00
        world.engine.interpreter_busy.setdefault("口译员林", []).append(
            (local(2026, 11, 25, 9, 30), local(2026, 11, 25, 10, 0)))
        with self.assertRaises(BookingError):
            world.engine.book_two_slots(
                "当事人丙", "口译员林",
                [(local(2026, 11, 25, 9, 0), local(2026, 11, 25, 10, 0)),
                 (local(2026, 11, 25, 10, 0),
                  local(2026, 11, 25, 11, 0))])
        self.assertEqual(world.engine.reservations, [])

    def test_fee_schedule(self):
        rid = local(2026, 11, 20, 10, 0)
        self.assertEqual(reschedule_fee(local(2026, 11, 18, 9, 0), rid)[0], 0.0)
        self.assertEqual(reschedule_fee(local(2026, 11, 19, 9, 0), rid)[0], 300.0)
        self.assertEqual(reschedule_fee(local(2026, 11, 20, 9, 0), rid)[0], 600.0)
        amount, _ = adhoc_fee(local(2026, 11, 1, 13, 0),
                              local(2026, 11, 2, 9, 0))  # 提前 20h
        self.assertEqual(amount, 900.0)


if __name__ == "__main__":
    unittest.main()
