"""本地虚拟阅稿终端和只能出具虚构证明的设备替身。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .clock import Clock
from .model import Component, DrillError, Inquiry, ReadingSession
from .security import (
    FICTIONAL_ATTESTATION_CLASS,
    LocalHmacAuthority,
    SignedMessage,
    payload_digest,
    sha256_hex,
)


@dataclass
class TerminalState:
    terminal_id: str
    boot_generation: str
    revision: int
    modules: list[Component]
    interaction_claims: dict[str, str]

    def manifests(self) -> list[dict[str, str]]:
        return [component.manifest() for component in sorted(self.modules, key=lambda c: c.coordinate)]

    def module_digest(self) -> str:
        return payload_digest({"modules": self.manifests()})

    def state_generation(self) -> str:
        digest = payload_digest(
            {
                "modules": self.manifests(),
                "interaction_claims": self.interaction_claims,
            }
        )
        return f"state-r{self.revision:02d}-{digest[:12]}"

    def coordinates(self) -> list[str]:
        return [c.coordinate for c in self.modules]


class VirtualTerminal:
    def __init__(
        self,
        terminal_id: str,
        device_authority: LocalHmacAuthority,
        desk_verifier: Callable[[SignedMessage], bool],
        clock: Clock,
    ) -> None:
        self.terminal_id = terminal_id
        self.device = device_authority
        # 只把“验证借稿台签名”的能力交给阅读器，不把借稿台签发/释放正文的能力交给它。
        self.verify_desk_signature = desk_verifier
        self.clock = clock
        self.state: Optional[TerminalState] = None
        self.boot_receipt: Optional[SignedMessage] = None
        self.sessions: dict[str, list[ReadingSession]] = {}
        self._session_seq = 0

    def boot(
        self,
        modules: list[Component],
        interaction_claims: dict[str, str],
        boot_generation: str = "BOOT-20261127-0001",
    ) -> SignedMessage:
        self.state = TerminalState(
            terminal_id=self.terminal_id,
            boot_generation=boot_generation,
            revision=0,
            modules=list(modules),
            interaction_claims=dict(interaction_claims),
        )
        payload = {
            "message_type": "boot-receipt",
            "attestation_class": FICTIONAL_ATTESTATION_CLASS,
            "device_double_notice": "本地演练替身；此回执是虚构度量，不代表真实硬件启动链。",
            "receipt_id": "BOOT-RCPT-0001",
            "terminal_id": self.terminal_id,
            "boot_generation": boot_generation,
            "boot_count": 1,
            "tick": self.clock.now(),
            "modules": self.state.manifests(),
            "module_digest": self.state.module_digest(),
            "interaction_claims": dict(interaction_claims),
        }
        self.boot_receipt = self.device.sign(payload)
        return self.boot_receipt

    def load_components(
        self,
        modules: list[Component],
        interaction_claims: dict[str, str],
    ) -> TerminalState:
        """班中换入组件。

        启动回执保留开机时的旧模块清单；当前代次由新的状态证明报告。
        """

        if self.state is None:
            raise DrillError("终端尚未开机")
        self.state = TerminalState(
            terminal_id=self.terminal_id,
            boot_generation=self.state.boot_generation,
            revision=self.state.revision + 1,
            modules=list(modules),
            interaction_claims=dict(interaction_claims),
        )
        return self.state

    def answer_inquiry(self, inquiry: Inquiry) -> SignedMessage:
        if self.state is None or self.boot_receipt is None:
            raise DrillError("终端尚未开机，无法回应借稿询问")
        if inquiry.terminal_id != self.terminal_id:
            raise DrillError("询问指定的终端与本终端不符")

        payload = {
            "message_type": "state-attestation",
            "attestation_class": FICTIONAL_ATTESTATION_CLASS,
            "device_double_notice": "只能出具虚构证明的本地设备替身；MAC 仅用于演练防串改。",
            "proof_id": f"ATP-{inquiry.inquiry_id}",
            "terminal_id": self.terminal_id,
            "inquiry_id": inquiry.inquiry_id,
            "nonce": inquiry.nonce,
            "order_id": inquiry.order_id,
            "manuscript_no": inquiry.manuscript_no,
            "purpose": inquiry.purpose,
            "tick": self.clock.now(),
            "boot_generation": self.state.boot_generation,
            "boot_receipt": self.boot_receipt.to_wire(),
            "boot_module_digest": self.boot_receipt.payload["module_digest"],
            "state_revision": self.state.revision,
            "state_generation": self.state.state_generation(),
            "module_digest": self.state.module_digest(),
            "modules": self.state.manifests(),
            "interaction_claims": dict(self.state.interaction_claims),
        }
        return self.device.sign(payload)

    def display_release(
        self, release: SignedMessage
    ) -> tuple[ReadingSession, str, SignedMessage]:
        if not self.verify_desk_signature(release):
            raise DrillError("借稿台正文包签名无效，拒绝显示")
        p = release.payload
        if p.get("audience_terminal_id") != self.terminal_id:
            raise DrillError("正文包不是签发给本终端的")
        if sha256_hex(p["body"]) != p["content_sha256"]:
            raise DrillError("正文摘要不一致，拒绝显示")

        self._session_seq += 1
        session_id = f"READ-{self._session_seq:03d}"
        display_text = "\n".join(
            [
                f"{p['classification']}｜{p['title']}｜{p['displayed_version']}",
                f"稿号：{p['manuscript_no']}    订单：{p['order_id']}",
                "",
                p["body"].rstrip(),
                "",
                f"[本次阅读已钉住] 核验代次={p['state_generation']}",
                f"[核验单] {p['verification_id']} / 组合 {p['combination_id']}",
            ]
        )

        session = ReadingSession(
            session_id=session_id,
            order_id=p["order_id"],
            manuscript_no=p["manuscript_no"],
            displayed_version=p["displayed_version"],
            content_sha256=p["content_sha256"],
            state_generation=p["state_generation"],
            verification_id=p["verification_id"],
            combination_id=p["combination_id"],
            display_text=display_text,
            opened_tick=self.clock.current(),
        )
        self.sessions.setdefault(session.order_id, []).append(session)

        receipt_payload = {
            "message_type": "display-receipt",
            "attestation_class": FICTIONAL_ATTESTATION_CLASS,
            "receipt_id": f"DSP-{session_id}",
            "terminal_id": self.terminal_id,
            "session_id": session_id,
            "release_id": p["release_id"],
            "order_id": session.order_id,
            "manuscript_no": session.manuscript_no,
            "displayed_version": session.displayed_version,
            "content_sha256": session.content_sha256,
            "state_generation": session.state_generation,
            "verification_id": session.verification_id,
            "combination_id": session.combination_id,
            "tick": self.clock.now(),
        }
        receipt = self.device.sign(receipt_payload)
        return session, display_text, receipt

    def continue_reading(self, order_id: str) -> ReadingSession:
        """已打开的阅读固定在旧核验代次，不按新活跃组件重新渲染/取证。"""

        copies = self.sessions.get(order_id)
        if not copies:
            raise DrillError("没有可继续的已打开阅读")
        return copies[-1]

    def has_open_reading(self, order_id: str) -> bool:
        return bool(self.sessions.get(order_id))
