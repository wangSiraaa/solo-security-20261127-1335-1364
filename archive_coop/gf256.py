"""GF(2^8) 有限域运算，不可约多项式取 Rijndael/AES 所用的 x^8+x^4+x^3+x+1（0x11b）。

Shamir 门限份在该域上做多项式求值与拉格朗日插值；
这是标准做法（与 GF(256) 上的 Reed-Solomon / Shamir 同族）。
"""

_MOD = 0x11B
_GENERATOR = 3          # Rijndael 域中 3 是乘法群生成元（2 只有 51 阶，不可用）
_EXP = [0] * 512
_LOG = [0] * 256


def _gmul_naive(a: int, b: int) -> int:
    """不依赖对数表的多项式乘法 + 模约，仅供建表时迭代生成元使用。"""
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        high = a & 0x80
        a = (a << 1) & 0xFF
        if high:
            a ^= (_MOD & 0xFF)   # 减去 x^8，等价异或低 8 位 0x1B
        b >>= 1
    return p


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x = _gmul_naive(x, _GENERATOR)
    assert x == 1, "3 不是该域的生成元，表未覆盖完整乘法群"
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def add(a: int, b: int) -> int:
    return a ^ b


def mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def inv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError("GF(256) 中 0 无逆元")
    return _EXP[255 - _LOG[a]]


def div(a: int, b: int) -> int:
    if a == 0:
        return 0
    return mul(a, inv(b))


def eval_poly(coeffs: bytes, x: int) -> int:
    """Horner 法求多项式在 x 处的值，coeffs[0] 为常数项（秘密）。"""
    if x == 0:
        raise ValueError("份位 x=0 保留给秘密本身")
    y = 0
    for c in reversed(coeffs):
        y = add(mul(y, x), c)
    return y


def interpolate_const(points: list[tuple[int, int]]) -> int:
    """拉格朗日插值，只取 x=0 处的常数项（即秘密）。"""
    secret = 0
    for i, (xi, yi) in enumerate(points):
        num = 1
        den = 1
        for j, (xj, _) in enumerate(points):
            if i == j:
                continue
            num = mul(num, xj)            # (0 - xj)，减法即异或，等于 xj
            den = mul(den, xi ^ xj)       # (xi - xj)
        secret = add(secret, mul(yi, div(num, den)))
    return secret
