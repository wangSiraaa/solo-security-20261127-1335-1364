from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import AuthorizationDenied, BindingMismatch
from .store import JsonStore, new_id, secret_digest, utc_now


class School:
    """一所学校自己的学籍与结业卡签发记录。"""

    def __init__(self, school_id: str, root: str | Path):
        self.school_id = school_id
        self.store = JsonStore(
            Path(root) / f"{school_id}_records.json",
            {"school_id": school_id, "cards": {}, "idempotency": {}, "audit": []},
        )

    def issue_completion(
        self,
        *,
        student_local_id: str,
        name: str,
        course: str,
        qualification: str,
        expires_at: str = "2999-01-01T00:00:00+00:00",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        def op(data: dict[str, Any]) -> dict[str, Any]:
            if idempotency_key is not None:
                card_id = data["idempotency"].get(idempotency_key)
                if card_id:
                    return deepcopy(data["cards"][card_id])

            card_id = new_id("card")
            card = {
                "card_id": card_id,
                "school_id": self.school_id,
                "student_local_id": student_local_id,
                "name": name,
                "course": course,
                "qualification": qualification,
                "status": "active",
                "issued_at": utc_now(),
                "expires_at": expires_at,
                "replaced_by": None,
                "holder_handle": None,
            }
            data["cards"][card_id] = card
            if idempotency_key is not None:
                data["idempotency"][idempotency_key] = card_id
            data["audit"].append(
                {
                    "at": utc_now(),
                    "action": "issue_completion",
                    "card_id": card_id,
                    "student_local_id": student_local_id,
                    "idempotency_key": idempotency_key,
                }
            )
            return deepcopy(card)

        return self.store.update(op)

    def attach_holder_handle(self, card_id: str, handle: str) -> dict[str, Any]:
        def op(data: dict[str, Any]) -> dict[str, Any]:
            card = data["cards"].get(card_id)
            if not card:
                raise KeyError(card_id)
            if card.get("holder_handle") == handle:
                return deepcopy(card)
            card["holder_handle"] = handle
            data["audit"].append(
                {
                    "at": utc_now(),
                    "action": "bind_to_school_specific_holder_handle",
                    "card_id": card_id,
                }
            )
            return deepcopy(card)

        return self.store.update(op)

    def mark_replaced(self, old_card_id: str, new_card_id: str) -> None:
        def op(data: dict[str, Any]) -> None:
            old = data["cards"][old_card_id]
            old["status"] = "replaced"
            old["replaced_by"] = new_card_id
            data["audit"].append(
                {
                    "at": utc_now(),
                    "action": "replace_card",
                    "old_card_id": old_card_id,
                    "new_card_id": new_card_id,
                }
            )

        self.store.update(op)

    def get_card(self, card_id: str) -> dict[str, Any]:
        card = self.store.read()["cards"].get(card_id)
        if not card:
            raise KeyError(card_id)
        return deepcopy(card)

    def find_card(self, card_id: str) -> dict[str, Any] | None:
        card = self.store.read()["cards"].get(card_id)
        return deepcopy(card) if card else None

    def export_records(self) -> dict[str, Any]:
        data = self.store.read()
        return {
            "school_id": self.school_id,
            "exported_at": utc_now(),
            "entitlement": "仅本校学籍、发证和换卡记录",
            "student_records": deepcopy(data.get("cards", {})),
            "audit": deepcopy(data.get("audit", [])),
        }


class ProofOracle:
    """模拟持卡器/验证器：外部只能得到 True/False。

    这里不实现密码学，只用不可猜测的 proof_id 和一次性票据模拟“本次证明是否成立”。
    验证成功后票据立即删除，因此同一证明不能重放。
    """

    def __init__(self, path: str | Path):
        self.store = JsonStore(Path(path), {"proofs": {}})

    def issue(
        self,
        proof_type: str,
        *,
        bench_id: str,
        nonce: str,
        principal: str,
        claims: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proof_id = new_id("proof")
        record = {
            "type": proof_type,
            "bench_id": bench_id,
            "nonce": nonce,
            "principal": principal,
            "claims": deepcopy(claims or {}),
            "created_at": utc_now(),
        }

        def op(data: dict[str, Any]) -> dict[str, Any]:
            data["proofs"][proof_id] = record
            ticket: dict[str, Any] = {"proof_id": proof_id}
            if "presentation_id" in record["claims"]:
                ticket["presentation_id"] = record["claims"]["presentation_id"]
            return deepcopy(ticket)

        return self.store.update(op)

    def verify_one(
        self,
        ticket: dict[str, Any] | None,
        *,
        proof_type: str,
        bench_id: str,
        nonce: str,
        claims: dict[str, Any] | None = None,
    ) -> bool:
        if not isinstance(ticket, dict) or not ticket.get("proof_id"):
            return False
        proof_id = ticket["proof_id"]

        def op(data: dict[str, Any]) -> bool:
            rec = data["proofs"].get(proof_id)
            if not rec:
                return False
            if rec.get("type") != proof_type:
                return False
            if rec.get("bench_id") != bench_id or rec.get("nonce") != nonce:
                return False
            if claims is not None and rec.get("claims") != claims:
                return False
            del data["proofs"][proof_id]
            return True

        try:
            return bool(self.store.update(op))
        except Exception:
            return False

    def verify_bundle(
        self,
        bundle: dict[str, Any] | None,
        expected_card_ids: list[str],
        bench_id: str,
        nonce: str,
    ) -> bool:
        if not isinstance(bundle, dict):
            return False
        same = bundle.get("same_proof")
        presence = bundle.get("presence_proof")
        if not isinstance(same, dict) or not isinstance(presence, dict):
            return False
        same_id = same.get("proof_id")
        presence_id = presence.get("proof_id")
        presentation_id = same.get("presentation_id")
        if not same_id or not presence_id or not presentation_id:
            return False
        if presence.get("presentation_id") != presentation_id:
            return False

        sorted_cards = sorted(expected_card_ids)

        def op(data: dict[str, Any]) -> bool:
            r_same = data["proofs"].get(same_id)
            r_present = data["proofs"].get(presence_id)
            if not r_same or not r_present:
                return False
            if r_same.get("type") != "same_subject":
                return False
            if r_present.get("type") != "presence":
                return False
            for rec in (r_same, r_present):
                if rec.get("bench_id") != bench_id or rec.get("nonce") != nonce:
                    return False
            if not r_same.get("principal") or r_same.get("principal") != r_present.get(
                "principal"
            ):
                return False
            if r_same.get("claims") != {
                "card_ids": sorted_cards,
                "presentation_id": presentation_id,
            }:
                return False
            if r_present.get("claims") != {"presentation_id": presentation_id}:
                return False

            # 成功后删除本次票据；旧台到场证明不能拿到新台重放。
            del data["proofs"][same_id]
            del data["proofs"][presence_id]
            return True

        try:
            return bool(self.store.update(op))
        except Exception:
            return False


class HolderDevice:
    """学员亲自掌握的持卡器。

    设备文件保存 card_id -> 本校专用匿名句柄的绑定。全局 holder_id 和恢复码
    从不交给学校或实验台。设备文件彻底删除且没有恢复码/备份时，跨校关联即丢失。
    """

    def __init__(self, holder_id: str, secret: str, root: str | Path):
        self.holder_id = holder_id
        self.secret = secret
        self.path = Path(root) / "holders" / f"{holder_id}.json"
        self.store = JsonStore(
            self.path,
            {
                "holder_id": holder_id,
                "secret_hash": secret_digest(secret),
                "label": "personal-holder",
                "bindings": {},
                "created_at": utc_now(),
            },
        )

    @classmethod
    def create(
        cls, root: str | Path, *, label: str = "personal-holder"
    ) -> "HolderDevice":
        holder_id = new_id("holder")
        secret = new_id("hsecret")
        device = cls(holder_id, secret, root)

        def op(data: dict[str, Any]) -> dict[str, Any]:
            data["label"] = label
            return data

        device.store.update(op)
        return device

    @classmethod
    def unlock(cls, root: str | Path, holder_id: str, secret: str) -> "HolderDevice":
        path = Path(root) / "holders" / f"{holder_id}.json"
        if not path.exists():
            raise AuthorizationDenied("持卡器不存在或已彻底遗失")
        device = cls(holder_id, secret, root)
        data = device.store.read()
        if data.get("secret_hash") != secret_digest(secret):
            raise AuthorizationDenied("持卡器解锁失败")
        return device

    def _check_secret(self, data: dict[str, Any]) -> None:
        if data.get("secret_hash") != secret_digest(self.secret):
            raise AuthorizationDenied("持卡器解锁失败")

    def bind_card(self, card_id: str, school_id: str) -> str:
        def op(data: dict[str, Any]) -> str:
            self._check_secret(data)
            existing = data["bindings"].get(card_id)
            if existing:
                return existing["school_handle"]
            handle = new_id("schoolhandle")
            data["bindings"][card_id] = {
                "school_id": school_id,
                "school_handle": handle,
                "bound_at": utc_now(),
            }
            return handle

        return self.store.update(op)

    def binding_count(self) -> int:
        return len(self.store.read().get("bindings", {}))

    def issue_presence_proof(
        self,
        *,
        bench_id: str,
        nonce: str,
        oracle: ProofOracle,
        presentation_id: str | None = None,
    ) -> dict[str, Any]:
        data = self.store.read()
        self._check_secret(data)
        presentation_id = presentation_id or new_id("presentation")
        return oracle.issue(
            "presence",
            bench_id=bench_id,
            nonce=nonce,
            principal=self.holder_id,
            claims={"presentation_id": presentation_id},
        ) | {"presentation_id": presentation_id}

    def issue_bundle(
        self,
        card_ids: list[str],
        *,
        bench_id: str,
        nonce: str,
        oracle: ProofOracle,
    ) -> dict[str, Any]:
        unique_cards = sorted(set(card_ids))
        data = self.store.read()
        self._check_secret(data)
        bindings = data.get("bindings", {})
        if len(unique_cards) != 2 or any(cid not in bindings for cid in unique_cards):
            raise BindingMismatch("两张卡未绑定到同一持卡器")

        presentation_id = new_id("presentation")
        same_proof = oracle.issue(
            "same_subject",
            bench_id=bench_id,
            nonce=nonce,
            principal=self.holder_id,
            claims={"card_ids": unique_cards, "presentation_id": presentation_id},
        )
        presence_proof = self.issue_presence_proof(
            bench_id=bench_id,
            nonce=nonce,
            oracle=oracle,
            presentation_id=presentation_id,
        )
        return {
            "mode": "holder",
            "nonce": nonce,
            "card_ids": unique_cards,
            "same_proof": same_proof,
            "presence_proof": presence_proof,
        }

    def destroy_without_backup(self) -> None:
        """模拟持卡器彻底遗失：删除设备文件，且调用方不保留 holder_id/secret。"""
        if self.path.exists():
            self.path.unlink()
        if self.store.lock_path.exists():
            self.store.lock_path.unlink()


class Registry:
    """登记处模式：保存可更正的两校账号关联；持卡器模式下不保存关联。"""

    def __init__(self, root: str | Path, *, mode: str = "registry"):
        self.mode = mode
        self.root = Path(root)
        self.store = JsonStore(
            self.root / "registry_records.json",
            {
                "mode": mode,
                "links": {},
                "card_index": {},
                "account_index": {},
                "history": [],
            },
        )
        self.oracle = ProofOracle(self.root / "registry_proof_oracle.json")

    def witness_link(self, left_card: dict[str, Any], right_card: dict[str, Any]) -> dict[str, Any]:
        link_id = new_id("link")
        sides = [left_card, right_card]

        def op(data: dict[str, Any]) -> dict[str, Any]:
            if data.get("mode") != "registry":
                raise BindingMismatch("当前为持卡器模式，登记处不保存跨校关联")
            accounts: dict[str, Any] = {}
            card_ids: list[str] = []
            for card in sides:
                sid = card["school_id"]
                local_id = card["student_local_id"]
                cid = card["card_id"]
                if sid in accounts:
                    raise BindingMismatch("关联必须来自两所不同学校")
                accounts[sid] = {"student_local_id": local_id, "card_id": cid}
                card_ids.append(cid)
                data["card_index"][cid] = link_id
                data["account_index"].setdefault(sid, {})[local_id] = link_id

            link = {
                "link_id": link_id,
                "status": "active",
                "school_accounts": accounts,
                "card_ids": card_ids,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            data["links"][link_id] = link
            data["history"].append(
                {
                    "at": utc_now(),
                    "action": "witness_same_person",
                    "link_id": link_id,
                    "card_ids": card_ids,
                }
            )
            return deepcopy(link)

        return self.store.update(op)

    def replace_card(
        self,
        link_id: str,
        school_id: str,
        old_card_id: str,
        new_card: dict[str, Any],
        *,
        requester: str = "当事人重办关系",
    ) -> dict[str, Any]:
        def op(data: dict[str, Any]) -> dict[str, Any]:
            link = data["links"][link_id]
            if link["status"] != "active":
                raise BindingMismatch("关联状态不可更正")
            account = link["school_accounts"][school_id]
            if account["card_id"] != old_card_id:
                raise BindingMismatch("旧卡与登记关联不一致")

            before = deepcopy(account)
            account["card_id"] = new_card["card_id"]
            account["student_local_id"] = new_card["student_local_id"]
            link["card_ids"] = [a["card_id"] for a in link["school_accounts"].values()]
            link["updated_at"] = utc_now()
            data["card_index"][new_card["card_id"]] = link_id
            correction = {
                "at": utc_now(),
                "action": "correct_card_replacement",
                "link_id": link_id,
                "school_id": school_id,
                "before": before,
                "after": deepcopy(account),
                "requester": requester,
            }
            link.setdefault("corrections", []).append(correction)
            data["history"].append(correction)
            return deepcopy(link)

        return self.store.update(op)

    def find_by_account(self, school_id: str, student_local_id: str) -> dict[str, Any] | None:
        data = self.store.read()
        link_id = data.get("account_index", {}).get(school_id, {}).get(student_local_id)
        return deepcopy(data["links"][link_id]) if link_id else None

    def recover_cross_school_link(
        self, school_id: str, student_local_id: str
    ) -> dict[str, Any] | None:
        """持卡器遗失后的登记处查询入口。

        registry 模式可查到两校账号如何相连；holder 模式没有关联表，返回 None。
        """
        if self.mode != "registry":
            return None
        return self.find_by_account(school_id, student_local_id)

    def issue_presence_proof(
        self,
        link_id: str,
        *,
        bench_id: str,
        nonce: str,
        presentation_id: str | None = None,
    ) -> dict[str, Any]:
        data = self.store.read()
        link = data["links"].get(link_id)
        if not link or link.get("status") != "active":
            raise BindingMismatch("登记关联不存在或无效")
        presentation_id = presentation_id or new_id("presentation")
        ticket = self.oracle.issue(
            "presence",
            bench_id=bench_id,
            nonce=nonce,
            principal=link_id,
            claims={"presentation_id": presentation_id},
        )
        ticket["presentation_id"] = presentation_id
        return ticket

    def issue_bundle(
        self,
        link_id: str,
        card_ids: list[str],
        *,
        bench_id: str,
        nonce: str,
    ) -> dict[str, Any]:
        data = self.store.read()
        link = data["links"].get(link_id)
        if not link or link["status"] != "active":
            raise BindingMismatch("登记关联不存在或无效")
        if sorted(link["card_ids"]) != sorted(card_ids):
            raise BindingMismatch("提交的卡与登记关联不一致")

        presentation_id = new_id("presentation")
        same_proof = self.oracle.issue(
            "same_subject",
            bench_id=bench_id,
            nonce=nonce,
            principal=link_id,
            claims={"card_ids": sorted(card_ids), "presentation_id": presentation_id},
        )
        presence_proof = self.oracle.issue(
            "presence",
            bench_id=bench_id,
            nonce=nonce,
            principal=link_id,
            claims={"presentation_id": presentation_id},
        )
        presence_proof["presentation_id"] = presentation_id
        return {
            "mode": "registry",
            "nonce": nonce,
            "card_ids": sorted(card_ids),
            "same_proof": same_proof,
            "presence_proof": presence_proof,
        }

    def verify_bundle(
        self,
        bundle: dict[str, Any] | None,
        expected_card_ids: list[str],
        bench_id: str,
        nonce: str,
    ) -> bool:
        return self.oracle.verify_bundle(bundle, expected_card_ids, bench_id, nonce)

    def export_registry(self) -> dict[str, Any]:
        data = self.store.read()
        return {
            "exported_at": utc_now(),
            "mode": self.mode,
            "notice": (
                "本文件包含两校账号关联，仅登记处可见"
                if self.mode == "registry"
                else "持卡器模式：登记处未保存跨校人员关联"
            ),
            "links": deepcopy(data.get("links", {})),
            "account_index": deepcopy(data.get("account_index", {})),
            "history": deepcopy(data.get("history", [])),
        }
