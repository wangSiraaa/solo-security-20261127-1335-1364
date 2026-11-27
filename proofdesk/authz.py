from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .common import canonical_json, parse_iso
from .trust import LocalTrust


def digest_package_core(package: dict[str, Any]) -> str:
    core = {
        "book_id": package.get("book_id"),
        "chapter_id": package.get("chapter_id"),
        "base_rev_id": package.get("base_rev_id"),
        "replacements": package.get("replacements", []),
        "operator_id": package.get("operator_id"),
    }
    return hashlib.sha256(canonical_json(core)).hexdigest()


def _time_ok(doc: dict[str, Any], at: datetime) -> str | None:
    valid_from = doc.get("valid_from")
    expires_at = doc.get("expires_at")
    if valid_from and at < parse_iso(valid_from):
        return "not_yet_valid"
    if expires_at and at >= parse_iso(expires_at):
        return "expired"
    return None


def _chapter_ok(doc: dict[str, Any], chapter_id: str) -> bool:
    chapters = doc.get("chapter_ids", [])
    return "*" in chapters or chapter_id in chapters


@dataclass
class AuthOutcome:
    ok: bool
    code: str
    reason: str
    mode: str
    chain: list[dict[str, Any]]
    basis: list[dict[str, str]]
    represented_author: dict[str, str] | None
    actual_operator: dict[str, str] | None
    allowed_actions: list[str]
    boundary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "reason": self.reason,
            "mode": self.mode,
            "chain": self.chain,
            "basis": self.basis,
            "represented_author": self.represented_author,
            "actual_operator": self.actual_operator,
            "allowed_actions": self.allowed_actions,
            "local_revocation_boundary": self.boundary,
        }


class Authorizer:
    """Verify the concrete basis presented with one revision package."""

    def __init__(self, trust: LocalTrust, library_state: dict[str, Any]):
        self.trust = trust
        self.state = library_state

    def boundary(self) -> dict[str, Any]:
        revocations = self.state.get("revocations", [])
        return {
            "authority": "book-library-local",
            "rule": (
                "书库只执行已经进入本书库本地凭据柜/吊销册的撤回或修订授权；"
                "编辑站口头或站内已撤回但尚未送达书库的事项，不构成当地已知边界。"
            ),
            "known_revocation_ids": [r.get("id") for r in revocations],
            "known_mandate_versions": [
                {"line_id": m.get("line_id"), "version": m.get("version"), "id": m.get("id")}
                for m in self.state.get("known_mandates", [])
            ],
            "editor_station_link": self.state.get("station_link", "unknown"),
        }

    def _bad(self, code: str, reason: str, mode: str = "unknown") -> AuthOutcome:
        return AuthOutcome(
            False,
            code,
            reason,
            mode,
            [],
            [],
            None,
            None,
            [],
            self.boundary(),
        )

    def _latest_mandate(self, mandate: dict[str, Any]) -> dict[str, Any]:
        line_id = mandate.get("line_id")
        latest = mandate
        for known in self.state.get("known_mandates", []):
            if known.get("line_id") != line_id:
                continue
            if int(known.get("version", 0)) > int(latest.get("version", 0)):
                latest = known
        return latest

    def _is_revoked(self, *docs: dict[str, Any]) -> dict[str, Any] | None:
        targets: set[str] = set()
        for doc in docs:
            for key in ("id", "line_id"):
                value = doc.get(key)
                if value:
                    targets.add(value)
        at = self.state.get("verification_time")
        for revocation in self.state.get("revocations", []):
            effective = revocation.get("effective_at")
            if at and effective and parse_iso(at) < parse_iso(effective):
                continue
            if revocation.get("target_id") in targets:
                return revocation
        return None

    def verify(self, package: dict[str, Any], at: datetime) -> AuthOutcome:
        mode = package.get("mode", "unknown")
        if not self.trust.verify(package):
            return self._bad("bad_package_signature", "修订件签名或封装校验失败。", mode)

        book = self.state.get("book", {})
        if package.get("book_id") != book.get("id"):
            return self._bad("wrong_book", "修订件不是投递给本册书库的。", mode)
        if package.get("chapter_id") not in book.get("chapters", {}):
            return self._bad("unknown_chapter", "修订件指向不存在的章节。", mode)

        handler = {
            "author_self": self._verify_author_self,
            "editor_self": self._verify_editor_self,
            "portable_pass": self._verify_portable,
            "hosted_station": self._verify_hosted,
            "hosted_claim_only": self._verify_claim_only,
        }.get(mode)
        if handler is None:
            return self._bad("unknown_mode", f"未知办理方式：{mode}", mode)
        return handler(package, at)

    def _verify_author_self(self, pkg: dict[str, Any], at: datetime) -> AuthOutcome:
        author_id = self.state["book"].get("owner_id")
        if pkg.get("operator_id") != author_id or pkg.get("signature_key") != author_id:
            return self._bad("not_book_author", "只有本册作者本人可走作者自办通道。", "author_self")
        return AuthOutcome(
            True,
            "author_self_ok",
            "作者本人到场操作，无需委托链。",
            "author_self",
            [{
                "order": 1,
                "principal_id": author_id,
                "name": pkg.get("operator_name", "作者"),
                "role": "作者/著作权人",
                "act": "亲自修订",
            }],
            [{"kind": "author_signature", "id": pkg.get("id", "package")}],
            {"id": author_id, "name": pkg.get("operator_name", "作者")},
            {"id": author_id, "name": pkg.get("operator_name", "作者")},
            ["chapter.edit"],
            self.boundary(),
        )

    def _verify_editor_self(self, pkg: dict[str, Any], at: datetime) -> AuthOutcome:
        mandate = pkg.get("mandate") or {}
        if not self.trust.verify(mandate):
            return self._bad("bad_mandate_signature", "作者上级授权无效或被篡改。", "editor_self")
        if mandate.get("issuer") != self.state["book"].get("owner_id"):
            return self._bad("mandate_issuer_not_author", "上级授权不是本册作者签发。", "editor_self")
        if pkg.get("signature_key") != mandate.get("editor_id"):
            return self._bad("not_authorized_editor", "送件人不是上级授权中的责编。", "editor_self")
        latest = self._latest_mandate(mandate)
        if not latest.get("permissions", {}).get("edit_self"):
            return self._bad("editor_edit_removed", "编辑本人处理条款已被移除。", "editor_self")
        if not _chapter_ok(latest, pkg["chapter_id"]):
            return self._bad("chapter_outside_mandate", "该章不在编辑本人权限范围内。", "editor_self")
        time_error = _time_ok(latest, at)
        if time_error:
            return self._bad(time_error, "上级授权不在有效期内。", "editor_self")
        revoked = self._is_revoked(mandate, latest)
        if revoked:
            return self._bad("mandate_revoked", f"书库本地已知撤回：{revoked.get('id')}", "editor_self")

        author = {"id": mandate["author_id"], "name": mandate.get("author_name", "")}
        editor = {"id": mandate["editor_id"], "name": mandate.get("editor_name", "")}
        return AuthOutcome(
            True,
            "editor_self_ok",
            "上级授权保留“编辑本人可处理”，因此责编可自行落稿；这不等同于允许转委托。",
            "editor_self",
            [
                {"order": 1, **author, "role": "作者/授权源头", "act": "签发上级授权"},
                {"order": 2, **editor, "role": "责任编辑", "act": "本人处理并送件"},
            ],
            [
                {"kind": "superior_mandate", "id": mandate.get("id")},
                {"kind": "effective_mandate", "id": latest.get("id")},
            ],
            author,
            editor,
            [f"chapter.edit@{pkg['chapter_id']}"],
            self.boundary(),
        )

    def _verify_portable(self, pkg: dict[str, Any], at: datetime) -> AuthOutcome:
        mandate = pkg.get("mandate") or {}
        task_pass = pkg.get("task_pass") or {}
        if not self.trust.verify(mandate):
            return self._bad("bad_mandate_signature", "上级授权签名无效或被篡改。", "portable_pass")
        if not self.trust.verify(task_pass):
            return self._bad("bad_pass_signature", "代办凭据签名无效或被篡改。", "portable_pass")

        author_id = self.state["book"].get("owner_id")
        if mandate.get("issuer") != author_id or mandate.get("author_id") != author_id:
            return self._bad("mandate_author_mismatch", "上级授权不代表本册作者。", "portable_pass")
        if task_pass.get("issuer") != mandate.get("editor_id"):
            return self._bad("pass_issuer_not_editor", "代办凭据不是受托责编签发。", "portable_pass")
        if task_pass.get("subject_id") != pkg.get("operator_id"):
            return self._bad("pass_subject_mismatch", "在场校录员不是凭据指定的人。", "portable_pass")
        if pkg.get("signature_key") != task_pass.get("subject_id"):
            return self._bad("package_not_signed_by_subject", "修订件须由凭据指定校录员签署送件。", "portable_pass")

        latest = self._latest_mandate(mandate)
        perms = latest.get("permissions", {})
        # The two facts are checked separately.  A valid sub-pass needs both
        # the underlying edit scope and the independent redelegation fact.
        if not perms.get("edit_self"):
            return self._bad("editor_edit_absent", "上级授权没有编辑本人处理权，转委托内容失去基础权限。", "portable_pass")
        if not perms.get("redelegate"):
            return self._bad(
                "redelegation_clause_absent",
                "上级授权当前没有“编辑可再委托别人”条款；编辑本人仍可能可处理，但同一校录员不能代表作者送件。",
                "portable_pass",
            )
        if not _chapter_ok(latest, pkg["chapter_id"]) or not _chapter_ok(task_pass, pkg["chapter_id"]):
            return self._bad("chapter_narrowing_failed", "章节超出上级授权或代办凭据逐层收窄后的范围。", "portable_pass")
        if "chapter.edit" not in task_pass.get("actions", []):
            return self._bad("action_not_authorized", "代办凭据未授予 chapter.edit 动作。", "portable_pass")
        for doc, label in ((latest, "上级授权"), (task_pass, "代办凭据")):
            time_error = _time_ok(doc, at)
            if time_error == "expired":
                return self._bad("pass_expired", f"{label}已超过有效期。", "portable_pass")
            if time_error == "not_yet_valid":
                return self._bad("pass_not_yet_valid", f"{label}尚未到生效时间。", "portable_pass")

        revoked = self._is_revoked(mandate, latest, task_pass)
        if revoked:
            return self._bad(
                "locally_revoked",
                f"书库本地凭据册已知撤回 {revoked.get('id')}，不能落稿。",
                "portable_pass",
            )

        author = {"id": author_id, "name": mandate.get("author_name", "")}
        editor = {"id": mandate["editor_id"], "name": mandate.get("editor_name", "")}
        proofreader = {"id": task_pass["subject_id"], "name": task_pass.get("subject_name", "")}
        return AuthOutcome(
            True,
            "portable_pass_ok",
            "校录员持可带到书库的代办凭据送件；书库凭签名委托链、章节/有效期收窄和本地已知撤回边界接件。",
            "portable_pass",
            [
                {"order": 1, **author, "role": "作者/授权源头", "act": "授权责编，并单独授予转委托条款"},
                {"order": 2, **editor, "role": "责任编辑/转委托人", "act": "把指定章节和动作收窄后签给校录员"},
                {"order": 3, **proofreader, "role": "外包校录员/在场送件人", "act": "持代办凭据实际改稿并送件"},
            ],
            [
                {"kind": "superior_mandate", "id": mandate.get("id"), "version": str(mandate.get("version"))},
                {"kind": "effective_mandate", "id": latest.get("id"), "version": str(latest.get("version"))},
                {"kind": "task_pass", "id": task_pass.get("id")},
            ],
            author,
            proofreader,
            [f"chapter.edit@{pkg['chapter_id']}"],
            self.boundary(),
        )

    def _verify_hosted(self, pkg: dict[str, Any], at: datetime) -> AuthOutcome:
        mandate = pkg.get("mandate") or {}
        receipt = pkg.get("hosted_receipt") or {}
        if not self.trust.verify(mandate):
            return self._bad("bad_mandate_signature", "上级授权无效。", "hosted_station")
        if not self.trust.verify(receipt):
            return self._bad("bad_station_receipt", "编辑站托管回执签名无效；书库不能承认校录员自称代理。", "hosted_station")

        author_id = self.state["book"].get("owner_id")
        station_id = receipt.get("station_id")
        if mandate.get("author_id") != author_id or mandate.get("issuer") != author_id:
            return self._bad("mandate_author_mismatch", "上级授权不代表作者。", "hosted_station")
        if receipt.get("editor_id") != mandate.get("editor_id"):
            return self._bad("receipt_editor_mismatch", "托管回执与责编不符。", "hosted_station")
        if mandate.get("station_id") != station_id:
            return self._bad("station_not_designated", "该站不是上级授权指定的托管编辑站。", "hosted_station")
        if pkg.get("signature_key") != station_id:
            return self._bad("package_not_sealed_by_station", "托管方式必须由编辑站封装送件。", "hosted_station")
        if receipt.get("actual_operator_id") != pkg.get("operator_id"):
            return self._bad("actual_operator_mismatch", "托管回执未保存同一实际操作者。", "hosted_station")
        if receipt.get("payload_digest") != digest_package_core(pkg):
            return self._bad("receipt_payload_mismatch", "托管回执与本笔修订的章节、底本或改字不一致。", "hosted_station")
        if receipt.get("chapter_id") != pkg["chapter_id"] or receipt.get("base_rev_id") != pkg["base_rev_id"]:
            return self._bad("receipt_scope_mismatch", "托管回执的章节或底本不一致。", "hosted_station")

        latest = self._latest_mandate(mandate)
        if not latest.get("permissions", {}).get("edit_self"):
            return self._bad("editor_edit_removed", "编辑本人处理权已被移除，托管站不能送件。", "hosted_station")
        # Deliberately do NOT require redelegate: the station is the editor's
        # own delegated desk; the proofreader is recorded as actual operator,
        # not as a new representative with redelegated authority.
        if not _chapter_ok(latest, pkg["chapter_id"]):
            return self._bad("chapter_outside_mandate", "章节超出编辑本人授权。", "hosted_station")
        for doc, label in ((latest, "授权"), (receipt, "托管回执")):
            err = _time_ok(doc, at)
            if err:
                return self._bad(err, f"{label}不在有效期内。", "hosted_station")
        revoked = self._is_revoked(mandate, latest, receipt)
        if revoked:
            return self._bad("locally_revoked", f"书库本地已知撤回：{revoked.get('id')}", "hosted_station")
        if receipt.get("id") in self.state.get("used_receipt_ids", []):
            return self._bad("receipt_replay", "该笔托管回执已经使用过，不能重复落稿。", "hosted_station")

        author = {"id": author_id, "name": mandate.get("author_name", "")}
        editor = {"id": mandate["editor_id"], "name": mandate.get("editor_name", "")}
        station = {"id": station_id, "name": receipt.get("station_name", "编辑站")}
        operator = {"id": receipt["actual_operator_id"], "name": receipt.get("actual_operator_name", "")}
        return AuthOutcome(
            True,
            "hosted_station_ok",
            "编辑站以责编本人权限办理，并保存实际操作校录员；这不是向校录员转委托代表权。",
            "hosted_station",
            [
                {"order": 1, **author, "role": "作者/授权源头", "act": "授权责编本人，并指定托管编辑站"},
                {"order": 2, **editor, "role": "责任编辑", "act": "由编辑站托管身份并逐笔审核"},
                {"order": 3, **station, "role": "编辑站/办理和留痕主体", "act": "封装并向书库送件"},
                {"order": 4, **operator, "role": "校录终端前实际操作者", "act": "发起本笔改稿，身份被站内记录"},
            ],
            [
                {"kind": "superior_mandate", "id": mandate.get("id")},
                {"kind": "hosted_receipt", "id": receipt.get("id")},
            ],
            author,
            operator,
            [f"chapter.edit@{pkg['chapter_id']}"],
            self.boundary(),
        )

    def _verify_claim_only(self, pkg: dict[str, Any], at: datetime) -> AuthOutcome:
        outcome = self._bad(
            "hosted_claim_not_accepted_offline",
            "校录员只是自称由编辑站托管；书库与该站断联且本件没有站方逐笔回执，不能承认其代理。",
            "hosted_claim_only",
        )
        outcome.actual_operator = {
            "id": pkg.get("operator_id", ""),
            "name": pkg.get("operator_name", "校录员"),
        }
        outcome.chain = [{
            "order": 1,
            "principal_id": pkg.get("operator_id", ""),
            "name": pkg.get("operator_name", "校录员"),
            "role": "校录员/自称代理",
            "act": "直接向书库声称代表作者，但缺少编辑站办理依据",
        }]
        return outcome
