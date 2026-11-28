"""题面情节的共享构造器与端到端演示。

时间一律用 America/New_York 本地挂钟时间，覆盖 2026-11-01 的
夏令时秋退（EDT -04:00 → EST -05:00）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .booking import BookingEngine
from .external import ExternalLender
from .ical import ICalendarReader, ICalendarWriter
from .model import InternalArchive, Room, Series
from .notices import NoticeBoard
from .policy import describe_costs
from .time_utils import local, utc

FIXED_DTSTAMP = utc(2026, 10, 1, 9, 0)


def standard_rooms() -> dict[str, Room]:
    return {
        "接待室A": Room("接待室A"),
        "接待室B": Room("接待室B"),
        "无障碍接待室": Room("无障碍接待室", accessible=True),
    }


def biweekly_series(rooms, owner="desk-north", uid="north-biweekly",
                    summary="隔周面谈（甲）", guest="当事人甲") -> Series:
    """每隔一周的周三 10:00–11:00，跨 2026-11-01 夏令时切换。"""
    return Series(
        uid=uid,
        room=rooms["接待室B"],
        owner=owner,
        summary=summary,
        guest=guest,
        start=local(2026, 10, 7, 10, 0),
        end=local(2026, 10, 7, 11, 0),
        rrule={"FREQ": "WEEKLY", "INTERVAL": "2", "BYDAY": "WE", "COUNT": "5"},
    )


def accessible_series(rooms) -> Series:
    """另一馆放在无障碍房的单次日程（11 月 4 日 14:00）。"""
    return Series(
        uid="south-accessible",
        room=rooms["无障碍接待室"],
        owner="desk-south",
        summary="无障碍咨询",
        guest="当事人乙",
        start=local(2026, 11, 4, 14, 0),
        end=local(2026, 11, 4, 15, 0),
        rrule={"FREQ": "WEEKLY", "COUNT": "1"},
    )


@dataclass
class World:
    rooms: dict
    north: InternalArchive
    south: InternalArchive
    engine: BookingEngine
    board: NoticeBoard
    lender: ExternalLender
    writer: ICalendarWriter


def build_world(summary: str = "隔周面谈（甲）",
                guest: str = "当事人甲") -> World:
    rooms = standard_rooms()
    north = InternalArchive(firm="北馆")
    north.add_series(biweekly_series(rooms, summary=summary, guest=guest))
    south = InternalArchive(firm="南馆")
    south.add_series(accessible_series(rooms))
    board = NoticeBoard()
    writer = ICalendarWriter(dtstamp=FIXED_DTSTAMP)
    engine = BookingEngine("desk-north", list(rooms.values()), [north, south])
    lender = ExternalLender("接待室外借日历", [north, south],
                            reservations=engine.reservations,
                            board=board, writer=writer)
    return World(rooms, north, south, engine, board, lender, writer)


def run_scenario(out_dir: Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    world = build_world()

    # 1) 外借匿名日历（基线，先于一切改约）
    external_ics = world.lender.export()
    world.lender.assert_leaks_no_private_data(external_ics)
    (out_dir / "external_loan.ics").write_text(external_ics, encoding="utf-8")

    # 2) 把周期中的一次（原 11/18）挪到与另一实例同日（11/4 16:00）
    rid_nov18 = local(2026, 11, 18, 10, 0)
    move_receipt = world.engine.reschedule_instance(
        "north-biweekly", rid_nov18, local(2026, 11, 4, 16, 0), None,
        moment=local(2026, 11, 1, 9, 0))
    moved_ics = world.lender.publish(FIXED_DTSTAMP)
    world.lender.assert_leaks_no_private_data(moved_ics)
    (out_dir / "external_loan_after_move.ics").write_text(
        moved_ics, encoding="utf-8")

    # 3) 只撤其中一个原实例（12/02），另一单不受影响
    cancel_receipt = world.engine.cancel_instance(
        "north-biweekly", local(2026, 12, 2, 10, 0),
        moment=local(2026, 11, 20, 9, 0))
    cancelled_ics = world.lender.publish(FIXED_DTSTAMP)
    (out_dir / "external_loan_after_cancel.ics").write_text(
        cancelled_ics, encoding="utf-8")

    # 4) 新请求：无障碍房 + 连续两场同一口译员，原子订妥才通知
    slots = [(local(2026, 11, 25, 9, 0), local(2026, 11, 25, 10, 0)),
             (local(2026, 11, 25, 10, 0), local(2026, 11, 25, 11, 0))]
    reservations = world.engine.book_two_slots(
        "当事人丙", "口译员林", slots)
    final_ics = world.lender.publish(FIXED_DTSTAMP)
    world.lender.assert_leaks_no_private_data(final_ics)
    (out_dir / "external_loan_final.ics").write_text(
        final_ics, encoding="utf-8")

    # 5) 馆内完整 iCalendar（含周期、改时例外、取消项），并自证可重新导入
    internal_ics = world.writer.write_internal(world.north.series)
    (out_dir / "north_internal.ics").write_text(internal_ics, encoding="utf-8")
    reloaded = ICalendarReader().load_series(
        internal_ics, world.rooms, owner="desk-north")
    assert len(reloaded) == 1
    assert len(reloaded[0].overrides) == 2

    lines = [
        describe_costs(),
        "",
        f"已写出 ICS 到 {out_dir}/：",
        "  north_internal.ics              馆内完整周期+改时例外+取消项",
        "  external_loan.ics               外借匿名连续区段（基线）",
        "  external_loan_after_move.ics    一次改时后的外借件",
        "  external_loan_after_cancel.ics  撤一个原实例后的外借件",
        "  external_loan_final.ics         新请求两场订妥后的外借件",
        f"改约回执: {move_receipt.receipt_id}（{move_receipt.note}）",
        f"取消回执: {cancel_receipt.receipt_id}（{cancel_receipt.note}）",
        f"新请求确认号: {[r.reservation_id for r in reservations]}",
        f"更新通知条数: {len(world.board.for_calendar('接待室外借日历'))}",
    ]
    return "\n".join(lines)
