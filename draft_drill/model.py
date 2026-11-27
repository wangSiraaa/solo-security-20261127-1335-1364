"""演练系统的核心数据类型与异常。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .security import sha256_hex


class DrillError(Exception):
    """所有可预期演练错误的基类。"""


class DeskUnreachable(DrillError):
    """终端与借稿台/核验器之间短暂断线。"""


class FreshAttestationRequired(DrillError):
    """旧借据或环境回执不能充当本次开稿能力。"""


class DeliveryProofRequired(DrillError):
    """环境合格证明不能替代真正的正文释放和显示回执。"""


class OpenDenied(DrillError):
    def __init__(self, decision: "VerificationResult") -> None:
        self.decision = decision
        super().__init__(decision.summary)


@dataclass(frozen=True)
class Component:
    comp_id: str
    kind: str  # font / renderer / attachment
    version: str
    individually_approved: bool = True

    @property
    def coordinate(self) -> str:
        return f"{self.comp_id}@{self.version}"

    @property
    def measurement(self) -> str:
        # 演练中以坐标摘要代替真实文件度量；策略判断仍按精确版本和组合进行。
        return "sha256:" + sha256_hex(f"{self.kind}:{self.coordinate}")

    def manifest(self) -> dict[str, str]:
        return {
            "component_id": self.comp_id,
            "kind": self.kind,
            "version": self.version,
            "coordinate": self.coordinate,
            "measurement": self.measurement,
        }


@dataclass(frozen=True)
class Draft:
    manuscript_no: str
    title: str
    version: str
    classification: str
    body: str
    content_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "content_sha256", sha256_hex(self.body))


@dataclass(frozen=True)
class Order:
    order_id: str
    manuscript_no: str
    terminal_id: str
    purpose: str
    classification: str
    required_attachment: str
    reference_label: str = "初始订单"


@dataclass
class Inquiry:
    inquiry_id: str
    nonce: str
    order_id: str
    manuscript_no: str
    terminal_id: str
    purpose: str
    issued_tick: int
    consumed: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    ok: bool
    code: str
    summary: str
    reasons: tuple[str, ...] = ()
    combination_id: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationResult:
    ok: bool
    code: str
    summary: str
    inquiry_id: str
    verification_id: Optional[str] = None
    reasons: list[str] = field(default_factory=list)
    combination_id: Optional[str] = None
    state_generation: Optional[str] = None
    module_digest: Optional[str] = None
    proof_authority: Optional[str] = None


@dataclass
class ReadingSession:
    session_id: str
    order_id: str
    manuscript_no: str
    displayed_version: str
    content_sha256: str
    state_generation: str
    verification_id: str
    combination_id: str
    display_text: str
    opened_tick: int


@dataclass(frozen=True)
class DeliveryRecord:
    seq: int
    delivery_id: str
    order_id: str
    manuscript_no: str
    displayed_version: str
    content_sha256: str
    state_generation: str
    verification_id: str
    combination_id: str
    terminal_id: str
    session_id: str
    tick: int


@dataclass
class LoanGrant:
    """一次已获准组合办成的借稿凭据。

    它只证明“可以在以后请求开稿”，不是正文，也不是离线开稿密钥。
    """

    token: Any
    proof: Any
    verification_id: str
    state_generation: str
