from __future__ import annotations

from pathlib import Path
from typing import Any

from .store import append_jsonl, new_id, read_jsonl


class PublicReceiptBook:
    """公开回单簿。

    回单只含一次性随机编号和结果，不含姓名、学号、卡号、holder/link、
    实验台编号或席位号，因此学校无法把公开回单拼接成某人的跨台行程。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def issue(self, kind: str, result: str) -> dict[str, Any]:
        receipt = {
            "schema": "local-lab-public-receipt/v1",
            "receipt_id": new_id("rcpt"),
            "kind": kind,
            "result": result,
        }
        append_jsonl(self.path, receipt)
        return receipt

    def all(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path)
