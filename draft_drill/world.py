"""组装本地虚拟终端、核验器、借稿台和标准演练事实。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .clock import Clock
from .desk import LoanDesk
from .model import Draft, LoanGrant, OpenDenied, Order, VerificationResult
from .policy import PolicyStore, standard_policy
from .security import LocalHmacAuthority, SignedMessage
from .terminal import VirtualTerminal
from .verifier import StateVerifier


DEVICE_DRILL_KEY = b"drill-only-fictional-device-attestation-key"
DESK_DRILL_KEY = b"drill-only-local-loan-desk-key"


BASELINE_CLAIMS = {
    "native_font_code": "blocked",
    "shaper_sandbox": "enforced",
    "renderer_plugin_bridge": "disabled",
    "separated_screen_path": "enforced",
    "clipboard_export": "blocked",
}

UNAPPROVED_PAIR_CLAIMS = {
    "native_font_code": "dynamic_hints_native",
    "shaper_sandbox": "bypassed_by_hint_bridge",
    "renderer_plugin_bridge": "enabled_native_hint",
    "separated_screen_path": "enforced",
    "clipboard_export": "blocked",
}


@dataclass
class OpenOutcome:
    release: SignedMessage
    verification: VerificationResult
    inquiry: object
    proof: SignedMessage
    session: object
    display_text: str
    display_receipt: SignedMessage
    delivery: object


def standard_draft() -> Draft:
    body = "\n".join(
        [
            "【演练底稿·非真实出版物】",
            "夜航排字房密样第十四号",
            "一、三校已毕，允许在隔屏阅稿位开版；",
            "二、字模、渲染器与隔屏附件须按批准组合同时度量；",
            "三、断线时只可继续已钉住代次的页面，不得新开未取正文的订单。",
            "——底稿正文完——",
        ]
    )
    return Draft(
        manuscript_no="GAO-2026-014",
        title="夜航密样",
        version="v3",
        classification="restricted-draft",
        body=body,
    )


def standard_order() -> Order:
    return Order(
        order_id="ORD-2026-1127-014",
        manuscript_no="GAO-2026-014",
        terminal_id="VT-DRILL-014",
        purpose="夜班受限底稿排字",
        classification="restricted-draft",
        required_attachment="ATTACH-SCREEN@1.1.0",
    )


class DrillWorld:
    def __init__(self) -> None:
        self.clock = Clock()
        self.device_authority = LocalHmacAuthority(
            "fake-device-double", DEVICE_DRILL_KEY
        )
        self.desk_authority = LocalHmacAuthority("local-loan-desk", DESK_DRILL_KEY)
        self.policy: PolicyStore = standard_policy()
        self.verifier = StateVerifier(self.device_authority, self.policy, self.clock)
        self.terminal = VirtualTerminal(
            standard_order().terminal_id,
            self.device_authority,
            self.desk_authority.verify,
            self.clock,
        )
        self.desk = LoanDesk(self.desk_authority, self.verifier, self.clock)
        self.draft = standard_draft()
        self.order = standard_order()
        self.boot_receipt: Optional[SignedMessage] = None
        self.boot_result: Optional[VerificationResult] = None
        self.initial_grant: Optional[LoanGrant] = None
        self.initial_loan_result: Optional[VerificationResult] = None

    @classmethod
    def standard(cls) -> "DrillWorld":
        world = cls()
        world.desk.stock_draft(world.draft)
        world.desk.receive_order(world.order)
        world.boot_terminal_approved()
        return world

    def _coords(self, *coordinates: str):
        return [self.policy.catalog[coord] for coord in coordinates]

    def boot_terminal_approved(self) -> VerificationResult:
        modules = self._coords(
            "FONT-SONG@2.4.1",
            "RENDERER-PRESS@4.0.2",
            "ATTACH-SCREEN@1.1.0",
        )
        self.boot_receipt = self.terminal.boot(modules, dict(BASELINE_CLAIMS))
        self.boot_result = self.verifier.verify_boot_receipt(self.boot_receipt)
        if not self.boot_result.ok:
            raise RuntimeError(f"标准开机状态未通过核验：{self.boot_result.summary}")
        return self.boot_result

    def complete_loan(self) -> LoanGrant:
        inquiry = self.verifier.issue_inquiry(self.order, purpose="loan-request")
        proof = self.terminal.answer_inquiry(inquiry)
        result = self.verifier.verify_for_order(proof, inquiry, self.order)
        if not result.ok:
            raise OpenDenied(result)
        grant = self.desk.issue_grant(
            self.order,
            verification_id=result.verification_id or "VRF-?",
            state_generation=result.state_generation or "state-?",
            proof=proof,
        )
        self.initial_grant = grant
        self.initial_loan_result = result
        return grant

    def open_and_display(self, grant: Optional[LoanGrant] = None) -> OpenOutcome:
        grant = grant or self.initial_grant
        if grant is None:
            raise RuntimeError("尚未办成借稿，没有借据")
        release, verification, inquiry, proof = self.desk.open_draft(
            self.order.order_id, grant, self.terminal
        )
        session, display_text, receipt = self.terminal.display_release(release)
        delivery = self.desk.record_display(release, session, receipt)
        return OpenOutcome(
            release=release,
            verification=verification,
            inquiry=inquiry,
            proof=proof,
            session=session,
            display_text=display_text,
            display_receipt=receipt,
            delivery=delivery,
        )

    def swap_to_unapproved_pair(self):
        modules = self._coords(
            "FONT-KAI@9.0.0",
            "RENDERER-XIN@6.1.0",
            "ATTACH-SCREEN@1.1.0",
        )
        return self.terminal.load_components(modules, dict(UNAPPROVED_PAIR_CLAIMS))

    def restore_approved_combo(self, with_attachment: bool = True):
        coordinates = [
            "FONT-SONG@2.4.1",
            "RENDERER-PRESS@4.0.2",
        ]
        claims = dict(BASELINE_CLAIMS)
        if with_attachment:
            coordinates.append("ATTACH-SCREEN@1.1.0")
        else:
            claims["separated_screen_path"] = "unavailable"
        modules = self._coords(*coordinates)
        return self.terminal.load_components(modules, claims)

    def disconnect(self) -> None:
        self.desk.online = False

    def reconnect(self) -> None:
        self.desk.online = True
