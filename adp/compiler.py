"""编纂端（compiler）。

职责：
  1. 持有签发键，把有序术语库建成 Merkle B-tree；
  2. 每次修订独立建树、独立签发版本凭据（边界证明随每次修订重新生成，
     由服务器在答页时即时构造，编纂端不预签页面）；
  3. 输出快照（树 + 凭据）交给检索台，输出验签键给查词席/排字工坊。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import canonical as C
from .credential import Credential, EMPTY_ROOT_HEX, issue_credential
from .crypto import Signer
from .entries import Entry
from .tree import Node, build_tree


@dataclass
class Revision:
    version: int
    credential: Credential
    tree: Optional[Node]
    entries: list[Entry]

    def snapshot_json(self) -> dict[str, Any]:
        """交给检索台的完整修订快照（含全库条目，客户端永远拿不到它）。"""
        return {
            "version": self.version,
            "credential": self.credential.to_json(),
            "entries": [e.to_json() for e in self.entries],
            "tree": self.tree.to_json() if self.tree is not None else None,
        }


class Compiler:
    def __init__(self, signer: Signer):
        self.signer = signer

    def publish(self, version: int, entries: list[Entry],
                created_at: str) -> Revision:
        """对一版条目做完整编纂：排序、建树、签凭据。"""
        entries = sorted(entries, key=lambda e: e.key)
        tree = build_tree(entries)
        root_hex = C.to_hex(tree.hash) if tree is not None else EMPTY_ROOT_HEX
        cred = issue_credential(
            self.signer, version=version, root_hash_hex=root_hex,
            entry_count=len(entries), created_at=created_at,
        )
        return Revision(version, cred, tree, entries)


def save_snapshot(rev: Revision, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(rev.snapshot_json(), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
