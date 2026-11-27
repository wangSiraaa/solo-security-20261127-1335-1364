#!/usr/bin/env python3
"""End-to-end demonstration using only fresh processes and local JSON files.

Run:  python3 demo.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
SHARED_EGRESS = "shared-cafe-exit.example"

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    if not condition:
        raise AssertionError(message)
    CHECKS += 1
    print(f"  ✓ {message}")


def heading(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def run_cli(
    state: Path,
    command: str,
    *args: Any,
    expect: int = 0,
    env_now: int | float | None = None,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    if env_now is None:
        env.pop("ANON_STATION_NOW", None)
    if env_now is not None:
        env["ANON_STATION_NOW"] = str(int(env_now))
    proc = subprocess.run(
        [PYTHON, "-m", "anon_station", command, "--state", str(state), *[str(a) for a in args]],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"{command} produced non-JSON output\nstdout={proc.stdout}\nstderr={proc.stderr}"
        ) from exc
    if proc.returncode != expect:
        raise AssertionError(
            f"{command} exit {proc.returncode}, expected {expect}\n"
            f"payload={json.dumps(payload, ensure_ascii=False, indent=2)}\n"
            f"stderr={proc.stderr}"
        )
    return payload


def show(state: Path, slot: int | None = None) -> None:
    args = [PYTHON, "-m", "anon_station", "inspect", "--state", str(state)]
    if slot is not None:
        args += ["--slot", str(slot)]
    proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)


def roundtrip(state: Path, channel: str, out: Path) -> None:
    run_cli(state, "roundtrip", "--channel", channel, "--out", out)


def scenario_two_terminals(base: Path) -> None:
    heading("场景 1：两台终端共用出口；A 未写入即离开，再从新无线线路回来")
    state = base / "two-terminals"
    a_dir = base / "terminal-A"
    b_dir = base / "terminal-B"
    a_dir.mkdir(parents=True)
    b_dir.mkdir(parents=True)

    run_cli(
        state,
        "init",
        "--slots",
        2,
        "--policy",
        "reservation",
        "--reservation-ttl",
        3600,
        "--partial-ttl",
        7200,
    )

    a_old_witness = a_dir / "witness-old-line.json"
    a_new_witness = a_dir / "witness-new-line.json"
    a_ticket = a_dir / "ticket-A-private.json"
    b_witness = b_dir / "witness-B.json"
    b_ticket = b_dir / "ticket-B-private.json"

    roundtrip(state, "wireless-A-old", a_old_witness)
    roundtrip(state, "wireless-B", b_witness)

    a_ask = run_cli(
        state,
        "ask",
        "--witness",
        a_old_witness,
        "--segments",
        3,
        "--egress",
        SHARED_EGRESS,
        "--ticket-out",
        a_ticket,
    )
    b_ask = run_cli(
        state,
        "ask",
        "--witness",
        b_witness,
        "--segments",
        2,
        "--egress",
        SHARED_EGRESS,
        "--ticket-out",
        b_ticket,
    )
    check(a_ask["seat_held"] is True and a_ask["slot"] == 1, "A 首问成功，持久预约 1 号格")
    check(b_ask["seat_held"] is True and b_ask["slot"] == 2, "B 首问成功，持久预约 2 号格")
    check(a_ask["ticket"] != b_ask["ticket"], "席位由各自私密领位条代表，而不是出口名")

    forged = run_cli(
        state,
        "write",
        "--ticket",
        "forged-not-a-valid-ticket",
        "--witness",
        a_old_witness,
        "--index",
        0,
        "--data",
        "A0;",
        "--egress",
        SHARED_EGRESS,
        expect=4,
    )
    check(forged["code"] == "INVALID_TOKEN", "即使走同一个出口，伪造领位条也不能写入")

    b0 = run_cli(
        state,
        "write",
        "--ticket-file",
        b_ticket,
        "--witness",
        b_witness,
        "--index",
        0,
        "--data",
        "B0;",
        "--egress",
        SHARED_EGRESS,
    )
    check(b0["duplicate"] is False and b0["segments_received"] == 1, "B 写入首段")
    b0_retry = run_cli(
        state,
        "write",
        "--ticket-file",
        b_ticket,
        "--witness",
        b_witness,
        "--index",
        0,
        "--data",
        "B0;",
        "--egress",
        SHARED_EGRESS,
        "--idempotency-key",
        "b-first-retry",
    )
    check(b0_retry["duplicate"] is True, "B 的重复首段被幂等确认，没有产生第二段或第二格")

    print("\n  >> A 在写入任何段之前关机/离开；下面每条 CLI 都是新进程，网关无内存状态可依赖。")
    inspect_before_return = run_cli(state, "inspect", "--json")
    slot_a = inspect_before_return["slots"][0]
    check(slot_a["state"] == "reserved" and slot_a["segments_received"] == 0, "A 的空格仍是‘占位’，没有被当成空柜")

    roundtrip(state, "wireless-A-new-line", a_new_witness)
    moved_without_migration = run_cli(
        state,
        "write",
        "--ticket-file",
        a_ticket,
        "--witness",
        a_new_witness,
        "--index",
        0,
        "--data",
        "A0;",
        "--egress",
        SHARED_EGRESS,
        expect=7,
    )
    check(
        moved_without_migration["code"] == "CHANNEL_MIGRATION_REQUIRED",
        "A 只拿出另一通道的有效见证但未迁移，正文仍被拒绝",
    )

    migrated = run_cli(
        state,
        "migrate",
        "--ticket-file",
        a_ticket,
        "--witness",
        a_new_witness,
        "--egress",
        SHARED_EGRESS,
    )
    check(migrated["changed"] is True and migrated["bound_channel"] == "wireless-A-new-line", "A 持私密领位条完成换线迁移")

    a0 = run_cli(
        state,
        "write",
        "--ticket-file",
        a_ticket,
        "--witness",
        a_new_witness,
        "--index",
        0,
        "--data",
        "A0;",
        "--egress",
        SHARED_EGRESS,
    )
    check(a0["state"] == "writing", "迁移后 A 的首段落入原来的 1 号格")
    a0_dup = run_cli(
        state,
        "write",
        "--ticket-file",
        a_ticket,
        "--witness",
        a_new_witness,
        "--index",
        0,
        "--data",
        "A0;",
    )
    check(a0_dup["duplicate"] is True and a0_dup["segments_received"] == 1, "A 重发同一首段不会重复拼接")
    conflict = run_cli(
        state,
        "write",
        "--ticket-file",
        a_ticket,
        "--witness",
        a_new_witness,
        "--index",
        0,
        "--data",
        "DIFFERENT;",
        expect=5,
    )
    check(conflict["code"] == "SEGMENT_CONFLICT", "同序号但内容不同的首段被拒绝，不能覆盖")

    b1 = run_cli(
        state,
        "write",
        "--ticket-file",
        b_ticket,
        "--witness",
        b_witness,
        "--index",
        1,
        "--data",
        "B1",
        "--egress",
        SHARED_EGRESS,
    )
    check(b1["state"] == "complete", "B 的片段齐全，等待最终签收")
    b_delivered = run_cli(state, "deliver", "--ticket-file", b_ticket, "--signoff", "值班签收人-B")
    check(b_delivered["artifact"]["assembled"] == "B0;B1", "B 经最终签收交出成件")
    run_cli(state, "clear-slot", "--slot", 2, "--operator", "柜管员")

    run_cli(
        state,
        "write",
        "--ticket-file",
        a_ticket,
        "--witness",
        a_new_witness,
        "--index",
        1,
        "--data",
        "A1;",
    )
    run_cli(
        state,
        "write",
        "--ticket-file",
        a_ticket,
        "--witness",
        a_new_witness,
        "--index",
        2,
        "--data",
        "A2",
    )
    a_delivered = run_cli(state, "deliver", "--ticket-file", a_ticket, "--signoff", "值班签收人-A")
    check(a_delivered["artifact"]["assembled"] == "A0;A1;A2", "A 换线后仍在原预约格完成并交付")

    final_status = run_cli(state, "inspect", "--json")
    check(final_status["slots"][0]["state"] == "delivered", "检查暂存格可看到‘已交付成件’")
    check(final_status["slots"][1]["state"] == "empty", "B 交付并清格后，2 号格才是空")
    show(state)


def scenario_single_slot_channel(base: Path) -> None:
    heading("场景 2：只有一个暂存格；空柜新单、通道回报篡改/换通道、还原后正常交付")
    state = base / "single-slot"
    state.mkdir(parents=True)
    run_cli(state, "init", "--slots", 1, "--policy", "reservation")

    witness = state / "roundtrip.json"
    backup = state / "roundtrip.original.json"
    other_witness = state / "other-channel.json"
    ticket = state / "ticket.json"

    roundtrip(state, "line-home", witness)
    shutil.copy2(witness, backup)
    ask = run_cli(state, "ask", "--witness", witness, "--segments", 3, "--ticket-out", ticket)
    check(ask["slot"] == 1 and ask["seat_held"] is True, "从唯一格子尚空时发起新单并得到当前通道预约")

    roundtrip(state, "line-other", other_witness)
    other_rejected = run_cli(
        state,
        "write",
        "--ticket-file",
        ticket,
        "--witness",
        other_witness,
        "--index",
        0,
        "--data",
        "S0;",
        expect=7,
    )
    check(other_rejected["code"] == "CHANNEL_MIGRATION_REQUIRED", "把见证换成另一通道且未迁移，不能接下正文")

    report = json.loads(witness.read_text(encoding="utf-8"))
    report["channel"] = "line-other"
    witness.write_text(json.dumps(report), encoding="utf-8")
    tampered_rejected = run_cli(
        state,
        "write",
        "--ticket-file",
        ticket,
        "--witness",
        witness,
        "--index",
        0,
        "--data",
        "S0;",
        expect=4,
    )
    check(tampered_rejected["code"] == "REPORT_CHANNEL_MISMATCH", "只改写回报里的通道显示字段，过不了签名见证核对")

    shutil.copy2(backup, witness)  # “还原回报”
    restored0 = run_cli(
        state,
        "write",
        "--ticket-file",
        ticket,
        "--witness",
        witness,
        "--index",
        0,
        "--data",
        "S0;",
    )
    check(restored0["ok"] is True, "还原为本单已核对的当前通道回报后，首段可写入")

    run_cli(state, "migrate", "--ticket-file", ticket, "--witness", other_witness)
    run_cli(
        state,
        "write",
        "--ticket-file",
        ticket,
        "--witness",
        other_witness,
        "--index",
        1,
        "--data",
        "S1;",
    )
    run_cli(
        state,
        "write",
        "--ticket-file",
        ticket,
        "--witness",
        other_witness,
        "--index",
        2,
        "--data",
        "S2",
    )
    delivered = run_cli(state, "deliver", "--ticket-file", ticket, "--signoff", "单格班次签收")
    check(delivered["artifact"]["assembled"] == "S0;S1;S2", "完成迁移后可继续写入并正常交付")
    show(state)


def scenario_half_item_not_released(base: Path) -> None:
    heading("场景 3：首问前已有一件欠最后一段；它仍是‘已写段’占位，不能算已释放")
    state = base / "partial-blocks"
    state.mkdir(parents=True)
    run_cli(
        state,
        "init",
        "--slots",
        1,
        "--policy",
        "reservation",
        "--reservation-ttl",
        60,
        "--partial-ttl",
        60,
    )

    old_witness = state / "old-witness.json"
    old_ticket = state / "old-ticket.json"
    candidate_witness = state / "candidate-witness.json"
    roundtrip(state, "old-line", old_witness)
    run_cli(state, "ask", "--witness", old_witness, "--segments", 3, "--ticket-out", old_ticket)
    run_cli(state, "write", "--ticket-file", old_ticket, "--witness", old_witness, "--index", 0, "--data", "P0;")
    run_cli(state, "write", "--ticket-file", old_ticket, "--witness", old_witness, "--index", 1, "--data", "P1")
    # 故意不写 index=2（最后一段），并再次以新进程查看。
    before_new_ask = run_cli(state, "inspect", "--json")
    slot = before_new_ask["slots"][0]
    check(slot["state"] == "writing" and slot["missing_indexes"] == [2], "柜内确有一件尚欠最后一段的半件")

    illegal_clear = run_cli(state, "clear-slot", "--slot", 1, "--operator", "anyone", expect=5)
    check(illegal_clear["code"] == "SLOT_NOT_RELEASABLE", "未交付且未过明确放弃期限时，不能清格释放")

    roundtrip(state, "candidate-line", candidate_witness)
    full = run_cli(
        state,
        "ask",
        "--witness",
        candidate_witness,
        "--segments",
        1,
        "--egress",
        "some-exit",
        expect=3,
    )
    check(full["code"] == "CABINET_FULL" and full["seat_held"] is False, "新单首问拿不到席位；拒绝不是‘允许再来试’式承诺")
    check(full["retry_allowed"] is True, "可以允许以后再试，但响应明确没有保住位置")

    not_yet = run_cli(state, "reap")
    check(not_yet["count"] == 0, "当前时间未过半件截止时间，reap 不释放它")

    after_deadline = run_cli(state, "reap", env_now=time.time() + 120)
    check(after_deadline["count"] == 1, "只有超过公开的半件保留期限后，才标记 abandoned 并释放格子")
    after = run_cli(state, "inspect", "--json")
    check(after["slots"][0]["state"] == "empty", "逾期清理后格子才为空；此前的阻塞已被演示")
    show(state)


def scenario_stateless_contrast(base: Path) -> None:
    heading("对照路径 B：首问只给核对过的凭单，不留逐人状态；首段到达才原子分格子")
    state = base / "onwrite"
    state.mkdir(parents=True)
    run_cli(state, "init", "--slots", 1, "--policy", "onwrite", "--witness-ttl", 3600)

    x_witness = state / "x-witness.json"
    x_voucher = state / "x-voucher.json"
    y_witness = state / "y-witness.json"
    y_voucher = state / "y-voucher.json"
    y_ticket = state / "y-ticket.json"
    roundtrip(state, "line-X", x_witness)
    roundtrip(state, "line-Y", y_witness)

    x_ask = run_cli(state, "ask", "--witness", x_witness, "--segments", 2, "--voucher-out", x_voucher)
    check(x_ask["seat_held"] is False and x_ask["promise"] == "none", "X 的首问只得到可重试凭单，没有席位承诺")

    y_ask = run_cli(state, "ask", "--witness", y_witness, "--segments", 2, "--voucher-out", y_voucher)
    check(y_ask["seat_held"] is False, "Y 的空问也不占格")

    y_write = run_cli(
        state,
        "write",
        "--voucher-file",
        y_voucher,
        "--witness",
        y_witness,
        "--index",
        0,
        "--data",
        "Y0",
        "--ticket-out",
        y_ticket,
    )
    check(y_write["allocated"] is True and y_write["slot"] == 1, "Y 的有效当前见证加首段正文原子抢到唯一格")

    y_retry = run_cli(
        state,
        "write",
        "--ticket-file",
        y_ticket,
        "--witness",
        y_witness,
        "--index",
        0,
        "--data",
        "Y0",
    )
    check(y_retry["duplicate"] is True, "Y 的重复首段幂等，不另开格")

    x_write = run_cli(
        state,
        "write",
        "--voucher-file",
        x_voucher,
        "--witness",
        x_witness,
        "--index",
        0,
        "--data",
        "X0",
        expect=3,
    )
    check(
        x_write["code"] == "FULL_AT_FIRST_SEGMENT" and x_write["seat_held"] is False,
        "X 虽早拿到见证/凭单，发送首段时仍可能得知客满；‘可以再试’不是保住席位",
    )

    status = run_cli(state, "inspect", "--json")
    check(len(status["orders"]) == 1 and status["counts"].get("writing") == 1, "柜中只有 Y 的一件，X 的空问没有留下逐人预约")
    show(state)


def main() -> int:
    demo_root = ROOT / "demo-run"
    if demo_root.exists():
        shutil.rmtree(demo_root)
    demo_root.mkdir(parents=True)

    print("本演示全部通过 python -m anon_station 子进程执行；每条命令结束后进程即退出。")
    print(f"本地文件目录：{demo_root}")

    scenario_two_terminals(demo_root)
    scenario_single_slot_channel(demo_root)
    scenario_half_item_not_released(demo_root)
    scenario_stateless_contrast(demo_root)

    print("\n" + "#" * 78)
    print(f"DEMO PASSED — {CHECKS} 项行为断言全部通过；状态保留在 {demo_root}")
    print("#" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
