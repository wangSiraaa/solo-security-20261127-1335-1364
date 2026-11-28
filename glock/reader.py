"""普通 WAV 读器：不依赖外借清单，任何人都能用它复核单音与合响。

提供三件事：
  - 单音频差：两个 WAV 的基频/泛音音分差
  - 衰减对比：双段时间常数差异
  - 合响拍频：把两个 WAV 相加后直接数拍（幅度起伏频率），
    与工作台基于泛音的拍频判断互相印证
"""
from __future__ import annotations

import math

from .analysis import BarAnalysis, analyze_bar, compare
from .dsp import read_wav


def mix_wavs(path_a: str, path_b: str) -> tuple[int, list[float]]:
    sr_a, xa = read_wav(path_a)
    sr_b, xb = read_wav(path_b)
    if sr_a != sr_b:
        raise ValueError("两段录音采样率不同，无法直接合响")
    n = min(len(xa), len(xb))
    return sr_a, [0.5 * (xa[i] + xb[i]) for i in range(n)]


def count_beats(samples: list[float], sr: int, frame: int = 512,
                fmin: float = 0.5, fmax: float = 12.0) -> float:
    """从合响包络中数拍频（Hz）：分帧 RMS -> 自相关找起伏周期。"""
    n_frames = len(samples) // frame
    env = []
    for i in range(n_frames):
        seg = samples[i * frame:(i + 1) * frame]
        env.append(math.sqrt(sum(v * v for v in seg) / frame))
    mean = sum(env) / len(env)
    env = [e - mean for e in env]
    ac = [sum(env[i] * env[i + k] for i in range(len(env) - k))
          for k in range(len(env))]
    lo = max(1, int(1.0 / (fmax * frame / sr)))
    hi = min(len(ac) - 2, int(1.0 / (fmin * frame / sr)))
    peak = max(range(lo, hi + 1), key=lambda k: ac[k])
    if ac[peak] <= 0:
        return 0.0
    return sr / (frame * peak)


def reader_report(path_a: str, path_b: str) -> dict:
    a: BarAnalysis = analyze_bar(path_a, "A")
    b: BarAnalysis = analyze_bar(path_b, "B")
    sr, mixed = mix_wavs(path_a, path_b)
    return {
        "single_tone": compare(a, b),
        "ensemble_beat_hz": count_beats(mixed, sr),
        "ensemble_duration_s": len(mixed) / sr,
    }
