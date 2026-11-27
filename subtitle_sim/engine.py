"""Simulation engine: trusted files, negotiation, routing, proofs, relay."""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import (
    DIRECT_PROFILE,
    PROFILE_CONTRACTS,
    PROFILE_LABELS,
    RELAY_PROFILE,
    SimError,
    Store,
    doc_hash,
    now_iso,
    parse_message,
    serialize_message,
    sha256_hex,
    sign_doc,
    verify_doc,
)

SCENE = "A-17"
SUPERVISOR = "场记-林"
DIRECTOR = "导播-周"
TURNTABLE = "turntable-1"
ACCESS = "access-1"


# ---------------------------------------------------------------------------
# Initialization and trusted-channel file simulation
# ---------------------------------------------------------------------------

def init_store(root: str, force: bool = False) -> Store:
    store = Store(root)
    if store.keyring_file.exists():
        if not force:
            raise SimError(f"{root} 已初始化；如要重建演示环境请加 --force")
        if store.root == Path("/") or len(store.root.parts) < 2:
            raise SimError("拒绝清空位置异常的根目录")
        shutil.rmtree(store.root)
    store.ensure_dirs()

    ring_keys: Dict[str, str] = {}
    for name in ("access", "supervisor", "turntable", "director", "new-1", "old-1", "old-2"):
        _, pub = store.save_key(name)
        ring_keys[name] = pub
    store.write_json(
        store.keyring_file,
        {
            "schema": "subtitle-sim/trusted-keyring/v1",
            "delivery": "small files handed over the simulated trusted channel",
            "created_at": now_iso(),
            "keys": ring_keys,
        },
    )

    _write_dialect_table(store)
    create_capability(store, "new-1", "new", SCENE)
    create_capability(store, "old-1", "old", SCENE)
    create_capability(store, "old-2", "old", SCENE)

    issue_consent(
        store,
        profile=DIRECT_PROFILE,
        seat_ids=["new-1", "old-1"],
        consent_id=f"CONS-{SCENE}-DIRECT-OLD1",
    )
    issue_consent(
        store,
        profile=RELAY_PROFILE,
        seat_ids=["new-1", "old-1"],
        consent_id=f"CONS-{SCENE}-RELAY-OLD1",
    )
    return store


def _write_dialect_table(store: Store) -> None:
    table = (
        "# A-17 旧席方言读音表（转台受托保管；不得发给新席）\n"
        "code\thanzi_reading\tnote\n"
        "DY-01\t“伲”读如“尼”\t第二字稳妥，勿按半边读\n"
        "DY-02\t“朆”读如“芬”\t拖腔半拍\n"
    )
    path = store.turntable_private_dir / f"dialect-{SCENE}.tsv"
    store.write_text(path, table)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def create_capability(store: Store, seat_id: str, kind: str, scene_id: str) -> Dict[str, Any]:
    if kind not in ("new", "old"):
        raise SimError("终端类型只能是 new 或 old")
    doc: Dict[str, Any] = {
        "schema": "subtitle-sim/capability/v1",
        "seat_id": seat_id,
        "kind": kind,
        "scene_id": scene_id,
        "delivered_via": "simulated-trusted-channel-small-file",
        "statement_version": 1,
        "issued_at": now_iso(),
        "profiles": [DIRECT_PROFILE, RELAY_PROFILE],
        "pub": store.key(seat_id)["public_hex"],
    }
    if kind == "new":
        doc.update(
            {
                "accepts": [
                    "audience-body",
                    "duty-note",
                    "view-version-report",
                    "screen-render",
                ],
                "can_read_duty_note": True,
                "can_report_view_version": True,
            }
        )
    else:
        doc.update(
            {
                "accepts": ["audience-body", "received-ack", "screen-render"],
                "can_read_duty_note": False,
                "can_report_view_version": False,
                "knows_only": ["正文", "已收"],
                "dialect_table_custody": TURNTABLE,
            }
        )
    signed = sign_doc(doc, store.key(seat_id)["secret_hex"])
    store.write_json(store.capability_path(seat_id), signed)
    return signed


def set_capability_scene(store: Store, seat_id: str, scene_id: str) -> Dict[str, Any]:
    old = store.load_capability(seat_id)
    doc = {k: v for k, v in old.items() if k != "signature"}
    doc["scene_id"] = scene_id
    doc["statement_version"] = int(doc.get("statement_version", 1)) + 1
    doc["issued_at"] = now_iso()
    doc["delivered_via"] = "simulated-trusted-channel-small-file"
    signed = sign_doc(doc, store.key(seat_id)["secret_hex"])
    store.write_json(store.capability_path(seat_id), signed)
    append_audit(
        store,
        actor="trusted-channel",
        action="capability-statement-replaced",
        seat_id=seat_id,
        new_scene=scene_id,
        new_capability_hash=doc_hash(signed),
    )
    return signed


def issue_consent(
    store: Store,
    profile: str,
    seat_ids: List[str],
    consent_id: Optional[str] = None,
) -> Dict[str, Any]:
    if profile not in PROFILE_LABELS:
        raise SimError(f"未知会话约定: {profile}")
    caps = [store.load_capability(s) for s in seat_ids]
    scenes = {c["scene_id"] for c in caps}
    if len(scenes) != 1:
        raise SimError("同意记录中的端点不属于同一场次: " + ", ".join(sorted(scenes)))
    scene_id = scenes.pop()
    if consent_id is None:
        suffix = "-".join(seat_ids)
        consent_id = f"CONS-{scene_id}-{profile.split('/')[0]}-{suffix}"
    doc = {
        "schema": "subtitle-sim/consent/v1",
        "consent_id": consent_id,
        "scene_id": scene_id,
        "supervisor": SUPERVISOR,
        "profile": profile,
        "profile_label": PROFILE_LABELS[profile],
        "roster": list(seat_ids),
        "capability_hashes": {s: doc_hash(c) for s, c in zip(seat_ids, caps)},
        "consented_at": now_iso(),
        "delivery": "simulated trusted-channel small file",
    }
    signed = sign_doc(doc, store.key("supervisor")["secret_hex"])
    path = store.consents_dir / f"{consent_id}.consent.json"
    store.write_json(path, signed)
    return signed


# ---------------------------------------------------------------------------
# Audit, login, negotiation
# ---------------------------------------------------------------------------

def append_audit(store: Store, actor: str, action: str, **details: Any) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "schema": "subtitle-sim/audit-entry/v1",
        "at": now_iso(),
        "actor": actor,
        "action": action,
        **details,
    }
    # Audit entries are signed by the acting local role when a key exists.
    key_file = store.private_keys_dir / f"{actor}.key.json"
    if actor == TURNTABLE:
        secret = store.key("turntable")["secret_hex"]
        entry["signer_key_name"] = "turntable"
        entry = sign_doc(entry, secret)
    elif key_file.exists():
        entry["signer_key_name"] = actor
        entry = sign_doc(entry, store.key(actor)["secret_hex"])
    with (store.audit_dir / "audit.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def login(store: Store, seat_id: str) -> Dict[str, Any]:
    cap = store.load_capability(seat_id)
    doc = {
        "schema": "subtitle-sim/login-receipt/v1",
        "login": "ok",
        "seat_id": seat_id,
        "kind": cap["kind"],
        "scene_id": cap["scene_id"],
        "at": now_iso(),
        "note": "登录只证明本地钥匙/能力文件可用，不证明本期会话约定已经核对",
    }
    signed = sign_doc(doc, store.key("access")["secret_hex"])
    store.write_json(store.logins_dir / f"{seat_id}.login.json", signed)
    append_audit(store, "access", "login-ok", seat_id=seat_id)
    return signed


def _next_session_id(store: Store, scene_id: str) -> str:
    existing = list(store.sessions_dir.glob(f"SES-{scene_id}-*.session.json"))
    n = len(existing) + 1
    while (store.sessions_dir / f"SES-{scene_id}-{n:03d}.session.json").exists():
        n += 1
    return f"SES-{scene_id}-{n:03d}"


def negotiate(store: Store, consent_path: str) -> Dict[str, Any]:
    path = Path(consent_path)
    if not path.is_absolute():
        path = store.root / path
    consent = store.read_json(path)
    if not verify_doc(consent, store.public_for("supervisor")):
        raise SimError("同意记录签名无效，不能据以建立会话")
    profile = consent.get("profile")
    if profile not in PROFILE_LABELS:
        raise SimError("同意记录选择了未知会话约定")
    if consent.get("supervisor") != SUPERVISOR:
        raise SimError("同意记录不是本场记签署")

    seat_ids = consent.get("roster", [])
    if len(seat_ids) != 2:
        raise SimError("本演示要求一个新席和一个旧席")
    caps = {s: store.load_capability(s) for s in seat_ids}
    kinds = sorted(c["kind"] for c in caps.values())
    if kinds != ["new", "old"]:
        raise SimError("会话名册必须恰好包含一个新席和一个旧席")

    scene_id = consent["scene_id"]
    for seat_id, cap in caps.items():
        if cap["scene_id"] != scene_id:
            raise SimError(
                f"场次不一致: {seat_id} 声明 {cap['scene_id']}，同意记录为 {scene_id}；"
                "不能沿用旧会话，须先使可信能力声明/同意记录一致后重建会话"
            )
        if profile not in cap.get("profiles", []):
            raise SimError(f"{seat_id} 不支持约定 {profile}")
        if doc_hash(cap) != consent["capability_hashes"].get(seat_id):
            raise SimError(f"同意记录中的 {seat_id} 能力声明摘要与当前文件不符，须重新取得同意并重建会话")

    session_id = _next_session_id(store, scene_id)
    roster = [
        {
            "seat_id": seat_id,
            "kind": caps[seat_id]["kind"],
            "capability_hash": doc_hash(caps[seat_id]),
        }
        for seat_id in seat_ids
    ]

    # Simulate the two endpoints signing their convention acceptance after
    # comparing their capabilities.  In direct mode this is the endpoint-to-
    # endpoint common-text negotiation; Access only persists/verifies it.
    acceptances: List[Dict[str, Any]] = []
    for seat_id in seat_ids:
        acceptance: Dict[str, Any] = {
            "schema": "subtitle-sim/convention-acceptance/v1",
            "session_id": session_id,
            "scene_id": scene_id,
            "profile": profile,
            "seat_id": seat_id,
            "kind": caps[seat_id]["kind"],
            "capability_hash": doc_hash(caps[seat_id]),
            "common_readable_policy": (
                "old seat interprets audience-body only; new seat may additionally interpret duty-note"
                if profile == DIRECT_PROFILE
                else "turntable terminates new-side and old-side channels separately"
            ),
            "accepted_at": now_iso(),
        }
        signed_acceptance = sign_doc(acceptance, store.key(seat_id)["secret_hex"])
        store.write_json(
            store.sessions_dir / f"{session_id}.{seat_id}.acceptance.json",
            signed_acceptance,
        )
        acceptances.append(signed_acceptance)

    doc: Dict[str, Any] = {
        "schema": "subtitle-sim/session-convention/v1",
        "session_id": session_id,
        "scene_id": scene_id,
        "profile": profile,
        "profile_label": PROFILE_LABELS[profile],
        "text_contract": PROFILE_CONTRACTS[profile],
        "generation": int(session_id.rsplit("-", 1)[1]),
        "roster": roster,
        "consent_id": consent["consent_id"],
        "consent_hash": doc_hash(consent),
        "consent": consent,
        "endpoint_acceptances": acceptances,
        "capability_hashes": {s: doc_hash(caps[s]) for s in seat_ids},
        "established_at": now_iso(),
    }
    if profile == RELAY_PROFILE:
        doc["relay"] = {
            "turntable_id": TURNTABLE,
            "channel_new_turntable": f"CH-NT-{session_id}",
            "channel_turntable_old": f"CH-TO-{session_id}",
            "dialect_table": f"dialect-{scene_id}",
        }
    signed = sign_doc(doc, store.key("access")["secret_hex"])
    store.write_json(store.sessions_dir / f"{session_id}.session.json", signed)
    store.write_active_pointer(session_id, True, "")
    append_audit(
        store,
        "access",
        "session-established",
        session_id=session_id,
        scene_id=scene_id,
        profile=profile,
        roster=seat_ids,
        consent_id=consent["consent_id"],
    )
    return signed


def suspend_active_session(store: Store, reason: str, replacement_seat: str = "") -> Dict[str, Any]:
    pointer = store.active_pointer()
    if not pointer:
        raise SimError("当前没有活动会话")
    session_id = pointer["active_session_id"]
    record = {
        "schema": "subtitle-sim/session-suspension/v1",
        "session_id": session_id,
        "reason": reason,
        "replacement_seat": replacement_seat,
        "suspended_at": now_iso(),
        "rule": "旧约定不得无声沿用到替换端点；必须取得新同意并重新协商",
    }
    signed = sign_doc(record, store.key("access")["secret_hex"])
    repl_dir = store.access_dir / "seat-replacements"
    repl_dir.mkdir(parents=True, exist_ok=True)
    store.write_json(repl_dir / f"{session_id}-{now_iso().replace(':', '')}.json", signed)
    store.write_active_pointer(session_id, False, reason)
    append_audit(store, "access", "session-suspended", **record)
    return signed


# ---------------------------------------------------------------------------
# Sending custom text messages
# ---------------------------------------------------------------------------

def roster_seat_by_kind(session: Dict[str, Any], kind: str) -> str:
    for member in session["roster"]:
        if member["kind"] == kind:
            return member["seat_id"]
    raise SimError(f"会话中没有 {kind} 席")


def _message_id(sequence: str, version: int) -> str:
    return f"MSG-{sequence}-v{version}"


def send_message(
    store: Store,
    sequence: str,
    version: int,
    body: str,
    note: Optional[str] = None,
    kind: Optional[str] = None,
    author: str = DIRECTOR,
    body_source: Optional[str] = None,
) -> Dict[str, Any]:
    session = store.active_session(require_current_caps=True)
    profile = session["profile"]
    message_id = _message_id(sequence, version)
    meta_path = store.messages_dir / f"{message_id}.json"
    if meta_path.exists():
        raise SimError(f"{message_id} 已存在；不能覆盖同版消息")
    if kind is None:
        kind = "director-revision" if version > 1 else "ordinary-subtitle"

    body_path = store.audience_dir / f"{message_id}.audience.txt"
    note_path = store.duty_dir / f"{message_id}.duty-note.txt"
    store.write_text(body_path, body)
    if note is not None:
        store.write_text(note_path, note)

    headers: List[Tuple[str, Any]] = [
        ("Message-Id", message_id),
        ("Scene", session["scene_id"]),
        ("Session", session["session_id"]),
        ("Sequence", sequence),
        ("Version", version),
        ("Kind", kind),
        ("Author", author),
        ("Profile", profile),
    ]

    new_seat = roster_seat_by_kind(session, "new")
    old_seat = roster_seat_by_kind(session, "old")

    meta: Dict[str, Any] = {
        "schema": "subtitle-sim/message-metadata/v1",
        "message_id": message_id,
        "sequence": sequence,
        "version": version,
        "kind": kind,
        "author": author,
        "body_source_file": body_source,
        "scene_id": session["scene_id"],
        "session_id": session["session_id"],
        "profile": profile,
        "body_sha256": sha256_hex(body),
        "note_sha256": sha256_hex(note) if note else None,
        "new_seat": new_seat,
        "old_seat": old_seat,
        "created_at": now_iso(),
    }

    full_text = serialize_message(headers, body, note)
    store.write_text(store.seat_inbox(new_seat) / f"{message_id}.msg", full_text)

    if profile == DIRECT_PROFILE:
        # The old seat receives only the common body.  The turntable is not
        # given a job and never receives plaintext in this branch.
        common_text = serialize_message(headers, body, None)
        store.write_text(store.seat_inbox(old_seat) / f"{message_id}.msg", common_text)
        meta["routing"] = {
            "mode": "direct-common-text",
            "turntable_invoked": False,
            "turntable_body_access": False,
            "new_received": ["body", "note"],
            "old_received": ["body"],
        }
        append_audit(
            store,
            "access",
            "direct-routing",
            message_id=message_id,
            session_id=session["session_id"],
            body_sha256=meta["body_sha256"],
            turntable_invoked=False,
            turntable_saw_body=False,
            explanation="两端直接收取共同正文；未创建转台任务",
        )
    elif profile == RELAY_PROFILE:
        sections = split_note_sections(note or "")
        relay_text = sections["relay"] or None
        job_id = f"JOB-{message_id}"
        job_headers: List[Tuple[str, Any]] = [
            ("Job-Id", job_id),
            ("Message-Id", message_id),
            ("Scene", session["scene_id"]),
            ("Session", session["session_id"]),
            ("Sequence", sequence),
            ("Version", version),
            ("Profile", profile),
            ("Turntable", TURNTABLE),
            ("Full-Note-SHA256", meta["note_sha256"] or "-"),
            ("Relay-Note-SHA256", sha256_hex(relay_text) if relay_text else "-"),
        ]
        job_text = serialize_message(job_headers, body, relay_text)
        store.write_text(store.turntable_inbox / f"{job_id}.job.msg", job_text)
        meta["routing"] = {
            "mode": "relay-terminated",
            "turntable_invoked": True,
            "job_id": job_id,
            "new_channel": session["relay"]["channel_new_turntable"],
            "old_channel": session["relay"]["channel_turntable_old"],
            "duty_only_section_seen_by_turntable": False,
        }
        append_audit(
            store,
            "access",
            "relay-job-created",
            message_id=message_id,
            job_id=job_id,
            session_id=session["session_id"],
            gave_turntable=["body", "@relay note sections"],
            did_not_give_turntable=["@duty note section"],
        )
    else:
        raise SimError("未知 profile")

    signed_meta = sign_doc(meta, store.key("access")["secret_hex"])
    store.write_json(meta_path, signed_meta)
    return signed_meta


def split_note_sections(note: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {"relay": [], "duty": [], "preamble": []}
    current = "preamble"
    for raw_line in note.splitlines():
        line = raw_line.strip()
        marker = re.match(r"^@(relay|duty)(?:\s*[:：]\s*|\s+|$)(.*)$", line)
        if marker:
            current = marker.group(1)
            rest = marker.group(2).strip()
            if rest:
                sections[current].append(rest)
        else:
            sections[current].append(raw_line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


# ---------------------------------------------------------------------------
# Receipt/proof verification helpers
# ---------------------------------------------------------------------------

def load_message_meta(store: Store, message_id: str) -> Dict[str, Any]:
    path = store.messages_dir / f"{message_id}.json"
    if not path.exists():
        raise SimError(f"找不到联播消息元数据: {message_id}")
    meta = store.read_json(path)
    if not verify_doc(meta, store.public_for("access")):
        raise SimError(f"消息元数据签名无效: {message_id}")
    return meta


def load_receipt(store: Store, seat_id: str, message_id: str) -> Dict[str, Any]:
    path = store.seat_receipts(seat_id) / f"{message_id}.receipt.json"
    if not path.exists():
        raise SimError(f"{seat_id} 尚未实际显示并签收回单: {message_id}")
    receipt = store.read_json(path)
    if not verify_doc(receipt, store.public_for(seat_id)):
        raise SimError(f"{seat_id} 回单签名无效")
    return receipt


def load_target_receipt_with_skew_context(
    store: Store, seat_id: str, message_id: str, sequence: str, version: int
) -> Dict[str, Any]:
    try:
        return load_receipt(store, seat_id, message_id)
    except SimError as exc:
        if "尚未实际显示" not in str(exc):
            raise
        state_path = store.seat_state(seat_id) / "viewed.json"
        if state_path.exists():
            state = store.read_json(state_path)
            latest = state.get("latest_by_sequence", {}).get(sequence)
            if latest:
                reported = latest.get("viewed_version")
                envelope = latest.get("envelope_version")
                version_desc = (
                    f"viewed_version=v{reported}"
                    if reported is not None
                    else f"旧席不回报版本；其实际显示/已收的是 envelope v{envelope}"
                )
                raise SimError(
                    "VERSION_SKEW: "
                    f"{seat_id} 在约定仍有效时只看到 {latest.get('message_id')} ({version_desc}); "
                    f"没有实际看过 {message_id}，因此不能确认两端见过新词 v{version}"
                ) from exc
        raise


def verify_receipt_for_session(receipt: Dict[str, Any], session: Dict[str, Any], store: Store) -> None:
    seat_id = receipt.get("seat_id")
    roster_ids = {m["seat_id"] for m in session["roster"]}
    if seat_id not in roster_ids:
        raise SimError(f"RECEIPT_FROM_UNKNOWN_SEAT: {seat_id} 不在会话 {session['session_id']} 名册；旧约定不能无声沿用")
    if receipt.get("session_id") != session["session_id"]:
        raise SimError(f"回单会话 {receipt.get('session_id')} 与约定 {session['session_id']} 不同")
    if receipt.get("scene_id") != session["scene_id"]:
        raise SimError("回单场次与本期会话不同")
    if receipt.get("profile") != session["profile"]:
        raise SimError("回单所依据的会话约定与本期约定不同")
    cap = store.load_capability(seat_id)
    member = next(m for m in session["roster"] if m["seat_id"] == seat_id)
    if doc_hash(cap) != member["capability_hash"]:
        raise SimError(f"{seat_id} 当前能力声明已不同于会话建立时")


def _screen_text_from_receipt(store: Store, receipt: Dict[str, Any]) -> str:
    screen_path = store.root / receipt["screen_file"]
    if not screen_path.exists():
        raise SimError(f"显示稿不存在: {screen_path}")
    text = screen_path.read_text(encoding="utf-8")
    if sha256_hex(text) != receipt["screen_sha256"]:
        raise SimError(f"显示稿摘要与 {receipt['seat_id']} 回单不符")
    return text


def confirm_same_text(store: Store, sequence: str, version: int) -> Dict[str, Any]:
    session = store.active_session(require_current_caps=True)
    if session["profile"] != DIRECT_PROFILE:
        raise SimError("当前会话是转台转换约定；同文确认请使用 direct 约定，或调用 relay-handover 核对转换链")
    message_id = _message_id(sequence, version)
    meta = load_message_meta(store, message_id)
    if meta["session_id"] != session["session_id"]:
        raise SimError("该消息不属于当前有效会话；会话重建后不能拿旧消息直接确认")

    new_seat = roster_seat_by_kind(session, "new")
    old_seat = roster_seat_by_kind(session, "old")
    new_receipt = load_target_receipt_with_skew_context(store, new_seat, message_id, sequence, version)
    old_receipt = load_target_receipt_with_skew_context(store, old_seat, message_id, sequence, version)
    verify_receipt_for_session(new_receipt, session, store)
    verify_receipt_for_session(old_receipt, session, store)

    if new_receipt.get("receipt_type") != "new-view-report/v1":
        raise SimError("新席回单类型不对")
    if old_receipt.get("receipt_type") != "old-received/v1":
        raise SimError("旧席回单类型不对")
    if new_receipt.get("viewed_version") != version:
        raise SimError(
            f"VERSION_SKEW: 新席实际看过 v{new_receipt.get('viewed_version')}，不能确认两端见过 v{version} 新词"
        )
    if new_receipt.get("displayed_body_sha256") != meta["body_sha256"]:
        raise SimError("新席显示正文与送播正文摘要不同")
    if old_receipt.get("displayed_body_sha256") != meta["body_sha256"]:
        raise SimError(
            "VERSION_SKEW_OR_TEXT_MISMATCH: 旧席仍停留在改词前版本或显示稿不同；"
            "不能确认两端见过同一条新词"
        )
    if old_receipt.get("note_endorsed") is not False or old_receipt.get("displayed_note_sha256") is not None:
        raise SimError("旧席回单只能证明正文/已收，不能被写成背书其看不到的备注")

    new_screen = _screen_text_from_receipt(store, new_receipt)
    old_screen = _screen_text_from_receipt(store, old_receipt)
    body = (store.audience_dir / f"{message_id}.audience.txt").read_text(encoding="utf-8").rstrip("\r\n")
    if body not in new_screen or body not in old_screen:
        raise SimError("显示稿中找不到实际显示的完整正文")

    caps = {s: store.load_capability(s) for s in (new_seat, old_seat)}
    proof: Dict[str, Any] = {
        "schema": "subtitle-sim/same-text-proof/v1",
        "proof_type": "same-text-confirmation/v1",
        "issued_at": now_iso(),
        "scene_id": session["scene_id"],
        "session_id": session["session_id"],
        "profile": session["profile"],
        "sequence": sequence,
        "version": version,
        "message_id": message_id,
        "body_sha256": meta["body_sha256"],
        "body_text": body,
        "message_routing": meta.get("routing"),
        "claim": (
            "新席与旧席均已实际显示同一观众可见正文并签名；"
            "旧席“已收”不包含场记备注背书"
        ),
        "note_claim": "本证明不主张旧席看过或同意场记备注",
        "session_hash": doc_hash(session),
        "session": session,
        "capabilities": caps,
        "receipts": {new_seat: new_receipt, old_seat: old_receipt},
        "screens": {new_seat: new_screen, old_seat: old_screen},
    }
    signed = sign_doc(proof, store.key("access")["secret_hex"])
    out = store.proofs_dir / f"{message_id}.same-text.proof.json"
    store.write_json(out, signed)
    append_audit(
        store,
        "access",
        "same-text-confirmed",
        proof=str(out.relative_to(store.root)),
        message_id=message_id,
        body_sha256=meta["body_sha256"],
    )
    signed["_proof_path"] = str(out)
    return signed


# ---------------------------------------------------------------------------
# Turntable relay/conversion
# ---------------------------------------------------------------------------

def _load_dialect_table(store: Store, scene_id: str) -> Tuple[Path, str, Dict[str, str]]:
    path = store.turntable_private_dir / f"dialect-{scene_id}.tsv"
    if not path.exists():
        raise SimError(f"转台找不到受托保管的方言读音表: {path}")
    raw = path.read_text(encoding="utf-8")
    rows: Dict[str, str] = {}
    reader = csv.DictReader(io.StringIO("\n".join(line for line in raw.splitlines() if not line.startswith("#"))), delimiter="\t")
    for row in reader:
        if row.get("code"):
            rows[row["code"]] = row["hanzi_reading"]
    return path, sha256_hex(raw), rows


def process_turntable_job(store: Store, job_name: str) -> Dict[str, Any]:
    job_path = Path(job_name)
    if not job_path.is_absolute():
        # Accept either a path, JOB id, or job filename.
        candidate = store.turntable_inbox / job_name
        if candidate.exists():
            job_path = candidate
        elif (store.turntable_inbox / f"{job_name}.job.msg").exists():
            job_path = store.turntable_inbox / f"{job_name}.job.msg"
        else:
            job_path = store.root / job_name
    if not job_path.exists():
        raise SimError(f"找不到转台任务: {job_name}")
    parsed = parse_message(job_path.read_text(encoding="utf-8"))
    h = parsed["headers"]
    message_id = h["Message-Id"]
    session_id = h["Session"]
    scene_id = h["Scene"]
    body = parsed["body"]
    relay_note = parsed["note"] or ""

    existing = list((store.access_dir / "conversion-certs").glob(f"{message_id}.*.cert.json")) if (store.access_dir / "conversion-certs").exists() else []
    if existing:
        raise SimError(f"{message_id} 已有转台转换证明，不能重复转换")

    table_path, table_hash, table = _load_dialect_table(store, scene_id)
    segments: List[Dict[str, Any]] = [
        {
            "index": 0,
            "kind": "audience-body",
            "saw_plaintext": True,
            "input_text": body,
            "input_sha256": sha256_hex(body),
            "action": "pass-through-into-old-prompt-script",
            "output_text": body,
            "output_sha256": sha256_hex(body),
        }
    ]

    converted_parts: List[str] = []
    pos = 0
    seg_index = 1
    for match in re.finditer(r"\{(DY-\d+)\}", relay_note):
        if match.start() > pos:
            prose = relay_note[pos : match.start()]
            if prose:
                segments.append(
                    {
                        "index": seg_index,
                        "kind": "relay-note-prose",
                        "saw_plaintext": True,
                        "input_text": prose,
                        "input_sha256": sha256_hex(prose),
                        "action": "translate-prose-pass-through",
                        "output_text": prose,
                        "output_sha256": sha256_hex(prose),
                    }
                )
                seg_index += 1
                converted_parts.append(prose)
        code = match.group(1)
        if code not in table:
            raise SimError(f"方言读音表中没有 {code}；转台不能生成旧席播稿")
        reading = table[code]
        token = match.group(0)
        segments.append(
            {
                "index": seg_index,
                "kind": "entrusted-dialect-code",
                "code": code,
                "saw_plaintext": True,
                "input_text": token,
                "input_sha256": sha256_hex(token),
                "table_file": table_path.name,
                "table_row_sha256": sha256_hex(code + "\t" + reading),
                "action": "use-entrusted-table-to-produce-hanzi",
                "output_text": reading,
                "output_sha256": sha256_hex(reading),
            }
        )
        seg_index += 1
        converted_parts.append(reading)
        pos = match.end()
    tail = relay_note[pos:]
    if tail:
        segments.append(
            {
                "index": seg_index,
                "kind": "relay-note-prose",
                "saw_plaintext": True,
                "input_text": tail,
                "input_sha256": sha256_hex(tail),
                "action": "translate-prose-pass-through",
                "output_text": tail,
                "output_sha256": sha256_hex(tail),
            }
        )
        converted_parts.append(tail)

    converted_note = "".join(converted_parts).strip()
    if converted_note:
        old_script = f"【旧式播稿】\n{body}\n【正音】{converted_note}"
    else:
        old_script = f"【旧式播稿】\n{body}"

    cert_id = f"CERT-{message_id}"
    cert: Dict[str, Any] = {
        "schema": "subtitle-sim/turntable-conversion-cert/v1",
        "cert_type": "turntable-conversion-attestation/v1",
        "cert_id": cert_id,
        "issued_at": now_iso(),
        "attestor_role": "turntable",
        "attestor_id": TURNTABLE,
        "session_id": session_id,
        "scene_id": scene_id,
        "message_id": message_id,
        "sequence": h.get("Sequence"),
        "version": int(h.get("Version", "0")),
        "dialect_table_id": table_path.name,
        "dialect_table_sha256": table_hash,
        "source_body_sha256": sha256_hex(body),
        "relay_note_sha256": sha256_hex(relay_note) if relay_note else None,
        "full_note_sha256": h.get("Full-Note-SHA256") if h.get("Full-Note-SHA256") != "-" else None,
        "duty_only_section_seen": False,
        "duty_only_section_attested": False,
        "old_script_sha256": sha256_hex(old_script),
        "segments": segments,
        "attestation": (
            "本证明只能由持有转台私钥和受托方言读音表的转台作出；"
            "下列 segments 如实记录转台本次打开过的内容与逐段转换结果"
        ),
    }
    signed_cert = sign_doc(cert, store.key("turntable")["secret_hex"])
    cert_dir = store.access_dir / "conversion-certs"
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / f"{cert_id}.cert.json"
    store.write_json(cert_path, signed_cert)
    store.write_json(store.turntable_sent / cert_path.name, signed_cert)

    audit_entry = append_audit(
        store,
        "turntable",
        "relay-conversion",
        cert_id=cert_id,
        message_id=message_id,
        session_id=session_id,
        old_script_sha256=sha256_hex(old_script),
        saw_segments=[s.get("input_text") for s in segments],
        cert_path=str(cert_path.relative_to(store.root)),
    )
    # Also keep a turntable-local signed audit trail with exact segments.
    store.write_json(store.audit_dir / f"turntable-{cert_id}.entry.json", audit_entry)

    # Deliver representation on the separately terminated turntable->old leg.
    session = store.load_session(session_id)
    old_seat = roster_seat_by_kind(session, "old")
    old_headers: List[Tuple[str, Any]] = [
        ("Message-Id", message_id),
        ("Representation-Id", f"{message_id}.OLD-SCRIPT"),
        ("Scene", scene_id),
        ("Session", session_id),
        ("Sequence", h.get("Sequence", "")),
        ("Version", h.get("Version", "")),
        ("Kind", h.get("Kind", "")),
        ("Profile", RELAY_PROFILE),
        ("Converted-By-Cert", cert_id),
    ]
    store.write_text(
        store.seat_inbox(old_seat) / f"{message_id}.msg",
        serialize_message(old_headers, old_script, None),
    )

    meta_path = store.messages_dir / f"{message_id}.json"
    if meta_path.exists():
        meta = store.read_json(meta_path)
        meta["routing"]["conversion_cert"] = cert_id
        meta["routing"]["conversion_cert_path"] = str(cert_path.relative_to(store.root))
        store.write_json(meta_path, sign_doc({k: v for k, v in meta.items() if k != "signature"}, store.key("access")["secret_hex"]))
    return signed_cert


def confirm_relay_handover(store: Store, sequence: str, version: int) -> Dict[str, Any]:
    session = store.active_session(require_current_caps=True)
    if session["profile"] != RELAY_PROFILE:
        raise SimError("当前会话不是 relay-turntable-terminated 约定")
    message_id = _message_id(sequence, version)
    meta = load_message_meta(store, message_id)
    if meta["session_id"] != session["session_id"]:
        raise SimError("消息不属于当前有效会话")
    new_seat = roster_seat_by_kind(session, "new")
    old_seat = roster_seat_by_kind(session, "old")
    new_receipt = load_target_receipt_with_skew_context(store, new_seat, message_id, sequence, version)
    old_receipt = load_target_receipt_with_skew_context(store, old_seat, message_id, sequence, version)
    verify_receipt_for_session(new_receipt, session, store)
    verify_receipt_for_session(old_receipt, session, store)

    cert_dir = store.access_dir / "conversion-certs"
    cert_path = cert_dir / f"CERT-{message_id}.cert.json"
    if not cert_path.exists():
        raise SimError("缺少只能由转台作出的逐段转换证明")
    cert = store.read_json(cert_path)
    if not verify_doc(cert, store.public_for("turntable")):
        raise SimError("转台转换证明签名无效；其他角色不能替代转台作此证明")
    if cert["session_id"] != session["session_id"] or cert["message_id"] != message_id:
        raise SimError("转换证明与会话/消息不匹配")
    if cert["source_body_sha256"] != meta["body_sha256"]:
        raise SimError("转换证明所见正文摘要与送播稿不符")
    if old_receipt["displayed_body_sha256"] != cert["old_script_sha256"]:
        raise SimError("旧席显示稿不是转台证明对应的旧式播稿，或旧席尚未查看转换稿")
    if new_receipt["displayed_body_sha256"] != meta["body_sha256"]:
        raise SimError("新席显示正文不符")
    if new_receipt.get("viewed_version") != version:
        raise SimError("新席未实际查看指定版本")

    new_screen = _screen_text_from_receipt(store, new_receipt)
    old_screen = _screen_text_from_receipt(store, old_receipt)
    caps = {s: store.load_capability(s) for s in (new_seat, old_seat)}
    proof: Dict[str, Any] = {
        "schema": "subtitle-sim/relay-handover-proof/v1",
        "proof_type": "relay-handover-accounting/v1",
        "issued_at": now_iso(),
        "scene_id": session["scene_id"],
        "session_id": session["session_id"],
        "profile": session["profile"],
        "sequence": sequence,
        "version": version,
        "message_id": message_id,
        "same_text": False,
        "claim": (
            "两段通道由转台分别终结；新席见原稿和值班备注，旧席见转台生成的旧式播稿。"
            "文本对应关系以转台签名的逐段转换证明为准"
        ),
        "session_hash": doc_hash(session),
        "session": session,
        "capabilities": caps,
        "receipts": {new_seat: new_receipt, old_seat: old_receipt},
        "screens": {new_seat: new_screen, old_seat: old_screen},
        "conversion_cert": cert,
    }
    signed = sign_doc(proof, store.key("access")["secret_hex"])
    out = store.proofs_dir / f"{message_id}.relay-handover.proof.json"
    store.write_json(out, signed)
    append_audit(store, "access", "relay-handover-confirmed", proof=str(out.relative_to(store.root)), message_id=message_id)
    signed["_proof_path"] = str(out)
    return signed


def load_conversion_cert(store: Store, identifier_or_path: str) -> Tuple[Dict[str, Any], Path]:
    candidate = Path(identifier_or_path)
    if candidate.exists():
        path = candidate
    else:
        cert_id = identifier_or_path
        if not cert_id.startswith("CERT-"):
            cert_id = f"CERT-{cert_id}"
        path = store.access_dir / "conversion-certs" / f"{cert_id}.cert.json"
    if not path.is_absolute():
        path = store.root / path
    if not path.exists():
        raise SimError(f"找不到转台转换证明: {identifier_or_path}")
    cert = store.read_json(path)
    if not verify_doc(cert, store.public_for("turntable")):
        raise SimError("转台证明签名无效；只有转台私钥才能作出该证明")
    if cert.get("attestor_role") != "turntable":
        raise SimError("证明中的 attestor_role 不是 turntable")
    return cert, path


def verify_saved_proof(
    store: Store, proof_path: str | Path, keyring_override: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    path = Path(proof_path)
    if not path.is_absolute():
        path = store.root / path
    proof = store.read_json(path)
    result = {"valid": False, "checks": [], "proof_path": str(path)}

    def public_key(name: str) -> str:
        if keyring_override is not None:
            try:
                return keyring_override["keys"][name]
            except KeyError as exc:
                raise SimError(f"验证用公钥副本中没有 {name}") from exc
        return store.public_for(name)

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            raise SimError(f"验证失败 [{name}] {detail}")
        result["checks"].append({"name": name, "ok": True, "detail": detail})

    check("access-signature", verify_doc(proof, public_key("access")))
    session = proof["session"]
    check("session-signature", verify_doc(session, public_key("access")))
    check("session-hash", doc_hash(session) == proof.get("session_hash"))
    consent = session["consent"]
    check("consent-signature", verify_doc(consent, public_key("supervisor")))

    acceptances = session.get("endpoint_acceptances", [])
    check("two-endpoint-acceptances", len(acceptances) == 2)
    for acceptance in acceptances:
        seat_id = acceptance.get("seat_id", "")
        check(f"acceptance-signature:{seat_id}", verify_doc(acceptance, public_key(seat_id)))
        check(f"acceptance-session:{seat_id}", acceptance.get("session_id") == proof["session_id"])
        member = next((m for m in session["roster"] if m["seat_id"] == seat_id), None)
        check(f"acceptance-roster:{seat_id}", member is not None)
        if member:
            check(
                f"acceptance-capability:{seat_id}",
                acceptance.get("capability_hash") == member["capability_hash"],
            )

    for seat_id, cap in proof["capabilities"].items():
        check(f"capability-signature:{seat_id}", verify_doc(cap, public_key(seat_id)))
        member = next(m for m in session["roster"] if m["seat_id"] == seat_id)
        check(f"capability-hash:{seat_id}", doc_hash(cap) == member["capability_hash"])
        check(f"capability-scene:{seat_id}", cap["scene_id"] == proof["scene_id"])

    for seat_id, receipt in proof["receipts"].items():
        check(f"receipt-signature:{seat_id}", verify_doc(receipt, public_key(seat_id)))
        check(f"receipt-session:{seat_id}", receipt["session_id"] == proof["session_id"])
        screen = proof["screens"][seat_id]
        check(f"screen-hash:{seat_id}", sha256_hex(screen) == receipt["screen_sha256"])

    ptype = proof["proof_type"]
    if ptype == "same-text-confirmation/v1":
        target = proof["body_sha256"]
        new_seat, old_seat = proof["receipts"].keys()
        # Dict order is insertion order from creation; still identify by type.
        new_r = next(r for r in proof["receipts"].values() if r["receipt_type"] == "new-view-report/v1")
        old_r = next(r for r in proof["receipts"].values() if r["receipt_type"] == "old-received/v1")
        check("new-body-hash", new_r["displayed_body_sha256"] == target)
        check("old-body-hash", old_r["displayed_body_sha256"] == target)
        check("new-version", new_r.get("viewed_version") == proof["version"])
        check("old-no-note-endorsement", old_r.get("note_endorsed") is False)
        check("old-no-note-hash", old_r.get("displayed_note_sha256") is None)
        check("profile-direct", proof["profile"] == DIRECT_PROFILE)
        routing = proof.get("message_routing") or {}
        check("direct-turntable-did-not-open-body", routing.get("turntable_body_access") is False)
        check("body-text-hash", sha256_hex(proof.get("body_text", "")) == target)
        check("body-in-new-screen", proof.get("body_text", "") in proof["screens"][new_r["seat_id"]])
        check("body-in-old-screen", proof.get("body_text", "") in proof["screens"][old_r["seat_id"]])
        result["valid"] = True
        result["conclusion"] = "可向持有可信钥匙串公钥副本的导播/审计/第三方证明：两席实际显示并核对了同一句正文；旧席未背书备注。"
    elif ptype == "relay-handover-accounting/v1":
        cert = proof["conversion_cert"]
        check("turntable-cert-signature", verify_doc(cert, public_key("turntable")))
        check("cert-attestor-role", cert.get("attestor_role") == "turntable")
        check("cert-session", cert["session_id"] == proof["session_id"])
        old_r = next(r for r in proof["receipts"].values() if r["receipt_type"] == "old-received/v1")
        new_r = next(r for r in proof["receipts"].values() if r["receipt_type"] == "new-view-report/v1")
        check("cert-old-script-hash", cert["old_script_sha256"] == old_r["displayed_body_sha256"])
        check("cert-source-body-hash", cert["source_body_sha256"] == new_r["displayed_body_sha256"])
        check("profile-relay", proof["profile"] == RELAY_PROFILE)
        result["valid"] = True
        result["conclusion"] = "转换链和转台见证有效；该证明不声称两席显示同文，而如实证明各自文本归宿与转换关系。"
    else:
        raise SimError(f"未知证明类型: {ptype}")
    return result


def _extract_body_from_screen_proof(proof: Dict[str, Any], seat_id: str) -> str:
    # Screens are human-readable; tests/verifiers mainly rely on signed hashes.
    return proof["screens"][seat_id]
