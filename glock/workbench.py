"""校音工作台：拍频判断、重调决策、练习伴奏合成。

输入只有外借件 + 两根独立录音 WAV + 校音员后交的参考音与音律目标，
全程离线。判断分两层：
  1. 两根琴条的重合泛音（小整数比）能否维持目标拍频；
  2. 若不能，搜索让失配最小的微调量，指出哪一根须重调、调多少音分。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import synth
from .analysis import BarAnalysis, analyze_bar, compare
from .chords import CHORD_SHAPES, chord_tones
from .dsp import write_wav
from .tuning import Temperament, load_temperament, note_name

# 两泛音视为“重合”的小整数比候选（来自两根琴条在和弦中的泛音列）
SMALL_RATIOS = [(1, 1), (2, 1), (3, 2), (4, 3), (5, 4), (6, 5),
                (3, 1), (5, 3), (5, 2)]
# 各半音音程实际用到的重合比（决定某条重合对是否属于某个和弦）
INTERVAL_RATIOS: dict[int, set[tuple[int, int]]] = {
    0: {(1, 1), (2, 1), (3, 1)},
    3: {(6, 5)},
    4: {(5, 4)},
    5: {(4, 3)},
    6: {(45, 32), (6, 5)},
    7: {(3, 2)},
    8: {(5, 3)},
    10: {(5, 3), (3, 2)},
    11: {(5, 4)},
}
COINCIDE_TOL = 0.01          # 频率比对不上的容忍度 1%
BEAT_TOL_HZ = 1.5            # 目标拍频的可接受误差
MAX_TUNE_CENTS = 40.0        # 现场微调允许范围，超出即判须回厂/换条


@dataclass
class Coincidence:
    bar_a_partial: int
    bar_b_partial: int
    ratio: tuple[int, int]
    freq_a: float
    freq_b: float
    beat_now: float
    beat_target: float
    error_hz: float


@dataclass
class TuningVerdict:
    chord: dict
    achievable: bool
    tune_a_cents: float
    tune_b_cents: float
    which: list[str]
    residual_beat_error: float
    coincidences: list[Coincidence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _coincidences(a: BarAnalysis, b: BarAnalysis,
                  target_beat: dict[tuple[int, int], float],
                  allowed_ratios: set[tuple[int, int]]) -> list[Coincidence]:
    am, bm = a.partial_map(), b.partial_map()
    out: list[Coincidence] = []
    for ia, pa in am.items():
        for ib, pb in bm.items():
            for ra, rb in SMALL_RATIOS:
                if (ra, rb) not in allowed_ratios:
                    continue
                # 重合对必须是“第 ra 号泛音 对 第 rb 号泛音”的精确成员；
                # 高次倍频（如 4:4）会以 (1,1) 在 4 号泛音自身处另行成立，
                # 不在此用整除放宽，避免无关泛音被误计入某个和弦
                if ia != ra or ib != rb:
                    continue
                expected = pb.freq * (ra / rb)
                if abs(pa.freq - expected) / expected > COINCIDE_TOL:
                    continue
                target = target_beat.get((ra, rb), 0.0)
                beat = abs(pa.freq - pb.freq * (ra / rb))
                out.append(Coincidence(
                    bar_a_partial=ia, bar_b_partial=ib, ratio=(ra, rb),
                    freq_a=pa.freq, freq_b=pb.freq, beat_now=beat,
                    beat_target=target, error_hz=beat - target))
                break
    return out


def _beat_mismatch(co: list[Coincidence], da: float, db: float) -> float:
    """两根分别微调 da/db 音分后的总拍频失配（按重合对幅度加权省略，等权）。"""
    total = 0.0
    for c in co:
        fa = c.freq_a * (2.0 ** (da / 1200.0))
        fb = c.freq_b * (2.0 ** (db / 1200.0)) * (c.ratio[0] / c.ratio[1])
        total += (abs(fa - fb) - c.beat_target) ** 2
    return total


def judge_chord(a: BarAnalysis, b: BarAnalysis, root_midi: int, shape: str,
                temperament: Temperament,
                target_beat: dict[tuple[int, int], float] | None = None,
                step: float = 0.5) -> TuningVerdict:
    target_beat = target_beat or {}
    tones = chord_tones(root_midi, shape)
    chord = {"root": root_midi, "root_name": note_name(root_midi),
             "shape": shape, "tones": tones,
             "tone_names": [note_name(t) for t in tones]}
    intervals = CHORD_SHAPES[shape][1:]
    allowed = {(1, 1)}
    for iv in intervals:
        allowed |= INTERVAL_RATIOS.get(iv, set())
    allowed &= set(SMALL_RATIOS)
    co = _coincidences(a, b, target_beat, allowed)
    if not co:
        return TuningVerdict(chord, achievable=False, tune_a_cents=0.0,
                             tune_b_cents=0.0, which=[],
                             residual_beat_error=float("inf"),
                             notes=["两根琴条无重合泛音，无法评估拍频"])

    best = (float("inf"), 0.0, 0.0)
    grid = [v * step for v in range(-int(MAX_TUNE_CENTS / step),
                                    int(MAX_TUNE_CENTS / step) + 1)]
    for da in grid:
        for db in grid:
            m = _beat_mismatch(co, da, db)
            if m < best[0]:
                best = (m, da, db)
    _, da, db = best
    residual = math.sqrt(best[0] / len(co))
    which = []
    notes = []
    if abs(da) >= 0.5:
        which.append(a.name)
    if abs(db) >= 0.5:
        which.append(b.name)
    achievable = residual <= BEAT_TOL_HZ
    if not achievable:
        notes.append(f"最小残余拍频误差 {residual:.2f}Hz 仍超容差 "
                     f"{BEAT_TOL_HZ}Hz，现场微调不足以维持目标拍频")
    if max(abs(da), abs(db)) >= MAX_TUNE_CENTS:
        notes.append("所需微调达到机械极限，建议回厂重调或换条")
    return TuningVerdict(chord, achievable, da, db, which, residual, co, notes)


def chord_uses_partial(shape: str, partial_index: int) -> bool:
    """该和弦的重合泛音对里，第 partial_index 号泛音是否参与。

    (1,1) 同音比只涉及基音本身，不能让所有泛音都算“参与”，故排除。
    """
    allowed = set()
    for iv in CHORD_SHAPES[shape][1:]:
        allowed |= INTERVAL_RATIOS.get(iv, set())
    for ra, rb in allowed & set(SMALL_RATIOS):
        if (ra, rb) == (1, 1):
            continue
        if partial_index % ra == 0 or partial_index % rb == 0:
            return True
    return False


def render_accompaniment(a: BarAnalysis, b: BarAnalysis, verdict: TuningVerdict,
                         temperament: Temperament, out_wav: str,
                         duration: float = 2.4) -> str:
    """按判定结果合成可听练习伴奏：两根按目标音律重定音高后合奏。

    用本地合成短音（与外借库同一合成器），不触碰留厂资产。
    """
    root = verdict.chord["root"]
    tones = verdict.chord["tones"]
    # 两根琴条各承担和弦的一个组成音（最低两音）
    target_a = temperament.target_hz(tones[0])
    target_b = temperament.target_hz(tones[1])
    fa = target_a * (2.0 ** (verdict.tune_a_cents / 1200.0))
    fb = target_b * (2.0 ** (verdict.tune_b_cents / 1200.0))
    va = synth.render_bar(fa, velocity=96, midi=tones[0],
                          duration=duration, seed=tones[0] * 131 + 7,
                          decay_scale=a.decay.get("tau2", 0.7) / 0.75)
    vb = synth.render_bar(fb, velocity=96, midi=tones[1],
                          duration=duration, seed=tones[1] * 131 + 11,
                          decay_scale=b.decay.get("tau2", 0.7) / 0.75)
    n = min(len(va), len(vb))
    mix = [0.5 * (va[i] + vb[i]) for i in range(n)]
    write_wav(out_wav, synth.SR, mix)
    return out_wav


def run_workbench(wav_a: str, wav_b: str, temperament_spec: dict,
                  chord_specs: list[dict],
                  beat_targets: dict[str, dict] | None = None) -> dict:
    """工作台一次完整运行，返回可序列化报告。"""
    temperament = load_temperament(temperament_spec)
    a = analyze_bar(wav_a, "bar_A")
    b = analyze_bar(wav_b, "bar_B")
    beat_targets = beat_targets or {}
    verdicts = []
    for spec in chord_specs:
        bt = {tuple(map(int, k.split(":"))): v
              for k, v in beat_targets.get(spec["shape"], {}).items()}
        v = judge_chord(a, b, spec["root"], spec["shape"], temperament, bt)
        verdicts.append(_verdict_to_dict(v))
    return {
        "temperament": {
            "name": temperament.name,
            "reference_midi": temperament.reference_midi,
            "reference_hz": temperament.reference_hz,
        },
        "bar_a": {"f0": a.f0, "partials": [
            {"index": p.index, "freq": p.freq, "tau": p.tau}
            for p in a.partials]},
        "bar_b": {"f0": b.f0, "partials": [
            {"index": p.index, "freq": p.freq, "tau": p.tau}
            for p in b.partials]},
        "comparison": compare(a, b),
        "verdicts": verdicts,
    }


def _verdict_to_dict(v: TuningVerdict) -> dict:
    return {
        "chord": v.chord,
        "achievable": v.achievable,
        "tune_a_cents": v.tune_a_cents,
        "tune_b_cents": v.tune_b_cents,
        "which_to_retune": v.which,
        "residual_beat_error_hz": v.residual_beat_error,
        "coincidences": [
            {"bar_a_partial": c.bar_a_partial,
             "bar_b_partial": c.bar_b_partial,
             "ratio": list(c.ratio),
             "beat_now_hz": c.beat_now,
             "beat_target_hz": c.beat_target,
             "error_hz": c.error_hz}
            for c in v.coincidences],
        "notes": v.notes,
    }
