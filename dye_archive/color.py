"""虚构色度学：16 点可见光谱、Kubelka-Munk 混色、CIE Lab 与 CIEDE2000。

数据为虚构模拟值，仅用于离线演示，不代表任何真实染料。
"""
from __future__ import annotations

import math

WL = list(range(400, 701, 20))  # 16 个波长点

# D65 相对光谱功率（20nm 抽样，虚构演示用表）
D65 = [
    82.75, 93.43, 104.86, 117.81, 115.92, 109.35, 104.79, 104.41,
    100.00, 95.79, 90.01, 87.70, 83.70, 80.21, 78.28, 71.61,
]
# CIE 1931 2° 标准色度观察者（20nm 抽样）
XBAR = [
    0.0143, 0.1344, 0.3483, 0.2908, 0.0956, 0.0049, 0.0637, 0.2835,
    0.5945, 0.9163, 1.0622, 0.8544, 0.4479, 0.1649, 0.0468, 0.0114,
]
YBAR = [
    0.0004, 0.0040, 0.0230, 0.0600, 0.1390, 0.3230, 0.7100, 0.9540,
    0.9950, 0.8700, 0.6310, 0.3810, 0.1750, 0.0610, 0.0170, 0.0041,
]
ZBAR = [
    0.0679, 0.6456, 1.7471, 1.6692, 0.8130, 0.2720, 0.0782, 0.0122,
    0.0039, 0.0017, 0.0008, 0.0002, 0.0000, 0.0000, 0.0000, 0.0000,
]

# 三种虚构染料的单位剂量 K/S 谱
COLORANT_KS = {
    "A": [0.05, 0.10, 0.30, 0.55, 0.75, 0.80, 0.55, 0.25,
          0.08, 0.04, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01],  # 品红系
    "B": [0.90, 0.85, 0.70, 0.40, 0.18, 0.07, 0.03, 0.02,
          0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01],  # 黄系
    "C": [0.02, 0.02, 0.03, 0.05, 0.08, 0.12, 0.20, 0.35,
          0.55, 0.72, 0.80, 0.78, 0.62, 0.40, 0.22, 0.12],  # 青系
}
BASE_KS = [0.020] * 16


def reflectance(doses: dict[str, float], strengths: dict[str, float]) -> list[float]:
    """Kubelka-Munk：由剂量与各染料强度保留率算反射率。"""
    ks = list(BASE_KS)
    for colorant, dose in doses.items():
        if dose <= 0:
            continue
        alpha = strengths.get(colorant, 1.0)
        for i, k in enumerate(COLORANT_KS[colorant]):
            ks[i] += alpha * dose * k
    return [1.0 + k - math.sqrt(k * k + 2.0 * k) for k in ks]


def _white_point() -> tuple[float, float, float]:
    norm = sum(s * y for s, y in zip(D65, YBAR))
    return (
        sum(s * x for s, x in zip(D65, XBAR)) / norm,
        1.0,
        sum(s * z for s, z in zip(D65, ZBAR)) / norm,
    )


XN, YN, ZN = _white_point()


def xyz_from_reflectance(r: list[float]) -> tuple[float, float, float]:
    norm = sum(s * y for s, y in zip(D65, YBAR))
    x = sum(s * rr * cmf for s, rr, cmf in zip(D65, r, XBAR)) / norm * 100.0
    y = sum(s * rr * cmf for s, rr, cmf in zip(D65, r, YBAR)) / norm * 100.0
    z = sum(s * rr * cmf for s, rr, cmf in zip(D65, r, ZBAR)) / norm * 100.0
    return x, y, z


def _f(t: float) -> float:
    eps = 216.0 / 24389.0
    if t > eps:
        return t ** (1.0 / 3.0)
    return 7.787 * t + 16.0 / 116.0


def lab_from_xyz(xyz: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = xyz
    fx, fy, fz = _f(x / (XN * 100.0)), _f(y / (YN * 100.0)), _f(z / (ZN * 100.0))
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def lab_of(doses: dict[str, float], strengths: dict[str, float]) -> tuple[float, float, float]:
    return lab_from_xyz(xyz_from_reflectance(reflectance(doses, strengths)))


def ciede2000(lab1: tuple[float, float, float],
              lab2: tuple[float, float, float]) -> float:
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c1 = math.hypot(a1, b1)
    c2 = math.hypot(a2, b2)
    c_bar = (c1 + c2) / 2.0
    g = 0.5 * (1.0 - math.sqrt(c_bar ** 7 / (c_bar ** 7 + 25.0 ** 7)))
    a1p, a2p = (1.0 + g) * a1, (1.0 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360.0

    dlp = l2 - l1
    dcp = c2p - c1p
    if c1p * c2p == 0.0:
        dhp = 0.0
    else:
        diff = h2p - h1p
        if diff > 180.0:
            diff -= 360.0
        elif diff < -180.0:
            diff += 360.0
        dhp = diff
    dHp = 2.0 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp / 2.0))

    l_bar = (l1 + l2) / 2.0
    c_bar_p = (c1p + c2p) / 2.0
    if c1p * c2p == 0.0:
        h_bar = h1p + h2p
    elif abs(h1p - h2p) > 180.0:
        h_bar = (h1p + h2p + 360.0) / 2.0
    else:
        h_bar = (h1p + h2p) / 2.0

    t = (1.0
         - 0.17 * math.cos(math.radians(h_bar - 30.0))
         + 0.24 * math.cos(math.radians(2.0 * h_bar))
         + 0.32 * math.cos(math.radians(3.0 * h_bar + 6.0))
         - 0.20 * math.cos(math.radians(4.0 * h_bar - 63.0)))
    d_theta = 30.0 * math.exp(-(((h_bar - 275.0) / 25.0) ** 2))
    rc = 2.0 * math.sqrt(c_bar_p ** 7 / (c_bar_p ** 7 + 25.0 ** 7))
    sl = 1.0 + (0.015 * (l_bar - 50.0) ** 2) / math.sqrt(20.0 + (l_bar - 50.0) ** 2)
    sc = 1.0 + 0.045 * c_bar_p
    sh = 1.0 + 0.015 * c_bar_p * t
    rt = -math.sin(math.radians(2.0 * d_theta)) * rc

    sl_, sc_, sh_ = sl, sc, sh
    return math.sqrt(
        (dlp / sl_) ** 2
        + (dcp / sc_) ** 2
        + (dHp / sh_) ** 2
        + rt * (dcp / sc_) * (dHp / sh_)
    )
