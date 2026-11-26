"""第三方寄存柜：独立于两站，夜间清理不影响寄存材料。"""
from __future__ import annotations

import json
from pathlib import Path

from .models import Bundle
from .store import CaseStore


class EscrowLocker:
    def __init__(self, root: str | Path):
        self.dir = Path(root)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.dir / "manifest.jsonl"

    def deposit(self, source: str, bundle: Bundle) -> None:
        """寄存一包材料，逐文件写入并登记来源；同内容去重。"""
        existing_store = CaseStore(self.dir / "store")
        before = self._counts(existing_store)
        stats = existing_store.import_bundle(bundle)
        with self.manifest_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "source": source,
                "stats": stats,
            }, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _counts(store: CaseStore) -> dict:
        return {"pages": len(list((store.dir / 'pages').glob('*.json'))),
                "receipts": len(store.all_receipts())}

    def as_store(self) -> CaseStore:
        """把寄存柜内容作为只读案件库打开，供复查与重放。"""
        return CaseStore(self.dir / "store")

    def manifest(self) -> list[dict]:
        if not self.manifest_path.exists():
            return []
        return [json.loads(line)
                for line in self.manifest_path.read_text(
                    encoding="utf-8").splitlines() if line.strip()]
