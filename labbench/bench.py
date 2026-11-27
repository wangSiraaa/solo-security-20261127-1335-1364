from __future__ import annotations

import hmac
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .errors import AuthorizationDenied, InvalidStateError
from .privacy import PublicReceiptBook
from .store import JsonStore, canonical_digest, new_id, secret_digest, utc_now

REQUIRED_QUALIFICATION = "practical-power"

SAMPLE_STEPS: list[dict[str, Any]] = [
    {"code": "inspect", "title": "检查样件绝缘与外观", "base": 11, "factor": 2},
    {"code": "measure", "title": "测量基准电流", "base": 18, "factor": 3},
    {"code": "trim", "title": "校准保护阈值", "base": 25, "factor": 4},
    {"code": "verify", "title": "复检并封样", "base": 32, "factor": 5},
]


def new_sample_state(sample_code: str = "local-rotary-sample-v1") -> dict[str, Any]:
    return {
        "sample_code": sample_code,
        "status": "ready",
        "next_step": 0,
        "accumulator": 0,
        "readings": [],
    }


def advance_sample(sample: dict[str, Any]) -> dict[str, Any]:
    next_index = int(sample.get("next_step", 0))
    if next_index >= len(SAMPLE_STEPS):
        raise InvalidStateError("样件工序已经完成")
    step = SAMPLE_STEPS[next_index]
    reading = int(step["base"]) + next_index * int(step["factor"])
    event = {
        "step_index": next_index,
        "code": step["code"],
        "title": step["title"],
        "reading": reading,
        "at": utc_now(),
    }
    sample.setdefault("readings", []).append(event)
    sample["accumulator"] = int(sample.get("accumulator", 0)) + reading
    sample["next_step"] = next_index + 1
    sample["status"] = "completed" if sample["next_step"] == len(SAMPLE_STEPS) else "in_progress"
    return event


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


CardResolver = Callable[[str], dict[str, Any] | None]
IdentityVerifier = Callable[[dict[str, Any], list[str], str, str], bool]


class Bench:
    """本地实验台及其操作席状态机。"""

    def __init__(self, bench_id: str, root: str | Path):
        self.bench_id = bench_id
        self.root = Path(root)
        self.store = JsonStore(
            self.root / "bench_states" / f"{bench_id}.json",
            {
                "bench_id": bench_id,
                "challenges": {},
                "confirmations": {},
                "seats": {},
                "handoff_receipts": {},
                "denials": [],
            },
        )
        self.workspace = self.root / "workspaces" / bench_id
        self.workspace.mkdir(parents=True, exist_ok=True)

    # ---------- challenge / capability helpers ----------

    def create_challenge(self, purpose: str = "open") -> str:
        nonce = new_id("nonce")

        def op(data: dict[str, Any]) -> str:
            data["challenges"][nonce] = {
                "purpose": purpose,
                "created_at": utc_now(),
                "used_at": None,
            }
            return nonce

        return self.store.update(op)

    @staticmethod
    def _parse_capability(seat_token: str) -> tuple[str, str]:
        if not isinstance(seat_token, str) or "." not in seat_token:
            raise AuthorizationDenied("操作席凭证格式无效")
        seat_id, secret = seat_token.split(".", 1)
        if not seat_id or not secret:
            raise AuthorizationDenied("操作席凭证无效")
        return seat_id, secret

    def _authenticate(self, data: dict[str, Any], seat_token: str) -> dict[str, Any]:
        seat_id, secret = self._parse_capability(seat_token)
        seat = data["seats"].get(seat_id)
        if not seat or not hmac.compare_digest(
            str(seat.get("capability_hash", "")), secret_digest(secret)
        ):
            raise AuthorizationDenied("操作席凭证无效")
        return seat

    @staticmethod
    def _view(seat: dict[str, Any]) -> dict[str, Any]:
        view = {k: v for k, v in seat.items() if k != "capability_hash"}
        return json.loads(json.dumps(view, ensure_ascii=False))

    def _sample_file(self, seat: dict[str, Any]) -> Path:
        return Path(seat["operation"]["sample_file"])

    def _write_sample(self, seat: dict[str, Any]) -> None:
        _atomic_write_json(self._sample_file(seat), seat["operation"]["sample"])

    # ---------- policy ----------

    def _valid_cards(
        self, card_ids: Any, resolve_card: CardResolver
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        if not isinstance(card_ids, list) or len(card_ids) != 2:
            return None, "必须出示恰好两张结业卡"
        if len(set(card_ids)) != 2:
            return None, "两张卡不能重复"

        cards: list[dict[str, Any]] = []
        for card_id in card_ids:
            if not isinstance(card_id, str):
                return None, "卡号无效"
            card = resolve_card(card_id)
            if not card:
                return None, "结业卡不存在"
            if card.get("status") != "active":
                return None, "结业卡已换发、撤销或失效"
            expires_at = card.get("expires_at")
            if expires_at:
                try:
                    if datetime.fromisoformat(expires_at) <= datetime.now().astimezone():
                        return None, "结业卡已过期"
                except ValueError:
                    return None, "结业卡有效期格式无效"
            if card.get("qualification") != REQUIRED_QUALIFICATION:
                return None, "资格项目不符合本台要求"
            cards.append(card)

        if cards[0]["school_id"] == cards[1]["school_id"]:
            return None, "两张卡必须来自两所不同学校"

        # 安全关键：这里刻意不比较 name。同名既不是通过条件，也不是拒绝条件；
        # 同一人只能由持卡器同持证明或登记处关联证明保证。
        return cards, None

    @staticmethod
    def _deny(data: dict[str, Any], nonce: Any, reason_code: str) -> None:
        data.setdefault("denials", []).append(
            {"at": utc_now(), "nonce": nonce if isinstance(nonce, str) else None, "reason_code": reason_code}
        )

    # ---------- open ----------

    def open_seat(
        self,
        bundle: dict[str, Any],
        *,
        resolve_card: CardResolver,
        verify_identity: IdentityVerifier,
        receipt_book: PublicReceiptBook,
    ) -> tuple[dict[str, Any], str]:
        nonce = bundle.get("nonce") if isinstance(bundle, dict) else None
        card_ids = bundle.get("card_ids") if isinstance(bundle, dict) else None
        denial: str | None = None
        result: tuple[dict[str, Any], str] | None = None

        with self.store.transaction() as data:
            challenge = data["challenges"].get(nonce) if isinstance(nonce, str) else None
            if not challenge or challenge.get("used_at"):
                denial = "本台挑战不存在或已使用，请重新到台取号"
            else:
                cards, card_error = self._valid_cards(card_ids, resolve_card)
                if card_error or cards is None:
                    denial = card_error or "卡片无效"
                elif not verify_identity(bundle, card_ids, self.bench_id, nonce):
                    # 验证器只给 bool；实验台不区分是同持证明还是到场证明失败。
                    denial = "同一人同持及本次到场证明不成立"
                else:
                    seat_id = new_id("seat")
                    capability = new_id("seatcap")
                    receipt = receipt_book.issue("seat.opened", "granted")
                    confirmation = {
                        "scope": "fresh_open",
                        "nonce": nonce,
                        "confirmed_at": utc_now(),
                        "identity_mode": bundle.get("mode"),
                        "card_ids": sorted(card_ids),
                        "same_subject_proof_id": bundle.get("same_proof", {}).get("proof_id"),
                        "presence_proof_id": bundle.get("presence_proof", {}).get("proof_id"),
                        "seat_id": seat_id,
                    }
                    data["confirmations"][nonce] = confirmation
                    seat = {
                        "seat_id": seat_id,
                        "bench_id": self.bench_id,
                        "status": "open",
                        "card_ids": sorted(card_ids),
                        "identity_mode": bundle.get("mode"),
                        "challenge_nonce": nonce,
                        "confirmation": confirmation,
                        "capability_hash": secret_digest(capability),
                        "opened_at": utc_now(),
                        "closed_at": None,
                        "operation": None,
                        "source": {"type": "fresh_confirmation"},
                        "public_open_receipt": receipt,
                        "exit_receipt": None,
                        "events": [{"at": utc_now(), "event": "seat_opened"}],
                    }
                    data["seats"][seat_id] = seat
                    challenge["used_at"] = utc_now()
                    result = (self._view(seat), f"{seat_id}.{capability}")

            if denial is not None:
                self._deny(data, nonce, "open_denied")

        if denial is not None:
            raise AuthorizationDenied(denial)
        assert result is not None
        return result

    # ---------- sample operation ----------

    def start_operation(self, seat_token: str, sample_code: str = "local-rotary-sample-v1") -> dict[str, Any]:
        with self.store.transaction() as data:
            seat = self._authenticate(data, seat_token)
            if seat["status"] != "open":
                raise InvalidStateError(f"席位状态为 {seat['status']}，不能开始操作")
            if seat.get("operation") and seat["operation"]["status"] != "completed":
                raise InvalidStateError("已有未完成操作；请继续、中断或交席")

            operation_id = new_id("op")
            sample = new_sample_state(sample_code)
            seat_id = seat["seat_id"]
            seat["operation"] = {
                "operation_id": operation_id,
                "status": "in_progress",
                "started_at": utc_now(),
                "sample": sample,
                "sample_file": str(self.workspace / f"{seat_id}_sample.json"),
            }
            seat["status"] = "open"
            seat["events"].append({"at": utc_now(), "event": "operation_started", "operation_id": operation_id})
            self._write_sample(seat)
            return self._view(seat)

    def operate_next(self, seat_token: str) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.store.transaction() as data:
            seat = self._authenticate(data, seat_token)
            if seat["status"] != "open":
                raise InvalidStateError(f"席位状态为 {seat['status']}，不能继续操作")
            op = seat.get("operation")
            if not op or op.get("status") != "in_progress":
                raise InvalidStateError("没有进行中的操作")

            event = advance_sample(op["sample"])
            if op["sample"]["status"] == "completed":
                op["status"] = "completed"
                op["completed_at"] = utc_now()
                seat["events"].append({"at": utc_now(), "event": "operation_completed"})
            else:
                seat["events"].append({"at": utc_now(), "event": "step_completed", "code": event["code"]})
            self._write_sample(seat)
            return event, self._view(seat)

    def interrupt_operation(self, seat_token: str, reason: str = "manual_interrupt") -> dict[str, Any]:
        with self.store.transaction() as data:
            seat = self._authenticate(data, seat_token)
            op = seat.get("operation")
            if not op or op.get("status") != "in_progress":
                raise InvalidStateError("只有进行中的操作可以中断")
            if op["sample"].get("status") == "completed":
                raise InvalidStateError("操作已完成，无需中断")

            op["status"] = "interrupted"
            op["interrupted_at"] = utc_now()
            op["interrupt_reason"] = reason
            seat["status"] = "interrupted"
            seat["events"].append({"at": utc_now(), "event": "operation_interrupted", "reason": reason})
            self._write_sample(seat)
            return self._view(seat)

    def resume_operation(self, seat_token: str) -> dict[str, Any]:
        with self.store.transaction() as data:
            seat = self._authenticate(data, seat_token)
            op = seat.get("operation")
            if not op or op.get("status") != "interrupted":
                raise InvalidStateError("没有可恢复的中断操作")
            op["status"] = "in_progress"
            op["resumed_at"] = utc_now()
            seat["status"] = "open"
            seat["events"].append({"at": utc_now(), "event": "operation_resumed"})
            self._write_sample(seat)
            return self._view(seat)

    # ---------- handoff ----------

    def create_handoff_receipt(
        self, seat_token: str, target_bench_id: str
    ) -> tuple[str, dict[str, Any]]:
        receipt_id = new_id("ho")
        secret = new_id("hocap")
        with self.store.transaction() as data:
            seat = self._authenticate(data, seat_token)
            op = seat.get("operation")
            if seat["status"] != "interrupted" or not op or op.get("status") != "interrupted":
                raise InvalidStateError("只有已中断且未结清的操作可以交席")
            if not target_bench_id or target_bench_id == self.bench_id:
                raise InvalidStateError("交席回单必须指定另一台实验台")

            for old_id in seat.get("handoff_receipt_ids", []):
                old = data["handoff_receipts"].get(old_id)
                if old and old.get("status") == "unsettled":
                    old["status"] = "superseded"

            checkpoint = json.loads(json.dumps(op["sample"], ensure_ascii=False))
            receipt = {
                "receipt_id": receipt_id,
                "kind": "unsettled_operation_handoff",
                "from_bench": self.bench_id,
                "to_bench": target_bench_id,
                "seat_id": seat["seat_id"],
                "operation_id": op["operation_id"],
                "card_ids": sorted(seat["card_ids"]),
                "checkpoint": checkpoint,
                "checkpoint_hash": canonical_digest(checkpoint),
                "remaining_steps": len(SAMPLE_STEPS) - int(checkpoint["next_step"]),
                "status": "unsettled",
                "secret_hash": secret_digest(secret),
                "created_at": utc_now(),
                "accepted_at": None,
                "accepted_by_bench": None,
                "accepted_by_seat": None,
            }
            data["handoff_receipts"][receipt_id] = receipt
            seat.setdefault("handoff_receipt_ids", []).append(receipt_id)
            seat["events"].append(
                {"at": utc_now(), "event": "handoff_receipt_issued", "to_bench": target_bench_id}
            )
            public_view = {k: v for k, v in receipt.items() if k != "secret_hash"}
            code = f"{receipt_id}.{secret}"
            return code, json.loads(json.dumps(public_view, ensure_ascii=False))

    def consume_handoff_receipt(
        self,
        code: str,
        *,
        target_bench_id: str,
        expected_card_ids: list[str],
        new_seat_id: str,
    ) -> dict[str, Any] | None:
        try:
            receipt_id, secret = self._parse_capability(code)
        except AuthorizationDenied:
            return None

        with self.store.transaction() as data:
            receipt = data["handoff_receipts"].get(receipt_id)
            if not receipt or receipt.get("status") != "unsettled":
                return None
            if receipt.get("to_bench") != target_bench_id:
                return None
            if not hmac.compare_digest(str(receipt.get("secret_hash", "")), secret_digest(secret)):
                return None
            if sorted(receipt.get("card_ids", [])) != sorted(expected_card_ids):
                return None

            receipt["status"] = "accepted"
            receipt["accepted_at"] = utc_now()
            receipt["accepted_by_bench"] = target_bench_id
            receipt["accepted_by_seat"] = new_seat_id
            old_seat = data["seats"].get(receipt["seat_id"])
            if old_seat:
                old_seat["status"] = "handed_over"
                old_seat["events"].append(
                    {
                        "at": utc_now(),
                        "event": "handoff_accepted_by_other_bench",
                        "by_bench": target_bench_id,
                        "new_seat_id": new_seat_id,
                    }
                )
            return {k: v for k, v in receipt.items() if k != "secret_hash"}

    def accept_handoff(
        self,
        code: str,
        bundle: dict[str, Any],
        *,
        old_bench: "Bench",
        resolve_card: CardResolver,
        verify_identity: IdentityVerifier,
        receipt_book: PublicReceiptBook,
    ) -> tuple[dict[str, Any], str]:
        nonce = bundle.get("nonce") if isinstance(bundle, dict) else None
        card_ids = bundle.get("card_ids") if isinstance(bundle, dict) else None
        denial: str | None = None
        result: tuple[dict[str, Any], str] | None = None
        seat_id = new_id("seat")
        capability = new_id("seatcap")

        with self.store.transaction() as data:
            challenge = data["challenges"].get(nonce) if isinstance(nonce, str) else None
            if not challenge or challenge.get("used_at") or challenge.get("purpose") != "handoff":
                denial = "新台接手挑战不存在、用途错误或已使用"
            else:
                cards, card_error = self._valid_cards(card_ids, resolve_card)
                if card_error or cards is None:
                    denial = card_error or "卡片无效"
                elif not verify_identity(bundle, card_ids, self.bench_id, nonce):
                    denial = "新台本次同持及到场依据不成立"
                else:
                    receipt = old_bench.consume_handoff_receipt(
                        code,
                        target_bench_id=self.bench_id,
                        expected_card_ids=card_ids,
                        new_seat_id=seat_id,
                    )
                    if receipt is None:
                        denial = "旧台未结操作回单无效、已使用或不是开给本台"
                    elif canonical_digest(receipt["checkpoint"]) != receipt["checkpoint_hash"]:
                        denial = "旧台样件检查点摘要不一致"
                    else:
                        confirmation = {
                            "scope": "handoff_with_fresh_presence",
                            "nonce": nonce,
                            "confirmed_at": utc_now(),
                            "identity_mode": bundle.get("mode"),
                            "card_ids": sorted(card_ids),
                            "same_subject_proof_id": bundle.get("same_proof", {}).get("proof_id"),
                            "presence_proof_id": bundle.get("presence_proof", {}).get("proof_id"),
                            "old_bench": receipt["from_bench"],
                            "handoff_receipt_id": receipt["receipt_id"],
                            "seat_id": seat_id,
                        }
                        data["confirmations"][nonce] = confirmation
                        seat = {
                            "seat_id": seat_id,
                            "bench_id": self.bench_id,
                            "status": "open",
                            "card_ids": sorted(card_ids),
                            "identity_mode": bundle.get("mode"),
                            "challenge_nonce": nonce,
                            "confirmation": confirmation,
                            "capability_hash": secret_digest(capability),
                            "opened_at": utc_now(),
                            "closed_at": None,
                            "source": {
                                "type": "handoff",
                                "from_bench": receipt["from_bench"],
                                "handoff_receipt_id": receipt["receipt_id"],
                            },
                            "operation": {
                                "operation_id": receipt["operation_id"],
                                "status": "interrupted",
                                "accepted_at": utc_now(),
                                "sample": receipt["checkpoint"],
                                "sample_file": str(self.workspace / f"{seat_id}_sample.json"),
                            },
                            "public_open_receipt": receipt_book.issue("seat.handoff", "accepted"),
                            "exit_receipt": None,
                            "events": [
                                {"at": utc_now(), "event": "handoff_accepted"},
                                {"at": utc_now(), "event": "operation_waiting_resume"},
                            ],
                        }
                        data["seats"][seat_id] = seat
                        challenge["used_at"] = utc_now()
                        self._write_sample(seat)
                        result = (self._view(seat), f"{seat_id}.{capability}")

            if denial is not None:
                self._deny(data, nonce, "handoff_denied")

        if denial is not None:
            raise AuthorizationDenied(denial)
        assert result is not None
        return result

    # ---------- exit ----------

    def exit_seat(
        self, seat_token: str, receipt_book: PublicReceiptBook, *, abandon_interrupted: bool = False
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        denial: str | None = None
        result: tuple[dict[str, Any], dict[str, Any]] | None = None

        with self.store.transaction() as data:
            try:
                seat = self._authenticate(data, seat_token)
            except AuthorizationDenied as exc:
                denial = str(exc)
            else:
                if seat["status"] == "closed":
                    # 退席重办/重试：返回原回单，不产生新的公开记录。
                    result = (self._view(seat), seat["exit_receipt"])
                elif seat["status"] == "handed_over":
                    # 旧台已被新台凭未结回单接手；旧台退席是释放动作，可重办。
                    receipt = seat.get("exit_receipt") or receipt_book.issue(
                        "seat.exit", "handed_over"
                    )
                    seat["status"] = "closed"
                    seat["closed_at"] = utc_now()
                    seat["exit_receipt"] = receipt
                    seat["closure_reason"] = "handed_over"
                    seat["events"].append(
                        {"at": utc_now(), "event": "seat_exited_after_handoff"}
                    )
                    result = (self._view(seat), receipt)
                else:
                    op = seat.get("operation")
                    if op and op.get("status") == "in_progress":
                        denial = "操作仍在进行；请先完成或中断"
                    elif op and op.get("status") == "interrupted" and not abandon_interrupted:
                        denial = "操作未结清；请交席、恢复完成，或明确放弃后退席"
                    else:
                        op_completed = bool(op and op.get("status") == "completed")
                        result_kind = "completed" if op_completed else "closed_without_completion"
                        if op and op.get("status") == "interrupted" and abandon_interrupted:
                            result_kind = "abandoned_interrupted"
                            op["status"] = "abandoned"
                        receipt = receipt_book.issue("seat.exit", result_kind)
                        seat["status"] = "closed"
                        seat["closed_at"] = utc_now()
                        seat["exit_receipt"] = receipt
                        seat["events"].append({"at": utc_now(), "event": "seat_exited", "result": result_kind})
                        result = (self._view(seat), receipt)

        if denial is not None:
            raise InvalidStateError(denial) if "仍在进行" in denial or "未结清" in denial else AuthorizationDenied(denial)
        assert result is not None
        return result

    def read_state(self) -> dict[str, Any]:
        return self.store.read()
