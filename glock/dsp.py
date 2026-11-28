"""零依赖 DSP：FFT、单频测量、自相关、包络拟合、重采样、WAV 读写。"""
from __future__ import annotations

import math
import struct
import wave


# ---------- FFT（迭代版基 2 Cooley-Tukey，长度须为 2 的幂） ----------

def fft(re_in: list[float], re_im: list[float] | None = None) -> tuple[list[float], list[float]]:
    n = len(re_in)
    if n & (n - 1):
        raise ValueError("FFT 长度必须是 2 的幂")
    re = list(re_in)
    im = [0.0] * n if re_im is None else list(re_im)

    # 位逆序置换
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            re[i], re[j] = re[j], re[i]
            im[i], im[j] = im[j], im[i]

    length = 2
    while length <= n:
        ang = -2.0 * math.pi / length
        wlr, wli = math.cos(ang), math.sin(ang)
        half = length >> 1
        for start in range(0, n, length):
            wr, wi = 1.0, 0.0
            for k in range(half):
                a, b = start + k, start + k + half
                tr = wr * re[b] - wi * im[b]
                ti = wr * im[b] + wi * re[b]
                re[b] = re[a] - tr
                im[b] = im[a] - ti
                re[a] += tr
                im[a] += ti
                wr, wi = wr * wlr - wi * wli, wr * wli + wi * wlr
        length <<= 1
    return re, im


def ifft(re_in: list[float], im_in: list[float]) -> tuple[list[float], list[float]]:
    """逆 FFT：IFFT(X) = conj(FFT(conj(X))) / N。"""
    n = len(re_in)
    re, im = fft(re_in, [-v for v in im_in])
    return [v / n for v in re], [-v / n for v in im]


def magnitude_spectrum(x: list[float], min_size: int | None = None) -> list[float]:
    size = 1
    target = max(len(x), min_size or 0)
    while size < target:
        size <<= 1
    padded = x + [0.0] * (size - len(x))
    re, im = fft(padded)
    return [math.hypot(re[i], im[i]) for i in range(size // 2 + 1)]


# ---------- 任意频率的精确测量（Goertzel） ----------

def goertzel(x: list[float], freq: float, sr: int) -> tuple[float, float]:
    """返回 (幅度, 相位弧度)。"""
    n = len(x)
    w = 2.0 * math.pi * freq / sr
    coeff = 2.0 * math.cos(w)
    s_prev = s_prev2 = 0.0
    for sample in x:
        s = sample + coeff * s_prev - s_prev2
        s_prev2, s_prev = s_prev, s
    power = s_prev2 * s_prev2 + s_prev * s_prev - coeff * s_prev * s_prev2
    mag = math.sqrt(max(power, 0.0)) / (n / 2.0)
    phase = math.atan2(s_prev * math.sin(w), s_prev - s_prev2 * math.cos(w))
    return mag, phase


# ---------- 自相关基频估计 ----------

def autocorrelate(x: list[float]) -> list[float]:
    n = len(x)
    size = 1
    while size < 2 * n:
        size <<= 1
    re, im = fft(x + [0.0] * (size - n))
    power = [re[i] * re[i] + im[i] * im[i] for i in range(size)]
    # 维纳-辛钦：自相关 = IFFT(功率谱)；IFFT = 共轭输入做 FFT 再 /size
    re2, _ = fft(power, [0.0] * size)
    return [re2[i] / size for i in range(n)]


def estimate_f0(x: list[float], sr: int, fmin: float = 50.0,
                fmax: float = 5000.0) -> float:
    """基频估计：候选基频的谐波支持度评分（对敲击噪声与非谐性稳健）。"""
    spec = magnitude_spectrum(x, min_size=1 << 17)
    size = (len(spec) - 1) * 2
    df = sr / size
    i_lo = max(1, int(fmin / df))
    i_hi = min(len(spec) - 1, int(fmax / df))
    if i_hi <= i_lo:
        return float("nan")
    peak_idx = max(range(i_lo, i_hi + 1), key=lambda i: spec[i])
    peak_f = peak_idx * df
    noise = sorted(spec[i_lo:i_hi + 1])[len(range(i_lo, i_hi + 1)) // 4]

    def score(f0: float) -> float:
        total = 0.0
        for h in range(1, 7):
            hf = f0 * h
            if hf > sr / 2:
                break
            ci = int(round(hf / df))
            lo2 = max(i_lo, ci - 2)
            hi2 = min(i_hi, ci + 2)
            local = max(spec[lo2:hi2 + 1])
            total += local / h        # 高次谐波权重递减
        return total

    # 候选：主峰及其 2/3/4 分频
    candidates = [peak_f, peak_f / 2, peak_f / 3, peak_f / 4, peak_f / 5]
    candidates = [f for f in candidates if fmin <= f <= fmax]
    best_f = max(candidates, key=score)
    # 以评分最优候选为中心做一次局部细化
    ci = int(round(best_f / df))
    lo2 = max(i_lo, ci - 3)
    hi2 = min(i_hi, ci + 3)
    local_idx = max(range(lo2, hi2 + 1),
                    key=lambda i: spec[i] - noise * 0.0)
    if 1 <= local_idx < len(spec) - 1:
        a, b, c = spec[local_idx - 1], spec[local_idx], spec[local_idx + 1]
        denom = max(a - 2 * b + c, 1e-12)
        shift = max(-1.0, min(1.0, 0.5 * (a - c) / denom))
    else:
        shift = 0.0
    return (local_idx + shift) * df


# ---------- 峰值拾取 ----------

def spectral_peaks(x: list[float], sr: int, top_k: int = 12,
                   min_freq: float = 40.0) -> list[tuple[float, float]]:
    """返回 (频率 Hz, 幅度) 列表，按幅度降序。"""
    spec = magnitude_spectrum(x, min_size=1 << 17)
    size = (len(spec) - 1) * 2
    df = sr / size
    lo = max(1, int(min_freq / df))
    peaks: list[tuple[float, float]] = []
    for i in range(lo, len(spec) - 1):
        if spec[i] > spec[i - 1] and spec[i] >= spec[i + 1]:
            # 抛物线插值精修频率
            a, b, c = spec[i - 1], spec[i], spec[i + 1]
            denom = max(a - 2 * b + c, 1e-12)
            shift = max(-1.0, min(1.0, 0.5 * (a - c) / denom))
            peaks.append(((i + shift) * df, b))
    peaks.sort(key=lambda p: p[1], reverse=True)
    return peaks[:top_k]


# ---------- 双段衰减包络拟合 ----------

def fit_two_stage_decay(x: list[float], sr: int, frame: int = 256,
                        min_level: float = 0.02) -> dict:
    """拟合 A*exp(-t/tau1) 段 + B*exp(-t/tau2) 段。

    搜索最优段切换点，使两段对数线性拟合残差最小。返回秒为单位的时间常数。
    """
    n_frames = len(x) // frame
    if n_frames < 6:
        return {"tau1": 0.0, "tau2": 0.0, "split_s": 0.0}
    env: list[float] = []
    for f in range(n_frames):
        seg = x[f * frame:(f + 1) * frame]
        env.append(max(abs(v) for v in seg) + 1e-9)
    peak0 = env[0]
    log_env = [math.log(e / peak0) for e in env]
    times = [f * frame / sr for f in range(n_frames)]

    def log_fit(idx: range) -> tuple[float, float, float]:
        m = len(idx)
        sx = sum(times[i] for i in idx)
        sy = sum(log_env[i] for i in idx)
        sxx = sum(times[i] ** 2 for i in idx)
        sxy = sum(times[i] * log_env[i] for i in idx)
        slope = (m * sxy - sx * sy) / max(m * sxx - sx * sx, 1e-12)
        intercept = (sy - slope * sx) / m
        resid = sum((log_env[i] - (slope * times[i] + intercept)) ** 2
                    for i in idx)
        return slope, intercept, resid

    best = None
    for split in range(max(2, n_frames // 8), n_frames - 2):
        s1, i1, r1 = log_fit(range(0, split))
        s2, i2, r2 = log_fit(range(split, n_frames))
        if s1 >= 0 or s2 >= 0:
            continue
        score = r1 + r2
        if best is None or score < best[0]:
            best = (score, s1, s2, times[split])
    if best is None:
        return {"tau1": 0.0, "tau2": 0.0, "split_s": 0.0}
    _, s1, s2, split_t = best
    return {
        "tau1": max(-1.0 / s1, 1e-4),
        "tau2": max(-1.0 / s2, 1e-4),
        "split_s": split_t,
    }


# ---------- 重采样（线性插值，用于 B 路限域移频） ----------

def resample_linear(x: list[float], rate_ratio: float) -> list[float]:
    """按输出/输入采样率比重采样。

    ratio>1（输出采样率更高）时同一段信号需要更多样本 => 时长拉长、音高降低；
    输入长度 n 映射为 round(n * ratio) 个输出点。
    """
    if rate_ratio <= 0:
        raise ValueError("重采样比例必须为正")
    out_len = max(1, int(round(len(x) * rate_ratio)))
    out = [0.0] * out_len
    prev = 0.0
    for i in range(out_len):
        pos = i / rate_ratio
        k = int(pos)
        frac = pos - k
        nxt = x[k + 1] if k + 1 < len(x) else prev
        out[i] = x[k] * (1.0 - frac) + nxt * frac
        prev = nxt
    return out


def rms(x: list[float]) -> float:
    if not x:
        return 0.0
    return math.sqrt(sum(v * v for v in x) / len(x))


# ---------- WAV 读写（8/16/24-bit PCM，多声道自动混缩） ----------

def read_wav(path: str) -> tuple[int, list[float]]:
    with wave.open(path, "rb") as w:
        nch, sw, sr, nframes = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(nframes)
    if sw == 1:
        vals = [b - 128 for b in raw]
        scale = 128.0
    elif sw == 2:
        vals = list(struct.unpack("<%dh" % (len(raw) // 2), raw))
        scale = 32768.0
    elif sw == 3:
        vals = []
        for i in range(0, len(raw), 3):
            b0, b1, b2 = raw[i], raw[i + 1], raw[i + 2]
            v = b0 | (b1 << 8) | (b2 << 16)
            if v & 0x800000:
                v -= 1 << 24
            vals.append(v)
        scale = 8388608.0
    else:
        raise ValueError(f"不支持的采样位宽 {sw} 字节")
    if nch > 1:
        mono = [0.0] * (len(vals) // nch)
        for i, v in enumerate(vals):
            mono[i // nch] += v / nch
        data = mono
    else:
        data = vals
    return sr, [v / scale for v in data]


def write_wav(path: str, sr: int, samples: list[float], bits: int = 16) -> None:
    if bits != 16:
        raise ValueError("本项目仅写 16-bit PCM")
    n = len(samples)
    raw = bytearray(n * 2)
    for i, v in enumerate(samples):
        iv = int(round(max(-1.0, min(1.0, v)) * 32767))
        struct.pack_into("<h", raw, i * 2, iv)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(raw))
