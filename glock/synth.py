"""钟琴琴条的本地加性合成器。

四类“音色库参数”都在这里落地：
  * 分键区 zone：低/中/高音区不同泛音比与衰减
  * 力度层 layer：4 层，相邻层力度范围交叠，亮度/增益/衰减随力度变化
  * 微调音分：每根琴条确定性的音分偏移
  * 双段衰减：快起振段 tau1 + 长余韵段 tau2
另可单独生成“释放尾音”资产（工厂留厂，不随意外发）。
全部确定性：同参数必出同样本，便于做不可区分性哈希比对。
"""
from __future__ import annotations

import math

SR = 32000

# 三个键区：泛音频率比（含非谐性）、相对增益、长段时间常数（秒）
ZONES = {
    "low": {
        "ratios": [1.0, 2.76, 5.40, 8.93],
        "gains": [1.00, 0.55, 0.22, 0.08],
        "tau2": 1.10,
    },
    "mid": {
        "ratios": [1.0, 2.80, 5.52, 9.20],
        "gains": [1.00, 0.50, 0.18, 0.06],
        "tau2": 0.75,
    },
    "high": {
        "ratios": [1.0, 2.87, 5.71],
        "gains": [1.00, 0.42, 0.12],
        "tau2": 0.45,
    },
}

# 四个力度层：(中心力度, 亮度系数, 整体增益, 衰减时间系数)
LAYER_CENTERS = [32, 64, 96, 127]
LAYER_PARAMS = [
    (0.65, 0.55, 1.15),
    (0.85, 0.75, 1.05),
    (1.10, 0.95, 0.95),
    (1.35, 1.10, 0.85),
]
# 相邻力度层的交叠范围（含端点）
LAYER_RANGES = [(1, 42), (34, 74), (66, 106), (98, 127)]

TAU1 = 0.08          # 起振快衰减
SPLIT_S = 0.12       # 双段切换点
ATTACK_S = 0.003     # 敲击起音时长


def zone_for_midi(midi: int) -> str:
    if midi <= 60:
        return "low"
    if midi <= 79:
        return "mid"
    return "high"


def factory_cents(midi: int) -> float:
    """工厂调音的确定性音分偏移（约 ±1.7 音分）。"""
    return 1.2 * math.sin(midi * 1.73) + 0.5 * math.sin(midi * 0.41)


def layer_blend(velocity: int) -> list[tuple[int, float]]:
    """返回该力度命中的力度层及线性混合权重（体现相邻力度交叠）。"""
    hits = [(i, 1.0) for i, (lo, hi) in enumerate(LAYER_RANGES) if lo <= velocity <= hi]
    if len(hits) == 1:
        return hits
    if hits:
        i0, _ = hits[0]
        c0, c1 = LAYER_CENTERS[i0], LAYER_CENTERS[i0 + 1]
        w1 = (velocity - c0) / (c1 - c0)
        return [(i0, 1.0 - w1), (i0 + 1, w1)]
    # 超出表范围时贴边
    return [(0 if velocity < LAYER_RANGES[0][0] else len(LAYER_RANGES) - 1, 1.0)]


def _blended_layer(velocity: int) -> tuple[float, float, float]:
    weights = layer_blend(velocity)
    bright = gain = tscale = 0.0
    for idx, w in weights:
        b, g, t = LAYER_PARAMS[idx]
        bright += b * w
        gain += g * w
        tscale += t * w
    return bright, gain, tscale


def _lcg(seed: int):
    state = seed & 0xFFFFFFFF
    while True:
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        yield state / 0x7FFFFFFF * 2.0 - 1.0


def render_bar(freq: float, velocity: int = 96, cents: float = 0.0,
               duration: float = 1.6, sr: int = SR, zone: str | None = None,
               midi: int = 69, seed: int = 0,
               ratio_override: dict[int, float] | None = None,
               gain_override: dict[int, float] | None = None,
               decay_scale: float = 1.0) -> list[float]:
    """合成一根琴条的单音。

    ratio_override/gain_override：替换第 k 个泛音的频率比/增益
    （校音台“第二泛音偏移样本”实验即用此入口）。
    decay_scale：整体衰减时间缩放（模拟两根琴条不同的余韵）。
    """
    z = ZONES[zone or zone_for_midi(midi)]
    ratios = list(z["ratios"])
    gains = list(z["gains"])
    if ratio_override:
        for k, r in ratio_override.items():
            ratios[k - 1] = r
    if gain_override:
        for k, gv in gain_override.items():
            gains[k - 1] = gv

    bright, vel_gain, tscale = _blended_layer(velocity)
    tau1 = TAU1 * tscale * decay_scale
    tau2 = z["tau2"] * tscale * decay_scale
    f = freq * (2.0 ** (cents / 1200.0))

    n = int(duration * sr)
    out = [0.0] * n
    rng = _lcg(seed or (midi * 1009 + velocity))
    noise_prev = 0.0
    for i in range(n):
        t = i / sr
        # 双段衰减包络，段切换处保持连续
        if t < SPLIT_S:
            env = math.exp(-t / tau1)
        else:
            env = math.exp(-SPLIT_S / tau1) * math.exp(-(t - SPLIT_S) / tau2)
        env *= min(1.0, t / ATTACK_S)
        v = 0.0
        for ratio, base_gain in zip(ratios, gains):
            g = base_gain * (bright if ratio != 1.0 else 1.0)
            v += g * math.sin(2.0 * math.pi * f * ratio * t)
        # 确定性敲击噪声（差分近似高通）
        noise = next(rng)
        hit_noise = (noise - noise_prev) * 0.03 * math.exp(-t / 0.005)
        noise_prev = noise
        out[i] = (v * 0.25 + hit_noise) * env * vel_gain
    return out


def render_tail(freq: float, velocity: int = 96, cents: float = 0.0,
                duration: float = 0.8, sr: int = SR, midi: int = 69,
                seed: int = 0) -> list[float]:
    """单独的释放尾音资产：只余长衰减段，无敲击起振。"""
    z = ZONES[zone_for_midi(midi)]
    _, vel_gain, tscale = _blended_layer(velocity)
    tau2 = z["tau2"] * tscale
    f = freq * (2.0 ** (cents / 1200.0))
    n = int(duration * sr)
    out = [0.0] * n
    for i in range(n):
        t = i / sr
        env = math.exp(-t / tau2)
        v = 0.0
        for ratio, gain in zip(z["ratios"], z["gains"]):
            v += gain * 0.5 * math.sin(2.0 * math.pi * f * ratio * t)
        out[i] = v * 0.25 * env * vel_gain
    return out
