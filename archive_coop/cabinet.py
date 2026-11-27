"""可信本地开封柜（发行/合份服务）与保管人柜。

边界约定（任务要求）：
- 开封柜是可信本地组件，可以模拟“发行/合份”，但卷钥（原钥）只在柜内内存中出现，
  不写入账本、不返回给柜外程序，更没有“测试原钥”这种备用入口。
- 柜外恢复台（workbench.py）只能提交实有封套来合份，
  拿到的是合份结果（用于核验页标签），永远拿不到卷钥本身。
- 交接回单（receipt）只是交接凭证；在途封套既不在交出方也不在接收方的有效柜存里。
  两只盒子、两张回单、两名到场人都不等于两份有效材料——以封套唯一 ID 与份位去重。
- 所有账本写入都在跨进程文件锁内做读-改-写，多进程/多组件不会互相覆盖。
"""

import base64
import functools
import os
import uuid
from datetime import datetime

from . import secretshare
from .crypto import page_digest, page_tag
from .policy import DEFAULT_CUSTODIANS, POLICIES
from .storage import Storage


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def transactional(method):
    """变更方法装饰器：进入即加锁并重读账本，正常返回时原子写回；异常不写。"""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.storage.ledger_lock():
            self.ledger = self.storage.load_ledger()
            result = method(self, *args, **kwargs)
            self.storage.save_ledger(self.ledger)
            return result

    return wrapper


class IssuingCabinet:
    """发行柜：封卷发行、补发换代、柜内合份。原钥不出柜。"""

    def __init__(self, storage: Storage):
        self.storage = storage
        self.ledger = storage.load_ledger()
        if not self.ledger["custodians"]:
            self._init_custodians()

    @transactional
    def _init_custodians(self):
        for name, info in DEFAULT_CUSTODIANS.items():
            self.ledger["custodians"][name] = {
                "site": info["site"],
                "index": info["index"],
                "cabinet_id": f"CAB-{name}",
                "envelopes": [],
            }

    def _reload(self) -> None:
        self.ledger = self.storage.load_ledger()

    # ---------- 保管人 ----------
    def custodian_index(self, custodian: str) -> int:
        self._reload()
        if custodian not in self.ledger["custodians"]:
            raise KeyError(f"未知保管人：{custodian}")
        return self.ledger["custodians"][custodian]["index"]

    def envelopes_held_by(self, custodian: str) -> list[dict]:
        """该保管人柜内当前有效（非在途、本代）的封套。"""
        self._reload()
        out = []
        for env_id in self.ledger["custodians"][custodian]["envelopes"]:
            env = self.storage.read_envelope(env_id)
            if env["status"] != "sealed":
                continue
            vol = self.ledger["volumes"][env["volume_id"]]
            if env["generation"] != vol["generation"]:
                continue  # 旧代份额仍登记在持有人名下，但已不是有效材料
            out.append(env)
        return out

    # ---------- 封卷发行 ----------
    @transactional
    def seal_volume(
        self, volume_id: str, pages: list[tuple[int, str]], policy_name: str
    ) -> dict:
        if volume_id in self.ledger["volumes"]:
            raise ValueError(f"卷 {volume_id} 已存在，不能重复封卷")
        policy = POLICIES[policy_name]

        # 32 字节卷钥在柜内生成，永不落盘、永不返回
        volume_key = os.urandom(32)
        shares = secretshare.split(volume_key, policy.threshold, policy.indices)

        manifest_entries = []
        for page_no, text in pages:
            content = text.encode("utf-8")
            tag = page_tag(volume_key, volume_id, page_no, content)
            manifest_entries.append(
                {
                    "page_no": page_no,
                    "digest": page_digest(volume_id, page_no, content),
                    "tag": _b64e(tag),
                    "length": len(content),
                }
            )
            # 每只保管柜各存一份封存页（正文 + 本代卷钥标签 + 代际）
            for replica in policy.indices:
                self.storage.write_page_bundle(
                    volume_id, page_no, replica, content, tag, 1
                )

        manifest = {"volume_id": volume_id, "generation": 1, "pages": manifest_entries}
        self.storage.write_manifest(volume_id, manifest)

        # 份额封套入各保管人柜
        index_to_custodian = {
            info["index"]: name for name, info in self.ledger["custodians"].items()
        }
        for index, body in shares.items():
            env = {
                "envelope_id": _new_id("ENV"),
                "volume_id": volume_id,
                "generation": 1,
                "index": index,
                "site": policy.site_of(index),
                "body_b64": _b64e(body),
                "status": "sealed",
                "holder": index_to_custodian[index],
                "history": [{"at": _now(), "event": "issued", "holder": index_to_custodian[index]}],
            }
            self.storage.write_envelope(env)
            self.ledger["custodians"][env["holder"]]["envelopes"].append(
                env["envelope_id"]
            )

        self.ledger["volumes"][volume_id] = {
            "volume_id": volume_id,
            "policy": policy_name,
            "threshold": policy.threshold,
            "generation": 1,
            "page_numbers": [p for p, _ in pages],
            "sealed_at": _now(),
            "old_generation_blobs": [],   # 补发时留下的旧代封存页，仅供旧页演练
        }
        return manifest

    # ---------- 交接 ----------
    @transactional
    def start_handover(self, envelope_id: str, frm: str, to: str) -> dict:
        """发起交接：封套进入在途，双方各执一张同份位回单。

        在途期间该封套不计入任何一方的有效柜存——回单不是材料。
        """
        env = self.storage.read_envelope(envelope_id)
        if env["status"] != "sealed":
            raise ValueError(f"封套 {envelope_id} 状态为 {env['status']}，不能交接")
        if env["holder"] != frm:
            raise ValueError(f"封套当前在 {env['holder']} 柜中，{frm} 无权交出")
        if to not in self.ledger["custodians"]:
            raise KeyError(f"未知接收保管人：{to}")

        receipt_id = _new_id("RCP")
        receipt = {
            "receipt_id": receipt_id,
            "envelope_id": envelope_id,
            "volume_id": env["volume_id"],
            "index": env["index"],
            "generation": env["generation"],
            "from": frm,
            "to": to,
            "state": "pending",          # pending -> completed / void
            "created_at": _now(),
            "note": "交接回单仅为凭证，本身不是有效开封材料",
        }
        self.storage.write_receipt(receipt)

        env["status"] = "in_transit"
        env["history"].append({"at": _now(), "event": "handover_started", "from": frm, "to": to})
        self.storage.write_envelope(env)

        self.ledger["pending_handover"].append(
            {"receipt_id": receipt_id, "envelope_id": envelope_id, "from": frm, "to": to}
        )
        return receipt

    @transactional
    def sign_handover(self, receipt_id: str, signer: str) -> dict:
        """接收方签收：封套才真正进入接收方柜存。"""
        receipt = self.storage.read_receipt(receipt_id)
        if receipt["state"] != "pending":
            raise ValueError("回单不在待签收状态")
        if signer != receipt["to"]:
            raise PermissionError(f"只有接收方 {receipt['to']} 可以签收")

        env = self.storage.read_envelope(receipt["envelope_id"])
        frm, to = receipt["from"], receipt["to"]
        self.ledger["custodians"][frm]["envelopes"].remove(env["envelope_id"])
        self.ledger["custodians"][to]["envelopes"].append(env["envelope_id"])

        env["status"] = "sealed"
        env["holder"] = to
        env["history"].append({"at": _now(), "event": "received", "holder": to})
        self.storage.write_envelope(env)

        receipt["state"] = "completed"
        receipt["signed_at"] = _now()
        self.storage.write_receipt(receipt)
        self.ledger["pending_handover"] = [
            h for h in self.ledger["pending_handover"] if h["receipt_id"] != receipt_id
        ]
        return receipt

    @transactional
    def cancel_handover(self, receipt_id: str) -> dict:
        """撤销交接：封套退回交出方柜存。"""
        receipt = self.storage.read_receipt(receipt_id)
        if receipt["state"] != "pending":
            raise ValueError("回单不在待签收状态")
        env = self.storage.read_envelope(receipt["envelope_id"])
        env["status"] = "sealed"
        env["history"].append({"at": _now(), "event": "handover_cancelled"})
        self.storage.write_envelope(env)
        receipt["state"] = "void"
        self.storage.write_receipt(receipt)
        self.ledger["pending_handover"] = [
            h for h in self.ledger["pending_handover"] if h["receipt_id"] != receipt_id
        ]
        return receipt

    # ---------- 补发换代 ----------
    @transactional
    def reissue(self, volume_id: str, submitted_envelope_ids: list[str]) -> dict:
        """以足额到场份重建卷钥，换新一代份额与页标签。

        - 旧代份额不销毁、不抹账：照实标记 superseded 并登记现持有人。
        - 在途封套、旧代封套、回单一律不得作为到场材料。
        - 不能拿另一卷的份来凑：卷号不符直接拒绝。
        """
        vol = self.ledger["volumes"][volume_id]
        policy = POLICIES[vol["policy"]]
        old_gen = vol["generation"]

        pieces, seen_index = [], set()
        for env_id in submitted_envelope_ids:
            env = self.storage.read_envelope(env_id)
            if env["volume_id"] != volume_id:
                raise ValueError(f"封套 {env_id} 属于另一卷 {env['volume_id']}，不能补足本卷")
            if env["generation"] != old_gen:
                raise ValueError(f"封套 {env_id} 不是当前代材料")
            if env["status"] != "sealed":
                raise ValueError(f"封套 {env_id} 尚在途或已失效，不能提交")
            if env["index"] in seen_index:
                raise ValueError(f"份位 {env['index']} 重复提交，两张同份位材料不能当两份")
            seen_index.add(env["index"])
            pieces.append((env["index"], _b64d(env["body_b64"])))

        if len(pieces) < vol["threshold"]:
            raise ValueError(
                f"到场 {len(pieces)} 份，不足门限 {vol['threshold']}，不能补发"
            )

        old_key = secretshare.combine(pieces)  # 柜内合份，旧钥不离开本方法
        new_key = os.urandom(32)
        new_gen = old_gen + 1
        new_shares = secretshare.split(new_key, policy.threshold, policy.indices)

        # 封存旧代页面：整只旧封存页（正文 + 旧钥标签 + 旧代际）入快照目录（柜内操作，不暴露旧钥）。
        # 日后“旧页顶替”演练把旧封存页铺回副本位，新代卷钥下标签必然核对失败。
        snapshot_dir = os.path.join(
            self.storage.root, "copies", volume_id, f"_gen{old_gen}_snapshot"
        )
        os.makedirs(snapshot_dir, exist_ok=True)
        old_snapshot = {"generation": old_gen, "pages": []}

        manifest = self.storage.read_manifest(volume_id)
        for entry in manifest["pages"]:
            pn = entry["page_no"]
            old_bundle = self.storage.read_page_bundle(volume_id, pn, policy.indices[0])
            old_blob = old_bundle["content"]
            # 快照保留整只旧封存页（含旧标签、旧代际，不存裸钥）
            old_snapshot["pages"].append({"page_no": pn, "bundle_file": f"page_{pn}.json"})
            self.storage._atomic_write_json(
                os.path.join(snapshot_dir, f"page_{pn}.json"),
                {k: v for k, v in old_bundle.items() if k not in ("content", "tag")},
            )
            # 用新钥重写页标签与全部封存页（补发只换钥不换页正文）
            new_tag = page_tag(new_key, volume_id, pn, old_blob)
            entry["tag"] = _b64e(new_tag)
            for replica in policy.indices:
                self.storage.write_page_bundle(
                    volume_id, pn, replica, old_blob, new_tag, new_gen
                )
        manifest["generation"] = new_gen
        self.storage.write_manifest(volume_id, manifest)
        self.storage._atomic_write_json(
            os.path.join(snapshot_dir, "snapshot.json"), old_snapshot
        )

        # 旧代份额照实登记为 superseded，保留持有人记录
        index_to_custodian = {
            info["index"]: name for name, info in self.ledger["custodians"].items()
        }
        for name, info in self.ledger["custodians"].items():
            kept = []
            for env_id in info["envelopes"]:
                env = self.storage.read_envelope(env_id)
                if env["volume_id"] == volume_id and env["generation"] == old_gen:
                    env["status"] = "superseded"
                    env["history"].append(
                        {"at": _now(), "event": "superseded", "still_held_by": name}
                    )
                    self.storage.write_envelope(env)
                else:
                    kept.append(env_id)
            info["envelopes"] = kept

        # 新一代份额入柜
        for index, body in new_shares.items():
            env = {
                "envelope_id": _new_id("ENV"),
                "volume_id": volume_id,
                "generation": new_gen,
                "index": index,
                "site": policy.site_of(index),
                "body_b64": _b64e(body),
                "status": "sealed",
                "holder": index_to_custodian[index],
                "history": [{"at": _now(), "event": "issued", "holder": index_to_custodian[index]}],
            }
            self.storage.write_envelope(env)
            self.ledger["custodians"][env["holder"]]["envelopes"].append(env["envelope_id"])

        vol["generation"] = new_gen
        vol.setdefault("old_generation_blobs", []).append(snapshot_dir)
        return {"volume_id": volume_id, "new_generation": new_gen}

    # ---------- 柜内合份（供恢复台调用） ----------
    def combine_for_recovery(
        self, volume_id: str, submitted_envelope_ids: list[str]
    ) -> bytes:
        """恢复台提交实有封套，柜内合份；只回组合结果，不回卷钥。

        返回的卷钥字节仅交给本地恢复工作台做页标签核验，工作台不持久化、不转吐。
        """
        self._reload()
        vol = self.ledger["volumes"][volume_id]
        pieces, seen_index = [], set()
        for env_id in submitted_envelope_ids:
            env = self.storage.read_envelope(env_id)
            if env["volume_id"] != volume_id:
                raise ValueError(f"封套 {env_id} 属于另一卷，不能用于本卷")
            if env["generation"] != vol["generation"]:
                raise ValueError(
                    f"封套 {env_id} 是旧代（第 {env['generation']} 代）材料，"
                    f"当前为第 {vol['generation']} 代，旧份不能开新卷"
                )
            if env["status"] != "sealed":
                raise ValueError(f"封套 {env_id} 状态 {env['status']}，不是有效柜存材料")
            if env["index"] in seen_index:
                raise ValueError(f"份位 {env['index']} 重复：回单/盒子重复不等于两份材料")
            seen_index.add(env["index"])
            pieces.append((env["index"], _b64d(env["body_b64"])))

        if len(pieces) < vol["threshold"]:
            raise ValueError(
                f"有效到场 {len(pieces)} 份，不足门限 {vol['threshold']}，开封失败"
            )
        return secretshare.combine(pieces)

    # ---------- 保障单签收/退件 ----------
    @transactional
    def record_guarantee_decision(self, guarantee: dict, decision: str) -> dict:
        if decision not in ("signed", "rejected"):
            raise ValueError("决定只能是 signed（签收）或 rejected（退件）")
        guarantee["status"] = decision
        guarantee["decided_at"] = _now()
        self.ledger["guarantees"][guarantee["volume_id"]] = guarantee
        return guarantee
