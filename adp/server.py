"""不受信检索台（server）。

检索台保存若干修订的完整快照并负责答页。它可以篡改、拼接、漏给，
但客户端只认编纂端签名的根哈希，所以一切篡改都会在验页时暴露。

答页内容 = 本页条目 + 稀疏范围证明 + 版本凭据。证明的展开窗口是
“前驱键（无则前缀下界）→ 后继键（无则前缀上界）”，使客户端能够：
  - 看到本页紧邻的前一个/后一个真实条目，判定页界与翻页终止；
  - 确认窗口内没有任何被整棵剪枝藏起来的子树。
"""
from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import Any, Optional

from .credential import Credential
from .entries import Entry, prefix_bounds
from .tree import build_tree, make_proof


def cursor_json(version: int, after: Optional[tuple]) -> Optional[dict[str, Any]]:
    """游标绑定修订版本；after=None 表示前缀起点（不作为续页游标签发）。"""
    if after is None:
        return None
    return {"version": version, "after": [after[0], after[1]]}


class Server:
    def __init__(self) -> None:
        # version -> (entries 升序, 键数组, tree, credential)
        self._revs: dict[int, tuple] = {}

    # ------------------------------------------------ 编纂端投递快照

    def load_snapshot(self, snapshot: dict[str, Any]) -> None:
        version = int(snapshot["version"])
        entries = sorted(
            (Entry.from_json(d) for d in snapshot["entries"]),
            key=lambda e: e.key,
        )
        tree = build_tree(entries)
        cred = Credential.from_json(snapshot["credential"])
        if cred.version != version:
            raise ValueError("快照版本与凭据版本不一致")
        self._revs[version] = (entries, [e.key for e in entries], tree, cred)

    def load_snapshot_file(self, path: str | Path) -> None:
        self.load_snapshot(json.loads(Path(path).read_text(encoding="utf-8")))

    def versions(self) -> list[int]:
        return sorted(self._revs)

    def latest_version(self) -> int:
        return max(self._revs)

    def get_credential(self, version: int) -> Credential:
        return self._revs[version][3]

    # ------------------------------------------------ 答页

    def get_page(self, version: int, prefix: str, limit: int,
                 cursor: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        if version not in self._revs:
            raise KeyError(f"服务器没有版本 {version}")
        if limit < 1:
            raise ValueError("limit 必须 >= 1")
        entries, keys, tree, cred = self._revs[version]
        lo, hi = prefix_bounds(prefix)
        after: Optional[tuple] = None
        if cursor is not None:
            # 游标自带版本：客户端据此拒绝跨版续页
            if int(cursor.get("version", version)) != version:
                raise ValueError(
                    "游标属于另一修订版本，必须从前缀首页重新查询"
                )
            after = tuple(cursor["after"]) if cursor.get("after") else None

        # bisect 不能用含 None 的 NEG_INF；空前缀下界直接取全库起点
        lo_idx = 0 if lo == prefix_bounds("")[0] else bisect_left(keys, lo)
        hi_idx = bisect_right(keys, hi) if hi is not None else len(keys)

        if after is not None:
            start = max(lo_idx, bisect_right(keys, after))
        else:
            start = lo_idx

        page = entries[start:min(start + limit, hi_idx)]
        end_idx = start + len(page)

        # 证明窗口必须**包含**紧邻的前驱/后继叶子本身（各最多一个），
        # 验证器亲眼看到这两个边界条目后，窗口外的子树才可整棵剪枝：
        #   - 前驱存在：窗口下界取前驱键（前驱自身进入展开集）；
        #   - 后继存在：窗口上界取“后继之后紧邻的键”（无则无上界），
        #     这样后继叶子在窗口内、其后的子树仍被剪枝；
        #   - 无前驱/后继时退到前缀下界/上界。
        reveal_lo_key = keys[start - 1] if start > 0 else lo
        if end_idx < hi_idx:
            succ_idx = end_idx
            reveal_hi_key = keys[succ_idx + 1] if succ_idx + 1 < len(keys) else None
        else:
            reveal_hi_key = hi

        proof = make_proof(tree, reveal_lo_key, reveal_hi_key)
        has_more = end_idx < hi_idx
        next_after = page[-1].key if page else after

        return {
            "version": version,
            "prefix": prefix,
            "limit": limit,
            "cursor": cursor_json(version, after),
            "entries": [e.to_json() for e in page],
            "next_cursor": cursor_json(version, next_after) if has_more else None,
            "has_more": has_more,
            "proof": proof,
            "credential": cred.to_json(),
        }
