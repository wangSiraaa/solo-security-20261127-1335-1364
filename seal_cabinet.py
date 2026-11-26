#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""电子档案保全室的命令行封存柜。

仅使用 Python 标准库。模型：
- A/B/C/D 四个编号块；
- 模拟源按操作顺序记录代次提交和哈希链；
- 封存柜使用内容寻址对象库 + 两个 WORM 新块位置；
- 封条号是封存正文的 SHA-256 前缀；
- 独立复核只需要副本目录、seal.json、witness.json 和可信封条号。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

BLOCKS = ["A", "B", "C", "D"]
BLOCK_ALIASES = {
    "1": "A",
    "2": "B",
    "3": "C",
    "4": "D",
    "A": "A",
    "B": "B",
    "C": "C",
    "D": "D",
}
BLOCK_NAMES = {
    "A": "目录",
    "B": "签收页",
    "C": "交接说明",
    "D": "介质底账",
}
BLOCK_FILES = {
    "A": "A-catalog.txt",
    "B": "B-signoff.txt",
    "C": "C-handover.txt",
    "D": "D-media-ledger.txt",
}
SLOT_CAPACITY = 2

STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_INCOMPLETE = "incomplete"
STATUS_INVALID = "invalid"

STATUS_EXIT = {
    STATUS_COMPLETE: 0,
    STATUS_PARTIAL: 0,
    STATUS_INCOMPLETE: 2,
    STATUS_INVALID: 3,
}

KIND_ZH = {
    STATUS_COMPLETE: "完整时点副本",
    STATUS_PARTIAL: "限定范围片段",
    STATUS_INCOMPLETE: "缺证待补",
    STATUS_INVALID: "验证失败",
}


class CliError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 1, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_obj(obj: Any) -> str:
    return sha256_hex(canonical_bytes(obj))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_block(value: str) -> str:
    key = value.strip().upper()
    if key not in BLOCK_ALIASES:
        raise CliError("UNKNOWN_BLOCK", f"未知块编号 {value!r}；只支持 A/B/C/D 或 1/2/3/4")
    return BLOCK_ALIASES[key]


def normalize_blocks(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        for part in str(value).split(","):
            if part.strip():
                b = normalize_block(part)
                if b not in out:
                    out.append(b)
    return [b for b in BLOCKS if b in out]


def new_id(prefix: str, nbytes: int = 5) -> str:
    return f"{prefix}-{secrets.token_hex(nbytes)}"


def seal_number_for_digest(digest: str) -> str:
    return "SL-" + digest[:32]


def seal_ref_matches(ref: str | None, wrapper: dict[str, Any]) -> bool | None:
    """None 表示没有提供可信封条号；True/False 表示是否一致。"""
    if not ref:
        return None
    ref = ref.strip()
    digest = wrapper.get("seal_sha256", "")
    seal_id = wrapper.get("seal_id", "")
    normalized = ref[len("sha256:"):] if ref.startswith("sha256:") else ref
    if ref == seal_id or normalized.lower() == digest.lower():
        return True
    # 柜内命令允许使用足够长的封条号前缀；独立包仍建议传完整封条号。
    if ref.startswith("SL-") and len(ref) >= 18 and seal_id.startswith(ref):
        return True
    return False


class ObjectStore:
    def __init__(self, root: Path):
        self.root = root

    def path_for(self, digest: str) -> Path:
        return self.root / digest[:2] / digest

    def has(self, digest: str) -> bool:
        p = self.path_for(digest)
        if not p.is_file():
            return False
        return sha256_hex(p.read_bytes()) == digest

    def put(self, data: bytes) -> str:
        digest = sha256_hex(data)
        path = self.path_for(digest)
        if not path.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(path, data)
        return digest

    def get(self, digest: str) -> bytes:
        path = self.path_for(digest)
        if not path.is_file():
            raise CliError("OBJECT_NOT_FOUND", f"对象不存在或已在清机后不可读：{digest}")
        data = path.read_bytes()
        actual = sha256_hex(data)
        if actual != digest:
            raise CliError("OBJECT_CORRUPT", f"对象 {digest} 哈希损坏：实际 {actual}")
        return data


class App:
    def __init__(self, home: str | Path | None):
        env_home = os.environ.get("SEALCAB_HOME")
        self.home = Path(home or env_home or (Path.home() / ".sealcab")).resolve()
        self.source_dir = self.home / "source"
        self.cabinet_dir = self.home / "cabinet"
        self.orders_dir = self.cabinet_dir / "orders"
        self.seals_dir = self.cabinet_dir / "seals"
        self.witness_dir = self.cabinet_dir / "witness-snapshots"
        self.receipts_dir = self.cabinet_dir / "receipts"
        self.notices_dir = self.cabinet_dir / "notices"
        self.deliveries_dir = self.home / "deliveries"
        self.config_path = self.home / "config.json"
        self.journal_path = self.source_dir / "journal.json"
        self.slots_path = self.cabinet_dir / "slots.json"
        self.ledger_path = self.cabinet_dir / "ledger.jsonl"
        self.source_objects = ObjectStore(self.source_dir / "objects")
        self.cab_objects = ObjectStore(self.cabinet_dir / "objects")

    def require(self) -> None:
        if not self.config_path.exists() or not self.journal_path.exists():
            raise CliError(
                "NOT_INITIALIZED",
                f"封存柜尚未初始化：{self.home}；请先运行 seal-cabinet init",
            )

    def dirs(self) -> None:
        for p in [
            self.home,
            self.source_dir,
            self.cabinet_dir,
            self.orders_dir,
            self.seals_dir,
            self.witness_dir,
            self.receipts_dir,
            self.notices_dir,
            self.deliveries_dir,
        ]:
            p.mkdir(parents=True, exist_ok=True)

    def config(self) -> dict[str, Any]:
        return read_json(self.config_path)

    def save_config(self, cfg: dict[str, Any]) -> None:
        atomic_write_json(self.config_path, cfg)

    def journal(self) -> dict[str, Any]:
        return read_json(self.journal_path)

    def save_journal(self, journal: dict[str, Any]) -> None:
        atomic_write_json(self.journal_path, journal)

    def slots(self) -> dict[str, Any]:
        return read_json(self.slots_path)

    def save_slots(self, slots: dict[str, Any]) -> None:
        atomic_write_json(self.slots_path, slots)

    def append_ledger(self, event: dict[str, Any]) -> None:
        event = {"at": now_iso(), **event}
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(line)


def default_gen7_blocks() -> dict[str, bytes]:
    return {
        "A": (
            "目录 / 第七代 A7\n"
            "1. 立案与来源\n"
            "2. 签收页\n"
            "3. 交接说明（旧）\n"
            "4. 介质校验底账\n"
            "版本：A7；本目录与 B7 同属第七代提交。\n"
        ).encode("utf-8"),
        "B": (
            "签收页 / 第七代 B7\n"
            "收件人：档案保全室\n"
            "签收事项：四个编号块完整旧卷\n"
            "版本：B7；与 A7、C7、D7 在第七代同一状态共存。\n"
        ).encode("utf-8"),
        "C": (
            "备考页 / 第七代 C7\n"
            "本页记录旧卷封装、介质编号和连续保管链。\n"
            "版本：C7。\n"
        ).encode("utf-8"),
        "D": (
            "介质底账 / 第七代 D7\n"
            "块位置 A-D；整块校验随旧卷封条归档。\n"
            "版本：D7。\n"
        ).encode("utf-8"),
    }


def gen8_blocks() -> dict[str, bytes]:
    return {
        "A": (
            "目录 / 第八代 A8\n"
            "1. 立案与来源\n"
            "2. 新版签收页\n"
            "3. 交接说明（旧页待换）\n"
            "4. 介质校验底账\n"
            "变更：第八代调整目录条目并指向 B8。\n"
        ).encode("utf-8"),
        "B": (
            "签收页 / 第八代 B8\n"
            "收件人：档案保全室\n"
            "签收事项：第八代目录调整与签收页换版\n"
            "版本：B8；与 A8 同属第八代提交，C/D 由第七代连续接回。\n"
        ).encode("utf-8"),
    }


def gen9_blocks() -> dict[str, bytes]:
    return {
        "B": (
            "签收页 / 第九代 B9\n"
            "业务员补签：签收页再次修改。\n"
            "版本：B9；必须与 C9 成对验证，不能与 B8 或 A7 混卷。\n"
        ).encode("utf-8"),
        "C": (
            "交接说明 / 第九代 C9\n"
            "交接事项：业务员当面移交新材料并修改签收页。\n"
            "版本：C9；与 B9 在第九代同一提交中共存。\n"
        ).encode("utf-8"),
    }


def source_write_bytes(app: App, block: str, data: bytes, label: str | None = None) -> dict[str, Any]:
    app.require()
    block = normalize_block(block)
    journal = app.journal()
    digest = app.source_objects.put(data)
    journal["op_seq"] = int(journal.get("op_seq", 0)) + 1
    op = {
        "op_id": journal["op_seq"],
        "at": now_iso(),
        "block": block,
        "sha256": digest,
        "size": len(data),
        "label": label or BLOCK_NAMES[block],
    }
    journal.setdefault("pending_ops", []).append(op)
    journal.setdefault("current", {})[block] = digest
    app.save_journal(journal)
    return op


def source_commit(app: App, generation: int, message: str | None = None) -> dict[str, Any]:
    app.require()
    journal = app.journal()
    pending = journal.get("pending_ops", [])
    if not pending:
        raise CliError("NO_PENDING_OPERATIONS", f"第 {generation} 代没有待提交写入")
    records = journal.get("commits", [])
    last_gen = records[-1]["generation"] if records else 0
    if records and generation <= last_gen:
        raise CliError(
            "GENERATION_IMMUTABLE",
            f"不能改写第 {generation} 代；当前最后代为第 {last_gen} 代，只能提交新代次",
        )

    state, _by_gen, replay_errors = replay_records(records)
    if replay_errors:
        raise CliError("SOURCE_CHAIN_BROKEN", "既有源见证链校验失败", details=replay_errors)

    final_for_block: dict[str, dict[str, Any]] = {}
    for op in pending:
        final_for_block[op["block"]] = op

    changes: dict[str, dict[str, Any]] = {}
    for block, op in final_for_block.items():
        changes[block] = {
            "sha256": op["sha256"],
            "size": op["size"],
            "prior": state.get(block),
            "last_op_id": op["op_id"],
        }

    body = {
        "record_type": "generation_commit",
        "format": "sealcab-witness-record/v1",
        "source_id": journal["source_id"],
        "generation": generation,
        "seq": len(records) + 1,
        "parent": records[-1]["record_hash"] if records else None,
        "operations": pending,
        "changes": changes,
        "message": message or f"提交第 {generation} 代",
        "committed_at": now_iso(),
    }
    record = dict(body)
    record["record_hash"] = digest_obj(body)
    records.append(record)
    journal["commits"] = records
    journal["pending_ops"] = []
    journal["last_generation"] = generation
    app.save_journal(journal)
    app.append_ledger({"event": "source_commit", "generation": generation, "record_hash": record["record_hash"]})
    return record


def replay_records(records: list[dict[str, Any]]) -> tuple[dict[str, str], dict[int, dict[str, str]], list[dict[str, Any]]]:
    state: dict[str, str] = {}
    by_generation: dict[int, dict[str, str]] = {}
    errors: list[dict[str, Any]] = []
    expected_parent: str | None = None

    for record in records:
        claimed_hash = record.get("record_hash")
        body = {k: v for k, v in record.items() if k != "record_hash"}
        actual_hash = digest_obj(body)
        gen = record.get("generation")

        if claimed_hash != actual_hash:
            errors.append({
                "code": "witness_record_digest_mismatch",
                "generation": gen,
                "claimed": claimed_hash,
                "actual": actual_hash,
            })
        if record.get("parent") != expected_parent:
            errors.append({
                "code": "witness_parent_chain_broken",
                "generation": gen,
                "expected_parent": expected_parent,
                "actual_parent": record.get("parent"),
            })

        for block, change in record.get("changes", {}).items():
            prior = change.get("prior")
            current = state.get(block)
            # 创世记录允许 prior=null；之后 prior 必须与重放状态一致。
            if prior is not None and prior != current:
                errors.append({
                    "code": "cross_generation_precondition",
                    "generation": gen,
                    "block": block,
                    "recorded_prior": prior,
                    "replayed_current": current,
                })
            if prior is None and current is not None:
                errors.append({
                    "code": "block_reappeared_without_prior",
                    "generation": gen,
                    "block": block,
                    "replayed_current": current,
                })
            state[block] = change.get("sha256")

        if isinstance(gen, int):
            by_generation[gen] = dict(state)
        expected_parent = claimed_hash

    return state, by_generation, errors


def make_witness(app: App) -> dict[str, Any]:
    journal = app.journal()
    return {
        "format": "sealcab-witness/v1",
        "source_id": journal["source_id"],
        "exported_at": now_iso(),
        "last_generation": journal.get("last_generation"),
        "records": copy.deepcopy(journal.get("commits", [])),
    }


def save_witness_snapshot(app: App, witness: dict[str, Any], target_hash: str) -> Path:
    path = app.witness_dir / f"{target_hash}.json"
    if not path.exists():
        atomic_write_json(path, witness)
    return path


def init_home(app: App, force: bool = False) -> dict[str, Any]:
    if app.config_path.exists():
        if not force:
            cfg = app.config()
            old_seal = cfg.get("old_seal_id")
            return {
                "ok": True,
                "initialized": True,
                "home": str(app.home),
                "cabinet_id": cfg.get("cabinet_id"),
                "old_seal_id": old_seal,
                "message": "封存柜已存在；未重做开机固化。",
                "_lines": [
                    f"封存柜已初始化：{app.home}",
                    f"旧卷封条号：{old_seal or '(未记录)'}",
                    "提示：如需演示全新柜，请使用临时 SEALCAB_HOME，或 init --force。",
                ],
            }
        shutil.rmtree(app.home)

    app.dirs()
    cabinet_id = new_id("CAB", 6)
    source_id = new_id("SRC", 8)
    cfg = {
        "version": 1,
        "cabinet_id": cabinet_id,
        "source_id": source_id,
        "created_at": now_iso(),
        "boot_count": 1,
        "slot_capacity": SLOT_CAPACITY,
        "old_seal_id": None,
    }
    app.save_config(cfg)
    atomic_write_json(app.slots_path, {
        "capacity": SLOT_CAPACITY,
        "slots": [
            {"id": "S1", "status": "empty"},
            {"id": "S2", "status": "empty"},
        ],
    })
    journal = {
        "format": "sealcab-source-journal/v1",
        "source_id": source_id,
        "created_at": now_iso(),
        "op_seq": 0,
        "pending_ops": [],
        "current": {},
        "commits": [],
        "last_generation": None,
    }
    app.save_journal(journal)

    for block, data in default_gen7_blocks().items():
        source_write_bytes(app, block, data, f"开机固化 {BLOCK_NAMES[block]}")
    record7 = source_commit(app, 7, "开机时已固化的完整旧卷")

    order_id = "ORD-G7-ANCHOR"
    order = {
        "order_id": order_id,
        "code": "OLD-VOL-G7",
        "volume": "开机固化旧卷",
        "generation": 7,
        "requested_blocks": BLOCKS,
        "promised_kind": STATUS_COMPLETE,
        "status": "open",
        "created_at": now_iso(),
        "note": "开机前已经固化；不占用本轮两个新块位置。",
        "received": {},
        "seal_id": None,
        "predecessor_seal_id": None,
    }
    for block, data in default_gen7_blocks().items():
        digest = app.cab_objects.put(data)
        order["received"][block] = {
            "sha256": digest,
            "size": len(data),
            "via": "boot_anchor",
            "received_at": now_iso(),
            "slot_id": None,
        }
    atomic_write_json(app.orders_dir / f"{order_id}.json", order)
    seal_result = seal_order(app, order_id)
    if seal_result.get("promised_kind") != STATUS_COMPLETE or not seal_result.get("seal_id"):
        raise CliError("BOOT_SEAL_FAILED", "第七代旧卷固化失败", details=seal_result)

    cfg = app.config()
    cfg["old_seal_id"] = seal_result["seal_id"]
    app.save_config(cfg)
    app.append_ledger({"event": "boot_complete_volume", "seal_id": seal_result["seal_id"]})

    return {
        "ok": True,
        "initialized": True,
        "home": str(app.home),
        "cabinet_id": cabinet_id,
        "source_id": source_id,
        "gen7_record_hash": record7["record_hash"],
        "old_seal_id": seal_result["seal_id"],
        "_lines": [
            f"已初始化封存柜：{app.home}",
            "已固化完整旧卷：第 7 代 A7/B7/C7/D7",
            f"旧卷封条号：{seal_result['seal_id']}",
            f"源端创世见证：{record7['record_hash'][:16]}…",
        ],
    }


def order_path(app: App, order_id: str) -> Path:
    return app.orders_dir / f"{order_id}.json"


def load_order(app: App, order_id: str) -> dict[str, Any]:
    path = order_path(app, order_id)
    if not path.exists():
        raise CliError("ORDER_NOT_FOUND", f"找不到委托单 {order_id}")
    return read_json(path)


def save_order(app: App, order: dict[str, Any]) -> None:
    atomic_write_json(order_path(app, order["order_id"]), order)


def submit_order(
    app: App,
    generation: int,
    blocks: list[str],
    code: str | None = None,
    volume: str | None = None,
    predecessor: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    app.require()
    if not blocks:
        raise CliError("EMPTY_SCOPE", "委托范围不能为空；用 --all 或 --blocks A,B")
    if generation < 1:
        raise CliError("BAD_GENERATION", "代次必须为正整数")
    order_id = new_id("ORD")
    all_blocks = set(blocks) == set(BLOCKS)
    order = {
        "order_id": order_id,
        "code": code or f"DA-G{generation}-{secrets.token_hex(2).upper()}",
        "volume": volume or f"第 {generation} 代保全委托",
        "generation": generation,
        "requested_blocks": blocks,
        "promised_kind": STATUS_COMPLETE if all_blocks else STATUS_PARTIAL,
        "status": "open",
        "created_at": now_iso(),
        "note": note,
        "predecessor_ref": predecessor,
        "predecessor_seal_id": None,
        "received": {},
        "seal_id": None,
    }
    save_order(app, order)
    app.append_ledger({"event": "order_submitted", "order_id": order_id, "generation": generation, "blocks": blocks})
    return {
        "ok": True,
        "order_id": order_id,
        "code": order["code"],
        "generation": generation,
        "requested_blocks": blocks,
        "promised_kind": order["promised_kind"],
        "_lines": [
            f"新委托单：{order_id}",
            f"档案代号：{order['code']}",
            f"具名版本：第 {generation} 代",
            f"委托范围：{'全部四块（完整时点副本）' if all_blocks else '仅 ' + ','.join(blocks) + '（限定范围片段）'}",
        ],
    }


def receive_block(
    app: App,
    order_id: str,
    block: str,
    data: bytes,
    via: str,
    source_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    app.require()
    block = normalize_block(block)
    order = load_order(app, order_id)
    if order.get("seal_id") or order.get("status") == "sealed":
        raise CliError("ORDER_ALREADY_SEALED", f"委托单 {order_id} 已封卷；原凭据不得改写，新证据请另提交委托单")

    digest = sha256_hex(data)
    existing = order.get("received", {}).get(block)
    if existing and existing["sha256"] != digest:
        raise CliError(
            "BLOCK_POSITION_CONFLICT",
            f"{block} 位已收到不同内容；两份不同内容不能占同一个委托位置，也不得覆盖",
            details={"existing": existing["sha256"], "incoming": digest},
        )

    slots = app.slots()
    slot_id: str | None = None
    reused = False
    for slot in slots["slots"]:
        if slot.get("status") == "occupied" and slot.get("sha256") == digest:
            slot_id = slot["id"]
            reused = True
            break

    if slot_id is None:
        empty = next((s for s in slots["slots"] if s.get("status") == "empty"), None)
        if empty is None:
            occupied = [
                {"slot": s["id"], "block": s.get("block"), "sha256": s.get("sha256"), "order_id": s.get("order_id")}
                for s in slots["slots"]
                if s.get("status") == "occupied"
            ]
            raise CliError(
                "SLOT_FULL_WORM",
                "两个新整块位置均已 WORM 占用；不能覆盖。请使用已封旧卷引用，或另案/另介质出具补件。",
                exit_code=4,
                details={"capacity": slots.get("capacity", SLOT_CAPACITY), "occupied": occupied},
            )
        slot_id = empty["id"]
        empty.update({
            "status": "occupied",
            "block": block,
            "sha256": digest,
            "size": len(data),
            "received_at": now_iso(),
            "order_id": order_id,
            "seal_id": None,
        })

    app.cab_objects.put(data)

    if existing and existing["sha256"] == digest:
        received = existing
    else:
        received = {
            "sha256": digest,
            "size": len(data),
            "via": via,
            "received_at": now_iso(),
            "slot_id": slot_id,
            "slot_content_reused": reused,
            "source_observation": source_meta,
        }
        order.setdefault("received", {})[block] = received

    save_order(app, order)
    app.save_slots(slots)
    app.append_ledger({
        "event": "block_received",
        "order_id": order_id,
        "block": block,
        "sha256": digest,
        "slot_id": slot_id,
        "reused_slot_content": reused,
    })
    return {
        "ok": True,
        "order_id": order_id,
        "block": block,
        "block_name": BLOCK_NAMES[block],
        "sha256": digest,
        "slot_id": slot_id,
        "reused_existing_content": reused,
        "_lines": [
            f"已收 {block}（{BLOCK_NAMES[block]}）：{digest[:16]}…",
            f"位置：{slot_id}" + ("（相同内容已在柜，幂等引用）" if reused else "（新整块，WORM 不得覆盖）"),
        ],
    }


def load_seal_wrapper(app: App, ref: str) -> dict[str, Any]:
    app.require()
    exact = app.seals_dir / f"{ref}.json"
    if exact.is_file():
        return read_json(exact)
    matches = list(app.seals_dir.glob(f"{ref}*.json"))
    if not matches:
        raise CliError("SEAL_NOT_FOUND", f"找不到封条号或封条文件 {ref}")
    if len(matches) > 1:
        raise CliError("SEAL_REF_AMBIGUOUS", f"封条前缀 {ref} 匹配多个封条", details=[p.name for p in matches])
    return read_json(matches[0])


def resolve_predecessor(app: App, order: dict[str, Any], journal: dict[str, Any]) -> dict[str, Any] | None:
    ref = order.get("predecessor_ref")
    if ref:
        wrapper = load_seal_wrapper(app, ref)
        order["predecessor_seal_id"] = wrapper["seal_id"]
        return wrapper
    old_id = app.config().get("old_seal_id")
    if old_id:
        order["predecessor_seal_id"] = old_id
        return load_seal_wrapper(app, old_id)
    return None


def issue_notice(
    app: App,
    kind: str,
    order: dict[str, Any],
    generation: int,
    blocks: list[str],
    missing: list[str],
    conflicts: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    prefix = "WAIT" if kind == STATUS_INCOMPLETE else "REJECT"
    notice_id = new_id(prefix)
    notice = {
        "notice_id": notice_id,
        "kind": kind,
        "receipt_kind_zh": KIND_ZH[kind],
        "issued_at": now_iso(),
        "order_id": order.get("order_id"),
        "code": order.get("code"),
        "named_generation": generation,
        "requested_blocks": blocks,
        "missing_blocks": missing,
        "conflicts": conflicts,
        "reason": reason,
    }
    atomic_write_json(app.notices_dir / f"{notice_id}.json", notice)
    text_lines = [
        f"电子档案封存柜对外回执：{notice['receipt_kind_zh']}",
        f"回执编号：{notice_id}",
        f"委托单：{order.get('order_id')}；代号：{order.get('code')}",
        f"具名版本：第 {generation} 代；请求范围：{', '.join(blocks)}",
        f"结论说明：{reason}",
    ]
    if missing:
        text_lines.append(f"待补块/见证：{', '.join(sorted(set(missing)))}")
    for c in conflicts:
        text_lines.append(f"矛盾：{c.get('code')} {c.get('block', '')}")
    atomic_write_bytes(app.notices_dir / f"{notice_id}.txt", ("\n".join(text_lines) + "\n").encode("utf-8"))
    app.append_ledger({"event": "notice_issued", "notice_id": notice_id, "kind": kind, "order_id": order.get("order_id")})
    return notice


def seal_order(app: App, order_id: str) -> dict[str, Any]:
    app.require()
    order = load_order(app, order_id)
    if order.get("seal_id"):
        wrapper = load_seal_wrapper(app, order["seal_id"])
        return {
            "ok": True,
            "idempotent": True,
            "message": "该单已封过；返回原封条，不生成新凭据。",
            **wrapper,
            "_lines": [
                "该委托单已经封卷；原封条、代号和范围不得改写。",
                f"封条号：{wrapper['seal_id']}",
                f"回执种类：{KIND_ZH.get(wrapper['body'].get('promised_kind'), '未知')}",
            ],
        }

    journal = app.journal()
    witness = make_witness(app)
    _state, by_generation, replay_errors = replay_records(witness["records"])
    if replay_errors:
        raise CliError("WITNESS_CHAIN_INVALID", "源见证链未通过自校验，不能封卷", exit_code=3, details=replay_errors)

    generation = int(order["generation"])
    target_record = next((r for r in witness["records"] if r.get("generation") == generation), None)
    target_state = by_generation.get(generation, {})

    predecessor: dict[str, Any] | None = None
    if not order.get("predecessor_seal_id"):
        predecessor = resolve_predecessor(app, order, journal)
    elif order.get("predecessor_seal_id"):
        predecessor = load_seal_wrapper(app, order["predecessor_seal_id"])

    resolved: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    conflicts: list[dict[str, Any]] = []

    for block in order["requested_blocks"]:
        received = order.get("received", {}).get(block)
        if received:
            digest = received["sha256"]
            if not app.cab_objects.has(digest):
                conflicts.append({
                    "code": "cabinet_block_object_missing_or_corrupt",
                    "block": block,
                    "sha256": digest,
                })
                continue
            resolved[block] = {
                "sha256": digest,
                "size": received.get("size"),
                "storage": "direct_worm",
                "slot_id": received.get("slot_id"),
                "source_observation": received.get("source_observation"),
            }
        elif predecessor and block in predecessor.get("body", {}).get("blocks", {}):
            pred_entry = predecessor["body"]["blocks"][block]
            digest = pred_entry["sha256"]
            if not app.cab_objects.has(digest):
                missing.append(block)
                continue
            resolved[block] = {
                "sha256": digest,
                "size": pred_entry.get("size"),
                "storage": "inherited_reference",
                "referenced_seal_id": predecessor["seal_id"],
            }
        else:
            missing.append(block)
            continue

        expected = target_state.get(block)
        if target_record is None:
            missing.append(block)
        elif expected is None:
            conflicts.append({
                "code": "witness_has_no_block_at_named_generation",
                "block": block,
                "generation": generation,
            })
        elif expected != digest:
            seen_at = [g for g, st in sorted(by_generation.items()) if st.get(block) == digest]
            conflicts.append({
                "code": "cross_generation_conflict",
                "block": block,
                "sealed_sha256": digest,
                "witness_expected_sha256": expected,
                "digest_also_seen_at_generations": seen_at,
                "named_generation": generation,
            })

    if conflicts:
        notice = issue_notice(
            app, STATUS_INVALID, order, generation, order["requested_blocks"],
            missing, conflicts, "块哈希虽各自正确，但不能在具名代次的同一状态中共存。",
        )
        raise CliError(
            "CROSS_GENERATION_PROOF_FAILED",
            "整卷/委托范围证明失效：发现跨代矛盾；已出具 REJECT 记录，不予封条。",
            exit_code=3,
            details={"conflicts": conflicts, "notice": notice},
        )

    if target_record is None:
        notice = issue_notice(
            app, STATUS_INCOMPLETE, order, generation, order["requested_blocks"],
            order["requested_blocks"], [], f"源见证中没有第 {generation} 代提交，缺证待补。",
        )
        return {
            "ok": False,
            "status": STATUS_INCOMPLETE,
            "_exit_code": 2,
            "notice": notice,
            "_lines": ["[缺证待补] 源端没有该代次的连续见证。", f"待补凭据：{notice['notice_id']}"],
        }

    if missing:
        notice = issue_notice(
            app, STATUS_INCOMPLETE, order, generation, order["requested_blocks"],
            sorted(set(missing)), [], "未收齐委托块，也不能由已封旧卷连续接回。",
        )
        return {
            "ok": False,
            "status": STATUS_INCOMPLETE,
            "_exit_code": 2,
            "notice": notice,
            "_lines": [
                "[缺证待补] 尚不能封成承诺的副本。",
                f"缺失块：{', '.join(sorted(set(missing)))}",
                f"待补回执：{notice['notice_id']}",
                "可补齐后重试本单，或提交范围更窄的新单。",
            ],
        }

    promised_kind = order["promised_kind"]
    if promised_kind == STATUS_COMPLETE and set(resolved) != set(BLOCKS):
        raise CliError("INTERNAL_SCOPE_ERROR", "完整副本必须解析全部四块")

    for block, entry in resolved.items():
        entry["block_name"] = BLOCK_NAMES[block]

    body = {
        "format": "sealcab-seal-body/v1",
        "order_id": order_id,
        "code": order.get("code"),
        "volume": order.get("volume"),
        "named_generation": generation,
        "requested_blocks": order["requested_blocks"],
        "promised_kind": promised_kind,
        "blocks": resolved,
        "witness_anchor": {
            "source_id": witness["source_id"],
            "genesis_record_hash": witness["records"][0]["record_hash"],
            "target_record_hash": target_record["record_hash"],
            "target_generation": generation,
            "witness_record_count": len(witness["records"]),
        },
        "predecessor_seal_id": predecessor["seal_id"] if predecessor else None,
        "cabinet_id": app.config()["cabinet_id"],
        "sealed_at": now_iso(),
    }
    digest = digest_obj(body)
    seal_id = seal_number_for_digest(digest)
    wrapper = {
        "format": "sealcab-seal/v1",
        "seal_id": seal_id,
        "seal_sha256": digest,
        "body": body,
    }
    atomic_write_json(app.seals_dir / f"{seal_id}.json", wrapper)
    save_witness_snapshot(app, witness, target_record["record_hash"])

    order["status"] = "sealed"
    order["seal_id"] = seal_id
    order["sealed_at"] = body["sealed_at"]
    save_order(app, order)

    slots = app.slots()
    for slot in slots["slots"]:
        if slot.get("status") != "occupied":
            continue
        matching = [b for b, r in order.get("received", {}).items() if r.get("slot_id") == slot["id"]]
        if matching and slot.get("order_id") == order_id:
            slot["seal_id"] = seal_id
        elif slot.get("sha256") in {e["sha256"] for e in resolved.values()}:
            used = slot.setdefault("referenced_by_seals", [])
            if seal_id not in used:
                used.append(seal_id)
    app.save_slots(slots)
    app.append_ledger({"event": "seal_issued", "order_id": order_id, "seal_id": seal_id, "kind": promised_kind})

    return {
        "ok": True,
        "sealed": True,
        "seal_id": seal_id,
        "seal_sha256": digest,
        "promised_kind": promised_kind,
        "receipt_kind_zh": KIND_ZH[promised_kind],
        "code": order.get("code"),
        "generation": generation,
        "blocks": list(resolved),
        "_lines": [
            f"已封卷：{seal_id}",
            f"档案代号：{order.get('code')}",
            f"对外回执：{KIND_ZH[promised_kind]}（第 {generation} 代）",
            f"证明块：{', '.join(resolved)}",
            "下一步：deliver 登记收件，或 export-bundle 交给独立复核人。",
        ],
    }


def cmd_init(app: App, args: argparse.Namespace) -> dict[str, Any]:
    return init_home(app, force=bool(args.force))


def cmd_supplement_open(app: App, args: argparse.Namespace) -> dict[str, Any]:
    """从原案源见证派生一个独立补卷介质/另案柜；原柜两枚 WORM 位置保持不动。"""
    old_app = App(args.from_home)
    old_app.require()
    predecessor = load_seal_wrapper(old_app, args.predecessor)
    if app.config_path.exists():
        raise CliError("SUPPLEMENT_HOME_EXISTS", f"补卷目录已初始化：{app.home}")

    app.dirs()
    shutil.copytree(old_app.source_dir, app.source_dir, dirs_exist_ok=True)

    old_cfg = old_app.config()
    cfg = {
        "version": 1,
        "cabinet_id": new_id("SUP-CAB", 6),
        "source_id": old_cfg["source_id"],
        "created_at": now_iso(),
        "boot_count": 1,
        "slot_capacity": SLOT_CAPACITY,
        "old_seal_id": predecessor["seal_id"],
        "lineage": {
            "kind": "separate_supplement_medium",
            "from_home": str(old_app.home),
            "predecessor_seal_id": predecessor["seal_id"],
            "reason": "原柜两个 WORM 位置已占用；第九代成对证据另出补件，不覆盖原凭据。",
        },
    }
    app.save_config(cfg)
    atomic_write_json(app.slots_path, {
        "capacity": SLOT_CAPACITY,
        "slots": [
            {"id": "S1", "status": "empty", "note": "补卷位置一"},
            {"id": "S2", "status": "empty", "note": "补卷位置二"},
        ],
    })
    atomic_write_json(app.seals_dir / f"{predecessor['seal_id']}.json", predecessor)
    target_hash = predecessor["body"]["witness_anchor"]["target_record_hash"]
    old_witness = old_app.witness_dir / f"{target_hash}.json"
    if old_witness.is_file():
        shutil.copy2(old_witness, app.witness_dir / f"{target_hash}.json")
    app.append_ledger({"event": "supplement_opened", "predecessor_seal_id": predecessor["seal_id"], "from_home": str(old_app.home)})
    return {
        "ok": True,
        "supplement_home": str(app.home),
        "predecessor_seal_id": predecessor["seal_id"],
        "source_id": cfg["source_id"],
        "_lines": [
            f"已另开补卷介质：{app.home}",
            f"连续前据封条：{predecessor['seal_id']}",
            "该补卷有两个新的 WORM 位置；原柜封条和位置均未改动。",
        ],
    }


def cmd_restart(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    cfg = app.config()
    cfg["boot_count"] = int(cfg.get("boot_count", 1)) + 1
    cfg["last_restart_at"] = now_iso()
    app.save_config(cfg)
    seal_count = len(list(app.seals_dir.glob("SL-*.json")))
    order_count = len(list(app.orders_dir.glob("*.json")))
    return {
        "ok": True,
        "restarted": True,
        "boot_count": cfg["boot_count"],
        "home": str(app.home),
        "recovered_orders": order_count,
        "recovered_seals": seal_count,
        "_lines": [
            "已模拟重启：没有内存状态需要恢复，所有凭据均从磁盘恢复。",
            f"第 {cfg['boot_count']} 次开机；恢复委托单 {order_count} 份、封条 {seal_count} 枚。",
            "现在可用 seal show / verify / fetch 按原封条号取回副本。",
        ],
    }


def cmd_source_write(app: App, args: argparse.Namespace) -> dict[str, Any]:
    if args.file:
        data = Path(args.file).read_bytes()
    elif args.content is not None:
        data = args.content.encode("utf-8")
    else:
        data = sys.stdin.buffer.read()
    op = source_write_bytes(app, args.block, data, args.label)
    return {"ok": True, "operation": op, "_lines": [f"源端写入 op={op['op_id']} {op['block']} {op['sha256'][:16]}…；尚未指定代次提交。"]}


def cmd_source_commit(app: App, args: argparse.Namespace) -> dict[str, Any]:
    rec = source_commit(app, args.generation, args.message)
    return {
        "ok": True,
        "record_hash": rec["record_hash"],
        "generation": rec["generation"],
        "changes": rec["changes"],
        "_lines": [
            f"源端已提交第 {rec['generation']} 代",
            f"见证记录：{rec['record_hash']}",
            f"变更块：{', '.join(rec['changes'])}",
        ],
    }


def cmd_source_status(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    j = app.journal()
    return {
        "source_id": j["source_id"],
        "last_generation": j.get("last_generation"),
        "op_seq": j.get("op_seq"),
        "pending_ops": len(j.get("pending_ops", [])),
        "current": j.get("current", {}),
        "slots": app.slots() if app.slots_path.exists() else None,
        "_lines": [
            f"源端：{j['source_id']}",
            f"最后代次：第 {j.get('last_generation')} 代；操作序号 {j.get('op_seq')}",
            f"待提交操作：{len(j.get('pending_ops', []))}",
        ],
    }


def cmd_source_journal(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    return app.journal()


def cmd_source_block(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    block = normalize_block(args.block)
    j = app.journal()
    digest = j.get("current", {}).get(block)
    if not digest:
        raise CliError("BLOCK_NOT_FOUND", f"源端当前没有 {block} 块")
    data = app.source_objects.get(digest)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(out, data)
        return {"ok": True, "block": block, "sha256": digest, "out": str(out), "size": len(data)}
    sys.stdout.buffer.write(data)
    return {"_silent_json": True}


def cmd_source_snapshot(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    out = Path(args.out).resolve()
    if out.exists():
        raise CliError("SNAPSHOT_EXISTS", f"快照目录已存在：{out}")
    shutil.copytree(app.source_dir, out)
    return {"ok": True, "snapshot": str(out), "_lines": [f"源端快照已保存：{out}"]}


def cmd_source_restore(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    src = Path(args.input).resolve()
    if not (src / "journal.json").is_file():
        raise CliError("BAD_SNAPSHOT", f"{src} 不含 journal.json")
    if not args.yes:
        raise CliError("CONFIRM_REQUIRED", "恢复源端会替换当前模拟源目录；如确认请加 --yes")
    shutil.rmtree(app.source_dir)
    shutil.copytree(src, app.source_dir)
    return {"ok": True, "restored_from": str(src), "_lines": [f"已还原来源：{src}；柜内封条和副本未改动。"]}


def cmd_source_wipe(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    if not args.yes:
        raise CliError("CONFIRM_REQUIRED", "清机将删除源端块字节，仅保留哈希见证；确认请加 --yes")
    objects = app.source_dir / "objects"
    if objects.exists():
        shutil.rmtree(objects)
    objects.mkdir(parents=True, exist_ok=True)
    j = app.journal()
    j["content_wiped_at"] = now_iso()
    app.save_journal(j)
    return {"ok": True, "wiped": True, "_lines": ["源端块字节已清除；journal 哈希链仍在，但旧代内容不能从源端补读。"]}


def cmd_scenario_gen8(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    j = app.journal()
    if any(r.get("generation") == 8 for r in j.get("commits", [])):
        return {"ok": True, "already": True, **next(r for r in j["commits"] if r["generation"] == 8)}
    for b, data in gen8_blocks().items():
        source_write_bytes(app, b, data, "第八代变更")
    rec = source_commit(app, 8, "第八代：改目录 A、签收页 B")
    return {"ok": True, **rec, "_lines": ["已模拟第八代：A8、B8 写入并提交；C/D 未改。", f"见证：{rec['record_hash']}"]}


def cmd_scenario_gen9(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    j = app.journal()
    if any(r.get("generation") == 9 for r in j.get("commits", [])):
        return {"ok": True, "already": True, **next(r for r in j["commits"] if r["generation"] == 9)}
    if not any(r.get("generation") == 8 for r in j.get("commits", [])):
        # 允许“工位直接换到第九代”的分支，但先在源日志中保留真实操作顺序。
        for b, data in gen8_blocks().items():
            source_write_bytes(app, b, data, "第八代变更（事后见证）")
        source_commit(app, 8, "第八代：改目录 A、签收页 B")
    for b, data in gen9_blocks().items():
        source_write_bytes(app, b, data, "第九代业务员修改/新添")
    rec = source_commit(app, 9, "第九代：B9 签收页再改，C9 交接说明新增")
    return {"ok": True, **rec, "_lines": ["已模拟第九代：B9、C9 成对提交。", f"见证：{rec['record_hash']}"]}


def cmd_order_submit(app: App, args: argparse.Namespace) -> dict[str, Any]:
    blocks = normalize_blocks(args.blocks or [])
    if args.all:
        blocks = list(BLOCKS)
    return submit_order(app, args.generation, blocks, args.code, args.volume, args.predecessor, args.note)


def cmd_order_list(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    orders = []
    for p in sorted(app.orders_dir.glob("*.json")):
        o = read_json(p)
        orders.append({
            "order_id": o["order_id"],
            "code": o.get("code"),
            "generation": o.get("generation"),
            "scope": o.get("requested_blocks"),
            "promised_kind": o.get("promised_kind"),
            "status": o.get("status"),
            "seal_id": o.get("seal_id"),
        })
    return {"orders": orders, "_lines": [f"{o['order_id']}  G{o['generation']}  {o['status']}  {o.get('seal_id') or ''}" for o in orders] or ["（无委托单）"]}


def cmd_order_show(app: App, args: argparse.Namespace) -> dict[str, Any]:
    return load_order(app, args.order_id)


def cmd_copy_receive(app: App, args: argparse.Namespace) -> dict[str, Any]:
    if args.file:
        data = Path(args.file).read_bytes()
        via = f"file:{args.file}"
    elif args.text is not None:
        data = args.text.encode("utf-8")
        via = "text-argument"
    elif args.content is not None:
        data = args.content.encode("utf-8")
        via = "content-argument"
    else:
        data = sys.stdin.buffer.read()
        via = "stdin"
    meta = None
    if args.from_source:
        app.require()
        j = app.journal()
        block = normalize_block(args.block)
        digest = j.get("current", {}).get(block)
        if not digest:
            raise CliError("SOURCE_BLOCK_MISSING", f"源端当前没有 {block} 块")
        source_data = app.source_objects.get(digest)
        if data and data != source_data and not args.allow_text_override:
            raise CliError("SOURCE_CONTENT_MISMATCH", "显式内容与 --from-source 指定的源端当前块不一致")
        data = source_data
        via = "source-current"
        meta = {
            "source_last_generation": j.get("last_generation"),
            "source_head_record_hash": j["commits"][-1]["record_hash"] if j.get("commits") else None,
            "source_op_seq": j.get("op_seq"),
            "source_current_sha256": digest,
        }
    return receive_block(app, args.order_id, args.block, data, via, meta)


def cmd_seal(app: App, args: argparse.Namespace) -> dict[str, Any]:
    result = seal_order(app, args.order_id)
    if args.export_bundle and result.get("seal_id"):
        exported = export_bundle(app, result["seal_id"], Path(args.export_bundle))
        result["bundle"] = exported
        result.setdefault("_lines", []).append(f"独立复核包已导出：{exported['out']}")
    elif args.export_bundle:
        result.setdefault("_lines", []).append("尚未形成封条，不能导出独立复核包；请按缺证待补回执补证或提交更窄新单。")
    return result


def cmd_seal_show(app: App, args: argparse.Namespace) -> dict[str, Any]:
    wrapper = load_seal_wrapper(app, args.seal_ref)
    b = wrapper["body"]
    return {
        **wrapper,
        "_lines": [
            f"封条号：{wrapper['seal_id']}",
            f"代号：{b.get('code')}；具名版本：第 {b['named_generation']} 代",
            f"种类：{KIND_ZH.get(b['promised_kind'], b['promised_kind'])}",
            f"块：{', '.join(b['blocks'])}",
            f"前置封条：{b.get('predecessor_seal_id') or '（无）'}",
        ],
    }


def export_bundle(app: App, seal_ref: str, out: Path, force: bool = False) -> dict[str, Any]:
    wrapper = load_seal_wrapper(app, seal_ref)
    target_hash = wrapper["body"]["witness_anchor"]["target_record_hash"]
    witness_path = app.witness_dir / f"{target_hash}.json"
    if not witness_path.is_file():
        raise CliError("WITNESS_SNAPSHOT_MISSING", f"柜内缺少封条锚定的源端见证快照：{target_hash}")
    if out.exists():
        if not force:
            raise CliError("BUNDLE_EXISTS", f"导出目录已存在：{out}；如需覆盖导出文件请加 --force（不会改写原封条）")
        shutil.rmtree(out)
    replica = out / "replica"
    replica.mkdir(parents=True)
    files = []
    for block, entry in wrapper["body"]["blocks"].items():
        data = app.cab_objects.get(entry["sha256"])
        if sha256_hex(data) != entry["sha256"]:
            raise CliError("BLOCK_HASH_MISMATCH", f"柜内 {block} 块与封条不符")
        atomic_write_bytes(replica / f"{block}.bin", data)
        files.append(f"replica/{block}.bin")
    shutil.copy2(witness_path, out / "witness.json")
    atomic_write_json(out / "seal.json", wrapper)
    return {"out": str(out), "files": files + ["seal.json", "witness.json"], "seal_id": wrapper["seal_id"]}


def cmd_export_bundle(app: App, args: argparse.Namespace) -> dict[str, Any]:
    result = export_bundle(app, args.seal_ref, Path(args.out).resolve(), bool(args.force))
    result["_lines"] = [f"独立复核包已导出：{result['out']}", "内含副本块、seal.json、witness.json；复核不需要柜内状态。"]
    return result


def fetch_copy(app: App, seal_ref: str, out: Path, force: bool = False) -> dict[str, Any]:
    wrapper = load_seal_wrapper(app, seal_ref)
    body = wrapper["body"]
    if out.exists() and any(out.iterdir()) and not force:
        raise CliError("FETCH_DIR_NOT_EMPTY", f"取回目录非空：{out}；换一个目录或加 --force")
    out.mkdir(parents=True, exist_ok=True)
    retrieved = []
    for block, entry in body["blocks"].items():
        data = app.cab_objects.get(entry["sha256"])
        actual = sha256_hex(data)
        if actual != entry["sha256"]:
            raise CliError("BLOCK_HASH_MISMATCH", f"{block} 块哈希损坏")
        path = out / BLOCK_FILES.get(block, f"{block}.bin")
        atomic_write_bytes(path, data)
        retrieved.append({"block": block, "path": str(path), "sha256": actual})
    manifest = (
        f"封条号：{wrapper['seal_id']}\n"
        f"代号：{body.get('code')}\n"
        f"具名版本：第 {body['named_generation']} 代\n"
        f"回执种类：{KIND_ZH.get(body['promised_kind'])}\n"
        "取回块：" + ", ".join(body["blocks"]) + "\n"
    )
    atomic_write_bytes(out / "RETRIEVED.txt", manifest.encode("utf-8"))
    return {"ok": True, "seal_id": wrapper["seal_id"], "out": str(out), "retrieved": retrieved}


def cmd_fetch(app: App, args: argparse.Namespace) -> dict[str, Any]:
    result = fetch_copy(app, args.seal_ref, Path(args.out).resolve(), bool(args.force))
    result["_lines"] = [f"已从封存柜取回原副本：{result['out']}"] + [f"{x['block']}: {x['path']}" for x in result["retrieved"]]
    return result


def cmd_deliver(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    wrapper = load_seal_wrapper(app, args.seal_ref)
    body = wrapper["body"]
    receipt_id = new_id("RCPT")
    receipt = {
        "receipt_id": receipt_id,
        "at": now_iso(),
        "recipient": args.to,
        "seal_id": wrapper["seal_id"],
        "seal_sha256": wrapper["seal_sha256"],
        "code": body.get("code"),
        "named_generation": body["named_generation"],
        "promised_kind": body["promised_kind"],
        "promised_blocks": body["requested_blocks"],
        "scope_note": args.scope_note,
    }
    receipt_text = "\n".join([
        "电子档案封存柜受托人收件回执",
        f"收件记录：{receipt_id}",
        f"受托人：{args.to}",
        f"收件时间：{receipt['at']}",
        f"封条号：{receipt['seal_id']}",
        f"封条完整 SHA-256：{receipt['seal_sha256']}",
        f"档案代号：{receipt['code']}",
        f"具名版本：第 {receipt['named_generation']} 代",
        f"可复核范围：{KIND_ZH[receipt['promised_kind']]} {','.join(receipt['promised_blocks'])}",
        f"范围备注：{args.scope_note or '（无）'}",
        "说明：后到代次只能另出新单/补件，本回执约束该封条的代号与可复核范围，原凭据不得改写。",
    ]) + "\n"
    atomic_write_json(app.receipts_dir / f"{receipt_id}.json", receipt)
    atomic_write_bytes(app.receipts_dir / f"{receipt_id}.txt", receipt_text.encode("utf-8"))
    with (app.receipts_dir / "receipts.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
    bundle_dir = Path(args.bundle_out).resolve() if args.bundle_out else (app.deliveries_dir / receipt_id / "bundle")
    exported = export_bundle(app, wrapper["seal_id"], bundle_dir, force=True)
    shutil.copy2(app.receipts_dir / f"{receipt_id}.json", bundle_dir.parent / "delivery-receipt.json")
    atomic_write_bytes(bundle_dir.parent / "delivery-receipt.txt", receipt_text.encode("utf-8"))
    app.append_ledger({"event": "delivered", "receipt_id": receipt_id, "seal_id": wrapper["seal_id"], "to": args.to})
    return {
        "ok": True,
        **receipt,
        "bundle": exported,
        "_lines": [
            f"收件记录：{receipt_id} → {args.to}",
            f"封条号：{receipt['seal_id']}",
            f"代号：{receipt['code']}；可复核范围：{KIND_ZH[receipt['promised_kind']]} {receipt['promised_blocks']}",
            f"交付包：{bundle_dir}",
            "此后第九代或新证据只能另出新单/补件，本收件记录约束原凭据范围。",
        ],
    }


def cmd_receipt_list(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    receipts = []
    for p in sorted(app.receipts_dir.glob("RCPT-*.json")):
        r = read_json(p)
        receipts.append({k: r.get(k) for k in ["receipt_id", "to", "recipient", "seal_id", "code", "promised_kind", "promised_blocks"]})
    return {"receipts": receipts, "_lines": [f"{r['receipt_id']}  {r['recipient']}  {r['seal_id']}  {KIND_ZH.get(r['promised_kind'], r['promised_kind'])}" for r in receipts] or ["（无收件记录）"]}


def verify_evidence(
    *,
    wrapper: dict[str, Any] | None = None,
    seal_path: Path | None = None,
    witness_path: Path | None = None,
    replica_dir: Path | None = None,
    object_store: ObjectStore | None = None,
    expected_seal: str | None = None,
    receipt_path: Path | None = None,
    retrieve_to: Path | None = None,
) -> dict[str, Any]:
    conflicts: list[dict[str, Any]] = []
    missing_blocks: list[str] = []
    verified_blocks: list[str] = []
    verified_data: dict[str, bytes] = {}
    warnings: list[dict[str, Any]] = []

    if wrapper is None:
        if seal_path is None or not seal_path.is_file():
            return {
                "status": STATUS_INCOMPLETE,
                "receipt_kind_zh": KIND_ZH[STATUS_INCOMPLETE],
                "conflicts": [],
                "missing": ["seal.json"],
                "_lines": ["[缺证待补] 找不到封条文件 seal.json。"],
                "_exit_code": 2,
            }
        try:
            wrapper = read_json(seal_path)
        except json.JSONDecodeError as e:
            return {
                "status": STATUS_INVALID,
                "receipt_kind_zh": KIND_ZH[STATUS_INVALID],
                "conflicts": [{"code": "seal_json_invalid", "message": str(e)}],
                "_lines": ["[失效] seal.json 不是合法 JSON。"],
                "_exit_code": 3,
            }

    body = wrapper.get("body") if isinstance(wrapper, dict) else None
    if not isinstance(body, dict):
        conflicts.append({"code": "seal_body_missing"})
        body = {}

    digest = digest_obj(body)
    if wrapper.get("seal_sha256") != digest:
        conflicts.append({
            "code": "seal_digest_mismatch",
            "claimed": wrapper.get("seal_sha256"),
            "actual": digest,
        })
    computed_seal_id = seal_number_for_digest(digest)
    if wrapper.get("seal_id") != computed_seal_id:
        conflicts.append({
            "code": "seal_number_recompute_mismatch",
            "claimed": wrapper.get("seal_id"),
            "computed": computed_seal_id,
        })

    receipt = None
    if receipt_path:
        if not Path(receipt_path).is_file():
            conflicts.append({"code": "receipt_file_missing", "path": str(receipt_path)})
        else:
            receipt = read_json(Path(receipt_path))
            expected_seal = expected_seal or receipt.get("seal_id")
            if receipt.get("seal_id") and receipt["seal_id"] != wrapper.get("seal_id"):
                conflicts.append({"code": "receipt_seal_id_mismatch", "receipt": receipt.get("seal_id"), "seal": wrapper.get("seal_id")})
            if receipt.get("seal_sha256") and receipt["seal_sha256"] != wrapper.get("seal_sha256"):
                conflicts.append({"code": "receipt_seal_digest_mismatch"})
            if receipt.get("promised_kind") and receipt["promised_kind"] != body.get("promised_kind"):
                conflicts.append({"code": "receipt_scope_kind_mismatch", "receipt": receipt.get("promised_kind"), "seal": body.get("promised_kind")})
            if receipt.get("promised_blocks") and set(receipt["promised_blocks"]) != set(body.get("requested_blocks", [])):
                conflicts.append({"code": "receipt_scope_blocks_mismatch", "receipt": receipt.get("promised_blocks"), "seal": body.get("requested_blocks")})

    seal_match = seal_ref_matches(expected_seal, wrapper)
    if seal_match is False:
        conflicts.append({
            "code": "wrong_trusted_seal_number",
            "expected": expected_seal,
            "presented": wrapper.get("seal_id"),
        })

    witness = None
    witness_errors: list[dict[str, Any]] = []
    by_generation: dict[int, dict[str, str]] = {}
    lineage_by_generation: dict[int, dict[str, str]] = {}
    target_record = None
    if witness_path is None or not Path(witness_path).is_file():
        conflicts_or_missing = "missing_witness"
        missing_blocks.append(conflicts_or_missing)
    else:
        try:
            witness = read_json(Path(witness_path))
            records = witness.get("records", []) if isinstance(witness, dict) else []
            # 全链仅用于查找某个哈希还出现在哪些代次；封条只锚定 target 及其前缀。
            _full_state, full_by_generation, _all_witness_errors = replay_records(records)
            by_generation = full_by_generation
            lineage_by_generation = full_by_generation
            anchor = body.get("witness_anchor", {})
            if witness.get("source_id") != anchor.get("source_id"):
                conflicts.append({"code": "source_id_mismatch", "seal_anchor": anchor.get("source_id"), "witness": witness.get("source_id")})
            target_hash = anchor.get("target_record_hash")
            target_index = next((i for i, r in enumerate(records) if r.get("record_hash") == target_hash), None)
            if target_index is not None:
                target_record = records[target_index]
                _prefix_state, prefix_by_generation, prefix_errors = replay_records(records[:target_index + 1])
                conflicts.extend(prefix_errors)
                by_generation = prefix_by_generation
                if target_record.get("generation") != body.get("named_generation"):
                    conflicts.append({
                        "code": "witness_target_generation_mismatch",
                        "seal_named": body.get("named_generation"),
                        "witness_record": target_record.get("generation"),
                    })
            else:
                same_index = next((i for i, r in enumerate(records) if r.get("generation") == body.get("named_generation")), None)
                if same_index is not None:
                    same_generation = records[same_index]
                    conflicts.append({
                        "code": "witness_anchor_record_replaced",
                        "anchored_record_hash": target_hash,
                        "presented_record_hash": same_generation.get("record_hash"),
                        "generation": body.get("named_generation"),
                    })
                    target_record = same_generation
                    _prefix_state, prefix_by_generation, prefix_errors = replay_records(records[:same_index + 1])
                    conflicts.extend(prefix_errors)
                    by_generation = prefix_by_generation
                else:
                    missing_blocks.append("witness_target_record")
        except json.JSONDecodeError as e:
            conflicts.append({"code": "witness_json_invalid", "message": str(e)})

    promised_kind = body.get("promised_kind")
    requested = body.get("requested_blocks", [])
    block_entries = body.get("blocks", {})
    if promised_kind == STATUS_COMPLETE and set(requested) != set(BLOCKS):
        conflicts.append({"code": "complete_receipt_without_all_four_blocks", "requested": requested})
    if set(requested) != set(block_entries.keys()):
        conflicts.append({"code": "requested_blocks_do_not_match_manifest", "requested": requested, "manifest": list(block_entries)})

    target_generation = body.get("named_generation")
    target_state = by_generation.get(target_generation, {}) if isinstance(target_generation, int) else {}

    for block in sorted(block_entries.keys()):
        entry = block_entries.get(block, {})
        expected_hash = entry.get("sha256")
        data: bytes | None = None
        read_error = None
        if replica_dir is not None:
            p = Path(replica_dir) / f"{block}.bin"
            if not p.is_file():
                read_error = "replica_file_missing"
            else:
                data = p.read_bytes()
        elif object_store is not None:
            if not object_store.has(expected_hash):
                read_error = "cabinet_object_missing"
            else:
                data = object_store.get(expected_hash)
        else:
            read_error = "no_block_reader"

        if read_error or data is None:
            missing_blocks.append(block)
            continue

        actual_hash = sha256_hex(data)
        if actual_hash != expected_hash:
            conflicts.append({
                "code": "block_self_hash_mismatch",
                "block": block,
                "manifest": expected_hash,
                "actual": actual_hash,
            })
            continue

        witness_hash = target_state.get(block)
        if target_record is not None and witness_hash is None:
            conflicts.append({"code": "witness_missing_block_at_generation", "block": block, "generation": target_generation})
        elif witness_hash is not None and witness_hash != expected_hash:
            seen_at = [g for g, st in sorted(lineage_by_generation.items()) if st.get(block) == expected_hash]
            conflicts.append({
                "code": "cross_generation_conflict",
                "block": block,
                "all_block_hashes_are_self_consistent": True,
                "sealed_hash": expected_hash,
                "witness_hash_at_named_generation": witness_hash,
                "sealed_hash_seen_at_generations": seen_at,
                "named_generation": target_generation,
            })
        else:
            verified_blocks.append(block)
            verified_data[block] = data

    if replica_dir is not None and Path(replica_dir).is_dir():
        declared = set(block_entries.keys())
        for p in sorted(Path(replica_dir).glob("?.bin")):
            block = p.stem
            if block in BLOCKS and block not in declared:
                warnings.append({"code": "undeclared_block_file_not_retrieved", "block": block, "path": str(p)})

    missing_blocks = sorted(set(missing_blocks))
    self_hash_mismatches = [c for c in conflicts if c.get("code") == "block_self_hash_mismatch"]
    block_self_hashes_valid = not self_hash_mismatches
    if conflicts:
        status = STATUS_INVALID
    elif missing_blocks or target_record is None or witness is None:
        status = STATUS_INCOMPLETE
    elif promised_kind == STATUS_COMPLETE:
        status = STATUS_COMPLETE if set(verified_blocks) == set(BLOCKS) else STATUS_INCOMPLETE
    elif promised_kind == STATUS_PARTIAL:
        status = STATUS_PARTIAL if set(verified_blocks) == set(requested) else STATUS_INCOMPLETE
    else:
        status = STATUS_INVALID
        conflicts.append({"code": "unknown_promised_kind", "promised_kind": promised_kind})

    retrieved_files: list[str] = []
    if retrieve_to is not None and status in (STATUS_COMPLETE, STATUS_PARTIAL):
        out = Path(retrieve_to)
        out.mkdir(parents=True, exist_ok=True)
        for block in verified_blocks:
            path = out / BLOCK_FILES.get(block, f"{block}.bin")
            atomic_write_bytes(path, verified_data[block])
            retrieved_files.append(str(path))
        scope_note = (
            f"封条号：{wrapper.get('seal_id')}\n"
            f"结论：{KIND_ZH[status]}\n"
            f"具名版本：第 {target_generation} 代\n"
            f"本次实际取回：{', '.join(verified_blocks)}\n"
            f"未交付/未取回块：{', '.join(b for b in BLOCKS if b not in verified_blocks)}\n"
        )
        atomic_write_bytes(out / "VERIFIED-RETRIEVAL.txt", scope_note.encode("utf-8"))
        retrieved_files.append(str(out / "VERIFIED-RETRIEVAL.txt"))

    lines = [
        f"封条号：{wrapper.get('seal_id')}" + ("（与可信封条号一致）" if seal_match else "（未提供可信封条号，仅自校验）" if seal_match is None else "（与可信封条号不符！）"),
        f"复核结论：[{status.upper()}] {KIND_ZH[status]}",
        f"具名版本：第 {target_generation} 代；承诺范围：{promised_kind} {requested}",
        f"已验证并可取回：{', '.join(verified_blocks) or '（无）'}",
    ]
    if missing_blocks:
        lines.append(f"缺证/缺块：{', '.join(missing_blocks)}")
    for c in conflicts:
        if c.get("code") == "cross_generation_conflict":
            lines.append(
                f"跨代矛盾：{c['block']} 的哈希在第 {c.get('named_generation')} 代应为 "
                f"{str(c.get('witness_hash_at_named_generation'))[:12]}…，封入的哈希仅见于第 "
                f"{c.get('sealed_hash_seen_at_generations')} 代；单块哈希正确也不能证明同卷共存。"
            )
        else:
            lines.append(f"矛盾/失效：{c.get('code')}")
    for w in warnings:
        lines.append(f"范围提示：发现未声明文件 {w['block']}，复核取回时不会写出。")
    if retrieved_files:
        lines.append(f"已实际取回 {len([p for p in retrieved_files if not p.endswith('VERIFIED-RETRIEVAL.txt')])} 个块到：{retrieve_to}")

    return {
        "ok": status in (STATUS_COMPLETE, STATUS_PARTIAL),
        "status": status,
        "receipt_kind_zh": KIND_ZH[status],
        "seal_id": wrapper.get("seal_id"),
        "seal_sha256": wrapper.get("seal_sha256"),
        "generation": target_generation,
        "verified_blocks": verified_blocks,
        "missing_blocks": missing_blocks,
        "block_self_hashes_valid": block_self_hashes_valid,
        "conflicts": conflicts,
        "warnings": warnings,
        "retrieved_files": retrieved_files,
        "receipt": receipt,
        "_lines": lines,
        "_exit_code": STATUS_EXIT[status],
    }


def cmd_verify(app: App, args: argparse.Namespace) -> dict[str, Any]:
    expected = args.expect_seal or args.seal_number
    if args.bundle:
        bundle = Path(args.bundle).resolve()
        return verify_evidence(
            seal_path=bundle / "seal.json",
            witness_path=bundle / "witness.json",
            replica_dir=bundle / "replica",
            expected_seal=expected,
            receipt_path=Path(args.receipt).resolve() if args.receipt else None,
            retrieve_to=Path(args.retrieve_to).resolve() if args.retrieve_to else None,
        )

    if not expected:
        raise CliError("SEAL_NUMBER_REQUIRED", "柜内复核需要给出封条号；独立复核请使用 --bundle")
    app.require()
    wrapper = load_seal_wrapper(app, expected)
    anchor = wrapper["body"]["witness_anchor"]["target_record_hash"]
    witness_path = app.witness_dir / f"{anchor}.json"
    return verify_evidence(
        wrapper=wrapper,
        witness_path=witness_path,
        object_store=app.cab_objects,
        expected_seal=expected,
        receipt_path=Path(args.receipt).resolve() if args.receipt else None,
        retrieve_to=Path(args.retrieve_to).resolve() if args.retrieve_to else None,
    )


def cmd_demo_mix(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    bundle = Path(args.bundle).resolve()
    out = Path(args.out).resolve()
    if not (bundle / "seal.json").is_file() or not (bundle / "witness.json").is_file():
        raise CliError("BAD_BUNDLE", "输入目录不像复核包，缺少 seal.json 或 witness.json")
    if out.exists():
        raise CliError("OUT_EXISTS", f"输出目录已存在：{out}")

    block = normalize_block(args.replace)
    donor_gen = args.donor_generation
    journal = app.journal()
    donor_record = next((r for r in journal["commits"] if r.get("generation") == donor_gen), None)
    if not donor_record or block not in donor_record.get("changes", {}):
        raise CliError("DONOR_NOT_FOUND", f"源见证中找不到第 {donor_gen} 代 {block} 块变更")
    donor_hash = donor_record["changes"][block]["sha256"]
    donor_data = app.source_objects.get(donor_hash)

    shutil.copytree(bundle, out)
    target_file = out / "replica" / f"{block}.bin"
    atomic_write_bytes(target_file, donor_data)

    witness = make_witness(app)
    atomic_write_json(out / "witness.json", witness)

    seal_wrapper = read_json(out / "seal.json")
    body = seal_wrapper["body"]
    if block not in body.get("blocks", {}):
        raise CliError("BLOCK_NOT_IN_SEAL", f"原封条正文没有 {block} 块，不能做单次替换演示")
    body["blocks"][block]["sha256"] = donor_hash
    body["blocks"][block]["size"] = len(donor_data)
    body["blocks"][block]["demo_cross_generation_swap"] = {
        "replaced_once_with": f"G{donor_gen}:{block}",
        "at": now_iso(),
    }
    digest = digest_obj(body)
    forged_id = seal_number_for_digest(digest)
    forged = {"format": seal_wrapper.get("format", "sealcab-seal/v1"), "seal_id": forged_id, "seal_sha256": digest, "body": body}
    atomic_write_json(out / "seal.json", forged)
    return {
        "ok": True,
        "bad_bundle": str(out),
        "forged_seal_number": forged_id,
        "replaced_block": block,
        "donor_generation": donor_gen,
        "donor_sha256": donor_hash,
        "_lines": [
            f"已只替换一次：{block} 位换成第 {donor_gen} 代有效块。",
            f"矛盾包：{out}",
            f"新封条号（若攻击者重新封条）：{forged_id}",
            "每个文件的自哈希仍正确，但 verify-bundle 应判定 cross_generation_conflict。",
        ],
    }


def cmd_demo_witness_swap(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.require()
    bundle = Path(args.bundle).resolve()
    out = Path(args.out).resolve()
    if not (bundle / "seal.json").is_file() or not (bundle / "witness.json").is_file():
        raise CliError("BAD_BUNDLE", "输入目录不像复核包，缺少 seal.json 或 witness.json")
    if out.exists():
        raise CliError("OUT_EXISTS", f"输出目录已存在：{out}")

    block = normalize_block(args.block)
    target_gen = args.target_generation
    donor_gen = args.donor_generation
    journal = app.journal()
    donor_record = next((r for r in journal["commits"] if r.get("generation") == donor_gen), None)
    if not donor_record or block not in donor_record.get("changes", {}):
        raise CliError("DONOR_NOT_FOUND", f"源见证中找不到第 {donor_gen} 代 {block} 块变更")
    donor_hash = donor_record["changes"][block]["sha256"]

    shutil.copytree(bundle, out)
    witness_path = out / "witness.json"
    witness = read_json(witness_path)
    target_record = next((r for r in witness.get("records", []) if r.get("generation") == target_gen), None)
    if target_record is None or block not in target_record.get("changes", {}):
        raise CliError("TARGET_RECORD_NOT_FOUND", f"包内见证找不到第 {target_gen} 代 {block} 记录")

    # 只替换这一条块见证，然后按源端哈希链格式重算该记录；封条仍锚定旧记录哈希。
    target_record["changes"][block]["sha256"] = donor_hash
    target_record["cross_generation_demo"] = {
        "replaced_once_with": f"G{donor_gen}:{block}",
        "at": now_iso(),
    }
    body = {k: v for k, v in target_record.items() if k != "record_hash"}
    target_record["record_hash"] = digest_obj(body)
    atomic_write_json(witness_path, witness)

    seal_wrapper = read_json(out / "seal.json")
    return {
        "ok": True,
        "bad_bundle": str(out),
        "original_seal_number": seal_wrapper.get("seal_id"),
        "target_generation": target_gen,
        "replaced_block": block,
        "donor_generation": donor_gen,
        "donor_sha256": donor_hash,
        "_lines": [
            f"已把第 {target_gen} 代见证中的 {block} 只换成第 {donor_gen} 代哈希。",
            f"矛盾见证包：{out}",
            f"仍用原封条号复核：{seal_wrapper.get('seal_id')}",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS 使全局位置和子命令位置的同名参数不会互相覆盖。
    common.add_argument("--home", default=argparse.SUPPRESS, help="封存柜数据目录；默认 $SEALCAB_HOME 或 ~/.sealcab")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="输出 JSON")

    parser = argparse.ArgumentParser(
        prog="seal-cabinet",
        parents=[common],
        description="电子档案保全室封存柜：代次见证、WORM 块、封条号和独立复核",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", parents=[common], help="初始化并固化第七代完整旧卷")
    p.add_argument("--force", action="store_true", help="删除并重建 HOME（仅演示用）")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("supplement-open", parents=[common], help="从原案派生另一个补卷介质，用于第九代补件")
    p.add_argument("--from-home", required=True)
    p.add_argument("--predecessor", required=True, help="不得改写的原第八代封条号")
    p.set_defaults(func=cmd_supplement_open)

    p = sub.add_parser("restart", parents=[common], help="模拟重启并从磁盘恢复")
    p.set_defaults(func=cmd_restart)

    p = sub.add_parser("verify", parents=[common], help="按封条号复核柜内状态或独立交付包")
    p.add_argument("seal_number", nargs="?", help="可信封条号 SL-...")
    p.add_argument("--bundle", help="独立复核包目录（含 seal.json/witness.json/replica）")
    p.add_argument("--expect-seal", help="可信封条号；也可用位置参数传入")
    p.add_argument("--receipt", help="受托人收件记录 JSON")
    p.add_argument("--retrieve-to", help="复核通过后，把承诺块实际取回到该目录")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("fetch", parents=[common], help="重启后按原封条号从柜内取回副本")
    p.add_argument("seal_ref")
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("export-bundle", parents=[common], help="导出独立复核包")
    p.add_argument("seal_ref")
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_export_bundle)

    p = sub.add_parser("deliver", parents=[common], help="登记独立受托人收件并导出交付包")
    p.add_argument("seal_ref")
    p.add_argument("--to", required=True, help="受托人/收件人")
    p.add_argument("--scope-note")
    p.add_argument("--bundle-out")
    p.set_defaults(func=cmd_deliver)

    p = sub.add_parser("receipt-list", parents=[common], help="列收件记录")
    p.set_defaults(func=cmd_receipt_list)

    # source
    source = sub.add_parser("source", parents=[common], help="可写模拟源")
    source_sub = source.add_subparsers(dest="source_command", required=True)

    sp = source_sub.add_parser("write", parents=[common], help="按顺序写一个块（可从 stdin）")
    sp.add_argument("block")
    sp.add_argument("content", nargs="?")
    sp.add_argument("--file")
    sp.add_argument("--label")
    sp.set_defaults(func=cmd_source_write)

    sp = source_sub.add_parser("commit", parents=[common], help="把待处理操作提交为具名代次")
    sp.add_argument("generation", type=int)
    sp.add_argument("--message")
    sp.set_defaults(func=cmd_source_commit)

    sp = source_sub.add_parser("status", parents=[common])
    sp.set_defaults(func=cmd_source_status)

    sp = source_sub.add_parser("journal", parents=[common])
    sp.set_defaults(func=cmd_source_journal)

    sp = source_sub.add_parser("block", parents=[common], help="读源端当前块")
    sp.add_argument("block")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_source_block)

    sp = source_sub.add_parser("snapshot", parents=[common], help="快照源端目录")
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_source_snapshot)

    sp = source_sub.add_parser("restore", parents=[common], help="从源端快照还原")
    sp.add_argument("--input", required=True)
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_source_restore)

    sp = source_sub.add_parser("wipe-blobs", parents=[common], help="清机：删除源端块字节，保留哈希见证")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_source_wipe)

    # scenario
    scenario = sub.add_parser("scenario", parents=[common], help="预置叙事节拍")
    scenario_sub = scenario.add_subparsers(dest="scenario_command", required=True)
    sp = scenario_sub.add_parser("gen8", parents=[common], help="写入第八代 A8/B8")
    sp.set_defaults(func=cmd_scenario_gen8)
    sp = scenario_sub.add_parser("gen9", parents=[common], help="写入第九代 B9/C9")
    sp.set_defaults(func=cmd_scenario_gen9)

    # orders
    order = sub.add_parser("order", parents=[common], help="保全委托单")
    order_sub = order.add_subparsers(dest="order_command", required=True)

    op = order_sub.add_parser("submit", parents=[common], help="提交新委托单")
    op.add_argument("--generation", type=int, required=True)
    op.add_argument("--blocks", action="append", help="A,B,C,D；可重复")
    op.add_argument("--all", action="store_true")
    op.add_argument("--code")
    op.add_argument("--volume")
    op.add_argument("--predecessor")
    op.add_argument("--note")
    op.set_defaults(func=cmd_order_submit)

    op = order_sub.add_parser("list", parents=[common])
    op.set_defaults(func=cmd_order_list)

    op = order_sub.add_parser("show", parents=[common])
    op.add_argument("order_id")
    op.set_defaults(func=cmd_order_show)

    # copy
    cp = sub.add_parser("copy", parents=[common], help="把实际拷到的块接收入柜")
    copy_sub = cp.add_subparsers(dest="copy_command", required=True)
    rp = copy_sub.add_parser("receive", parents=[common], help="接收一个块到 WORM 位置")
    rp.add_argument("order_id")
    rp.add_argument("block")
    rp.add_argument("content", nargs="?")
    rp.add_argument("--file")
    rp.add_argument("--text")
    rp.add_argument("--from-source", action="store_true")
    rp.add_argument("--allow-text-override", action="store_true")
    rp.set_defaults(func=cmd_copy_receive)

    # seal
    sl = sub.add_parser("seal", parents=[common], help="检验并封卷")
    sl.add_argument("order_id")
    sl.add_argument("--export-bundle", help="封卷成功后同时导出复核包")
    sl.set_defaults(func=cmd_seal)

    ss = sub.add_parser("seal-show", parents=[common])
    ss.add_argument("seal_ref")
    ss.set_defaults(func=cmd_seal_show)

    dm = sub.add_parser("demo-mix", parents=[common], help="生成一次跨代调换的矛盾复核包（演示攻击）")
    dm.add_argument("--bundle", required=True)
    dm.add_argument("--out", required=True)
    dm.add_argument("--replace", default="B")
    dm.add_argument("--donor-generation", type=int, default=8)
    dm.set_defaults(func=cmd_demo_mix)

    dw = sub.add_parser("demo-witness-swap", parents=[common], help="只替换一条跨代见证记录（演示失效）")
    dw.add_argument("--bundle", required=True)
    dw.add_argument("--out", required=True)
    dw.add_argument("--block", default="B")
    dw.add_argument("--target-generation", type=int, default=7)
    dw.add_argument("--donor-generation", type=int, default=8)
    dw.set_defaults(func=cmd_demo_witness_swap)

    return parser


def render_result(result: dict[str, Any], as_json: bool) -> str:
    silent_json = result.pop("_silent_json", False)
    lines = result.pop("_lines", None)
    exit_code = result.pop("_exit_code", 0)
    result.pop("_exit_code_rendered", None)
    if as_json:
        result["exit_code"] = int(exit_code)
        return json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n"
    if silent_json:
        return ""
    if lines:
        return "\n".join(str(x) for x in lines) + "\n"
    return json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    app = App(getattr(args, "home", None))
    try:
        result = args.func(app, args)
        if not isinstance(result, dict):
            result = {"ok": True, "value": result}
        exit_code = int(result.get("_exit_code", 0) or 0)
        output = render_result(result, bool(getattr(args, "json", False)))
        if output:
            print(output, end="")
        return exit_code
    except CliError as e:
        err = {
            "ok": False,
            "error": e.code,
            "message": e.message,
            "details": e.details,
            "exit_code": e.exit_code,
        }
        if getattr(args, "json", False):
            print(json.dumps(err, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"错误 [{e.code}]：{e.message}", file=sys.stderr)
            if e.details is not None and os.environ.get("SEALCAB_DEBUG"):
                print(json.dumps(e.details, ensure_ascii=False, indent=2, default=str), file=sys.stderr)
        return e.exit_code
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
