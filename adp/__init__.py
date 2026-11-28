"""adp：Authenticated Dictionary with Pagination（可认证分页术语库）。

模块一览
--------
canonical    确定性 TLV 编码与哈希
entries      条目、复合键、前缀区间
tree         Merkle B-tree 与稀疏范围证明的生成/结构验证
crypto       Ed25519 签名适配层（本机经 OpenSSL CLI 实现）
credential   版本凭据的签发与验签
compiler     编纂端：建树、签发修订
server       不受信检索台：保存快照、答页
client       查词席/排字工坊：独立验页、游标规则、稿件注入
"""
from .entries import Entry, prefix_bounds
from .tree import VerificationError, build_tree
from .credential import Credential
from .crypto import Signer, Verifier
from .compiler import Compiler
from .server import Server
from .client import (
    Client, Manuscript, NetworkTransport, OfflineTransport,
    StaleCursorError, Transport,
)

__all__ = [
    "Entry", "prefix_bounds", "VerificationError", "StaleCursorError",
    "build_tree", "Credential", "Signer", "Verifier", "Compiler",
    "Server", "Client", "Manuscript", "NetworkTransport",
    "OfflineTransport", "Transport",
]
