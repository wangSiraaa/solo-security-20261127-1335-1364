"""练习琴条录音分析：基音、泛音列与双段衰减。

输入是普通 WAV（培训室独立录音或外借合成样本均可），
输出结构化测量结果，供调律台做拍频判断。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .dsp import estimate_f0, fit_two_stage_decay, goertzel, spectral_peaks


@dataclass
class Partial:
    index: int                 # 第几泛音（1 = 基音）
    freq: float
    amplitude: float
    tau: float                 # 该泛音自身的长段时间常数（秒）


@dataclass
class BarAnalysis:
    name: str
    sr: int
    f0: float
    partials: list[Partial] = field(default_factory=list)
    decay: dict = field(default_factory=dict)

    def partial_ratio(self, index: int) -> float:
        p = self.partial_map().get(index)
        return p.freq / self.f0 if p else float(index)

    def partial_map(self) -> dict[int, Partial]:
        return {p.index: p for p in self.partials}


def _bandpass_mix(x: list[float], sr: int, f: float, width: float = 0.12):
    """Goertzel 只测单频；用中心频两侧三点近似窄带能量与衰减。"""
    mags = []
    for ff in (f * (1 - width), f, f * (1 + width)):
        mag, _ = goertzel(x, ff, sr)
        mags.append(mag)
    return sum(mags) / len(mags)


def analyze_bar(wav_path: str, name: str = "bar",
                max_partials: int = 6) -> BarAnalysis:
    from .dsp import read_wav
    sr, x = read_wav(wav_path)
    f0 = estimate_f0(x, sr, fmin=60.0, fmax=3000.0)
    peaks = spectral_peaks(x, sr, top_k=max_partials * 4, min_freq=f0 * 0.6)

    # 把谱峰按最近的整数倍频归入泛音序号（容差 1.5%，兼容非谐性）
    assigned: dict[int, tuple[float, float]] = {}
    for f, mag in peaks:
        kf = f / f0
        k = round(kf)
        if k < 1 or k > max_partials:
            continue
        # 容差 5%：允许第二泛音偏移这类实验性非谐性（2.06 相对 2.00）
        if abs(kf - k) / k > 0.05:
            continue
        if k not in assigned or mag > assigned[k][1]:
            assigned[k] = (f, mag)
    if 1 not in assigned:
        assigned[1] = (f0, _bandpass_mix(x, sr, f0))

    partials: list[Partial] = []
    # 注意：纯 Python 环境下逐泛音带通衰减代价过高，这里各泛音的 tau
    # 统一采用整信号双段拟合的长段时间常数（足够区分两根琴条的余韵差异）
    overall = fit_two_stage_decay(x, sr)
    for k in sorted(assigned):
        f, mag = assigned[k]
        partials.append(Partial(index=k, freq=f, amplitude=mag,
                                tau=overall["tau2"]))
    return BarAnalysis(name=name, sr=sr, f0=f0, partials=partials,
                       decay=overall)


def compare(a: BarAnalysis, b: BarAnalysis) -> dict:
    """普通 WAV 读器视角的对比：基频差（音分）、泛音差、衰减差。"""
    out = {
        "f0_a": a.f0,
        "f0_b": b.f0,
        "f0_delta_cents": 1200.0 * math.log2(b.f0 / a.f0),
        "partials": [],
        "decay": {
            "tau1_a": a.decay.get("tau1", 0.0),
            "tau1_b": b.decay.get("tau1", 0.0),
            "tau2_a": a.decay.get("tau2", 0.0),
            "tau2_b": b.decay.get("tau2", 0.0),
        },
    }
    am, bm = a.partial_map(), b.partial_map()
    for k in sorted(set(am) & set(bm)):
        out["partials"].append({
            "index": k,
            "freq_a": am[k].freq,
            "freq_b": bm[k].freq,
            "delta_cents": 1200.0 * math.log2(bm[k].freq / am[k].freq),
            "amp_ratio": bm[k].amplitude / max(am[k].amplitude, 1e-9),
        })
    return out
