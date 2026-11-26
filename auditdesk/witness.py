"""见证站：独立记录自己实际见过的链头，出具本地收件凭证。

每个站持有自造 Ed25519 钥匙；本地柜（cabinet）只在夜里清理前有效。
凭证分两级：
  VERIFIED  —— 已拆包验页（摘要、链接、条目根都核对通过）
  DELIVERED —— 包裹已送达、尚未验页（不承担页内容属实，不能用于共同见证）
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from . import crypto
from .chain import GENESIS, Chain, ChainError
from .models import RECEIPT_DELIVERED, RECEIPT_VERIFIED, Bundle, Page, Receipt
from .store import CaseStore


class WitnessStation:
    def __init__(self, station_id: str, root: str | Path):
        self.station_id = station_id
        self.root = Path(root) / station_id
        self.cabinet = self.root / "cabinet"
        self.cabinet.mkdir(parents=True, exist_ok=True)
        self.head_log_path = self.root / "head_log.jsonl"
        key_path = self.root / "identity.json"
        if key_path.exists():
            d = json.loads(key_path.read_text(encoding="utf-8"))
            self.key_id = d["key_id"]
            self.priv_pem = d["priv_pem"].encode()
            self.pub_pem = d["pub_pem"].encode()
        else:
            kp = crypto.generate_keypair()
            self.key_id, self.priv_pem, self.pub_pem = (
                kp.key_id, kp.priv_pem, kp.pub_pem)
            key_path.write_text(json.dumps({
                "station_id": station_id,
                "key_id": self.key_id,
                "priv_pem": self.priv_pem.decode(),
                "pub_pem": self.pub_pem.decode(),
            }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 本地柜 ----------
    def _cab_file(self, name: str) -> Path:
        return self.cabinet / name

    def _record_head(self, page: Page, kind: str, receipt_id: str) -> None:
        with self.head_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "station_id": self.station_id,
                "page_no": page.page_no,
                "page_digest": page.page_digest,
                "prev_digest": page.prev_digest,
                "kind": kind,
                "receipt_id": receipt_id,
            }, ensure_ascii=False, sort_keys=True) + "\n")

    def seen_digests(self) -> set[str]:
        out = set()
        if self.head_log_path.exists():
            for line in self.head_log_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.add(json.loads(line)["page_digest"])
        return out

    def _make_receipt(self, page: Page | None, kind: str,
                      ts: str, note: str) -> Receipt:
        import uuid
        rid = f"R-{self.station_id}-{uuid.uuid4().hex[:10]}"
        r = Receipt(
            receipt_id=rid,
            station_id=self.station_id,
            key_id=self.key_id,
            page_digest=page.page_digest if page else None,
            page_no=page.page_no if page else None,
            kind=kind,
            ts=ts,
            note=note,
        )
        r.signature = crypto.sign(self.priv_pem, r.signed_message()).hex()
        return r

    # ---------- 收件 ----------
    def receive_verified(self, page: Page, chain: Chain,
                         ts: str, note: str = "") -> Receipt:
        """拆包验页后签收 VERIFIED。重复页幂等返回原签收。"""
        existing = chain.find_receipt_for_page(
            self.station_id, page.page_digest, RECEIPT_VERIFIED)
        if existing:
            return existing
        chain.verify_page_integrity(page)
        if page.prev_digest != GENESIS:
            prev = chain.store.get_page(page.prev_digest)
            if prev is None:
                raise ChainError(
                    f"第{page.page_no}页的前页缺失，无法验页；"
                    "可先出具 DELIVERED，待补页后再升级")
        r = self._make_receipt(page, RECEIPT_VERIFIED, ts, note)
        chain.store.add_receipt(r)
        self._cab_file(f"page_{page.page_no}_{page.page_digest[:10]}.json"
                       ).write_text(json.dumps(asdict(page), ensure_ascii=False,
                                               indent=2, sort_keys=True),
                                    encoding="utf-8")
        self._cab_file(f"{r.receipt_id}.json").write_text(
            json.dumps(asdict(r), ensure_ascii=False, indent=2,
                       sort_keys=True), encoding="utf-8")
        self._record_head(page, RECEIPT_VERIFIED, r.receipt_id)
        return r

    def receive_delivered(self, ts: str, note: str = "包裹已送达、尚未验页",
                          page: Page | None = None) -> Receipt:
        """只登记送达，不验页。page 允许缺省（包裹未拆，未见摘要）。"""
        r = self._make_receipt(page, RECEIPT_DELIVERED, ts, note)
        (self.cabinet / f"{r.receipt_id}.json").write_text(
            json.dumps(asdict(r), ensure_ascii=False, indent=2,
                       sort_keys=True), encoding="utf-8")
        if page is not None:
            self._record_head(page, RECEIPT_DELIVERED, r.receipt_id)
        return r

    # ---------- 夜间清理与取证提取 ----------
    def export_cabinet(self) -> Bundle:
        """取证人在清理前提取的整柜原件（含本柜签收与本站公钥）。"""
        pages, receipts = [], []
        for p in self.cabinet.glob("page_*.json"):
            pages.append(json.loads(p.read_text(encoding="utf-8")))
        for p in self.cabinet.glob("R-*.json"):
            receipts.append(json.loads(p.read_text(encoding="utf-8")))
        return Bundle(
            pages=pages,
            entries=[],
            receipts=receipts,
            pubkeys={self.key_id: self.pub_pem.decode()},
        )

    def wipe_cabinet(self) -> None:
        """夜里清理本地柜；身份钥匙保留，原件与本地签收全部销毁。"""
        for p in self.cabinet.glob("*"):
            p.unlink()
        if self.head_log_path.exists():
            self.head_log_path.unlink()
