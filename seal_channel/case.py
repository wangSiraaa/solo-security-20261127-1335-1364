"""用例门面：把申请、分号、离站、写信、投递、核验、落册串成本地状态。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .errors import AllocationError, SealChannelError
from .models import (
    ALPHA,
    BETA,
    TRIP_LABELS,
    parse_seals,
    validate_partition,
)
from .receiver import Receiver, initial_receiver_state
from .store import JsonStore
from .terminal import OfflineTerminal, utc_now
from .verifier import (
    TrustedReceiptVerifier,
    issue_seal_receipt,
)

PRESETS: Dict[str, Dict[str, List[int]]] = {
    # 整段分：甲 1-3，乙 4-6。
    "segment": {ALPHA: [1, 2, 3], BETA: [4, 5, 6]},
    "block": {ALPHA: [1, 2, 3], BETA: [4, 5, 6]},
    "whole": {ALPHA: [1, 2, 3], BETA: [4, 5, 6]},
    # 常见穿插分法。
    "interleave": {ALPHA: [1, 3, 5], BETA: [2, 4, 6]},
    "alternate": {ALPHA: [1, 3, 5], BETA: [2, 4, 6]},
    # 对“整批甲先到/整批乙先到”两种次序最坏情况最好的预分。
    "balanced": {ALPHA: [1, 3, 6], BETA: [2, 4, 5]},
    "optimal": {ALPHA: [1, 3, 6], BETA: [2, 4, 5]},
    "recovery": {ALPHA: [1, 3, 6], BETA: [2, 4, 5]},
    "balanced-alt": {ALPHA: [1, 4, 5], BETA: [2, 3, 6]},
}


class SealCase:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.store = JsonStore(self.root)

    # ---------- 初始化与基础状态 ----------

    @classmethod
    def init(
        cls,
        root: str | Path,
        book_size: int = 6,
        window_size: int = 3,
        force: bool = False,
    ) -> "SealCase":
        root_path = Path(root).expanduser().resolve()
        if root_path.exists() and any(root_path.iterdir()):
            if not force:
                raise AllocationError(
                    f"状态目录非空：{root_path}；如要重建请加 --force",
                    code="CASE_NOT_EMPTY",
                )
            # --force 只允许清理一个看起来确实由本工具创建的状态目录，避免
            # 误把 --state 指向仓库或家目录时递归删除无关文件。
            marker = root_path / "case.json"
            valid_marker = False
            if marker.is_file():
                try:
                    valid_marker = json.loads(marker.read_text(encoding="utf-8")).get(
                        "version"
                    ) == 1
                except (OSError, json.JSONDecodeError):
                    valid_marker = False
            if not valid_marker:
                raise AllocationError(
                    f"拒绝在缺少 seal-channel case.json 标记的目录上执行 --force：{root_path}",
                    code="REFUSE_FORCE_UNKNOWN_DIR",
                )
            shutil.rmtree(root_path)
        root_path.mkdir(parents=True, exist_ok=True)

        case = cls(root_path)
        case.store.ensure_dir("terminals")
        case.store.ensure_dir("outbox")
        case.store.ensure_dir("receivers")
        case.store.ensure_dir("receipts")

        config = {
            "version": 1,
            "book_size": book_size,
            "window_size": window_size,
            "created_at": utc_now(),
            "allocation_frozen": False,
            "departed": [],
            "plan": None,
        }
        case.store.write_json("case.json", value=config)

        registry = {
            "version": 1,
            "seals": [
                {
                    "seal": n,
                    "status": "unassigned",
                    "owner": None,
                    "holder": None,
                    "issued": False,
                    "state": "未申请",
                    "grant_digest": None,
                }
                for n in range(1, book_size + 1)
            ],
        }
        case.store.write_json("registry.json", value=registry)
        case.store.write_json("applications.json", value={ALPHA: None, BETA: None})
        case.store.write_json("trusted_receipts.json", value={"version": 1, **_new_book_fields()})
        case.store.write_json(
            "receiver.json", value=initial_receiver_state(window_size)
        )
        return case

    def config(self) -> Dict[str, Any]:
        return self.store.read_json("case.json")

    def save_config(self, value: Dict[str, Any]) -> None:
        self.store.write_json("case.json", value=value)

    def registry(self) -> Dict[str, Any]:
        return self.store.read_json("registry.json")

    def save_registry(self, value: Dict[str, Any]) -> None:
        self.store.write_json("registry.json", value=value)

    def trusted_book(self) -> Dict[str, Any]:
        return self.store.read_json("trusted_receipts.json")

    def save_trusted_book(self, value: Dict[str, Any]) -> None:
        self.store.write_json("trusted_receipts.json", value=value)

    def verifier(self) -> TrustedReceiptVerifier:
        return TrustedReceiptVerifier(self.trusted_book(), self.registry())

    def terminal(self, trip: str) -> OfflineTerminal:
        return OfflineTerminal(self.store, trip)

    @staticmethod
    def receiver_state_path(name: str = "receiver") -> str:
        if name in ("receiver", "default", ""):
            return "receiver.json"
        return f"receivers/{name}.json"

    def receiver(self, name: str = "receiver") -> Receiver:
        return Receiver(
            self.store,
            state_path=self.receiver_state_path(name),
            verifier=self.verifier(),
        )

    # ---------- 申请、审批、分号、离站 ----------

    def apply(self, trip: str, seals: str | Sequence[int]) -> Dict[str, Any]:
        trip = _norm(trip)
        cfg = self.config()
        if cfg.get("allocation_frozen"):
            raise AllocationError(
                "两台已经离站/冻结分号，不能再申请或改挑封条号",
                code="ALLOCATION_FROZEN",
            )

        book_size = int(cfg["book_size"])
        selected = parse_seals(seals, book_size)
        if len(selected) != 3:
            raise AllocationError(
                f"每台终端固定申请 3 枚，{TRIP_LABELS[trip]}端申请了 {len(selected)} 枚",
                code="BAD_SEAL_COUNT",
            )

        applications = self.store.read_json("applications.json")
        registry = self.registry()
        records = {int(r["seal"]): r for r in registry["seals"]}

        for n in selected:
            record = records[n]
            owner = record.get("owner")
            if record.get("status") in ("requested", "granted") and owner and owner != trip:
                raise AllocationError(
                    f"{n} 号已被{TRIP_LABELS.get(owner, owner)}端申请/持有，不能重复分给{TRIP_LABELS[trip]}",
                    code="SEAL_ALREADY_ALLOCATED",
                )

        applications[trip] = {
            "trip": trip,
            "seals": selected,
            "status": "requested",
            "applied_at": utc_now(),
        }
        self.store.write_json("applications.json", value=applications)

        for n in selected:
            r = records[n]
            r["status"] = "requested"
            r["owner"] = trip
            r["holder"] = None
            r["issued"] = False
            r["state"] = f"已申请，待交{TRIP_LABELS[trip]}"
        self.save_registry(registry)
        return applications[trip]

    def approve(self, trip: str) -> Dict[str, Any]:
        trip = _norm(trip)
        applications = self.store.read_json("applications.json")
        application = applications.get(trip)
        if not application:
            raise AllocationError(
                f"{TRIP_LABELS[trip]}端尚未申请封条号", code="NO_APPLICATION"
            )

        seals = sorted(application["seals"])
        book = self.trusted_book()
        registry = self.registry()
        records = {int(r["seal"]): r for r in registry["seals"]}
        credentials: Dict[str, Dict[str, str]] = {}
        now = utc_now()

        for n in seals:
            receipt = issue_seal_receipt(book, n, trip)
            credentials[str(n)] = {
                "seal_key_hex": receipt["seal_key_hex"],
                "grant_digest": receipt["grant_digest"],
            }
            r = records[n]
            r.update(
                {
                    "status": "granted",
                    "owner": trip,
                    "holder": trip,
                    "issued": True,
                    "state": f"已交{TRIP_LABELS[trip]}",
                    "grant_digest": receipt["grant_digest"],
                    "granted_at": now,
                }
            )

        applications[trip]["status"] = "approved"
        applications[trip]["approved_at"] = now
        self.store.write_json("applications.json", value=applications)
        self.save_trusted_book(book)
        self.save_registry(registry)
        OfflineTerminal.create(self.store, trip, seals, credentials)
        return applications[trip]

    def allocate(
        self,
        alpha: Optional[str | Sequence[int]] = None,
        beta: Optional[str | Sequence[int]] = None,
        preset: Optional[str] = None,
    ) -> Dict[str, List[int]]:
        """断线前一次性预分；参数中没有抵达次序，防止读后补挑号码。"""

        if preset:
            key = preset.replace("_", "-").lower()
            if key not in PRESETS:
                raise AllocationError(
                    f"未知预分方案 {preset!r}，可选：segment/interleave/balanced",
                    code="UNKNOWN_PRESET",
                )
            chosen = PRESETS[key]
            alpha = chosen[ALPHA]
            beta = chosen[BETA]
        if alpha is None or beta is None:
            raise AllocationError(
                "必须提供 alpha/beta 两组号码或 --preset",
                code="MISSING_ALLOCATION",
            )

        cfg = self.config()
        a = parse_seals(alpha, int(cfg["book_size"])) if isinstance(alpha, str) else list(alpha)
        b = parse_seals(beta, int(cfg["book_size"])) if isinstance(beta, str) else list(beta)
        a, b = validate_partition(a, b, int(cfg["book_size"]))

        self.apply(ALPHA, a)
        self.apply(BETA, b)
        self.approve(ALPHA)
        self.approve(BETA)

        cfg = self.config()
        cfg["plan"] = {ALPHA: a, BETA: b, "preset": preset}
        self.save_config(cfg)
        return {ALPHA: a, BETA: b}

    def depart(self, trip: str) -> Dict[str, Any]:
        trip = _norm(trip)
        # 断线前必须两台都分好。
        for other in (ALPHA, BETA):
            if not self.store.exists(f"terminals/{other}.json"):
                raise AllocationError(
                    f"{TRIP_LABELS[other]}端尚未完成分号；断线前必须分好全册",
                    code="ALLOCATION_NOT_COMPLETE",
                )

        terminal = self.terminal(trip)
        terminal.depart()

        cfg = self.config()
        cfg["allocation_frozen"] = True
        departed = set(cfg.get("departed", []))
        departed.add(trip)
        cfg["departed"] = sorted(departed)
        self.save_config(cfg)
        return terminal.data

    def depart_all(self) -> None:
        self.depart(ALPHA)
        self.depart(BETA)

    def restart_terminal(self, trip: str) -> Dict[str, Any]:
        return self.terminal(trip).restart()

    # ---------- 写信与投递 ----------

    def write(self, trip: str, body: str, seal: Optional[int] = None) -> Dict[str, Any]:
        return self.terminal(trip).write_message(body, seal=seal)

    def receive_envelope(
        self, envelope: Dict[str, Any], receiver_name: str = "receiver"
    ):
        return self.receiver(receiver_name).receive_envelope(envelope)

    def receive_file(self, envelope_path: str | Path, receiver_name: str = "receiver"):
        p = Path(envelope_path)
        if not p.is_absolute():
            p = Path.cwd() / p
        envelope = json.loads(p.read_text(encoding="utf-8"))
        return self.receive_envelope(envelope, receiver_name)

    def deliver_trip(self, trip: str, receiver_name: str = "receiver") -> List[Any]:
        terminal = self.terminal(trip)
        receiver = self.receiver(receiver_name)
        results = []
        for envelope in terminal.all_envelopes():
            results.append(receiver.receive_envelope(envelope))
        return results

    def arrival(
        self, order: str | Sequence[str], receiver_name: str = "receiver"
    ) -> List[Any]:
        if isinstance(order, str):
            trips = [part.strip() for part in order.replace("，", ",").split(",") if part.strip()]
        else:
            trips = list(order)
        results = []
        for raw_trip in trips:
            trip = _norm(raw_trip)
            results.extend(self.deliver_trip(trip, receiver_name))
        return results

    def reconcile(self, seal: int, note: str = "", receiver_name: str = "receiver"):
        return self.receiver(receiver_name).reconcile(seal, note=note)

    # ---------- 故障演练/登记修复 ----------

    def mark_unissued(self, seal: int) -> Dict[str, Any]:
        """把已正确归某端的封条登记改成“尚未交该端”，但不动可信回执。"""

        seal = int(seal)
        registry = self.registry()
        for r in registry["seals"]:
            if int(r["seal"]) == seal:
                owner = r.get("owner")
                label = TRIP_LABELS.get(owner, "") if owner else ""
                r["status"] = "unissued"
                r["issued"] = False
                r["holder"] = None
                r["state"] = f"尚未交{label}" if label else "尚未交"
                r["fault_injected_at"] = utc_now()
                self.save_registry(registry)
                return r
        raise SealChannelError(f"登记中没有 {seal} 号", code="SEAL_NOT_FOUND")

    def repair_grants(self, seal: Optional[int] = None, all_seals: bool = False) -> int:
        """按预置可信回执恢复封条登记。"""

        book = self.trusted_book()
        registry = self.registry()
        count = 0
        for r in registry["seals"]:
            n = int(r["seal"])
            if seal is not None and n != int(seal):
                continue
            if seal is None and not all_seals:
                raise SealChannelError(
                    "请指定 --seal N 或 --all", code="MISSING_REPAIR_TARGET"
                )
            receipt = book["seals"].get(str(n))
            if not receipt:
                continue
            owner = receipt["owner"]
            r.update(
                {
                    "status": "granted",
                    "owner": owner,
                    "holder": owner,
                    "issued": True,
                    "state": f"已交{TRIP_LABELS[owner]}",
                    "grant_digest": receipt["grant_digest"],
                    "repaired_at": utc_now(),
                }
            )
            count += 1
        self.save_registry(registry)
        return count

    def expect_body(self, seal: int, body: str) -> None:
        from .security import sha256_hex

        book = self.trusted_book()
        receipt = book["seals"].get(str(int(seal)))
        if receipt is None:
            raise AllocationError(f"{seal} 号尚未分配，不能预置正文回执")
        receipt["expected_body_sha256"] = sha256_hex(body)
        self.save_trusted_book(book)


def _norm(trip: str) -> str:
    # 小包装便于本模块内保持简短导入。
    from .models import normalize_trip

    return normalize_trip(trip)


def _new_book_fields() -> Dict[str, Any]:
    from .security import new_key_hex

    return {"authority_key_hex": new_key_hex(32), "seals": {}}
