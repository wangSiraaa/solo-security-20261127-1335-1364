from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from .authz import digest_package_core
from .cabinet import TrustedCabinet
from .common import now_iso, read_json, write_json_atomic
from .trust import LocalTrust


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3)}"


DELIVERY_DESIGNS: dict[str, dict[str, str]] = {
    "portable_pass": {
        "name": "可携带逐层收窄代办凭据",
        "offline_library": "可凭签名链和书库本地已知撤回记录接件",
        "revocation_boundary": "撤回未同步到书库前存在离线有效窗口",
        "clause_removal": "授权书写明删除 redelegate 即终止下游代表权；否则旧凭据原则上仍有效至届满或被撤回",
    },
    "station_hosted": {
        "name": "编辑站托管代办身份",
        "offline_library": "不承认校录员自称代理；须有站方逐笔签名回执或由站方送件",
        "revocation_boundary": "站方收到撤回即可停止新单；书库按本地已收到的撤回/回执校验",
        "clause_removal": "不需要 redelegate；站方以编辑本人权限办理，校录员仅作为实际操作者留痕",
    },
}


def iso_after(minutes: int = 60) -> str:
    from datetime import datetime, timezone

    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    role: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "role": self.role}


class AuthorizationDenied(RuntimeError):
    pass


def build_mandate(
    trust: LocalTrust,
    *,
    mandate_id: str,
    line_id: str,
    version: int,
    author: Person,
    editor: Person,
    station_id: str,
    book_id: str,
    chapter_ids: list[str],
    edit_self: bool = True,
    redelegate: bool = True,
    valid_minutes: int = 24 * 60,
    supersedes: str | None = None,
    downstream_when_clause_removed: str = "derived_passes_lose_representation",
) -> dict[str, Any]:
    doc = {
        "kind": "superior_mandate",
        "id": mandate_id,
        "line_id": line_id,
        "version": version,
        "supersedes": supersedes,
        "issuer": author.id,
        "author_id": author.id,
        "author_name": author.name,
        "editor_id": editor.id,
        "editor_name": editor.name,
        "station_id": station_id,
        "book_id": book_id,
        "chapter_ids": chapter_ids,
        "permissions": {
            # Deliberately independent facts.
            "edit_self": edit_self,
            "redelegate": redelegate,
        },
        "downstream_when_clause_removed": downstream_when_clause_removed,
        "valid_from": now_iso(),
        "expires_at": iso_after(valid_minutes),
        "issued_at": now_iso(),
    }
    return trust.sign(author.id, doc)


def build_task_pass(
    trust: LocalTrust,
    *,
    pass_id: str,
    mandate: dict[str, Any],
    editor: Person,
    proofreader: Person,
    chapter_ids: list[str],
    actions: list[str] | None = None,
    valid_minutes: int = 120,
) -> dict[str, Any]:
    # Attenuation is explicit.  The constructor refuses to widen scope.
    forbidden_chapters = set(chapter_ids) - set(mandate.get("chapter_ids", []))
    if "*" not in mandate.get("chapter_ids", []) and forbidden_chapters:
        raise AuthorizationDenied("代办凭据章节不能宽于上级授权")
    if not mandate.get("permissions", {}).get("redelegate"):
        raise AuthorizationDenied("上级授权没有转委托条款，责编不能签出校录员代办凭据")
    doc = {
        "kind": "task_pass",
        "id": pass_id,
        "issuer": editor.id,
        "mandate_id": mandate["id"],
        "mandate_line_id": mandate["line_id"],
        "mandate_version": mandate["version"],
        "author_id": mandate["author_id"],
        "author_name": mandate["author_name"],
        "editor_id": editor.id,
        "editor_name": editor.name,
        "subject_id": proofreader.id,
        "subject_name": proofreader.name,
        "book_id": mandate["book_id"],
        "chapter_ids": chapter_ids,
        "actions": actions or ["chapter.read", "chapter.edit"],
        "valid_from": now_iso(),
        "expires_at": iso_after(valid_minutes),
        "issued_at": now_iso(),
    }
    return trust.sign(editor.id, doc)


def build_revocation(
    trust: LocalTrust,
    *,
    issuer: Person,
    target_id: str,
    target_kind: str,
    reason: str,
) -> dict[str, Any]:
    doc = {
        "kind": "revocation",
        "id": new_id("revoke"),
        "issuer": issuer.id,
        "issuer_name": issuer.name,
        "target_id": target_id,
        "target_kind": target_kind,
        "reason": reason,
        "effective_at": now_iso(),
        "issued_at": now_iso(),
    }
    return trust.sign(issuer.id, doc)


def unsigned_package(
    *,
    mode: str,
    book_id: str,
    chapter_id: str,
    base_rev_id: str,
    operator: Person,
    summary: str,
    replacements: list[dict[str, Any]],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": new_id("pkg"),
        "kind": "revision_package",
        "created_at": now_iso(),
        "mode": mode,
        "book_id": book_id,
        "chapter_id": chapter_id,
        "base_rev_id": base_rev_id,
        "operator_id": operator.id,
        "operator_name": operator.name,
        "summary": summary,
        "replacements": replacements,
        **extra,
    }


class AuthorDesk:
    component = "author_desk"

    def __init__(
        self,
        *,
        person: Person,
        trust: LocalTrust,
        cabinet: TrustedCabinet,
        state_path: Path,
    ):
        self.person = person
        self.trust = trust
        self.cabinet = cabinet
        self.state_path = Path(state_path)
        self.choices = read_json(self.state_path, default={"choices": []}) or {"choices": []}

    def choose_delivery(self, mode: str, accepted: bool, note: str) -> dict[str, Any]:
        if mode not in DELIVERY_DESIGNS:
            raise ValueError(f"unknown delivery design: {mode}")
        record = {
            "at": now_iso(),
            "author_id": self.person.id,
            "author_name": self.person.name,
            "mode": mode,
            "design": DELIVERY_DESIGNS[mode],
            "accepted": accepted,
            "note": note,
            "offline_boundary_acknowledged": mode == "portable_pass",
        }
        self.choices.setdefault("choices", []).append(record)
        write_json_atomic(self.state_path, self.choices)
        self.cabinet.send(
            self.component,
            "editor_station",
            "delivery_choice",
            record,
            connected=True,
        )
        return record

    def issue_mandate(
        self,
        editor: Person,
        *,
        station_id: str,
        book_id: str,
        chapter_ids: list[str],
        mandate_id: str,
        line_id: str,
        version: int = 1,
        edit_self: bool = True,
        redelegate: bool = True,
        supersedes: str | None = None,
        library_connected: bool = True,
    ) -> dict[str, Any]:
        mandate = build_mandate(
            self.trust,
            mandate_id=mandate_id,
            line_id=line_id,
            version=version,
            author=self.person,
            editor=editor,
            station_id=station_id,
            book_id=book_id,
            chapter_ids=chapter_ids,
            edit_self=edit_self,
            redelegate=redelegate,
            supersedes=supersedes,
        )
        self.cabinet.send(self.component, "editor_station", "mandate", mandate)
        self.cabinet.send(
            self.component,
            "library",
            "mandate",
            mandate,
            connected=library_connected,
        )
        return mandate

    def amend_permissions(
        self,
        current: dict[str, Any],
        *,
        edit_self: bool | None = None,
        redelegate: bool | None = None,
        library_connected: bool = True,
    ) -> dict[str, Any]:
        next_version = int(current["version"]) + 1
        permissions = current.get("permissions", {})
        return self.issue_mandate(
            Person(
                current["editor_id"],
                current.get("editor_name", current["editor_id"]),
                "责任编辑",
            ),
            station_id=current["station_id"],
            book_id=current["book_id"],
            chapter_ids=list(current["chapter_ids"]),
            mandate_id=f"{current['line_id']}-v{next_version}",
            line_id=current["line_id"],
            version=next_version,
            edit_self=permissions.get("edit_self", True) if edit_self is None else edit_self,
            redelegate=permissions.get("redelegate", True) if redelegate is None else redelegate,
            supersedes=current["id"],
            library_connected=library_connected,
        )

    def remove_redelegation(
        self, current: dict[str, Any], *, library_connected: bool = True
    ) -> dict[str, Any]:
        return self.amend_permissions(
            current,
            edit_self=current.get("permissions", {}).get("edit_self", True),
            redelegate=False,
            library_connected=library_connected,
        )

    def restore_redelegation(
        self, current: dict[str, Any], *, library_connected: bool = True
    ) -> dict[str, Any]:
        return self.amend_permissions(
            current,
            edit_self=current.get("permissions", {}).get("edit_self", True),
            redelegate=True,
            library_connected=library_connected,
        )

    def revoke(
        self,
        target_id: str,
        target_kind: str,
        reason: str,
        *,
        library_connected: bool,
    ) -> dict[str, Any]:
        revocation = build_revocation(
            self.trust,
            issuer=self.person,
            target_id=target_id,
            target_kind=target_kind,
            reason=reason,
        )
        self.cabinet.send(self.component, "editor_station", "revocation", revocation)
        self.cabinet.send(
            self.component,
            "library",
            "revocation",
            revocation,
            connected=library_connected,
        )
        return revocation

    def submit_self_edit(
        self,
        *,
        book_id: str,
        chapter_id: str,
        base_rev_id: str,
        replacements: list[dict[str, Any]],
        summary: str,
    ) -> dict[str, Any]:
        pkg = unsigned_package(
            mode="author_self",
            book_id=book_id,
            chapter_id=chapter_id,
            base_rev_id=base_rev_id,
            operator=self.person,
            summary=summary,
            replacements=replacements,
        )
        pkg = self.trust.sign(self.person.id, pkg)
        self.cabinet.send(self.component, "library", "revision_package", pkg, connected=True)
        return pkg


class EditorStation:
    component = "editor_station"

    def __init__(
        self,
        *,
        person: Person,
        station_id: str,
        trust: LocalTrust,
        cabinet: TrustedCabinet,
        state_path: Path | str | None = None,
    ):
        self.person = person
        self.station_id = station_id
        self.trust = trust
        self.cabinet = cabinet
        self.state_path = Path(state_path) if state_path else None
        persisted = read_json(self.state_path, default={}) if self.state_path else {}
        self.current_mandate: dict[str, Any] | None = persisted.get("current_mandate")
        self.revoked_targets: set[str] = set(persisted.get("revoked_targets", []))
        self.actual_operator_log: list[dict[str, Any]] = persisted.get("actual_operator_log", [])

    def save_state(self) -> None:
        if self.state_path is None:
            return
        write_json_atomic(self.state_path, {
            "current_mandate": self.current_mandate,
            "revoked_targets": sorted(self.revoked_targets),
            "actual_operator_log": self.actual_operator_log,
        })

    def sync_cabinet(self) -> list[dict[str, Any]]:
        processed: list[dict[str, Any]] = []
        state_changed = False
        for envelope in self.cabinet.take_inbox(self.component):
            kind = envelope.get("kind")
            body = envelope.get("body", {})
            if kind == "mandate":
                self.current_mandate = body
                state_changed = True
                processed.append({"kind": kind, "id": body.get("id")})
            elif kind == "revocation":
                self.revoked_targets.add(body.get("target_id"))
                state_changed = True
                processed.append({"kind": kind, "target_id": body.get("target_id")})
            elif kind == "hosted_request":
                processed.append(self.handle_hosted_request(body))
        if state_changed:
            self.save_state()
        return processed

    def issue_pass(
        self,
        proofreader: Person,
        *,
        pass_id: str,
        mandate: dict[str, Any],
        chapter_ids: list[str],
        valid_minutes: int = 120,
    ) -> dict[str, Any]:
        if mandate.get("line_id") in self.revoked_targets or mandate.get("id") in self.revoked_targets:
            raise AuthorizationDenied("该授权已撤回，编辑站不能签代办凭据")
        task_pass = build_task_pass(
            self.trust,
            pass_id=pass_id,
            mandate=mandate,
            editor=self.person,
            proofreader=proofreader,
            chapter_ids=chapter_ids,
            valid_minutes=valid_minutes,
        )
        self.cabinet.send(self.component, "proof_terminal", "task_pass", task_pass)
        return task_pass

    def submit_self_edit(
        self,
        *,
        mandate: dict[str, Any],
        book_id: str,
        chapter_id: str,
        base_rev_id: str,
        replacements: list[dict[str, Any]],
        summary: str,
        library_connected: bool,
    ) -> dict[str, Any]:
        if not mandate.get("permissions", {}).get("edit_self"):
            raise AuthorizationDenied("编辑本人处理条款不存在")
        pkg = unsigned_package(
            mode="editor_self",
            book_id=book_id,
            chapter_id=chapter_id,
            base_rev_id=base_rev_id,
            operator=self.person,
            summary=summary,
            replacements=replacements,
            mandate=mandate,
        )
        pkg = self.trust.sign(self.person.id, pkg)
        self.cabinet.send(
            self.component,
            "library",
            "revision_package",
            pkg,
            connected=library_connected,
        )
        return pkg

    def handle_hosted_request(self, request: dict[str, Any]) -> dict[str, Any]:
        mandate = request.get("mandate") or self.current_mandate
        if mandate is None:
            raise AuthorizationDenied("编辑站没有当前上级授权，不能托管办理")
        if mandate.get("line_id") in self.revoked_targets or mandate.get("id") in self.revoked_targets:
            return {
                "status": "stopped_after_revocation",
                "request_id": request.get("id"),
                "reason": "编辑站已收到撤回，停止新单",
            }
        if not mandate.get("permissions", {}).get("edit_self"):
            return {"status": "denied", "reason": "编辑本人处理权不存在"}

        operator = Person(
            request["operator_id"],
            request.get("operator_name", request["operator_id"]),
            "proofreader",
        )
        pkg = unsigned_package(
            mode="hosted_station",
            book_id=request["book_id"],
            chapter_id=request["chapter_id"],
            base_rev_id=request["base_rev_id"],
            operator=operator,
            summary=request.get("summary", "编辑站托管办理"),
            replacements=request["replacements"],
            mandate=mandate,
            station_id=self.station_id,
            editor_id=self.person.id,
        )
        digest = digest_package_core(pkg)
        receipt = {
            "kind": "hosted_receipt",
            "id": new_id("receipt"),
            "request_id": request.get("id"),
            "station_id": self.station_id,
            "station_name": "周砚的编辑站",
            "editor_id": self.person.id,
            "editor_name": self.person.name,
            "actual_operator_id": operator.id,
            "actual_operator_name": operator.name,
            "chapter_id": request["chapter_id"],
            "base_rev_id": request["base_rev_id"],
            "payload_digest": digest,
            "issued_at": now_iso(),
            "valid_from": now_iso(),
            "expires_at": iso_after(15),
        }
        receipt = self.trust.sign(self.station_id, receipt)
        pkg["hosted_receipt"] = receipt
        pkg = self.trust.sign(self.station_id, pkg)

        self.actual_operator_log.append({
            "at": now_iso(),
            "request_id": request.get("id"),
            "actual_operator_id": operator.id,
            "actual_operator_name": operator.name,
            "represented_editor": self.person.id,
            "represented_author": mandate.get("author_id"),
            "chapter_id": request["chapter_id"],
        })
        self.save_state()
        self.cabinet.send(
            self.component,
            "library",
            "revision_package",
            pkg,
            connected=bool(request.get("library_connected", True)),
        )
        return {"status": "sealed_for_library", "package": pkg, "receipt": receipt}


class ProofTerminal:
    component = "proof_terminal"

    def __init__(self, *, person: Person, trust: LocalTrust, cabinet: TrustedCabinet):
        self.person = person
        self.trust = trust
        self.cabinet = cabinet

    def explain_portable(self, mandate: dict[str, Any], task_pass: dict[str, Any]) -> str:
        chapter_scope = "、".join(task_pass.get("chapter_ids", []))
        return "\n".join([
            "校录终端当前会话",
            f"  当前在场操作人：{self.person.name}（{self.person.id}，外包校录员）",
            f"  所代表作者：{mandate.get('author_name')}（{mandate.get('author_id')}）",
            f"  经过的手：{mandate.get('author_name')} → "
            f"{mandate.get('editor_name')} → {self.person.name}（本终端）",
            f"  上级授权事实：编辑本人可处理="
            f"{mandate.get('permissions', {}).get('edit_self')}；"
            f"编辑可再委托="
            f"{mandate.get('permissions', {}).get('redelegate')}",
            f"  本笔具体授权动作：{', '.join(task_pass.get('actions', []))}，"
            f"章节仅限 {chapter_scope}",
            f"  凭据有效期：{task_pass.get('valid_from')} 至 {task_pass.get('expires_at')}",
            "  送件依据：作者上级授权 + 责编代办凭据；书库另按本地已知撤回记录校验。",
        ])

    def submit_portable(
        self,
        *,
        mandate: dict[str, Any],
        task_pass: dict[str, Any],
        book_id: str,
        chapter_id: str,
        base_rev_id: str,
        replacements: list[dict[str, Any]],
        summary: str,
    ) -> dict[str, Any]:
        if task_pass.get("subject_id") != self.person.id:
            raise AuthorizationDenied("终端前的人与凭据主体不一致")
        pkg = unsigned_package(
            mode="portable_pass",
            book_id=book_id,
            chapter_id=chapter_id,
            base_rev_id=base_rev_id,
            operator=self.person,
            summary=summary,
            replacements=replacements,
            mandate=mandate,
            task_pass=task_pass,
        )
        pkg = self.trust.sign(self.person.id, pkg)
        self.cabinet.send(self.component, "library", "revision_package", pkg, connected=True)
        return pkg

    def submit_hosted_claim_only(
        self,
        *,
        book_id: str,
        chapter_id: str,
        base_rev_id: str,
        replacements: list[dict[str, Any]],
        summary: str,
    ) -> dict[str, Any]:
        pkg = unsigned_package(
            mode="hosted_claim_only",
            book_id=book_id,
            chapter_id=chapter_id,
            base_rev_id=base_rev_id,
            operator=self.person,
            summary=summary,
            replacements=replacements,
        )
        pkg = self.trust.sign(self.person.id, pkg)
        self.cabinet.send(self.component, "library", "revision_package", pkg, connected=True)
        return pkg

    def request_hosted(
        self,
        *,
        book_id: str,
        chapter_id: str,
        base_rev_id: str,
        replacements: list[dict[str, Any]],
        summary: str,
        library_connected: bool,
    ) -> dict[str, Any]:
        request = {
            "id": new_id("request"),
            "kind": "hosted_request",
            "created_at": now_iso(),
            "operator_id": self.person.id,
            "operator_name": self.person.name,
            "book_id": book_id,
            "chapter_id": chapter_id,
            "base_rev_id": base_rev_id,
            "replacements": replacements,
            "summary": summary,
            "library_connected": library_connected,
        }
        request = self.trust.sign(self.person.id, request)
        self.cabinet.send(self.component, "editor_station", "hosted_request", request, connected=True)
        return request
