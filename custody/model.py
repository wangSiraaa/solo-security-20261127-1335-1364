"""领域模型：合同、保管单、交接、义务求值结果、便携许可、证据、库日志。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


# ---------------------------------------------------------------- 合同与单据
@dataclass(frozen=True)
class Contract:
    """合同版本。default_term_days 是旧解释器补算空白期限时参照的合同期限。"""
    contract_id: str
    version: int
    effective_on: date
    default_term_days: Optional[int]


@dataclass(frozen=True)
class Handover:
    """已经发生的交接：签发 / 转交 / 续约。"""
    handover_id: str
    on: date
    kind: str  # issue | transfer | renewal
    from_party: Optional[str]
    to_party: str


@dataclass(frozen=True)
class CustodyOrder:
    """资料保管单。不可变；seal 覆盖除 seal 外全部字段，代表原始签章。"""
    order_id: str
    interpreter_version: str          # 签发时认可的解释版本 v1 | v2
    contract_id: str
    contract_version: int             # 签发时参照的合同版本（快照绑定）
    issued_on: date
    term_days: Optional[int]          # 显式期限；None 表示空白期限
    until_date: Optional[date]        # 显式到期日（新版直接使用日期）
    start_trigger: str                # issue：签发即起算；transfer：遇转交才起算
    text: str
    seal: str

    def sealing_payload(self) -> dict:
        return {
            "order_id": self.order_id,
            "interpreter_version": self.interpreter_version,
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "issued_on": self.issued_on.isoformat(),
            "term_days": self.term_days,
            "until_date": self.until_date.isoformat() if self.until_date else None,
            "start_trigger": self.start_trigger,
            "text": self.text,
        }


@dataclass(frozen=True)
class Obligation:
    """解释器对一份单在给定交接事实下求出的保管义务。"""
    order_id: str
    interpreter_version: str
    term_start: Optional[date]
    term_end: Optional[date]
    status: str   # determined | pending-transfer | blank-unresolved
    reason: str


# ---------------------------------------------------------------- 许可与证据
@dataclass(frozen=True)
class PermitOperation:
    """一条可离线消费的明示操作。

    op:
      retain-until       arg=绝对到期日（ISO）
      dispose-after      arg=最早处置日（ISO）
      accept-transfer-in arg=可表达公式，如 transfer_on+90D / absolute-until
    """
    op: str
    arg: Optional[str]
    conditions: tuple[str, ...]


@dataclass(frozen=True)
class PortablePermit:
    permit_id: str
    order_id: str
    order_seal: str                   # 与具体签章单据绑定，别单不得复用
    evidence_id: str
    issued_on: date
    operations: tuple[PermitOperation, ...]
    ratification_required: bool
    dropped_automation: tuple[str, ...]   # 签出时无法表达而舍弃的自动化
    scope_note: str
    station_seal: str

    def sealing_payload(self) -> dict:
        return {
            "permit_id": self.permit_id,
            "order_id": self.order_id,
            "order_seal": self.order_seal,
            "evidence_id": self.evidence_id,
            "issued_on": self.issued_on.isoformat(),
            "operations": [
                {"op": o.op, "arg": o.arg, "conditions": list(o.conditions)}
                for o in self.operations
            ],
            "ratification_required": self.ratification_required,
            "dropped_automation": list(self.dropped_automation),
            "scope_note": self.scope_note,
        }


@dataclass(frozen=True)
class EvidenceBundle:
    """双解释器并行求值的语义分歧证据，由受托站封存。"""
    evidence_id: str
    order_id: str
    created_on: date
    payload: dict
    station_seal: str


# ---------------------------------------------------------------- 接收库日志
@dataclass(frozen=True)
class StoreLogEntry:
    seq: int
    store_id: str
    on: date
    action: str                       # receive | transfer-in | dispose | refuse
    order_id: str
    detail: dict
    prev_hash: str
    entry_hash: str


@dataclass(frozen=True)
class Refusal:
    store_id: str
    order_id: str
    reason: str
    proposal_kind: str = "amendment"   # store-swap：换库；amendment：改约/追认


# ---------------------------------------------------------------- 追认与客户单据
@dataclass(frozen=True)
class RatificationCase:
    case_id: str
    order_id: str
    opened_on: date
    trigger: str
    evidence_id: str
    status: str                      # open | approved | denied
    resolution: Optional[str]


@dataclass(frozen=True)
class CustomerProposal:
    """客户只能拿到的两类单据：换库单 store-swap / 改约单 amendment。"""
    proposal_id: str
    order_id: str
    kind: str
    reason: str
    options: tuple[str, ...]
    status: str                     # issued | accepted | rejected
