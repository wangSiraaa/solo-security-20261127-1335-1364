"""Human-readable reports and shift records."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .controller import STATE_LABELS, boot_block_reason


def shifts_remaining(state: Dict[str, Any]) -> int:
    clock = state["clock"]
    return max(0, int(clock["deadline_shift"]) - int(clock["shift"]))


def conclusion(machine: Dict[str, Any], state: Dict[str, Any]) -> str:
    mid = machine["id"]
    status = machine.get("state")
    last = machine.get("last_boot") or {}
    if status == "gen6_committed":
        return f"{mid} 最终运行第 6 代；回旧版的桥已烧，最低代次={machine['min_boot_generation']}。"
    if status == "gen6_active_uncommitted":
        return f"{mid} 最近实际启动第 6 代且自检合格，但尚未烧桥；第 5 代仍可按条件回退。"
    if status == "rolled_back_gen5":
        return f"{mid} 最终实际运行第 5 代（失败后已恢复）；尚未烧桥。"
    if status == "gen5_active":
        return f"{mid} 最终实际运行第 5 代。"
    if status == "trial_awaiting_boot":
        return f"{mid} 第 6 代仅写入/切换到试启槽，尚无开机合格回执，不能算接班。"
    if status == "disabled_repair_required":
        reason = machine.get("disabled_reason", "无法原地恢复")
        return f"{mid} 已停用，没有系统在运行；原因：{reason}。"
    if status == "retired_stopped":
        return f"{mid} 到班点后停用：第 5 代被禁止启动，第 6 代没有最近合格记录可承诺。"
    return f"{mid} 状态 {status}；最近开机记录={last}。"


def format_overview(state: Dict[str, Any]) -> str:
    shift = state["clock"]["shift"]
    deadline = state["clock"]["deadline_shift"]
    lines = [
        "馆内终端本地更新控制器",
        f"逻辑班点: 第 {shift} 班 / 统一停用点: 第 {deadline} 班 / 剩余班数: {shifts_remaining(state)}",
        "",
        "机器号  名称  当前状态  现役槽/代次  试启槽  最低代次  桥  最近实际开机",
        "------  ----  --------  -----------  ------  --------  --  ------------",
    ]
    for mid, m in state["machines"].items():
        active = m["slots"].get(m["active_slot"], {})
        last = m.get("last_boot") or {}
        last_text = (
            f"{last.get('generation')}代/{last.get('result')}@班{last.get('shift')}"
            if last
            else "无记录"
        )
        bridge = "已烧" if m["bridge_burned"] else "未烧"
        lines.append(
            "  ".join(
                [
                    mid,
                    m["label"],
                    STATE_LABELS.get(m["state"], m["state"]),
                    f"{m['active_slot']}:{active.get('generation', '空')}代",
                    str(m.get("trial_slot") or "-"),
                    str(m["min_boot_generation"]),
                    bridge,
                    last_text,
                ]
            )
        )
    return "\n".join(lines)


def _event_result(event: Dict[str, Any]) -> str:
    kind = event["kind"]
    if kind.endswith("_pass") or kind == "rollback_recovery":
        return "pass"
    if kind.endswith("_fail"):
        return "fail"
    if kind == "boot_blocked":
        return "blocked"
    if kind == "bridge_burned":
        return "commit"
    if kind in ("install_rejected", "machine_disabled", "retired_without_new_commit"):
        return "stop"
    return "-"


def shift_rows(state: Dict[str, Any], machine_id: Optional[str] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for event in state.get("events", []):
        if machine_id and event.get("machine") != machine_id:
            continue
        details = event.get("details", {})
        rows.append(
            {
                "seq": event["seq"],
                "shift": event["shift"],
                "machine": event.get("machine") or "-",
                "kind": event["kind"],
                "generation": details.get("generation", ""),
                "result": _event_result(event),
                "message": event["message"],
            }
        )
    return rows


def format_shift_records(state: Dict[str, Any], machine_id: Optional[str] = None) -> str:
    rows = shift_rows(state, machine_id)
    lines = ["班  序号  机器  结果     代次  事件 / 说明", "--  ----  ----  -------  ----  ----------"]
    for r in rows:
        lines.append(
            f"{r['shift']:<2}  {r['seq']:<4}  {r['machine']:<4}  {r['result']:<7}  "
            f"{str(r['generation']):<4}  [{r['kind']}] {r['message']}"
        )
    return "\n".join(lines)


def format_machine_report(state: Dict[str, Any], machine_id: str) -> str:
    try:
        m = state["machines"][machine_id]
    except KeyError as exc:
        raise KeyError(f"未知机器号: {machine_id}") from exc

    shift = state["clock"]["shift"]
    lines: List[str] = []
    lines.append(f"# {machine_id}（{m['label']} / {m['serial']}）")
    lines.append("")
    lines.append(f"- 当前状态: **{STATE_LABELS.get(m['state'], m['state'])}** (`{m['state']}`)")
    lines.append(f"- 解释: {conclusion(m, state)}")
    lines.append(
        f"- 班点: 第 {shift} 班；距第 {state['clock']['deadline_shift']} 班停用点还有 "
        f"{shifts_remaining(state)} 个计数单位"
    )
    lines.append(
        f"- 启动槽: active={m['active_slot']}, trial={m.get('trial_slot') or '-'}, "
        f"rollback={m.get('rollback_slot') or '-'}"
    )
    for slot_name in sorted(m["slots"]):
        slot = m["slots"][slot_name]
        lines.append(
            f"  - {slot_name}: 第{slot.get('generation')}代, status={slot.get('status')}, "
            f"last_result={slot.get('last_result')}, set={str(slot.get('set_digest'))[:24]}…"
        )
    lines.append(
        f"- 最低可启动代次: {m['min_boot_generation']}（单调上升）；"
        f"回旧版桥: {'已烧毁/不可逆' if m['bridge_burned'] else '仍在'}"
    )
    old_gen = state["clock"]["retired_generation"]
    old_allowed = boot_block_reason(state, m, old_gen, shift) is None
    lines.append(f"- 第 {old_gen} 代在当前班点是否还能启动: {'是' if old_allowed else '否'}")
    lines.append(f"- 连续合格自检: {m.get('consecutive_good', 0)}")
    lines.append("- 有据可查的回退条件:")
    lines.append(f"  - {m['rollback_conditions']['human']}")

    if m.get("failures"):
        lines.append("- 已经见到的失败:")
        for f in m["failures"]:
            lines.append(
                f"  - 班{f['shift']} {f['slot']}槽/第{f['generation']}代 "
                f"回执 {f['receipt_id']}: {f.get('note') or '自检失败'}"
            )
    else:
        lines.append("- 已经见到的失败: 无")

    if m.get("decision"):
        d = m["decision"]
        lines.append("- 不可逆承诺记录:")
        lines.append(
            f"  - 班{d['shift']} 由 {d['by']} 执行；{d['old_min_boot_generation']}→"
            f"{d['new_min_boot_generation']}；理由: {d['reason']}"
        )

    if m.get("field_operations"):
        lines.append("- 现场操作:")
        for op in m["field_operations"]:
            lines.append(f"  - 班{op['shift']} {op['operator']}: {op['operation']}（{op['contract']}）")

    lines.append("")
    lines.append("## 历次试启动/恢复回执")
    if not m.get("receipts"):
        lines.append("- 无")
    for r in m["receipts"]:
        note = f"；{r['note']}" if r.get("note") else ""
        lines.append(
            f"- {r['receipt_id']} 班{r['shift']} {r['slot']}槽 第{r['generation']}代 "
            f"{r['kind']}={r['result']} (sig {r['signature'][:12]}…){note}"
        )

    lines.append("")
    lines.append("## 班次时间线")
    for row in shift_rows(state, machine_id):
        lines.append(f"- 班{row['shift']} #{row['seq']} [{row['kind']}] {row['message']}")
    lines.append("")
    return "\n".join(lines)


def format_all_markdown(state: Dict[str, Any]) -> str:
    parts = ["# 三台模拟机最终记录", "", "```text", format_overview(state), "```", ""]
    for mid in sorted(state["machines"]):
        parts.append(format_machine_report(state, mid))
    parts.append("## 全班次记录")
    parts.append("")
    parts.append("```text")
    parts.append(format_shift_records(state))
    parts.append("```")
    return "\n".join(parts)
