#!/usr/bin/env python3
"""End-to-end story from the prompt.

Run with:  python3 examples/run_story.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forensic_desk import IntakeDesk, ReviewDesk, make_tree_writable, verify_station_bundle  # noqa: E402
from forensic_desk.authz import READ_RAW_CONTENT, WRITE_WORK_COPY  # noqa: E402
from forensic_desk.fixtures import write_all_fixtures  # noqa: E402


def line(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def status(response: dict) -> str:
    return response["conclusion"]["payload"]["status"]


def main() -> int:
    run_root = ROOT / "runs" / "story"
    if run_root.exists():
        make_tree_writable(run_root)
        shutil.rmtree(run_root)
    fixture_root = run_root / "disks"
    disks = write_all_fixtures(fixture_root)

    # ------------------------------------------------------------------
    line("1. 受理：方式 A，写保护阅盘台，第三方可重复核验")
    station_case = run_root / "cases" / "1401-station"
    intake = IntakeDesk(station_case)
    received = intake.receive(
        disks["case1401"],
        case_id="CASE-1401-STATION",
        source="虚构卷宗盘 SN-FD-1401",
        generation="卷代 G1 / 2026-11-27 接盘快照",
        signed_by="保全员甲；委托人乙签署唯一交接书 REC-000001",
        collection_mode="write_protected_station",
        readable_blocks=["DIRECTORY", "JOURNAL", "LAYOUT", "BLK100", "BLK200", "BLK300", "BLK900"],
        page_ranges=None,
        notes="原始块交写保护阅盘台；准许物理核验、原始观察和内存重放。",
    )
    handoff_id = received["intake_record"]["id"]
    print("交接书/无争议记录：", handoff_id)
    verification = verify_station_bundle(station_case / "write_protected_station")
    print("第三方首次核验：", verification["ok"], "文件数", len(verification["verified_files"]))
    print("第三方重复核验：", verify_station_bundle(station_case / "write_protected_station")["ok"])

    third_party = ReviewDesk(station_case, "third-party-verifier")
    blocked = third_party.open_legacy("write_protected_station")
    print("老阅卷器直接开盘：", status(blocked))

    # ------------------------------------------------------------------
    line("2. 同一收件的 COW 阅卷：未重放改名 -> 日志重放 -> 调查员再改名")
    cow_case = run_root / "cases" / "1401-cow"
    cow_intake = IntakeDesk(cow_case)
    cow_received = cow_intake.receive(
        disks["case1401"],
        case_id="CASE-1401-COW",
        source="虚构卷宗盘 SN-FD-1401（同一收件的委托保管/COW 通道）",
        generation="卷代 G1 / 2026-11-27 接盘快照",
        signed_by="保全员甲；委托人乙签署唯一交接书 REC-000001",
        collection_mode="custodian_cow",
        readable_blocks=["DIRECTORY", "JOURNAL", "LAYOUT", "BLK100", "BLK200", "BLK300"],
        page_ranges=["P100", "P201", "P301"],
        notes="卷留委托人处；只给逻辑结果和操作见证，不放行原始块内容。",
    )
    common_record = cow_received["intake_record"]["id"]
    print("两方可查的同一交接记录：", common_record)

    alice = ReviewDesk(cow_case, "investigator-alice")
    old_physical = alice.request_entry(name="未重放改名-旧名.txt")
    old_physical_id = old_physical["conclusion"]["id"]
    print("接盘物理目录查旧名：", status(old_physical), old_physical_id)

    replay_new = alice.request_entry(name="日志重放-新名.txt")
    print("未重放改名经日志还原：", status(replay_new))

    journal_line = alice.request_entry(name="Q-开卷即写行-来自日志重放.txt")
    print("旧阅卷器开卷将写入的日志新增行：", status(journal_line))

    print("打开老阅卷器：", status(alice.open_legacy()))
    print("开卷后该日志行的结论仍为：", status(alice.request_entry(name="Q-开卷即写行-来自日志重放.txt")))

    denied = alice.rename_entry("E100", "调查员再次改名.txt")
    print("未补授权的再次改名：", status(denied))
    cow_intake.amend_authorization(
        target_investigator="investigator-alice",
        authorized_by="保全员甲",
        reason="委托人补准 Alice 在一次性 COW 层再次改名",
        add_permissions=[WRITE_WORK_COPY],
    )
    renamed = alice.rename_entry("E100", "调查员再次改名.txt")
    print("补授权后再次改名：", status(renamed))
    final = alice.request_entry(name="调查员再次改名.txt")
    print("查调查员最终名：", status(final))
    old_record = alice.get_record(old_physical_id)
    print(
        "旧结论未被覆盖：",
        old_record["payload"]["status"],
        old_record["payload"]["result"]["original_existence_proof"]["raw_line"],
    )
    physical_verify = alice.submit({"kind": "verify_physical"})
    print("委托方侧物理哈希核验：", status(physical_verify), "异地复验承诺=", physical_verify["conclusion"]["payload"]["result"]["offsite_physical_reverification"])

    # ------------------------------------------------------------------
    line("3. 另起一卷：原始行、调查员首次写入、页范围收窄")
    second_case = run_root / "cases" / "second-volume"
    second_intake = IntakeDesk(second_case)
    second_intake.receive(
        disks["second"],
        case_id="SECOND-VOL",
        source="另一虚构卷宗盘 SN-FD-2",
        generation="卷代 G3",
        signed_by="委托人丙",
        collection_mode="custodian_cow",
        readable_blocks=["DIRECTORY", "JOURNAL", "LAYOUT", "BLK001"],
        page_ranges=["P001", "P002"],
    )
    second_intake.amend_authorization(
        target_investigator="reviewer-d",
        authorized_by="保全员甲",
        reason="准许读取原始页内容并在 COW 工作层写入",
        add_permissions=[READ_RAW_CONTENT, WRITE_WORK_COPY],
    )
    desk = ReviewDesk(second_case, "reviewer-d")
    original = desk.request_entry(name="接盘时已存在的行.txt")
    print("接盘时已存在行：", status(original), original["conclusion"]["id"])
    print("P002 收窄前可读：", status(desk.request_page("P002")))

    second_intake.amend_authorization(
        target_investigator="reviewer-d",
        authorized_by="保全员甲",
        reason="只保留 P001；来源和哈希不变",
        replace_page_ranges=["P001"],
    )
    denied_page = desk.request_page("P002")
    print("页范围改后 P002：", status(denied_page))
    redacted_block = desk.request_raw_block("BLK001")
    print("同一块整体观察：", status(redacted_block))

    desk.open_legacy()
    written = desk.write_line("E777", "调查员首次写入的行.txt")
    print("调查员写入：", status(written))
    provenance = desk.confirm_investigator_first_write("E777")
    print("补真实来源记录：", status(provenance), provenance["conclusion"]["payload"]["result"]["provenance_record_id"])
    observed = desk.request_entry(name="调查员首次写入的行.txt")
    print("仍可报告调查时看见：", status(observed))

    # ------------------------------------------------------------------
    line("4. 两名调查员各自开卷")
    iso_case = run_root / "cases" / "isolated"
    iso_intake = IntakeDesk(iso_case)
    iso_intake.receive(
        disks["case1401"],
        case_id="CASE-1401-ISO",
        source="虚构卷宗盘 SN-FD-1401",
        generation="G1",
        signed_by="委托人乙",
        collection_mode="custodian_cow",
        readable_blocks=["DIRECTORY", "JOURNAL", "LAYOUT", "BLK100"],
        page_ranges=["P100"],
    )
    iso_intake.amend_authorization(
        target_investigator="*",
        authorized_by="保全员甲",
        reason="两名调查员各自私有 COW 写入",
        add_permissions=[WRITE_WORK_COPY],
    )
    alice2 = ReviewDesk(iso_case, "alice")
    bob2 = ReviewDesk(iso_case, "bob")
    alice2.open_legacy()
    bob2.open_legacy()
    alice2.rename_entry("E100", "Alice-私有结果.txt")
    bob2.rename_entry("E100", "Bob-私有结果.txt")
    print("Alice 查 Bob 写入：", status(alice2.request_entry(name="Bob-私有结果.txt")))
    print("Bob 查 Alice 写入：", status(bob2.request_entry(name="Alice-私有结果.txt")))
    print("台账链：", alice2.verify_ledger())

    line("完成")
    print("所有产物位于", run_root)
    print("可运行：python3 -m unittest discover -s tests -v")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
