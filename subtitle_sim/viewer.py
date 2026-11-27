"""Local text viewers for new and old subtitle seats."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .common import SimError, Store, now_iso, parse_message, sha256_hex, sign_doc, verify_doc


class Viewer:
    def __init__(self, store: Store, seat_id: str):
        self.store = store
        self.seat_id = seat_id
        self.capability = store.load_capability(seat_id)
        if self.capability["seat_id"] != seat_id:
            raise SimError("能力声明中的席号与 --seat 不一致")
        self.kind = self.capability["kind"]

    def list_messages(self) -> List[Path]:
        inbox = self.store.seat_inbox(self.seat_id)
        return sorted(inbox.glob("*.msg"))

    def _resolve_message_file(self, message: str) -> Path:
        inbox = self.store.seat_inbox(self.seat_id)
        candidates = [
            inbox / message,
            inbox / f"{message}.msg",
        ]
        for p in candidates:
            if p.exists():
                return p
        matches = sorted(inbox.glob(f"*{message}*.msg"))
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise SimError(f"{self.seat_id} 信箱中没有消息 {message}")
        raise SimError(f"消息标识 {message} 不唯一: {[p.name for p in matches]}")

    def view(self, message: str) -> Dict[str, Any]:
        path = self._resolve_message_file(message)
        raw = path.read_text(encoding="utf-8")
        parsed = parse_message(raw)
        headers = parsed["headers"]
        body = parsed["body"]
        note = parsed.get("note")

        if self.kind == "old":
            screen = self._render_old(headers, body, note, path.name)
            receipt_type = "old-received/v1"
        else:
            screen = self._render_new(headers, body, note, path.name)
            receipt_type = "new-view-report/v1"

        screen_path = self.store.seat_screens(self.seat_id) / path.name.replace(".msg", ".screen.txt")
        self.store.write_text(screen_path, screen)
        screen_text = screen_path.read_text(encoding="utf-8")

        receipt: Dict[str, Any] = {
            "schema": "subtitle-sim/receipt/v1",
            "receipt_type": receipt_type,
            "seat_id": self.seat_id,
            "kind": self.kind,
            "scene_id": headers.get("Scene"),
            "session_id": headers.get("Session"),
            "profile": headers.get("Profile"),
            "message_id": headers.get("Message-Id"),
            "sequence": headers.get("Sequence"),
            "envelope_version": _maybe_int(headers.get("Version")),
            "screen_file": str(screen_path.relative_to(self.store.root)),
            "screen_sha256": sha256_hex(screen_text),
            "displayed_body_sha256": sha256_hex(body),
            "viewed_at": now_iso(),
        }

        if self.kind == "new":
            receipt.update(
                {
                    "status": "actually-displayed-and-version-reported",
                    "viewed_version": _maybe_int(headers.get("Version")),
                    "can_report_view_version": True,
                    "displayed_note_sha256": sha256_hex(note) if note else None,
                    "duty_note_displayed": bool(note),
                }
            )
        else:
            # Deliberately narrow claim: the old terminal knows 正文 and 已收.
            receipt.update(
                {
                    "status": "正文已显示_已收",
                    "ack": "已收",
                    "can_report_view_version": False,
                    "viewed_version": None,
                    "displayed_note_sha256": None,
                    "note_endorsed": False,
                    "note_visible_on_terminal": False,
                }
            )

        signed = sign_doc(receipt, self.store.key(self.seat_id)["secret_hex"])
        receipt_path = self.store.seat_receipts(self.seat_id) / f"{receipt['message_id']}.receipt.json"
        self.store.write_json(receipt_path, signed)

        state_path = self.store.seat_state(self.seat_id) / "viewed.json"
        state = self.store.read_json(state_path) if state_path.exists() else {"schema": "subtitle-sim/view-state/v1", "seat_id": self.seat_id, "latest_by_sequence": {}}
        latest = state.setdefault("latest_by_sequence", {})
        seq = receipt.get("sequence") or receipt["message_id"]
        prev = latest.get(seq)
        latest[seq] = {
            "message_id": receipt["message_id"],
            "envelope_version": receipt["envelope_version"],
            "viewed_version": receipt["viewed_version"],
            "body_sha256": receipt["displayed_body_sha256"],
            "viewed_at": receipt["viewed_at"],
            "previous": prev,
        }
        self.store.write_json(state_path, state)
        return {"screen": screen_text, "screen_path": screen_path, "receipt": signed, "receipt_path": receipt_path}

    def _common_header(self, headers: Dict[str, str], title: str) -> List[str]:
        return [
            "=" * 72,
            title,
            f"席号: {self.seat_id}    场次: {headers.get('Scene', '?')}",
            f"会话约定: {headers.get('Session', '?')} / {headers.get('Profile', '?')}",
            f"消息: {headers.get('Message-Id', '?')}    期号: {headers.get('Sequence', '?')}    封装版本: {headers.get('Version', '?')}",
            "-" * 72,
        ]

    def _render_new(self, headers: Dict[str, str], body: str, note: Optional[str], filename: str) -> str:
        lines = self._common_header(headers, f"新席收看器（可显示场记备注/回报版本） {filename}")
        lines += [
            "【观众可见稿 · 进入联播字幕归宿】",
            body,
            "-" * 72,
            "【当班场记备注 · 仅供当班人，不进观众可见稿】",
            note if note else "（本条无场记备注）",
            "-" * 72,
            f"实际显示正文 SHA256: {sha256_hex(body)}",
            f"实际回报看过版本: v{headers.get('Version', '?')}",
            "新席回单: 已显示，并签名回报实际版本",
            "=" * 72,
        ]
        return "\n".join(lines)

    def _render_old(self, headers: Dict[str, str], body: str, note: Optional[str], filename: str) -> str:
        lines = self._common_header(headers, f"旧席收看器（只解释正文与已收） {filename}")
        lines += [
            "【正文 / 旧席可显示稿】",
            body,
            "-" * 72,
        ]
        if note is not None:
            # Defensive: normal direct routing strips NOTE before delivery.
            lines.append("[旧席忽略无法解释的场记备注字段；不会在回单中背书]")
        else:
            lines.append("[未收到/不解释场记备注；备注保留在新席与值班审计归宿]")
        lines += [
            f"已收。正文 SHA256: {sha256_hex(body)}",
            "旧席回单仅证明正文显示与“已收”，不报告新版本、不背书备注。",
            "=" * 72,
        ]
        return "\n".join(lines)

    def latest_state(self) -> Dict[str, Any]:
        path = self.store.seat_state(self.seat_id) / "viewed.json"
        if not path.exists():
            return {"seat_id": self.seat_id, "latest_by_sequence": {}}
        return self.store.read_json(path)


def _maybe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
