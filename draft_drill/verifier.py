"""状态核验器：验签、核对本次询问/nonce、启动回执和管理员组合策略。"""

from __future__ import annotations

from typing import Optional

from .clock import Clock
from .model import Inquiry, Order, VerificationResult
from .policy import PolicyStore
from .security import LocalHmacAuthority, SignedMessage, payload_digest


class StateVerifier:
    def __init__(
        self,
        device_authority: LocalHmacAuthority,
        policy: PolicyStore,
        clock: Clock,
    ) -> None:
        self.device = device_authority
        self.policy = policy
        self.clock = clock
        self.inquiries: dict[str, Inquiry] = {}
        self.verification_log: list[VerificationResult] = []
        self._inquiry_seq = 0
        self._verification_seq = 0

    def _next_verification_id(self) -> str:
        self._verification_seq += 1
        return f"VRF-{self._verification_seq:03d}"

    def issue_inquiry(self, order: Order, purpose: str) -> Inquiry:
        self._inquiry_seq += 1
        inquiry_id = f"INQ-{self._inquiry_seq:03d}"
        inquiry = Inquiry(
            inquiry_id=inquiry_id,
            nonce=f"NONCE-{self._inquiry_seq:03d}-BOUND",
            order_id=order.order_id,
            manuscript_no=order.manuscript_no,
            terminal_id=order.terminal_id,
            purpose=purpose,
            issued_tick=self.clock.now(),
        )
        self.inquiries[inquiry_id] = inquiry
        return inquiry

    def verify_boot_receipt(self, message: SignedMessage) -> VerificationResult:
        verification_id = self._next_verification_id()
        p = message.payload
        if not self.device.verify(message) or p.get("message_type") != "boot-receipt":
            result = VerificationResult(
                ok=False,
                code="BAD_BOOT_RECEIPT",
                summary="开机回执签名或类型无效",
                inquiry_id="-",
                verification_id=verification_id,
            )
            self.verification_log.append(result)
            return result

        coordinates = [m["coordinate"] for m in p.get("modules", [])]
        decision = self.policy.evaluate(
            coordinates, p.get("interaction_claims", {}), order=None
        )
        result = VerificationResult(
            ok=decision.ok,
            code=decision.code,
            summary=f"开机代次 {p.get('boot_generation')}：{decision.summary}",
            inquiry_id="-",
            verification_id=verification_id,
            reasons=list(decision.reasons),
            combination_id=decision.combination_id,
            module_digest=p.get("module_digest"),
            state_generation=None,
            proof_authority=message.authority,
        )
        self.verification_log.append(result)
        return result

    def _validate_state_message(
        self,
        message: SignedMessage,
        inquiry: Inquiry,
    ) -> tuple[Optional[tuple[dict, list[str], dict, str]], Optional[VerificationResult]]:
        verification_id = self._next_verification_id()
        p = message.payload
        common = dict(
            inquiry_id=inquiry.inquiry_id,
            verification_id=verification_id,
            proof_authority=message.authority,
        )

        def fail(
            code: str, summary: str, reasons: Optional[list[str]] = None
        ) -> tuple[None, VerificationResult]:
            result = VerificationResult(
                ok=False,
                code=code,
                summary=summary,
                reasons=reasons or [],
                **common,
            )
            self.verification_log.append(result)
            return None, result

        if not self.device.verify(message):
            return fail("BAD_PROOF_SIGNATURE", "状态证明的本地签名校验失败")

        expected_binding = {
            "message_type": "state-attestation",
            "terminal_id": inquiry.terminal_id,
            "inquiry_id": inquiry.inquiry_id,
            "nonce": inquiry.nonce,
            "order_id": inquiry.order_id,
            "manuscript_no": inquiry.manuscript_no,
            "purpose": inquiry.purpose,
        }
        mismatches = [
            f"{key}: 期望 {expected!r}，实际 {p.get(key)!r}"
            for key, expected in expected_binding.items()
            if p.get(key) != expected
        ]
        if mismatches:
            return fail(
                "INQUIRY_BINDING_MISMATCH",
                "这张状态证明绑定的是另一张询问/订单，旧回条不能顶替本次询问",
                mismatches,
            )

        boot_wire = p.get("boot_receipt")
        if not isinstance(boot_wire, dict):
            return fail("MISSING_BOOT_RECEIPT", "状态证明未附带开机回执")
        boot_message = SignedMessage.from_wire(boot_wire)
        if not self.device.verify(boot_message):
            return fail("BAD_BOOT_SIGNATURE", "内嵌开机回执签名无效")
        if boot_message.payload.get("terminal_id") != inquiry.terminal_id:
            return fail("BOOT_TERMINAL_MISMATCH", "开机回执不属于该终端")
        if boot_message.payload.get("boot_generation") != p.get("boot_generation"):
            return fail(
                "BOOT_GENERATION_MISMATCH",
                "状态证明中的启动代次与开机回执不一致",
            )

        modules = p.get("modules", [])
        coordinates = [m.get("coordinate", "") for m in modules]
        expected_module_digest = payload_digest({"modules": modules})
        if p.get("module_digest") != expected_module_digest:
            return fail("MODULE_DIGEST_MISMATCH", "模块清单与摘要不一致")

        claims = p.get("interaction_claims", {})
        expected_state_digest = payload_digest(
            {"modules": modules, "interaction_claims": claims}
        )
        expected_state_generation = (
            f"state-r{p.get('state_revision', -1):02d}-{expected_state_digest[:12]}"
        )
        if p.get("state_generation") != expected_state_generation:
            return fail("STATE_GENERATION_MISMATCH", "组件代次/交互声明摘要不一致")

        return (p, coordinates, claims, verification_id), None

    def verify_for_order(
        self,
        message: SignedMessage,
        inquiry: Inquiry,
        order: Order,
    ) -> VerificationResult:
        validated, failure = self._validate_state_message(message, inquiry)
        if failure is not None:
            return failure
        assert validated is not None
        p, coordinates, claims, verification_id = validated
        common = dict(
            inquiry_id=inquiry.inquiry_id,
            verification_id=verification_id,
            proof_authority=message.authority,
        )

        if inquiry.consumed:
            result = VerificationResult(
                ok=False,
                code="INQUIRY_ALREADY_CONSUMED",
                summary="本次询问/nonce 已用于一次成功释放，不能重放",
                **common,
            )
            self.verification_log.append(result)
            return result

        decision = self.policy.evaluate(coordinates, claims, order)
        if not decision.ok:
            result = VerificationResult(
                ok=False,
                code=decision.code,
                summary=decision.summary,
                reasons=list(decision.reasons),
                combination_id=decision.combination_id,
                state_generation=p.get("state_generation"),
                module_digest=p.get("module_digest"),
                **common,
            )
            self.verification_log.append(result)
            return result

        inquiry.consumed = True
        result = VerificationResult(
            ok=True,
            code="APPROVED_FOR_ORDER",
            summary=f"本次询问的新鲜证明通过；{decision.summary}",
            reasons=list(decision.reasons),
            combination_id=decision.combination_id,
            state_generation=p.get("state_generation"),
            module_digest=p.get("module_digest"),
            **common,
        )
        self.verification_log.append(result)
        return result

    def verify_environment_only(
        self,
        message: SignedMessage,
        inquiry: Inquiry,
    ) -> VerificationResult:
        """只核验核心组件组合；不出具订单交付结论，也不消费开稿 nonce。"""

        validated, failure = self._validate_state_message(message, inquiry)
        if failure is not None:
            return failure
        assert validated is not None
        p, coordinates, claims, verification_id = validated

        decision = self.policy.evaluate(coordinates, claims, order=None)
        result = VerificationResult(
            ok=decision.ok,
            code="ENVIRONMENT_" + decision.code,
            summary="环境证明（与订单交付分离）：" + decision.summary,
            inquiry_id=inquiry.inquiry_id,
            verification_id=verification_id,
            reasons=list(decision.reasons),
            combination_id=decision.combination_id,
            state_generation=p.get("state_generation"),
            module_digest=p.get("module_digest"),
            proof_authority=message.authority,
        )
        self.verification_log.append(result)
        return result
