"""Core persistent state machine for terminal slot updates."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import PowerCut, StateError, VerificationError
from .packages import VerifiedPackage, verify_package
from .security import sign_payload
from .store import Store, add_event, new_device_secret, utc_now


DEADLINE_SHIFT = 3
RETIRED_GENERATION = 5
REQUIRED_GOOD_RECEIPTS = 2

GEN5_FILES: Dict[str, bytes] = {
    "system/core.bin": (
        "Museum terminal core, generation 5\n"
        "field-operation-contract: field-ops-v5\n"
        "rollback-slot: a\n"
    ).encode("utf-8"),
    "components/reader.pack": b"gen5 reader component\n",
    "components/index.pack": b"gen5 index component\n",
    "components/fonts.pack": b"gen5 font component\n",
}

MACHINES = [
    ("M1", "甲机", "JIA-0001"),
    ("M2", "乙机", "YI-0002"),
    ("M3", "丙机", "BING-0003"),
]

STATE_LABELS = {
    "gen5_active": "第五代运行中（未烧桥，可回退）",
    "trial_awaiting_boot": "第六代已切为试启槽，等待开机自检",
    "gen6_active_uncommitted": "第六代已接班但未烧桥；后续失败仍可回退第五代",
    "gen6_committed": "第六代已永久承诺；最低代次已提高，不能回第五代",
    "rolled_back_gen5": "第六代自检失败，已原地恢复到第五代",
    "disabled_repair_required": "已停用：无法原地恢复，需本地修复",
    "retired_stopped": "已到统一停用班点：第五代禁止启动，且无合格第六代可承诺",
}


def _machine(state: Dict[str, Any], machine_id: str) -> Dict[str, Any]:
    try:
        return state["machines"][machine_id]
    except KeyError as exc:
        raise StateError(f"未知机器号: {machine_id}") from exc


def boot_block_reason(
    state: Dict[str, Any],
    machine: Dict[str, Any],
    generation: int,
    shift: Optional[int] = None,
) -> Optional[str]:
    shift = int(state["clock"]["shift"] if shift is None else shift)
    floor = int(machine["min_boot_generation"])
    if generation < floor:
        return f"最低可启动代次为 {floor}，第 {generation} 代被单调防回滚策略拒绝"
    deadline = int(state["clock"]["deadline_shift"])
    retired = int(state["clock"]["retired_generation"])
    if generation <= retired and shift >= deadline:
        return f"第 {retired} 代已在第 {deadline} 班后统一停用（当前班点 {shift}）"
    return None


def _new_receipt(
    store: Store,
    state: Dict[str, Any],
    machine: Dict[str, Any],
    *,
    slot: str,
    result: str,
    kind: str,
    order_id: Optional[str] = None,
    note: str = "",
    checks: Optional[List[Dict[str, str]]] = None,
    shift: Optional[int] = None,
) -> Dict[str, Any]:
    receipt_no = int(state.get("next_receipt_seq", 1))
    state["next_receipt_seq"] = receipt_no + 1
    slot_info = machine["slots"][slot]
    if checks is None:
        if result == "pass":
            checks = [
                {"name": "hash-tree", "result": "pass"},
                {"name": "peripheral-probe", "result": "pass"},
                {"name": "archive-service", "result": "pass"},
            ]
        else:
            checks = [
                {"name": "hash-tree", "result": "pass"},
                {"name": "peripheral-probe", "result": "fail"},
                {"name": "archive-service", "result": "not-run"},
            ]
    payload: Dict[str, Any] = {
        "schema": "museum-boot-receipt/v1",
        "receipt_id": f"R-{machine['id']}-{receipt_no:04d}",
        "machine": machine["id"],
        "device_serial": machine["serial"],
        "shift": int(state["clock"]["shift"] if shift is None else shift),
        "slot": slot,
        "generation": int(slot_info["generation"]),
        "order_id": order_id or slot_info.get("order_id"),
        "package_set_digest": slot_info.get("set_digest"),
        "kind": kind,
        "result": result,
        "note": note,
        "checks": checks,
    }
    payload["signature"] = sign_payload(machine["device_secret"], payload)
    machine.setdefault("receipts", []).append(payload)
    return payload


def verify_external_receipt(
    state: Dict[str, Any], machine_id: str, receipt: Dict[str, Any]
) -> None:
    from .security import verify_signature

    machine = _machine(state, machine_id)
    if receipt.get("machine") != machine_id:
        raise VerificationError("回执机器号与目标机不一致")
    if receipt.get("device_serial") != machine.get("serial"):
        raise VerificationError("回执设备序列号不一致")
    if not verify_signature(machine["device_secret"], receipt):
        raise VerificationError("回执设备签名无效")
    slot = receipt.get("slot")
    if slot not in machine["slots"]:
        raise VerificationError("回执指向未知启动槽")
    if receipt.get("generation") != machine["slots"][slot].get("generation"):
        raise VerificationError("回执代次与槽内实际代次不一致")
    if receipt.get("package_set_digest") != machine["slots"][slot].get("set_digest"):
        raise VerificationError("回执整套摘要与槽内材料不一致")


def initialize_state(
    store: Store,
    *,
    force: bool = False,
    deadline_shift: int = DEADLINE_SHIFT,
    package_dir: str | Path | None = None,
) -> Dict[str, Any]:
    if store.exists() and not force:
        raise StateError(f"状态已存在: {store.state_path}（如要重放请使用 --force）")
    store.ensure_dirs()
    if force:
        shutil.rmtree(store.slots_root, ignore_errors=True)
        store.ensure_dirs()

    root = Path(__file__).resolve().parent.parent
    package_dir = Path(package_dir) if package_dir else root / "packages" / "gen6"
    approved = verify_package(package_dir)

    state: Dict[str, Any] = {
        "schema": "museum-controller-state/v1",
        "clock": {
            "shift": 0,
            "deadline_shift": deadline_shift,
            "retired_generation": RETIRED_GENERATION,
            "note": "起点：甲机第六代刚通过第一次试启；第五代还有三个班",
        },
        "next_event_seq": 1,
        "next_receipt_seq": 1,
        "machines": {},
        "orders": {},
        "created_at_shift": 0,
    }

    for machine_id, label, serial in MACHINES:
        machine: Dict[str, Any] = {
            "id": machine_id,
            "label": label,
            "serial": serial,
            "device_secret": new_device_secret(),
            "active_slot": "a",
            "trial_slot": None,
            "rollback_slot": "a",
            "slots": {},
            "min_boot_generation": 5,
            "bridge_burned": False,
            "committed_generation": None,
            "state": "gen5_active",
            "consecutive_good": 0,
            "failures": [],
            "receipts": [],
            "field_operations": [],
            "decision": None,
            "last_boot": None,
            "rollback_conditions": {
                "old_slot": "a",
                "old_generation": 5,
                "old_slot_must_be": "good",
                "floor_must_allow_generation": 5,
                "before_shift": deadline_shift,
                "contract": "rollback-if-old-slot-good-and-anti-rollback-floor-allows",
                "human": (
                    "仅当 a 槽第五代摘要完好、自检为 good、最低代次 <=5，"
                    "且未到第 "
                    f"{deadline_shift} 班停用点时，才允许原地回退。"
                ),
            },
        }

        gen5_digests: Dict[str, str] = {}
        for rel_path, data in GEN5_FILES.items():
            gen5_digests[rel_path] = store.write_bytes_to_slot(machine_id, "a", rel_path, data)
        from .security import set_digest

        machine["slots"]["a"] = {
            "generation": 5,
            "status": "good",
            "approved": True,
            "order_id": "FACTORY-GEN5",
            "file_digests": gen5_digests,
            "set_digest": set_digest(gen5_digests),
            "written_shift": None,
            "last_result": "pass",
        }
        state["machines"][machine_id] = machine

    # Seed the exact narrative starting point: M1 already has gen6 in b and
    # one successful trial receipt, but the bridge has not been burned.
    m1 = state["machines"]["M1"]
    copied = store.copy_verified_payload("M1", "b", approved.payload_root, list(approved.file_digests))
    if copied != approved.file_digests:
        raise StateError("初始化甲机第六代槽后摘要复核失败")
    m1["slots"]["b"] = {
        "generation": 6,
        "status": "good",
        "approved": True,
        "order_id": approved.order_id,
        "file_digests": copied,
        "set_digest": approved.set_digest,
        "components": approved.components,
        "written_shift": 0,
        "last_result": "pass",
    }
    m1["active_slot"] = "b"
    m1["rollback_slot"] = "a"
    m1["state"] = "gen6_active_uncommitted"
    m1["consecutive_good"] = 1
    receipt = _new_receipt(
        store,
        state,
        m1,
        slot="b",
        result="pass",
        kind="trial",
        order_id=approved.order_id,
        note="起点前已见到的甲机第一次试启合格回执",
        shift=0,
    )
    m1["last_boot"] = {
        "shift": 0,
        "slot": "b",
        "generation": 6,
        "result": "pass",
        "receipt_id": receipt["receipt_id"],
    }
    add_event(
        store,
        state,
        "seed",
        "初始化同一起点：甲机第六代已通过第一次试启但未烧桥；乙、丙机运行第五代。",
    )
    add_event(
        store,
        state,
        "trial_pass",
        "甲机第六代第一次试启自检通过，已登记接班；回旧版的桥仍在。",
        machine_id="M1",
        receipt_id=receipt["receipt_id"],
        generation=6,
    )
    store.save(state)
    return state


def _power_cut_after(when: Optional[str], expected: str) -> None:
    if when == expected:
        raise PowerCut(expected)


def install_order(
    store: Store,
    state: Dict[str, Any],
    order: Dict[str, Any],
    *,
    base_dir: Path | None = None,
    power_loss_after: Optional[str] = None,
) -> Dict[str, Any]:
    order_id = str(order.get("order_id") or "")
    machine_id = str(order.get("machine") or "")
    package_value = order.get("package")
    if not order_id or not machine_id or not package_value:
        raise StateError("安装单必须包含 order_id、machine、package")
    machine = _machine(state, machine_id)
    when = power_loss_after or order.get("power_loss_after")

    package_path = Path(str(package_value))
    if not package_path.is_absolute() and base_dir is not None:
        package_path = base_dir / package_path

    try:
        approved = verify_package(package_path)
    except VerificationError as exc:
        orders = state.setdefault("orders", {})
        rejection = {
            "at": utc_now(),
            "package": str(package_path),
            "error": str(exc),
        }
        if order_id in orders:
            orders[order_id].setdefault("rejected_attempts", []).append(rejection)
        else:
            orders[order_id] = {
                "id": order_id,
                "machine": machine_id,
                "package": str(package_path),
                "phase": "rejected",
                "status": "rejected",
                "error": str(exc),
            }
        add_event(
            store,
            state,
            "install_rejected",
            f"安装单 {order_id} 在核对阶段停止，未写槽、未切换：{exc}",
            machine_id=machine_id,
            order_id=order_id,
        )
        store.save(state)
        raise

    package_order_id = order.get("package_order_id")
    if package_order_id and package_order_id != approved.order_id:
        raise VerificationError(
            f"安装单引用的发布单号 {package_order_id} 与批准单号 {approved.order_id} 不一致"
        )

    target_slot = str(order.get("target_slot", "b"))
    if target_slot != "b":
        raise StateError("本模拟流程只允许写入非现役 b 槽，以保护第五代回退槽")
    if target_slot == machine.get("rollback_slot") and not machine.get("bridge_burned"):
        raise StateError("不能覆盖仍承担原地回退条件的槽位")
    if machine.get("trial_slot"):
        raise StateError(
            f"{machine_id} 已有等待开机的试启槽 {machine['trial_slot']}；请先开机并取得回执"
        )

    order_record = {
        "id": order_id,
        "machine": machine_id,
        "package": str(package_path),
        "generation": approved.generation,
        "package_set_digest": approved.set_digest,
        "file_digests": approved.file_digests,
        "phase": "verified",
        "status": "in_progress",
        "power_loss_after": when,
    }
    state["orders"][order_id] = order_record
    add_event(
        store,
        state,
        "install_verified",
        f"安装单 {order_id} 的字节、代次、组件清单和批准摘要核对通过。",
        machine_id=machine_id,
        order_id=order_id,
        package_set_digest=approved.set_digest,
    )
    store.save(state)
    _power_cut_after(when, "verified")

    copied = store.copy_verified_payload(
        machine_id, target_slot, approved.payload_root, list(approved.file_digests)
    )
    if copied != approved.file_digests:
        raise StateError("写槽后复核摘要不一致；安装停止")
    machine["slots"][target_slot] = {
        "generation": approved.generation,
        "status": "written",
        "approved": True,
        "order_id": order_id,
        "file_digests": copied,
        "set_digest": approved.set_digest,
        "components": approved.components,
        "written_shift": state["clock"]["shift"],
        "last_result": None,
    }
    order_record["phase"] = "written"
    add_event(
        store,
        state,
        "slot_written",
        f"第 {approved.generation} 代已写入 {target_slot} 槽并逐文件复核；写文件不等于接班。",
        machine_id=machine_id,
        order_id=order_id,
        slot=target_slot,
    )
    store.save(state)
    _power_cut_after(when, "written")

    machine["trial_slot"] = target_slot
    machine["state"] = "trial_awaiting_boot"
    order_record["phase"] = "switched"
    add_event(
        store,
        state,
        "slot_switched",
        f"启动选择器已设为 {target_slot} 槽一次性试启；尚需开机自检回执。",
        machine_id=machine_id,
        order_id=order_id,
        slot=target_slot,
    )
    store.save(state)
    _power_cut_after(when, "switched")

    order_record["phase"] = "awaiting_boot"
    order_record["status"] = "awaiting_boot"
    store.save(state)
    return order_record


def resume_pending(
    store: Store, state: Dict[str, Any], machine_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    resumed: List[Dict[str, Any]] = []
    for order_id, order in list(state.get("orders", {}).items()):
        if machine_id and order.get("machine") != machine_id:
            continue
        if order.get("status") not in ("in_progress", "awaiting_boot"):
            continue
        mid = order["machine"]
        machine = _machine(state, mid)
        phase = order.get("phase")

        if phase == "verified":
            approved = verify_package(order["package"])
            copied = store.copy_verified_payload(
                mid, "b", approved.payload_root, list(approved.file_digests)
            )
            machine["slots"]["b"] = {
                "generation": approved.generation,
                "status": "written",
                "approved": True,
                "order_id": order_id,
                "file_digests": copied,
                "set_digest": approved.set_digest,
                "components": approved.components,
                "written_shift": state["clock"]["shift"],
                "last_result": None,
            }
            order["phase"] = "written"
            add_event(store, state, "resume_write", "掉电恢复：根据持久安装单补做写槽。", machine_id=mid, order_id=order_id)
            phase = "written"

        if phase == "written":
            slot = machine["slots"].get("b", {})
            if slot.get("set_digest") != order.get("package_set_digest"):
                raise StateError(f"恢复前发现 b 槽摘要与持久安装单不符: {mid}")
            machine["trial_slot"] = "b"
            machine["state"] = "trial_awaiting_boot"
            order["phase"] = "switched"
            add_event(store, state, "resume_switch", "掉电恢复：根据持久记录重新选择 b 试启槽。", machine_id=mid, order_id=order_id)
            phase = "switched"

        if phase == "switched":
            order["phase"] = "awaiting_boot"
            order["status"] = "awaiting_boot"
            add_event(store, state, "resume_await_boot", "掉电恢复完成：安装单等待开机自检。", machine_id=mid, order_id=order_id)
        elif phase == "awaiting_boot":
            add_event(store, state, "resume_idle", "持久记录显示安装已完成切换，继续等待开机。", machine_id=mid, order_id=order_id)

        resumed.append(order)
    store.save(state)
    return resumed


def _can_rollback(
    state: Dict[str, Any], machine: Dict[str, Any], failed_slot: str, shift: int
) -> tuple[bool, str, Optional[str]]:
    old_slot = machine.get("rollback_slot") or ("a" if failed_slot == "b" else "b")
    if old_slot == failed_slot:
        return False, "没有不同于故障槽的回退槽", None
    old = machine["slots"].get(old_slot)
    if not old:
        return False, f"回退槽 {old_slot} 不存在", old_slot
    if old.get("status") != "good" or old.get("last_result") == "fail":
        return False, f"回退槽 {old_slot} 未记录为 good", old_slot
    old_gen = int(old["generation"])
    if int(machine["min_boot_generation"]) > old_gen:
        return False, f"最低代次 {machine['min_boot_generation']} 已高于旧槽第 {old_gen} 代", old_slot
    reason = boot_block_reason(state, machine, old_gen, shift)
    if reason:
        return False, reason, old_slot
    return True, "旧槽完好且防回滚地板/停用班点仍允许旧代次", old_slot


def boot_machine(
    store: Store,
    state: Dict[str, Any],
    machine_id: str,
    result: str = "pass",
    *,
    shift: Optional[int] = None,
    note: str = "",
    receipt_data: Optional[Dict[str, Any]] = None,
    slot: Optional[str] = None,
) -> Dict[str, Any]:
    if result not in ("pass", "fail"):
        raise StateError("开机结果只能是 pass 或 fail")
    machine = _machine(state, machine_id)
    shift = int(state["clock"]["shift"] if shift is None else shift)

    if receipt_data is not None:
        verify_external_receipt(state, machine_id, receipt_data)
        result = receipt_data["result"]
        receipt = receipt_data
        machine.setdefault("receipts", []).append(receipt)
    else:
        if slot is not None and slot not in machine["slots"]:
            raise StateError(f"{machine_id} 没有 {slot} 启动槽")
        boot_slot = slot or machine.get("trial_slot") or machine["active_slot"]
        receipt = _new_receipt(
            store,
            state,
            machine,
            slot=boot_slot,
            result=result,
            kind="boot_self_check",
            note=note,
            shift=shift,
        )

    boot_slot = receipt["slot"]
    generation = int(receipt["generation"])
    slot_info = machine["slots"][boot_slot]
    block_reason = boot_block_reason(state, machine, generation, shift)
    if block_reason:
        machine["last_boot"] = {
            "shift": shift,
            "slot": boot_slot,
            "generation": generation,
            "result": "blocked",
            "receipt_id": receipt["receipt_id"],
            "reason": block_reason,
        }
        machine["state"] = "retired_stopped" if generation <= 5 else "disabled_repair_required"
        add_event(
            store,
            state,
            "boot_blocked",
            f"开机被启动策略拒绝：{block_reason}。",
            machine_id=machine_id,
            receipt_id=receipt["receipt_id"],
        )
        store.save(state)
        return {"blocked": True, "reason": block_reason, "receipt": receipt}

    machine["last_boot"] = {
        "shift": shift,
        "slot": boot_slot,
        "generation": generation,
        "result": result,
        "receipt_id": receipt["receipt_id"],
    }

    if result == "pass":
        slot_info["status"] = "good"
        slot_info["last_result"] = "pass"
        slot_info["last_self_check_shift"] = shift
        machine["consecutive_good"] = int(machine.get("consecutive_good", 0)) + 1
        add_event(
            store,
            state,
            "boot_pass",
            f"第 {generation} 代在 {boot_slot} 槽自检通过（连续合格 {machine['consecutive_good']} 次）。",
            machine_id=machine_id,
            receipt_id=receipt["receipt_id"],
            generation=generation,
            slot=boot_slot,
        )
        if boot_slot != machine["active_slot"]:
            old_active = machine["active_slot"]
            machine["rollback_slot"] = old_active
            machine["active_slot"] = boot_slot
            machine["trial_slot"] = None
            if not machine["bridge_burned"]:
                machine["state"] = "gen6_active_uncommitted" if generation >= 6 else "gen5_active"
            add_event(
                store,
                state,
                "takeover_registered",
                f"非现役 {boot_slot} 槽开机合格，已登记第 {generation} 代接班；旧 {old_active} 槽保留为原地回退。",
                machine_id=machine_id,
                generation=generation,
                rollback_slot=old_active,
            )
            order_id = receipt.get("order_id")
            if order_id and order_id in state.get("orders", {}):
                state["orders"][order_id].update(
                    {
                        "status": "completed",
                        "phase": "takeover_registered",
                        "completed_shift": shift,
                    }
                )
        elif machine["bridge_burned"]:
            machine["state"] = "gen6_committed"
        else:
            machine["state"] = "gen6_active_uncommitted" if generation >= 6 else "gen5_active"
        store.save(state)
        return {"blocked": False, "rolled_back": False, "receipt": receipt}

    # Failed self-check.
    slot_info["status"] = "failed"
    slot_info["last_result"] = "fail"
    slot_info["last_self_check_shift"] = shift
    machine["consecutive_good"] = 0
    failure = {
        "shift": shift,
        "slot": boot_slot,
        "generation": generation,
        "receipt_id": receipt["receipt_id"],
        "note": note,
    }
    machine.setdefault("failures", []).append(failure)
    add_event(
        store,
        state,
        "boot_fail",
        f"第 {generation} 代在 {boot_slot} 槽自检失败；材料合格不等于可以登记接班。",
        machine_id=machine_id,
        receipt_id=receipt["receipt_id"],
        generation=generation,
        slot=boot_slot,
    )

    allowed, why, old_slot = _can_rollback(state, machine, boot_slot, shift)
    if allowed and old_slot is not None:
        machine["trial_slot"] = None
        machine["active_slot"] = old_slot
        old_gen = int(machine["slots"][old_slot]["generation"])
        recovery_receipt = _new_receipt(
            store,
            state,
            machine,
            slot=old_slot,
            result="pass",
            kind="recovery_self_check",
            order_id="RECOVERY",
            note="控制器切回旧槽后的恢复自检",
            shift=shift,
        )
        machine["slots"][old_slot]["status"] = "good"
        machine["slots"][old_slot]["last_result"] = "pass"
        machine["last_boot"] = {
            "shift": shift,
            "slot": old_slot,
            "generation": old_gen,
            "result": "pass",
            "receipt_id": recovery_receipt["receipt_id"],
            "recovered_from_receipt": receipt["receipt_id"],
        }
        machine["state"] = "rolled_back_gen5" if old_gen == 5 else "gen5_active"
        add_event(
            store,
            state,
            "rollback_recovery",
            f"满足有据可查的回退条件：已切回 {old_slot} 槽第 {old_gen} 代，恢复自检通过，服务保留。",
            machine_id=machine_id,
            receipt_id=recovery_receipt["receipt_id"],
            failed_receipt_id=receipt["receipt_id"],
            reason=why,
        )
        failed_order_id = receipt.get("order_id")
        if failed_order_id and failed_order_id in state.get("orders", {}):
            state["orders"][failed_order_id].update(
                {
                    "status": "trial_failed_rolled_back",
                    "phase": "rolled_back",
                    "resolved_shift": shift,
                }
            )
        store.save(state)
        return {"blocked": False, "rolled_back": True, "receipt": receipt, "recovery": recovery_receipt}

    machine["trial_slot"] = None
    machine["state"] = "disabled_repair_required"
    machine["disabled_reason"] = why
    add_event(
        store,
        state,
        "machine_disabled",
        f"不能烧掉回旧版的桥后再找桥：无法原地恢复，机器停用待本地修复。原因：{why}",
        machine_id=machine_id,
        failed_receipt_id=receipt["receipt_id"],
        reason=why,
    )
    failed_order_id = receipt.get("order_id")
    if failed_order_id and failed_order_id in state.get("orders", {}):
        state["orders"][failed_order_id].update(
            {
                "status": "trial_failed_disabled",
                "phase": "disabled",
                "resolved_shift": shift,
            }
        )
    store.save(state)
    return {"blocked": False, "rolled_back": False, "disabled": True, "reason": why, "receipt": receipt}


def burn_bridge(
    store: Store,
    state: Dict[str, Any],
    machine_id: str,
    *,
    by: str,
    reason: str,
    required_good_receipts: int = REQUIRED_GOOD_RECEIPTS,
    accept_early_risk: bool = False,
    deadline_promotion: bool = False,
) -> Dict[str, Any]:
    machine = _machine(state, machine_id)
    if machine.get("bridge_burned"):
        return machine["decision"]

    active_slot = machine["active_slot"]
    slot = machine["slots"].get(active_slot, {})
    generation = int(slot.get("generation", 0))
    good_receipts = [
        r
        for r in machine.get("receipts", [])
        if r.get("result") == "pass" and r.get("generation") == generation
    ]
    if not good_receipts:
        raise StateError(f"{machine_id} 没有第 {generation} 代的合格真机回执，不能烧桥")
    if machine.get("last_boot", {}).get("result") == "fail":
        raise StateError(f"{machine_id} 最近一次开机为失败，不能作出不可逆承诺")
    consecutive = int(machine.get("consecutive_good", 0))
    early = consecutive < required_good_receipts
    if early and not accept_early_risk and not deadline_promotion:
        raise StateError(
            f"{machine_id} 仅有连续 {consecutive} 次合格回执，策略建议 {required_good_receipts} 次；"
            "如维护员仍决定提前烧桥，请显式提供 --accept-early-risk 并记录理由"
        )

    old_floor = int(machine["min_boot_generation"])
    if generation < old_floor:
        raise StateError("不能把最低代次向后调")
    machine["min_boot_generation"] = generation
    machine["bridge_burned"] = True
    machine["committed_generation"] = generation
    decision = {
        "machine": machine_id,
        "shift": state["clock"]["shift"],
        "at": utc_now(),
        "by": by,
        "reason": reason,
        "old_min_boot_generation": old_floor,
        "new_min_boot_generation": generation,
        "good_receipt_count": len(good_receipts),
        "consecutive_good": consecutive,
        "early_risk_accepted": bool(early and accept_early_risk),
        "deadline_promotion": deadline_promotion,
        "irreversible": True,
    }
    machine["decision"] = decision
    machine["state"] = "gen6_committed"
    add_event(
        store,
        state,
        "bridge_burned",
        f"不可逆承诺：最低可启动代次 {old_floor}→{generation}；此后第 {old_floor} 代启动会被拒绝。",
        machine_id=machine_id,
        **{k: v for k, v in decision.items() if k != "machine"},
    )
    store.save(state)
    return decision


def advance_shift(store: Store, state: Dict[str, Any], shifts: int = 1) -> int:
    if shifts < 1:
        raise StateError("advance-shift 至少前进 1 班")
    for _ in range(shifts):
        state["clock"]["shift"] = int(state["clock"]["shift"]) + 1
        shift = state["clock"]["shift"]
        deadline = int(state["clock"]["deadline_shift"])
        if shift == deadline:
            add_event(
                store,
                state,
                "deadline_reached",
                f"第 {shift} 班停用点已到：第 {state['clock']['retired_generation']} 代开机将被拒绝；"
                "请对合格第六代执行 reconcile/commit，不能让其一直停在试装。",
            )
        else:
            add_event(store, state, "shift_advanced", f"进入第 {shift} 班。")
    store.save(state)
    return int(state["clock"]["shift"])


def reconcile_deadline(
    store: Store, state: Dict[str, Any], *, by: str = "deadline-policy"
) -> List[Dict[str, Any]]:
    shift = int(state["clock"]["shift"])
    deadline = int(state["clock"]["deadline_shift"])
    results: List[Dict[str, Any]] = []
    if shift < deadline:
        raise StateError(f"尚未到停用班点：当前第 {shift} 班，停用点第 {deadline} 班")

    for machine_id, machine in state["machines"].items():
        if machine.get("bridge_burned"):
            results.append({"machine": machine_id, "action": "already_committed"})
            continue

        active_slot = machine["active_slot"]
        active = machine["slots"].get(active_slot, {})
        healthy_new = (
            int(active.get("generation", 0)) >= 6
            and active.get("approved") is True
            and active.get("last_result") == "pass"
            and machine.get("last_boot", {}).get("result") == "pass"
        )
        if healthy_new:
            decision = burn_bridge(
                store,
                state,
                machine_id,
                by=by,
                reason="到班点后，获准且最近自检合格的新系统不得继续停在试装，策略自动作出代次承诺",
                deadline_promotion=True,
            )
            results.append({"machine": machine_id, "action": "deadline_commit", "decision": decision})
            continue

        if machine.get("state") == "disabled_repair_required":
            results.append({"machine": machine_id, "action": "remains_disabled"})
            continue

        machine["state"] = "retired_stopped"
        machine["retirement_reason"] = (
            "第五代已到统一停用班点；当前没有最近自检合格的第六代可自动承诺"
        )
        add_event(
            store,
            state,
            "retired_without_new_commit",
            "届期后拒绝启动旧版；又无最近合格的第六代，登记为停用待修，而不是继续试装。",
            machine_id=machine_id,
        )
        results.append({"machine": machine_id, "action": "retired_stopped"})
    store.save(state)
    return results


def field_operation(
    store: Store,
    state: Dict[str, Any],
    machine_id: str,
    operation: str,
    *,
    operator: str,
) -> Dict[str, Any]:
    machine = _machine(state, machine_id)
    shift = int(state["clock"]["shift"])
    last_boot = machine.get("last_boot") or {}
    generation = last_boot.get("generation", machine["slots"][machine["active_slot"]]["generation"])
    running_result = last_boot.get("result")
    if machine.get("state") in ("disabled_repair_required", "retired_stopped") or running_result in (
        "fail",
        "blocked",
    ):
        raise StateError(f"{machine_id} 当前未实际运行，不能接受现场操作: {machine.get('state')}")
    reason = boot_block_reason(state, machine, int(generation), shift)
    if reason:
        raise StateError(reason)
    contract = "field-ops-v5" if int(generation) == 5 else "field-ops-v6"
    record = {
        "shift": shift,
        "at": utc_now(),
        "operator": operator,
        "operation": operation,
        "generation": generation,
        "contract": contract,
        "accepted": True,
    }
    machine.setdefault("field_operations", []).append(record)
    add_event(
        store,
        state,
        "field_operation",
        f"按旧合同 {contract} 接受现场操作：{operation}",
        machine_id=machine_id,
        **record,
    )
    store.save(state)
    return record
