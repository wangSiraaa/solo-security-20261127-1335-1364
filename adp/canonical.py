"""确定性编码（canonical encoding）。

认证结构的所有哈希都建立在本模块的 TLV 编码之上，保证：
- 字段边界无歧义（长度前缀，不依赖分隔符）；
- 字符串统一 UTF-8，整数用无符号大端 varint；
- 任何数据变动都会改变哈希。

标签表（单字节，见文末常量）在叶子、内部节点、凭据等不同对象之间
互不复用，避免跨类型的碰撞。
"""
from __future__ import annotations

import hashlib

# ---- TLV 标签 ----
TAG_VARINT = 0x01
TAG_BYTES = 0x02
TAG_STRING = 0x03

# Entry
TAG_ENTRY_WORD = 0x10
TAG_ENTRY_SENSE = 0x11
TAG_ENTRY_PRON = 0x12
TAG_ENTRY_GLOSS = 0x13

# 树节点（标签跨对象不重叠）
TAG_LEAF = 0x20
TAG_INTERNAL = 0x21
TAG_INTERNAL_CHILD_HASH = 0x22
TAG_INTERNAL_FIRST = 0x23
TAG_INTERNAL_LAST = 0x24

# 版本凭据
TAG_CRED_VERSION = 0x30
TAG_CRED_ROOT = 0x31
TAG_CRED_COUNT = 0x32
TAG_CRED_CREATED = 0x33
TAG_CRED_KEYID = 0x34


def uvarint(n: int) -> bytes:
    """无符号 LEB128（base-128 varint）。"""
    if n < 0:
        raise ValueError("uvarint 不接受负数")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def tlv(tag: int, payload: bytes) -> bytes:
    return bytes([tag]) + uvarint(len(payload)) + payload


def enc_int(n: int) -> bytes:
    return tlv(TAG_VARINT, uvarint(n))


def enc_str(s: str) -> bytes:
    return tlv(TAG_STRING, s.encode("utf-8"))


def enc_bytes(b: bytes) -> bytes:
    return tlv(TAG_BYTES, b)


def sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def to_hex(b: bytes) -> str:
    return b.hex()


def from_hex(s: str) -> bytes:
    return bytes.fromhex(s)
