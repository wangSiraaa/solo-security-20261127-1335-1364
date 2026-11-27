from __future__ import annotations

from pathlib import Path
from typing import Any

from .audit import AuditLog
from .authz import Authorizer, digest_package_core
from .cabinet import TrustedCabinet
from .common import now_iso, parse_iso, read_json, write_json_atomic
from .trust import LocalTrust


class TextConflictError(ValueError):
    pass


def apply_replacements(text: str, replacements: list[dict[str, str]]) -> str:
    result = text
    for item in replacements:
        old = item["old"]
        new = item["new"]
        count = result.count(old)
        if count == 0:
            raise TextConflictError(f"底本中找不到待替换文字：{old!r}")
        if count > 1 and not item.get("replace_all"):
            raise TextConflictError(f"待替换文字不唯一（{count} 处）：{old!r}")
        result = result.replace(old, new, 1 if not item.get("replace_all") else count)
    return result


class Library:
    """The revision library: it lands words only from a verified basis."""

    def __init__(self, path: Path, trust: LocalTrust, audit: AuditLog):
        self.path = Path(path)
        self.trust = trust
        self.audit = audit
        self.state: dict[str, Any] = read_json(self.path) or {}
        if not self.state:
            raise FileNotFoundError(f"library state not initialized: {path}")

    @classmethod
    def initialize(
        cls,
        path: Path,
        trust: LocalTrust,
        audit: AuditLog,
        *,
        book_id: str,
        title: str,
        owner_id: str,
        owner_name: str,
        chapters: list[dict[str, str]],
    ) -> "Library":
        chapter_state: dict[str, Any] = {}
        for chapter in chapters:
            chapter_state[chapter["id"]] = {
                "id": chapter["id"],
                "title": chapter["title"],
                "text": chapter["text"],
                "head_rev_id": "rev-0000",
                "lock": None,
            }
        state = {
            "schema": 1,
            "book": {
                "id": book_id,
                "title": title,
                "owner_id": owner_id,
                "owner_name": owner_name,
                "chapters": chapter_state,
            },
            "revisions": [
                {
                    "rev_id": "rev-0000",
                    "rev_no": 0,
                    "chapter_id": "*",
                    "base_rev_id": None,
                    "status": "landed",
                    "mode": "initial",
                    "represented_author": {"id": owner_id, "name": owner_name},
                    "actual_operator": {"id": "system", "name": "初始化"},
                    "summary": "两章初始正文入库",
                    "landed_at": now_iso(),
                }
            ],
            "submissions": [],
            "pending_merges": [],
            "known_mandates": [],
            "revocations": [],
            "used_receipt_ids": [],
            "next_rev_no": 1,
            "next_receipt_no": 1,
            "next_merge_no": 1,
            "station_link": "connected",
        }
        write_json_atomic(path, state)
        return cls(path, trust, audit)

    def save(self) -> None:
        write_json_atomic(self.path, self.state)

    def set_station_link(self, connected: bool) -> None:
        self.state["station_link"] = "connected" if connected else "disconnected"
        self.audit.record(
            "library",
            "station_link_changed",
            connected=connected,
            boundary=(
                "书库可与编辑站核对/同步"
                if connected
                else "书库只承认本地已有的办理依据和撤回记录，不承认校录员口头自称代理"
            ),
        )
        self.save()

    def register_mandate(self, mandate: dict[str, Any]) -> dict[str, Any]:
        if not self.trust.verify(mandate):
            raise ValueError("refusing to register invalid mandate signature")
        known = self.state["known_mandates"]
        if not any(m.get("id") == mandate.get("id") for m in known):
            known.append(mandate)
            self.audit.record(
                "library",
                "mandate_registered",
                mandate_id=mandate.get("id"),
                line_id=mandate.get("line_id"),
                version=mandate.get("version"),
                permissions=mandate.get("permissions"),
            )
            self.save()
        return mandate

    def register_revocation(self, revocation: dict[str, Any]) -> dict[str, Any]:
        if not self.trust.verify(revocation):
            raise ValueError("refusing to register invalid revocation signature")
        if not any(r.get("id") == revocation.get("id") for r in self.state["revocations"]):
            self.state["revocations"].append(revocation)
            self.audit.record(
                "library",
                "revocation_registered_locally",
                revocation_id=revocation.get("id"),
                target_id=revocation.get("target_id"),
            )
            self.save()
        return revocation

    def lock_chapter(
        self,
        chapter_id: str,
        *,
        holder_id: str,
        holder_name: str,
        cycle_id: str,
        cycle_name: str,
        station_id: str | None = None,
    ) -> dict[str, Any]:
        chapter = self.state["book"]["chapters"][chapter_id]
        lock = {
            "chapter_id": chapter_id,
            "cycle_id": cycle_id,
            "cycle_name": cycle_name,
            "holder_id": holder_id,
            "holder_name": holder_name,
            "station_id": station_id,
            "acquired_rev_id": chapter["head_rev_id"],
            "acquired_at": now_iso(),
        }
        chapter["lock"] = lock
        self.audit.record(
            holder_id,
            "chapter_locked",
            chapter_id=chapter_id,
            cycle=cycle_name,
            acquired_rev_id=lock["acquired_rev_id"],
        )
        self.save()
        return lock

    def release_lock(self, chapter_id: str) -> None:
        self.state["book"]["chapters"][chapter_id]["lock"] = None
        self.save()

    def merge_pending(
        self,
        merge_id: str,
        *,
        resolver_id: str,
        resolver_name: str,
        summary: str | None = None,
    ) -> dict[str, Any]:
        pending = next(
            (item for item in self.state["pending_merges"] if item.get("merge_id") == merge_id),
            None,
        )
        if pending is None:
            raise KeyError(merge_id)
        if pending.get("status") != "awaiting_manual_merge":
            raise ValueError(f"{merge_id} is {pending.get('status')}")
        if resolver_id != self.state["book"].get("owner_id"):
            raise PermissionError("只有作者或其书面指定的合并复核人可以裁决待合并件")

        chapter_id = pending["chapter_id"]
        chapter = self.state["book"]["chapters"][chapter_id]
        package = pending["package"]
        old_text = chapter["text"]
        new_text = apply_replacements(old_text, package.get("replacements", []))

        rev_no = self.state["next_rev_no"]
        rev_id = f"rev-{rev_no:04d}"
        self.state["next_rev_no"] += 1
        chapter["text"] = new_text
        chapter["head_rev_id"] = rev_id

        original_operator = (
            pending.get("authorization", {}).get("actual_operator")
            or {"id": package.get("operator_id"), "name": package.get("operator_name")}
        )
        revision = {
            "rev_id": rev_id,
            "rev_no": rev_no,
            "landed_at": now_iso(),
            "chapter_id": chapter_id,
            "base_rev_id": pending.get("actual_head_rev_id"),
            "mode": "manual_merge",
            "merge_id": merge_id,
            "summary": summary or f"作者复核后合并 {merge_id} 的校录建议",
            "replacements": package.get("replacements", []),
            "text_before": old_text,
            "text_after": new_text,
            "represented_author": {"id": resolver_id, "name": resolver_name},
            "actual_operator": {"id": resolver_id, "name": resolver_name},
            "proposed_by": original_operator,
            "chain": [
                {
                    "order": 1,
                    "principal_id": resolver_id,
                    "name": resolver_name,
                    "role": "作者/合并裁决人",
                    "act": f"在最新 {pending.get('actual_head_rev_id')} 上合并旧建议",
                },
                {
                    "order": 2,
                    "principal_id": original_operator.get("id"),
                    "name": original_operator.get("name"),
                    "role": "原修订建议提出/操作者",
                    "act": f"见 {merge_id} 与原收件 {pending.get('receipt_id')}",
                },
            ],
            "basis": pending.get("authorization", {}).get("basis", []),
            "status": "landed",
        }
        self.state["revisions"].append(revision)
        pending["status"] = "merged"
        pending["merged_rev_id"] = rev_id
        pending["merged_at"] = now_iso()
        self.audit.record(
            resolver_id,
            "pending_merge_resolved",
            merge_id=merge_id,
            rev_id=rev_id,
            proposed_by=original_operator,
        )
        self.save()
        return revision

    def receive_package(
        self,
        package: dict[str, Any],
        *,
        cabinet_envelope_id: str | None = None,
    ) -> dict[str, Any]:
        receipt_no = self.state["next_receipt_no"]
        receipt_id = f"rcp-{receipt_no:04d}"
        self.state["next_receipt_no"] += 1
        received_at = now_iso()
        self.state["verification_time"] = received_at

        authorizer = Authorizer(self.trust, self.state)
        outcome = authorizer.verify(package, parse_iso(received_at))
        chapter_id = package.get("chapter_id", "")
        chapter = self.state["book"]["chapters"].get(chapter_id)

        submission = {
            "receipt_id": receipt_id,
            "cabinet_envelope_id": cabinet_envelope_id,
            "package_id": package.get("id"),
            "received_at": received_at,
            "chapter_id": chapter_id,
            "base_rev_id": package.get("base_rev_id"),
            "mode": package.get("mode"),
            "operator_id": package.get("operator_id"),
            "operator_name": package.get("operator_name"),
            "outcome": outcome.to_dict(),
            "status": "rejected",
        }

        if not outcome.ok or chapter is None:
            submission["status"] = "rejected"
            submission["reason_code"] = outcome.code
            submission["reason"] = outcome.reason
            self.state["submissions"].append(submission)
            self.audit.record(
                "library",
                "revision_rejected",
                receipt_id=receipt_id,
                code=outcome.code,
                reason=outcome.reason,
                package_id=package.get("id"),
            )
            self.save()
            return {"accepted": False, "status": "rejected", "receipt": submission, "outcome": outcome.to_dict()}

        if outcome.mode == "hosted_station":
            receipt_id_value = package.get("hosted_receipt", {}).get("id")
            used = self.state.setdefault("used_receipt_ids", [])
            if receipt_id_value and receipt_id_value not in used:
                used.append(receipt_id_value)

        # Concurrency / proof-cycle control.  A non-holder does not overwrite
        # the holder's newer proof cycle; park the exact proposed bytes.
        lock = chapter.get("lock")
        lock_conflict = bool(
            lock
            and outcome.mode == "portable_pass"
            and lock.get("holder_id") != outcome.actual_operator.get("id")
        )
        stale_base = package.get("base_rev_id") != chapter.get("head_rev_id")

        if lock_conflict or stale_base:
            reasons: list[str] = []
            if lock_conflict:
                reasons.append("chapter_locked_for_other_proof_cycle")
            if stale_base:
                reasons.append("stale_base_revision")
            merge_no = self.state["next_merge_no"]
            merge_id = f"merge-{merge_no:03d}"
            self.state["next_merge_no"] += 1
            pending = {
                "merge_id": merge_id,
                "receipt_id": receipt_id,
                "created_at": received_at,
                "chapter_id": chapter_id,
                "base_rev_id": package.get("base_rev_id"),
                "actual_head_rev_id": chapter["head_rev_id"],
                "lock": lock,
                "reason_codes": reasons,
                "status": "awaiting_manual_merge",
                "package": package,
                "authorization": outcome.to_dict(),
                "proposed_replacements": package.get("replacements", []),
                "current_text_snapshot": chapter["text"],
            }
            self.state["pending_merges"].append(pending)
            submission["status"] = "pending_merge"
            submission["merge_id"] = merge_id
            submission["reason_codes"] = reasons
            self.state["submissions"].append(submission)
            self.audit.record(
                "library",
                "revision_pending_merge",
                receipt_id=receipt_id,
                merge_id=merge_id,
                reasons=reasons,
                base_rev_id=package.get("base_rev_id"),
                actual_head_rev_id=chapter["head_rev_id"],
            )
            self.save()
            return {
                "accepted": True,
                "landed": False,
                "status": "pending_merge",
                "merge_id": merge_id,
                "reasons": reasons,
                "receipt": submission,
                "outcome": outcome.to_dict(),
            }

        try:
            new_text = apply_replacements(chapter["text"], package.get("replacements", []))
        except TextConflictError as exc:
            submission["status"] = "rejected"
            submission["reason_code"] = "text_conflict"
            submission["reason"] = str(exc)
            self.state["submissions"].append(submission)
            self.audit.record("library", "text_conflict", receipt_id=receipt_id, error=str(exc))
            self.save()
            return {"accepted": False, "status": "rejected", "reason": str(exc), "receipt": submission}

        rev_no = self.state["next_rev_no"]
        rev_id = f"rev-{rev_no:04d}"
        self.state["next_rev_no"] += 1
        old_text = chapter["text"]
        chapter["text"] = new_text
        chapter["head_rev_id"] = rev_id

        revision = {
            "rev_id": rev_id,
            "rev_no": rev_no,
            "receipt_id": receipt_id,
            "landed_at": received_at,
            "chapter_id": chapter_id,
            "base_rev_id": package.get("base_rev_id"),
            "mode": outcome.mode,
            "reason_code": outcome.code,
            "summary": package.get("summary", ""),
            "replacements": package.get("replacements", []),
            "text_before": old_text,
            "text_after": new_text,
            "represented_author": outcome.represented_author,
            "actual_operator": outcome.actual_operator,
            "chain": outcome.chain,
            "basis": outcome.basis,
            "local_revocation_boundary": outcome.boundary,
            "cabinet_envelope_id": cabinet_envelope_id,
            "package_id": package.get("id"),
            "status": "landed",
        }
        self.state["revisions"].append(revision)
        submission["status"] = "landed"
        submission["rev_id"] = rev_id
        self.state["submissions"].append(submission)
        self.audit.record(
            "library",
            "revision_landed",
            rev_id=rev_id,
            receipt_id=receipt_id,
            chapter_id=chapter_id,
            mode=outcome.mode,
            operator=outcome.actual_operator,
            represented_author=outcome.represented_author,
        )
        self.save()
        return {
            "accepted": True,
            "landed": True,
            "status": "landed",
            "rev_id": rev_id,
            "revision": revision,
            "receipt": submission,
            "outcome": outcome.to_dict(),
        }

    def accept_cabinet(self, cabinet: TrustedCabinet) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for envelope in cabinet.take_inbox("library"):
            verified = cabinet.verify_envelope(envelope)
            kind = envelope.get("kind")
            body = envelope.get("body", {})
            if not verified:
                results.append({"accepted": False, "status": "rejected", "reason": "bad_envelope_signature"})
                continue
            if kind == "mandate":
                self.register_mandate(body)
                results.append({"status": "mandate_registered", "id": body.get("id")})
            elif kind == "revocation":
                self.register_revocation(body)
                results.append({"status": "revocation_registered", "id": body.get("id")})
            elif kind == "revision_package":
                result = self.receive_package(body, cabinet_envelope_id=envelope.get("id"))
                results.append(result)
            else:
                results.append({"status": "ignored", "kind": kind})
        return results

    def chapter_text(self, chapter_id: str) -> str:
        return self.state["book"]["chapters"][chapter_id]["text"]

    def revision_report(self) -> list[dict[str, Any]]:
        return [r for r in self.state["revisions"] if r.get("rev_no", 0) > 0]

    def author_report_text(self) -> str:
        lines = [
            f"《{self.state['book']['title']}》修订号留痕（重启后从 library.json 读取）",
            "=" * 64,
        ]
        for rev in self.revision_report():
            author = rev.get("represented_author") or {}
            operator = rev.get("actual_operator") or {}
            chain = " → ".join(
                f"{step.get('name')}({step.get('role')})" for step in rev.get("chain", [])
            )
            lines.extend([
                f"{rev['rev_id']}  {rev['chapter_id']}  {rev['landed_at']}",
                f"  方式：{rev['mode']}；说明：{rev.get('summary', '')}",
                f"  代表作者：{author.get('name', '?')}；实际落字/操作者：{operator.get('name', '?')}",
            ])
            if rev.get("proposed_by"):
                lines.append(
                    f"  合并来源：裁决落稿人 {operator.get('name', '?')}；"
                    f"原建议/实际改字人 {rev['proposed_by'].get('name', '?')}"
                )
            lines.extend([
                f"  经过：{chain}",
                f"  改字：{rev.get('replacements')}",
                "",
            ])
        pending = [m for m in self.state.get("pending_merges", []) if m.get("status") == "awaiting_manual_merge"]
        if pending:
            lines.append("待合并，未覆盖现稿：")
            for merge in pending:
                pkg = merge["package"]
                lines.append(
                    f"  {merge['merge_id']} {merge['chapter_id']} "
                    f"base={merge['base_rev_id']} head={merge['actual_head_rev_id']} "
                    f"reasons={','.join(merge['reason_codes'])} "
                    f"proposal={pkg.get('replacements')}"
                )
        return "\n".join(lines)
