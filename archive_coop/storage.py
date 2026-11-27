"""本地持久化：所有材料都是目录里的普通文件，重启程序后仍可查回。

root/
  ledger.json          全局账本：卷清单、保管人/柜、在途交接、恢复单、保障单
  envelopes/<eid>.json 开封材料封套（份位上的实际份额）
  receipts/<rid>.json  交接回单（只是交接凭证，不是有效材料）
  copies/<volume_id>/page_<n>__<replica>.json  实际卷副本：封存页（正文+该代标签+代际）
  manifests/<volume_id>.json                   页号 + 摘要清单（封卷时另存）
"""

import base64
import contextlib
import fcntl
import json
import os
import tempfile


class Storage:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(os.path.join(self.root, "envelopes"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "receipts"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "copies"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "manifests"), exist_ok=True)
        self.ledger_path = os.path.join(self.root, "ledger.json")

    # ---- 账本 ----
    def load_ledger(self) -> dict:
        if not os.path.exists(self.ledger_path):
            return {
                "volumes": {},          # volume_id -> 卷记录
                "custodians": {},       # name -> {site, cabinet}
                "pending_handover": [], # 在途交接单
                "recovery_orders": {},  # order_id -> 恢复单
                "guarantees": {},       # volume_id -> 保障单
            }
        with open(self.ledger_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_ledger(self, ledger: dict) -> None:
        self._atomic_write_json(self.ledger_path, ledger)

    @contextlib.contextmanager
    def ledger_lock(self):
        """跨进程独占锁：锁内重读账本、修改、原子写回，杜绝多进程互相覆盖。"""
        lock_path = os.path.join(self.root, ".ledger.lock")
        os.makedirs(self.root, exist_ok=True)
        with open(lock_path, "w") as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)

    # ---- 封套（份额） ----
    def envelope_path(self, envelope_id: str) -> str:
        return os.path.join(self.root, "envelopes", f"{envelope_id}.json")

    def write_envelope(self, env: dict) -> None:
        self._atomic_write_json(self.envelope_path(env["envelope_id"]), env)

    def read_envelope(self, envelope_id: str) -> dict:
        with open(self.envelope_path(envelope_id), "r", encoding="utf-8") as f:
            return json.load(f)

    # ---- 回单 ----
    def receipt_path(self, receipt_id: str) -> str:
        return os.path.join(self.root, "receipts", f"{receipt_id}.json")

    def write_receipt(self, receipt: dict) -> None:
        self._atomic_write_json(self.receipt_path(receipt["receipt_id"]), receipt)

    def read_receipt(self, receipt_id: str) -> dict:
        with open(self.receipt_path(receipt_id), "r", encoding="utf-8") as f:
            return json.load(f)

    # ---- 卷副本与清单 ----
    def volume_dir(self, volume_id: str) -> str:
        d = os.path.join(self.root, "copies", volume_id)
        os.makedirs(d, exist_ok=True)
        return d

    def page_path(self, volume_id: str, page_no: int, replica: int) -> str:
        return os.path.join(
            self.volume_dir(volume_id), f"page_{page_no}__{replica}.json"
        )

    def write_page_bundle(
        self, volume_id: str, page_no: int, replica: int,
        content: bytes, tag: bytes, generation: int,
    ) -> None:
        bundle = {
            "volume_id": volume_id,
            "page_no": page_no,
            "replica": replica,
            "generation": generation,
            "content_b64": base64.b64encode(content).decode("ascii"),
            "tag_b64": base64.b64encode(tag).decode("ascii"),
        }
        self._atomic_write_json(self.page_path(volume_id, page_no, replica), bundle)

    def read_page_bundle(self, volume_id: str, page_no: int, replica: int) -> dict:
        with open(self.page_path(volume_id, page_no, replica), "r", encoding="utf-8") as f:
            bundle = json.load(f)
        bundle["content"] = base64.b64decode(bundle["content_b64"])
        bundle["tag"] = base64.b64decode(bundle["tag_b64"])
        return bundle

    def overwrite_page_bundle(self, volume_id: str, page_no: int, replica: int, bundle: dict) -> None:
        """演练用：把整只封存页（含旧代标签）原样铺到某个副本位。"""
        self._atomic_write_json(self.page_path(volume_id, page_no, replica), bundle)

    def list_page_replicas(self, volume_id: str, page_no: int) -> list[int]:
        d = self.volume_dir(volume_id)
        prefix = f"page_{page_no}__"
        out = []
        for name in os.listdir(d):
            if name.startswith(prefix) and name.endswith(".json"):
                out.append(int(name[len(prefix):-5]))
        return sorted(out)

    def manifest_path(self, volume_id: str) -> str:
        return os.path.join(self.root, "manifests", f"{volume_id}.json")

    def write_manifest(self, volume_id: str, manifest: dict) -> None:
        self._atomic_write_json(self.manifest_path(volume_id), manifest)

    def read_manifest(self, volume_id: str) -> dict:
        with open(self.manifest_path(volume_id), "r", encoding="utf-8") as f:
            return json.load(f)

    def _atomic_write_json(self, path: str, obj: dict) -> None:
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise
