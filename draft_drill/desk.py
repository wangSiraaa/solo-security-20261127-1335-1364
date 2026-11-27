"""借稿台：持有底稿正文，只在本次开稿重新取证通过后释放。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .clock import Clock
from .model import (
    DeliveryProofRequired,
    DeliveryRecord,
    DeskUnreachable,
    Draft,
    FreshAttestationRequired,
    LoanGrant,
    OpenDenied,
    Order,
    ReadingSession,
)
from .security import LocalHmacAuthority, SignedMessage
from .verifier import StateVerifier


class DeliveryLedger:
    def __init__(self) -> None:
        self.records: list[DeliveryRecord] = []

    def append(self, record: DeliveryRecord) -> None:
        self.records.append(record)

    def for_manuscript(self, manuscript_no: str) -> list[DeliveryRecord]:
        return [r for r in self.records if r.manuscript_no == manuscript_no]

    def append_from_environment_certificate(
        self,
        *,
        manuscript_no: str,
        environment_verification_id: str,
    ) -> DeliveryRecord:
        # 故意不提供“环境合格 -> 交付”的旁路。
        raise DeliveryProofRequired(
            f"环境证明 {environment_verification_id} 只能说明当时组件状态，"
            f"没有一次性正文释放和终端显示回执，不能把稿号 {manuscript_no} 记为已交付。"
        )


class LoanDesk:
    def __init__(
        self,
        authority: LocalHmacAuthority,
        verifier: StateVerifier,
        clock: Clock,
    ) -> None:
        self.authority = authority
        self.verifier = verifier
        self.clock = clock
        self.online = True
        self.drafts: dict[str, Draft] = {}
        self.orders: dict[str, Order] = {}
        self.grant_tokens: dict[str, SignedMessage] = {}
        self.releases: dict[str, SignedMessage] = {}
        self.consumed_releases: set[str] = set()
        self.ledger = DeliveryLedger()
        self._grant_seq = 0
        self._release_seq = 0
        self._delivery_seq = 0

    def stock_draft(self, draft: Draft) -> None:
        self.drafts[draft.manuscript_no] = draft

    def receive_order(self, order: Order) -> None:
        self.orders[order.order_id] = order

    def issue_grant(
        self,
        order: Order,
        verification_id: str,
        state_generation: str,
        proof: SignedMessage,
    ) -> LoanGrant:
        self._grant_seq += 1
        grant_id = f"GRT-{self._grant_seq:03d}"
        payload = {
            "message_type": "loan-grant",
            "grant_id": grant_id,
            "order_id": order.order_id,
            "manuscript_no": order.manuscript_no,
            "terminal_id": order.terminal_id,
            "loan_verification_id": verification_id,
            "loan_state_generation": state_generation,
            "scope": ["request-fresh-open"],
            "tick": self.clock.now(),
        }
        token = self.authority.sign(payload)
        self.grant_tokens[grant_id] = token
        return LoanGrant(
            token=token,
            proof=proof,
            verification_id=verification_id,
            state_generation=state_generation,
        )

    def validate_grant(self, grant: LoanGrant, order: Order) -> None:
        token = grant.token
        if not self.authority.verify(token):
            raise FreshAttestationRequired("借稿凭据签名无效")
        p = token.payload
        if p.get("order_id") != order.order_id:
            raise FreshAttestationRequired("借稿凭据不属于这笔订单")
        if p.get("terminal_id") != order.terminal_id:
            raise FreshAttestationRequired("借稿凭据不是签发给该终端的")
        if "request-fresh-open" not in p.get("scope", []):
            raise FreshAttestationRequired("借稿凭据不包含请求开稿范围")

    def present_credential_directly_for_body(self, grant: LoanGrant) -> None:
        """模拟调用旧的“凭借据直接取正文”端点；本方案明确关闭该能力。"""

        # 先证明原凭据本身是真的，从而排除“伪造凭据”干扰项。
        order = self.orders[grant.token.payload["order_id"]]
        self.validate_grant(grant, order)
        raise FreshAttestationRequired(
            "原借据签名真实，但借据不是开稿能力；每次开稿都必须提交绑定本次询问的新鲜状态证明。"
        )

    def open_draft(
        self,
        order_id: str,
        grant: LoanGrant,
        terminal: object,
    ) -> tuple[SignedMessage, object, Inquiry, SignedMessage]:
        if not self.online:
            raise DeskUnreachable(
                "借稿台和活跃代次打包器不可达：在线开稿方案不会在终端保存可离线兑现的正文密钥。"
            )

        order = self.orders[order_id]
        self.validate_grant(grant, order)

        inquiry = self.verifier.issue_inquiry(order, purpose="fresh-open")
        # 结构化类型：terminal 只要实现 answer_inquiry 即可。
        proof = terminal.answer_inquiry(inquiry)  # type: ignore[attr-defined]
        result = self.verifier.verify_for_order(proof, inquiry, order)
        if not result.ok:
            raise OpenDenied(result)

        draft = self.drafts[order.manuscript_no]
        self._release_seq += 1
        release_id = f"REL-{self._release_seq:03d}"
        payload = {
            "message_type": "draft-body-release",
            "release_id": release_id,
            "grant_id": grant.token.payload["grant_id"],
            "order_id": order.order_id,
            "manuscript_no": draft.manuscript_no,
            "title": draft.title,
            "classification": draft.classification,
            "displayed_version": draft.version,
            "content_sha256": draft.content_sha256,
            "audience_terminal_id": order.terminal_id,
            "verification_id": result.verification_id,
            "combination_id": result.combination_id,
            "state_generation": result.state_generation,
            "body": draft.body,
            "tick": self.clock.now(),
        }
        release = self.authority.sign(payload)
        self.releases[release_id] = release
        return release, result, inquiry, proof

    def record_display(
        self,
        release: SignedMessage,
        session: ReadingSession,
        display_receipt: SignedMessage,
    ) -> DeliveryRecord:
        if not self.authority.verify(release):
            raise DeliveryProofRequired("正文释放包签名无效")
        if not self.verifier.device.verify(display_receipt):
            raise DeliveryProofRequired("终端显示回执签名无效")

        release_id = release.payload["release_id"]
        if release_id not in self.releases:
            raise DeliveryProofRequired("未知正文释放编号")
        if release_id in self.consumed_releases:
            raise DeliveryProofRequired("该正文释放包已经记录过一次显示")

        rp = release.payload
        dp = display_receipt.payload
        matching_fields = (
            ("release_id", release_id),
            ("order_id", session.order_id),
            ("manuscript_no", session.manuscript_no),
            ("displayed_version", session.displayed_version),
            ("content_sha256", session.content_sha256),
            ("state_generation", session.state_generation),
            ("verification_id", session.verification_id),
        )
        for field_name, expected in matching_fields:
            actual_r = rp.get(field_name)
            actual_d = dp.get(field_name)
            if actual_r != expected or actual_d != expected:
                raise DeliveryProofRequired(
                    f"显示回执、释放包和阅读会话在字段 {field_name} 上不一致"
                )

        self._delivery_seq += 1
        record = DeliveryRecord(
            seq=len(self.ledger.records) + 1,
            delivery_id=f"DLV-{self._delivery_seq:03d}",
            order_id=session.order_id,
            manuscript_no=session.manuscript_no,
            displayed_version=session.displayed_version,
            content_sha256=session.content_sha256,
            state_generation=session.state_generation,
            verification_id=session.verification_id,
            combination_id=session.combination_id,
            terminal_id=dp["terminal_id"],
            session_id=session.session_id,
            tick=self.clock.now(),
        )
        self.ledger.append(record)
        self.consumed_releases.add(release_id)
        return record
