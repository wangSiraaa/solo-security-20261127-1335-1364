"""Shared helpers, on-disk layout, signed JSON, and custom text messages."""
from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from . import ed25519

DIRECT_PROFILE = "direct-common-text/v1"
RELAY_PROFILE = "relay-turntable-terminated/v1"

PROFILE_LABELS = {
    DIRECT_PROFILE: "两端直接协商共同文字；转台不接触正文",
    RELAY_PROFILE: "转台分别终结两段通道并生成旧式播稿",
}

PROFILE_CONTRACTS = {
    DIRECT_PROFILE: {
        "message_format": "SIMSG/1 custom UTF-8 text",
        "common_interpretable_part": "--BODY-- 中的观众可见正文",
        "new_only_part": "--NOTE-- 中的当班场记备注",
        "old_terminal_output": "正文显示稿 + 签名“已收”回单",
        "same_text_basis": "两端回单中的 displayed_body_sha256 相等",
    },
    RELAY_PROFILE: {
        "message_format": "SIMSG/1 custom UTF-8 text on each terminated leg",
        "new_leg": "原稿观众正文 + 值班备注",
        "turntable_only_resource": "受托方言读音表",
        "old_leg": "转台生成的旧式播稿正文",
        "accounting_basis": "转台签名的逐段转换证明绑定源文和旧稿摘要",
    },
}


class SimError(Exception):
    """Expected simulation/security-policy failure."""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_bytes(doc: Any) -> bytes:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    import hashlib

    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def doc_hash(doc: Dict[str, Any]) -> str:
    return sha256_hex(canonical_bytes(doc))


def sign_doc(doc: Dict[str, Any], secret_hex: str) -> Dict[str, Any]:
    out = copy.deepcopy(doc)
    out.pop("signature", None)
    sig = ed25519.sign(canonical_bytes(out), bytes.fromhex(secret_hex))
    out["signature"] = sig.hex()
    return out


def verify_doc(doc: Dict[str, Any], public_hex: str) -> bool:
    if not isinstance(doc, dict) or "signature" not in doc:
        return False
    unsigned = copy.deepcopy(doc)
    sig_hex = unsigned.pop("signature")
    try:
        sig = bytes.fromhex(sig_hex)
        pub = bytes.fromhex(public_hex)
    except ValueError:
        return False
    return ed25519.verify(canonical_bytes(unsigned), sig, pub)


class Store:
    """All state is small files under one local root."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).resolve()

    # ---- directories -----------------------------------------------------
    @property
    def trusted_dir(self) -> Path:
        return self.root / "trusted"

    @property
    def capabilities_dir(self) -> Path:
        return self.trusted_dir / "capabilities"

    @property
    def consents_dir(self) -> Path:
        return self.trusted_dir / "consents"

    @property
    def keyring_file(self) -> Path:
        return self.trusted_dir / "keyring.json"

    @property
    def private_keys_dir(self) -> Path:
        return self.root / "private" / "keys"

    @property
    def turntable_private_dir(self) -> Path:
        return self.root / "private" / "turntable"

    @property
    def access_dir(self) -> Path:
        return self.root / "access"

    @property
    def messages_dir(self) -> Path:
        return self.access_dir / "messages"

    @property
    def payloads_dir(self) -> Path:
        return self.access_dir / "payloads"

    @property
    def audience_dir(self) -> Path:
        # 观众可见稿的固定归宿。
        return self.root / "audience"

    @property
    def duty_dir(self) -> Path:
        # 当班场记备注/审计材料的固定归宿，不与 audience/ 混放。
        return self.root / "duty"

    @property
    def sessions_dir(self) -> Path:
        return self.access_dir / "sessions"

    @property
    def proofs_dir(self) -> Path:
        return self.access_dir / "proofs"

    @property
    def logins_dir(self) -> Path:
        return self.access_dir / "logins"

    @property
    def audit_dir(self) -> Path:
        return self.root / "audit"

    @property
    def turntable_inbox(self) -> Path:
        return self.root / "turntable" / "inbox"

    @property
    def turntable_sent(self) -> Path:
        return self.root / "turntable" / "converted"

    def seat_dir(self, seat_id: str) -> Path:
        return self.root / "seats" / seat_id

    def seat_inbox(self, seat_id: str) -> Path:
        return self.seat_dir(seat_id) / "inbox"

    def seat_screens(self, seat_id: str) -> Path:
        return self.seat_dir(seat_id) / "screens"

    def seat_receipts(self, seat_id: str) -> Path:
        return self.seat_dir(seat_id) / "receipts"

    def seat_state(self, seat_id: str) -> Path:
        return self.seat_dir(seat_id) / "state"

    def ensure_dirs(self) -> None:
        for p in [
            self.root,
            self.capabilities_dir,
            self.consents_dir,
            self.private_keys_dir,
            self.turntable_private_dir,
            self.messages_dir,
            self.payloads_dir,
            self.audience_dir,
            self.duty_dir,
            self.sessions_dir,
            self.proofs_dir,
            self.logins_dir,
            self.audit_dir,
            self.turntable_inbox,
            self.turntable_sent,
        ]:
            p.mkdir(parents=True, exist_ok=True)
        for seat in ("new-1", "old-1", "old-2"):
            for p in (
                self.seat_inbox(seat),
                self.seat_screens(seat),
                self.seat_receipts(seat),
                self.seat_state(seat),
            ):
                p.mkdir(parents=True, exist_ok=True)
        try:
            self.private_keys_dir.chmod(0o700)
            self.turntable_private_dir.chmod(0o700)
        except OSError:
            pass

    # ---- JSON/text IO ----------------------------------------------------
    def write_json(self, path: Path, doc: Any, private: bool = False) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if private:
            try:
                path.chmod(0o600)
            except OSError:
                pass
        return path

    def read_json(self, path: str | Path) -> Any:
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        return json.loads(p.read_text(encoding="utf-8"))

    def write_text(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text, encoding="utf-8")
        return path

    # ---- keys/keyring ----------------------------------------------------
    def save_key(self, name: str) -> Tuple[str, str]:
        secret = ed25519.generate_secret_key()
        pub = ed25519.public_key(secret)
        secret_hex = secret.hex()
        pub_hex = pub.hex()
        self.write_json(
            self.private_keys_dir / f"{name}.key.json",
            {"name": name, "secret_hex": secret_hex, "public_hex": pub_hex},
            private=True,
        )
        return secret_hex, pub_hex

    def key(self, name: str) -> Dict[str, str]:
        return self.read_json(self.private_keys_dir / f"{name}.key.json")

    def keyring(self) -> Dict[str, Any]:
        return self.read_json(self.keyring_file)

    def public_for(self, name: str) -> str:
        ring = self.keyring()["keys"]
        if name not in ring:
            raise SimError(f"可信钥匙串中没有 {name} 的公钥")
        return ring[name]

    # ---- capabilities ---------------------------------------------------
    def capability_path(self, seat_id: str) -> Path:
        return self.capabilities_dir / f"{seat_id}.cap.json"

    def load_capability(self, seat_id: str) -> Dict[str, Any]:
        path = self.capability_path(seat_id)
        if not path.exists():
            raise SimError(f"缺少经可信通道交来的能力声明: {path}")
        cap = self.read_json(path)
        pub = self.public_for(seat_id)
        if not verify_doc(cap, pub):
            raise SimError(f"能力声明签名/可信文件校验失败: {seat_id}")
        return cap

    # ---- active session -------------------------------------------------
    def active_pointer_path(self) -> Path:
        return self.access_dir / "active-session.json"

    def write_active_pointer(self, session_id: str, valid: bool, reason: str = "") -> None:
        doc = {
            "schema": "subtitle-sim/active-session-pointer/v1",
            "active_session_id": session_id,
            "convention_valid": valid,
            "reason": reason,
            "updated_at": now_iso(),
        }
        access_key = self.key("access")["secret_hex"]
        self.write_json(self.active_pointer_path(), sign_doc(doc, access_key))

    def active_pointer(self) -> Optional[Dict[str, Any]]:
        p = self.active_pointer_path()
        if not p.exists():
            return None
        doc = self.read_json(p)
        if not verify_doc(doc, self.public_for("access")):
            raise SimError("当前会话指针签名无效")
        return doc

    def load_session(self, session_id: str) -> Dict[str, Any]:
        doc = self.read_json(self.sessions_dir / f"{session_id}.session.json")
        if not verify_doc(doc, self.public_for("access")):
            raise SimError(f"会话约定签名无效: {session_id}")
        return doc

    def active_session(self, require_current_caps: bool = True) -> Dict[str, Any]:
        pointer = self.active_pointer()
        if pointer is None:
            raise SimError("尚未核对/建立本期会话约定；只有登录成功不能发送或确认联播内容")
        if not pointer.get("convention_valid"):
            raise SimError(
                "原会话约定已失效，必须先重建会话: " + pointer.get("reason", "capability/roster changed")
            )
        session = self.load_session(pointer["active_session_id"])
        if require_current_caps:
            for member in session["roster"]:
                cap = self.load_capability(member["seat_id"])
                current_hash = doc_hash(cap)
                if current_hash != member["capability_hash"]:
                    raise SimError(
                        f"CAPABILITY_DRIFT: {member['seat_id']} 的能力声明已变更；"
                        "旧会话不能继续使用，必须重新协商并建立会话"
                    )
        return session


# ---- custom line-oriented text messages ----------------------------------

def serialize_message(headers: Iterable[Tuple[str, Any]], body: str, note: Optional[str] = None) -> str:
    lines = ["SIMSG/1"]
    header_list = list(headers)
    header_dict = {k: str(v) for k, v in header_list}
    header_dict["Body-SHA256"] = sha256_hex(body)
    header_dict["Note-SHA256"] = sha256_hex(note) if note else "-"
    for k, v in header_dict.items():
        lines.append(f"{k}: {v}")
    lines.append("--BODY--")
    lines.append(body)
    if note is not None:
        lines.append("--NOTE--")
        lines.append(note)
    lines.append("--END--")
    return "\n".join(lines) + "\n"


def parse_message(text: str) -> Dict[str, Any]:
    # Normalize CRLF and tolerate a missing final newline.
    lines = text.replace("\r\n", "\n").rstrip("\n").split("\n")
    if not lines or lines[0] != "SIMSG/1":
        raise SimError("不是 SIMSG/1 自定义文本消息")
    i = 1
    headers: Dict[str, str] = {}
    while i < len(lines) and lines[i] != "--BODY--":
        if ": " not in lines[i]:
            raise SimError(f"消息头无法解析: {lines[i]!r}")
        k, v = lines[i].split(": ", 1)
        headers[k] = v
        i += 1
    if i >= len(lines) or lines[i] != "--BODY--":
        raise SimError("消息缺少 --BODY-- 段")
    i += 1
    body_start = i
    while i < len(lines) and lines[i] != "--NOTE--" and lines[i] != "--END--":
        i += 1
    body = "\n".join(lines[body_start:i])
    note: Optional[str] = None
    if i < len(lines) and lines[i] == "--NOTE--":
        i += 1
        note_start = i
        while i < len(lines) and lines[i] != "--END--":
            i += 1
        note = "\n".join(lines[note_start:i])
    if i >= len(lines) or lines[i] != "--END--":
        raise SimError("消息缺少 --END-- 标记")
    if headers.get("Body-SHA256") != sha256_hex(body):
        raise SimError("正文摘要与消息头不符")
    expected_note = headers.get("Note-SHA256", "-")
    actual_note_hash = sha256_hex(note) if note is not None and note != "" else "-"
    if expected_note != actual_note_hash:
        raise SimError("备注摘要与消息头不符")
    return {"headers": headers, "body": body, "note": note if note else None}


def verify_receipt_doc(receipt: Dict[str, Any], store: Store) -> bool:
    seat_id = receipt.get("seat_id", "")
    return verify_doc(receipt, store.public_for(seat_id))
