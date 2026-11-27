"""路线 B：旧解释器退场前，按对象签出可携带许可。

原则：
- 两版求值一致且日期绝对确定 -> 可离线消费的自动化操作；
- 两版分歧 -> 冻结自动化、生成追认案，只允许已绝对确定的留存；
- 触发条件在签出时无法表达 -> 舍弃那项自动化（dropped_automation），
  绝不依赖已退役字典事后补算。
"""
from __future__ import annotations

from .hashing import digest, station_seal
from .interpreters import LegacyInterpreter, NewInterpreter
from .model import (Contract, CustodyOrder, EvidenceBundle, Handover, Obligation,
                    PermitOperation, PortablePermit)

LEGACY = LegacyInterpreter()
NEW = NewInterpreter()


def evaluate_both(order: CustodyOrder, contracts: dict[tuple[str, int], Contract],
                  handovers: dict[str, list[Handover]], as_of) -> tuple[Obligation, Obligation]:
    contract = contracts[(order.contract_id, order.contract_version)]  # 签发时版本快照
    h = handovers.get(order.order_id, [])
    return LEGACY.evaluate(order, contract, h, as_of), NEW.evaluate(order, contract, h, as_of)


def _obligation_json(o: Obligation) -> dict:
    return {
        "order_id": o.order_id,
        "interpreter_version": o.interpreter_version,
        "term_start": o.term_start.isoformat() if o.term_start else None,
        "term_end": o.term_end.isoformat() if o.term_end else None,
        "status": o.status,
        "reason": o.reason,
    }


def seal_evidence(station_key: str, order: CustodyOrder, legacy: Obligation,
                  newer: Obligation, on) -> EvidenceBundle:
    payload = {
        "order_id": order.order_id,
        "order_seal": order.seal,
        "bound_interpreter": order.interpreter_version,
        "bound_contract": {"id": order.contract_id, "version": order.contract_version},
        "on": on.isoformat(),
        "legacy_v1": _obligation_json(legacy),
        "new_v2": _obligation_json(newer),
        "divergence": (legacy.term_end != newer.term_end
                       or legacy.status != newer.status),
    }
    eid = "EV-" + digest(payload)[:16]
    return EvidenceBundle(eid, order.order_id, on, payload,
                          station_seal(station_key, payload))


def _iso(d):
    return d.isoformat() if d else None


def sign_out(station_key: str, order: CustodyOrder,
             contracts: dict[tuple[str, int], Contract],
             handovers: dict[str, list[Handover]], as_of,
             seq: int) -> tuple[PortablePermit, EvidenceBundle]:
    legacy, newer = evaluate_both(order, contracts, handovers, as_of)
    evidence = seal_evidence(station_key, order, legacy, newer, as_of)
    divergent = (legacy.term_end != newer.term_end or legacy.status != newer.status)

    operations: list[PermitOperation] = []
    dropped: list[str] = []
    ratification = divergent

    if legacy.status == "determined":
        # 已确定的义务始终可以写绝对日期的留存/处置许可。
        # 分歧单只保留「留存到旧版义务日」，处置留给客户追认，不扩大处理范围。
        operations.append(PermitOperation("retain-until", _iso(legacy.term_end),
                                          ("义务按绑定解释版本与合同快照确定",)))
        if not divergent:
            operations.append(PermitOperation(
                "dispose-after", _iso(legacy.term_end),
                ("两版解释器求值一致", f"不早于 {_iso(legacy.term_end)}")))
        else:
            dropped.append("自动处置（两版对到期日存在分歧，留待客户追认）")
    elif legacy.status == "pending-transfer":
        # 转交才起算：转交未发生。只有起算公式在签出时能写明白，才允许自动化。
        if order.term_days is not None or order.until_date is not None:
            if order.until_date is not None:
                # 显式到期日与转交无关，属已确定
                operations.append(PermitOperation("retain-until", _iso(order.until_date),
                                                  ("显式到期日",)))
            else:
                # 条件式留存：留存义务成立，到期日随转交事实在库内派生
                operations.append(PermitOperation(
                    "retain-until", f"transfer_on+{order.term_days}D",
                    ("转交发生后按公式派生到期日，转交前持续留存",)))
                operations.append(PermitOperation(
                    "accept-transfer-in", f"transfer_on+{order.term_days}D",
                    ("收到带有效签章的转交事实后起算",)))
        else:
            # 空白期限 + 转交才起算：起算后的期限要靠旧字典查合同才能得出，
            # 签出时无法把该条件表达进许可 -> 舍弃自动化。
            dropped.append("转交后自动起算（空白期限无法在签出时表达，禁止退役字典补算）")
            ratification = True
    else:
        dropped.append("自动处置（空白期限新版无法解析）")
        ratification = True

    note = "范围不得超出原已批准处理；许可仅对本单据号+本签章有效"
    permit_payload = {
        "permit_id": f"PM-{seq:04d}",
        "order_id": order.order_id,
        "order_seal": order.seal,
        "evidence_id": evidence.evidence_id,
        "issued_on": as_of.isoformat(),
        "operations": [
            {"op": o.op, "arg": o.arg, "conditions": list(o.conditions)} for o in operations
        ],
        "ratification_required": ratification,
        "dropped_automation": dropped,
        "scope_note": note,
    }
    permit = PortablePermit(
        permit_id=permit_payload["permit_id"],
        order_id=order.order_id,
        order_seal=order.seal,
        evidence_id=evidence.evidence_id,
        issued_on=as_of,
        operations=tuple(operations),
        ratification_required=ratification,
        dropped_automation=tuple(dropped),
        scope_note=note,
        station_seal=station_seal(station_key, permit_payload),
    )
    return permit, evidence
