"""术语条目与有序键。

显示词允许重复（同形异义词），因此全库唯一键是二元组
``(显示词, 义项号)``，按 Unicode 码位序（Python 字符串序）排列。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Optional

from . import canonical as C

# 查询范围使用的哨兵键。
# - NEG_INF 低于一切真实键；
# - POS_INF 高于一切真实键（空前缀的上界即 POS_INF）。
NEG_INF: tuple = (None, -1)
POS_INF: tuple = (None, None)


@dataclass(frozen=True)
class Entry:
    word: str
    sense: int          # 同一显示词下的义项号，从 0 起
    pron: str           # 读音
    gloss: str          # 字义

    @property
    def key(self) -> tuple[str, int]:
        return (self.word, self.sense)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Entry":
        return Entry(
            word=d["word"], sense=int(d["sense"]),
            pron=d["pron"], gloss=d["gloss"],
        )

    def hash_bytes(self) -> bytes:
        """叶子提交值：词、义项、读音、字义全部进哈希，
        因此改读音或改字义都会让树根变化、签名失效。"""
        return C.sha256(
            C.tlv(C.TAG_LEAF, b"".join([
                C.enc_str(self.word),
                C.enc_int(self.sense),
                C.enc_str(self.pron),
                C.enc_str(self.gloss),
            ]))
        )


def key_in_range(key: tuple, lo: tuple, hi: Optional[tuple]) -> bool:
    """半开区间 ``lo <= key < hi``；``hi is None`` 表示无上界。
    下界可能是 NEG_INF（首元为 None），此时一切真实键都不低于它。"""
    if lo[0] is not None and key < lo:
        return False
    if hi is not None and not (key < hi):
        return False
    return True


def prefix_bounds(prefix: str) -> tuple[tuple, Optional[tuple]]:
    """前缀查询的半开键区间。

    下界为 ``(prefix, 0)``——真实义项号从 0 起，因此 ``>= (prefix, 0)``
    恰好覆盖显示词等于 prefix 或按字典序大于 prefix 的所有词。
    上界为 ``(prefix_successor, -1)``，其中 successor 是把前缀最后一个
    字符码位加 1 得到的字符串；空前缀无上界（全库）。
    """
    if prefix == "":
        return NEG_INF, None
    lo = (prefix, 0)
    succ = _string_successor(prefix)
    hi = (succ, -1)
    return lo, hi


def _string_successor(s: str) -> str:
    """最短的、使所有以 s 开头的字符串都严格小于它的字符串。

    例："ab" -> "ac"，"木" -> "朩"（U+6729 = U+6728+1）。
    空串无后继（由调用方处理为 POS_INF）。
    """
    if s == "":
        raise ValueError("空串无字符串后继")
    return s[:-1] + chr(ord(s[-1]) + 1)


def encode_key(key: tuple) -> bytes:
    """键的确定性编码，用于内部节点边界提交。"""
    word, sense = key
    return C.enc_str(word) + C.enc_int(sense)
