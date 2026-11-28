"""Merkle B-tree：有序认证索引。

结构
----
叶子层是全部条目按 ``(word, sense)`` 排序后按 FANOUT 分块；
内部节点同样按 FANOUT 聚合。每个内部节点的哈希同时提交：

  1. 每个孩子的子树根哈希；
  2. 每个孩子的首尾边界键。

第 2 点是"剪枝后仍能证明无漏项"的根基：客户端拿到剪枝节点时，
其首尾边界不是服务器自报的，而是被父节点哈希、最终被签名树根
层层提交的，篡改边界必然导致根哈希对不上。

证明（proof）是同一棵树的稀疏表示：
  {"leaf": {...条目...}}            展开的叶子
  {"internal": [孩子证明...]}      展开的内部节点
  {"pruned": {h, first, last}}     整棵不展开的子树（带哈希与边界）
"""
from __future__ import annotations

from typing import Any, Optional

from . import canonical as C
from .entries import Entry, encode_key

FANOUT = 16


# ---------------------------------------------------------------- 建树

class Node:
    """运行期树节点（仅编纂端/服务器持有完整形态）。"""

    def __init__(self, *, leaf: Optional[Entry] = None,
                 children: Optional[list["Node"]] = None):
        self.leaf = leaf
        self.children = children
        if leaf is not None:
            self.first = leaf.key
            self.last = leaf.key
            self.hash = leaf.hash_bytes()
        else:
            assert children
            self.first = children[0].first
            self.last = children[-1].last
            parts: list[bytes] = []
            for ch in children:
                parts.append(C.tlv(C.TAG_INTERNAL_CHILD_HASH, ch.hash))
                parts.append(C.tlv(C.TAG_INTERNAL_FIRST, encode_key(ch.first)))
                parts.append(C.tlv(C.TAG_INTERNAL_LAST, encode_key(ch.last)))
            self.hash = C.sha256(C.tlv(C.TAG_INTERNAL, b"".join(parts)))

    def to_json(self) -> dict[str, Any]:
        if self.leaf is not None:
            return {"leaf": self.leaf.to_json()}
        return {"internal": [ch.to_json() for ch in self.children]}


def build_tree(entries: list[Entry], fanout: int = FANOUT) -> Optional[Node]:
    """按有序条目建 Merkle B-tree；空库返回 None。"""
    if not entries:
        return None
    keys = [e.key for e in entries]
    if keys != sorted(keys):
        raise ValueError("条目必须按 (word, sense) 排序且键唯一")
    if len(set(keys)) != len(keys):
        raise ValueError("存在重复键 (word, sense)")

    level: list[Node] = [Node(leaf=e) for e in entries]
    while len(level) > 1:
        nxt = [
            Node(children=level[i:i + fanout])
            for i in range(0, len(level), fanout)
        ]
        level = nxt
    return level[0]


# ------------------------------------------------ 证明生成（服务器端）

def make_proof(node: Optional[Node], lo: tuple, hi: Optional[tuple]) -> dict[str, Any]:
    """生成稀疏证明：完全落在 [lo, hi) 之外的子树整棵剪枝，
    与区间有交的节点递归展开。"""
    if node is None:
        # 空树：证明就是空，由凭据中的 root_hash（全零）锚定
        return {"empty": True}
    if _node_outside(node, lo, hi):
        return {"pruned": {
            "h": C.to_hex(node.hash),
            "first": _key_json(node.first),
            "last": _key_json(node.last),
        }}
    if node.leaf is not None:
        return {"leaf": node.leaf.to_json()}
    return {"internal": [make_proof(ch, lo, hi) for ch in node.children]}


def _node_outside(node: Node, lo: tuple, hi: Optional[tuple]) -> bool:
    # lo 可能是 NEG_INF（首元为 None），此时没有任何真实键低于它
    if lo[0] is not None and node.last < lo:
        return True
    # 子树整体 >= hi
    if hi is not None and not (node.first < hi):
        return True
    return False


def _key_json(key: tuple) -> list[Any]:
    return [key[0], key[1]]


# ------------------------------------------------ 证明验证（客户端）

class VerificationError(Exception):
    """证明或页面未通过独立验证。"""


def _node_span(proof: dict[str, Any]) -> tuple[tuple, tuple]:
    """取任一证明节点的首尾键（叶子首尾相同）。"""
    if "pruned" in proof:
        p = proof["pruned"]
        return tuple(p["first"]), tuple(p["last"])
    if "leaf" in proof:
        e = Entry.from_json(proof["leaf"])
        return e.key, e.key
    kids = proof["internal"]
    first, _ = _node_span(kids[0])
    _, last = _node_span(kids[-1])
    return first, last


def verify_proof_structure(proof: dict[str, Any],
                           expected_root_hash_hex: str) -> list[Entry]:
    """重算根哈希并检查结构，返回所有展开叶子（严格升序）。

    不接受任何页面语义，只检查：根哈希匹配、每层边界严格递增、
    剪枝节点边界与其（被父节点提交的）哈希一致、叶子条目编码合法。
    """
    if proof.get("empty"):
        if expected_root_hash_hex != C.to_hex(b"\x00" * 32):
            raise VerificationError("空树证明与凭据根哈希不符")
        return []

    root_hash, leaves = _recompute(proof)
    if root_hash != C.from_hex(expected_root_hash_hex):
        raise VerificationError("重算的根哈希与签名凭据不一致（页面被改过或版本不符）")
    return leaves


def _recompute(proof: dict[str, Any]) -> tuple[bytes, list[Entry]]:
    if "pruned" in proof:
        p = proof["pruned"]
        # 剪枝节点的边界由父节点哈希链提交，这里只回传哈希供父层重算
        return C.from_hex(p["h"]), []
    if "leaf" in proof:
        e = Entry.from_json(proof["leaf"])
        return e.hash_bytes(), [e]

    kids = proof["internal"]
    if len(kids) == 0 or len(kids) > FANOUT:
        raise VerificationError("内部节点度数非法")
    parts: list[bytes] = []
    leaves: list[Entry] = []
    prev_last: Optional[tuple] = None
    for kp in kids:
        h, sub_leaves = _recompute(kp)
        first, last = _node_span(kp)
        if first > last:
            raise VerificationError("子树首尾键倒置")
        if prev_last is not None and not (prev_last < first):
            raise VerificationError("树内键序断裂，存在重叠或乱序")
        prev_last = last
        parts.append(C.tlv(C.TAG_INTERNAL_CHILD_HASH, h))
        parts.append(C.tlv(C.TAG_INTERNAL_FIRST, encode_key(first)))
        parts.append(C.tlv(C.TAG_INTERNAL_LAST, encode_key(last)))
        leaves.extend(sub_leaves)
    return C.sha256(C.tlv(C.TAG_INTERNAL, b"".join(parts))), leaves
