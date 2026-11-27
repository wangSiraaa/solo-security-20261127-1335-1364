"""Shamir 门限秘密分享（GF(256)，逐字节多项式）。

- split：对秘密的每个字节独立生成一次阈次多项式，份位 x 固定非 0。
- combine：拿到 >= threshold 个不同份位即可插回原秘密；
  份位重复的材料只会被去重，不会因为“交了两张纸”而变成两份。
"""

import secrets

from . import gf256


def split(secret: bytes, threshold: int, indices: list[int]) -> dict[int, bytes]:
    if threshold < 2:
        raise ValueError("门限至少为 2，任何单人都不得独自开卷")
    if threshold > len(indices):
        raise ValueError("门限不得超过份位数")
    if len(set(indices)) != len(indices):
        raise ValueError("份位必须互不相同")
    if any(x <= 0 or x > 255 for x in indices):
        raise ValueError("份位必须落在 1..255")

    shares: dict[int, bytearray] = {x: bytearray() for x in indices}
    for byte in secret:
        # 系数 0..threshold-2 随机，常数项是该字节秘密
        coeffs = [byte] + [secrets.randbelow(256) for _ in range(threshold - 1)]
        for x in indices:
            shares[x].append(gf256.eval_poly(coeffs, x))
    return {x: bytes(v) for x, v in shares.items()}


def combine(pieces: list[tuple[int, bytes]]) -> bytes:
    """pieces 为 (份位, 份体) 列表；重复份位只保留一份。"""
    unique: dict[int, bytes] = {}
    for x, body in pieces:
        if x in unique:
            # 同一份位的第二张回单/第二个盒子不产生新材料
            continue
        unique[x] = body
    if not unique:
        raise ValueError("没有可用于合份的材料")
    length = len(next(iter(unique.values())))
    if any(len(b) != length for b in unique.values()):
        raise ValueError("份体长度不一致，材料可能被截断或串卷")

    out = bytearray()
    points_template = list(unique.items())
    for pos in range(length):
        points = [(x, body[pos]) for x, body in points_template]
        out.append(gf256.interpolate_const(points))
    return bytes(out)
