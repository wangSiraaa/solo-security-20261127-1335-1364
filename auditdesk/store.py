"""取证台案件库：JSON 落盘、重启可恢复。"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from . import crypto
from .models import Bundle, Entry, Page, Receipt


class CaseStore:
    def __init__(self, case_dir: str | Path):
        self.dir = Path(case_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "pages").mkdir(exist_ok=True)
        (self.dir / "receipts").mkdir(exist_ok=True)
        (self.dir / "certs").mkdir(exist_ok=True)
        self.entries_path = self.dir / "entries.json"
        self.pubkeys_path = self.dir / "pubkeys.json"
        self.priv_path = self.dir / "desk_private.json"
        self.entries: dict[str, dict] = self._load(self.entries_path, {})
        self.pubkeys: dict[str, str] = self._load(self.pubkeys_path, {})
        self._desk_key = None

    @staticmethod
    def _load(path: Path, default):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return default

    def _save(self, path: Path, obj) -> None:
        path.write_text(
            json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    # ---- 取证台自身钥匙 ----
    def desk_keypair(self):
        if self._desk_key is not None:
            return self._desk_key
        if self.priv_path.exists():
            d = json.loads(self.priv_path.read_text(encoding="utf-8"))
            from .crypto import KeyPair
            self._desk_key = KeyPair(d["key_id"],
                                     d["priv_pem"].encode(),
                                     self.pubkeys[d["key_id"]].encode())
        else:
            kp = crypto.generate_keypair()
            self._desk_key = kp
            self.register_pubkey(kp.key_id, kp.pub_pem.decode())
            self._save(self.priv_path, {
                "key_id": kp.key_id,
                "priv_pem": kp.priv_pem.decode(),
            })
        return self._desk_key

    def register_pubkey(self, key_id: str, pub_pem: str) -> None:
        if key_id in self.pubkeys and self.pubkeys[key_id] != pub_pem:
            raise ValueError(f"公钥冲突: {key_id} 已登记为另一把钥匙")
        self.pubkeys[key_id] = pub_pem
        self._save(self.pubkeys_path, self.pubkeys)

    # ---- 条目 ----
    def add_entry(self, entry: Entry) -> None:
        if entry.entry_id in self.entries:
            return
        d = asdict(entry)
        d["digest"] = entry.digest()
        self.entries[entry.entry_id] = d
        self._save(self.entries_path, self.entries)

    def get_entry(self, entry_id: str) -> dict | None:
        return self.entries.get(entry_id)

    def entry_digest(self, entry_id: str) -> str | None:
        e = self.entries.get(entry_id)
        return e["digest"] if e else None

    # ---- 页 ----
    def page_path(self, digest: str) -> Path:
        return self.dir / "pages" / f"{digest}.json"

    def add_page(self, page: Page) -> None:
        p = self.page_path(page.page_digest)
        if p.exists():
            return
        self._save(p, asdict(page))

    def get_page(self, digest: str) -> Page | None:
        p = self.page_path(digest)
        if not p.exists():
            return None
        return Page(**json.loads(p.read_text(encoding="utf-8")))

    def all_pages(self) -> list[Page]:
        out = []
        for p in (self.dir / "pages").glob("*.json"):
            out.append(Page(**json.loads(p.read_text(encoding="utf-8"))))
        return out

    # ---- 签收 ----
    def receipt_path(self, receipt_id: str) -> Path:
        return self.dir / "receipts" / f"{receipt_id}.json"

    def add_receipt(self, r: Receipt) -> None:
        p = self.receipt_path(r.receipt_id)
        if p.exists():
            return
        self._save(p, asdict(r))

    def get_receipt(self, receipt_id: str) -> Receipt | None:
        p = self.receipt_path(receipt_id)
        if not p.exists():
            return None
        return Receipt(**json.loads(p.read_text(encoding="utf-8")))

    def all_receipts(self) -> list[Receipt]:
        out = []
        for p in (self.dir / "receipts").glob("*.json"):
            out.append(Receipt(**json.loads(p.read_text(encoding="utf-8"))))
        return out

    # ---- 证书 ----
    def save_cert(self, cert_id: str, cert: dict) -> Path:
        p = self.dir / "certs" / f"{cert_id}.json"
        self._save(p, cert)
        return p

    def all_certs(self) -> list[dict]:
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in (self.dir / "certs").glob("*.json")]

    # ---- 离线导入/导出 ----
    def export_bundle(self) -> Bundle:
        return Bundle(
            pages=[asdict(p) for p in self.all_pages()],
            entries=list(self.entries.values()),
            receipts=[asdict(r) for r in self.all_receipts()],
            pubkeys=dict(self.pubkeys),
        )

    def import_bundle(self, bundle: Bundle) -> dict:
        """导入回执和补页。按内容去重；公钥冲突直接拒绝。"""
        stats = {"pages": 0, "entries": 0, "receipts": 0, "pubkeys": 0,
                 "conflicts": []}
        for key_id, pem in bundle.pubkeys.items():
            if key_id in self.pubkeys:
                if self.pubkeys[key_id] != pem:
                    stats["conflicts"].append(f"公钥冲突 {key_id}")
                continue
            self.register_pubkey(key_id, pem)
            stats["pubkeys"] += 1
        for ed in bundle.entries:
            if ed["entry_id"] not in self.entries:
                self.entries[ed["entry_id"]] = ed
                stats["entries"] += 1
        if stats["entries"]:
            self._save(self.entries_path, self.entries)
        for pd in bundle.pages:
            page = Page(**pd)
            if not self.page_path(page.page_digest).exists():
                self.add_page(page)
                stats["pages"] += 1
        for rd in bundle.receipts:
            r = Receipt(**rd)
            if not self.receipt_path(r.receipt_id).exists():
                self.add_receipt(r)
                stats["receipts"] += 1
        return stats
