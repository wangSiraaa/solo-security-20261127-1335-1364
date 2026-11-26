"""End-to-end walk-through of the privacy desk, no CLI involved.

Run with::

    PYTHONPATH=/workspace python3 examples/walkthrough.py
"""

from privacy_desk import Session, WeekConfig, replay_week1, replay_week2
from privacy_desk.scenarios import run_late_discovery


def banner(title: str) -> None:
    print()
    print("=" * 68)
    print(title)
    print("=" * 68)


def main() -> None:
    # -- Week 1, reviewer-first hand-off --------------------------------
    banner("Week 1: reviewer publishes, then worker books")
    cfg = WeekConfig(
        week_id="W1",
        households=("甲", "乙", "丙"),
        groups={"北翼": ("甲", "乙"), "整层": ("甲", "乙", "丙")},
        protected=("甲", "乙", "丙"),
    )
    session = Session.from_raw(cfg, {"甲": 1, "乙": 0, "丙": 0})

    # reviewer submits two new publication requests
    north = session.publish(group="北翼", requester="租户")
    floor = session.publish(group="整层", requester="物业")
    print(north.describe())
    print(floor.describe())
    print(session.report())

    # worker tries to book from what each actually received
    bk_north = session.book(receipt_id=north.receipt_id, requester="租户",
                            slot="入户时段", all_slots=["入户时段"])
    bk_floor = session.book(receipt_id=floor.receipt_id, requester="物业",
                            slot="入楼检修", all_slots=["入楼检修"])
    print()
    print(f"tenant booking: {bk_north.status} slot={bk_north.slot}")
    print(f"property booking: {bk_floor.status} slot={bk_floor.slot}")

    # -- both arrival orders as pure replays ---------------------------
    for result in replay_week1():
        print()
        print(result.render())

    # -- week-2 replays ------------------------------------------------
    for result in replay_week2():
        print()
        print(result.render())

    # -- late discovery -------------------------------------------------
    print()
    print(run_late_discovery().render())

    # -- persistence round-trip -----------------------------------------
    banner("Session survives a JSON round-trip")
    restored = Session.from_json(session.to_json())
    print(restored.report())


if __name__ == "__main__":
    main()
