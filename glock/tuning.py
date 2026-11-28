"""音高与音律数学。

整个项目只依赖 Python 标准库。频率单位 Hz，音程差单位 cent（音分）。
非等分音律以十二半音音分表表达，参考音（通常 A4=440Hz）可被校音员现场重定。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# 常见音律：每半音相对等分律的音分偏移（长度 12）
TEMPERAMENTS = {
    # 等分十二平均律
    "equal": [0.0] * 12,
    # 纯律（C 大调，五度 3:2 / 大三度 5:4），偏移按 C 起点折算
    "just": [
        0.0,
        1200 * math.log2(16 / 15) - 100,
        1200 * math.log2(9 / 8) - 200,
        1200 * math.log2(6 / 5) - 300,
        1200 * math.log2(5 / 4) - 400,
        1200 * math.log2(4 / 3) - 500,
        1200 * math.log2(45 / 32) - 600,
        1200 * math.log2(3 / 2) - 700,
        1200 * math.log2(8 / 5) - 800,
        1200 * math.log2(5 / 3) - 900,
        1200 * math.log2(9 / 5) - 1000,
        1200 * math.log2(15 / 8) - 1100,
    ],
    # 四分之一逗号中庸全音律（五度窄 5.38 音分）
    "meantone": [0.0] * 12,
}

# 中庸全音律音分表：以 C 为基准按五度链生成
_MEANTONE_FIFTH_CENTS = 1200 * math.log2(3 / 2) - 5.37657
_MT_CHAIN = {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6,
             1: -5, 8: -4, 3: -3, 10: -2, 5: -1}
for _pc, _fifths in _MT_CHAIN.items():
    _c = _fifths * _MEANTONE_FIFTH_CENTS - 700 * _fifths
    TEMPERAMENTS["meantone"][_pc] = _c


def cents(f1: float, f2: float) -> float:
    """f2 相对 f1 的音分差。"""
    return 1200.0 * math.log2(f2 / f1)


def ratio_from_cents(c: float) -> float:
    return 2.0 ** (c / 1200.0)


def note_name(midi: int) -> str:
    pc = midi % 12
    octave = midi // 12 - 1
    return f"{NOTE_NAMES[pc]}{octave}"


def name_to_midi(name: str) -> int:
    if name in ("", None):
        raise ValueError("空音名")
    if len(name) < 2:
        raise ValueError(f"无法解析音名: {name!r}")
    pc_part = name[:-1]
    if pc_part not in NOTE_NAMES:
        raise ValueError(f"未知音级: {name!r}")
    octave = int(name[-1])
    return NOTE_NAMES.index(pc_part) + 12 * (octave + 1)


@dataclass(frozen=True)
class Temperament:
    """一个具体可用的非等分（或等分）音律。"""
    name: str
    offsets_cents: tuple[float, ...]   # 长度 12，相对等分律
    reference_midi: int = 69           # 参考音 MIDI（默认 A4）
    reference_hz: float = 440.0        # 参考音频率（校音员可重定）

    @classmethod
    def preset(cls, name: str, reference_hz: float = 440.0,
               reference_midi: int = 69) -> "Temperament":
        if name not in TEMPERAMENTS:
            raise KeyError(f"未知音律 {name!r}，可选: {sorted(TEMPERAMENTS)}")
        return cls(name, tuple(TEMPERAMENTS[name]), reference_midi, reference_hz)

    def target_hz(self, midi: int) -> float:
        """该音律下某个 MIDI 音的目标基频。"""
        semis = midi - self.reference_midi
        pc = midi % 12
        # 参考音本身不套偏移（偏移体系以参考音所在半音为 0 折算）
        ref_pc = self.reference_midi % 12
        offset = self.offsets_cents[pc] - self.offsets_cents[ref_pc]
        return self.reference_hz * ratio_from_cents(100.0 * semis + offset)


def load_temperament(spec: dict) -> Temperament:
    """从 JSON 风格字典构造：可指定预设或 12 个自定义音分。"""
    ref_midi = spec.get("reference_midi", 69)
    ref_hz = float(spec.get("reference_hz", 440.0))
    if "offsets_cents" in spec:
        offs = tuple(float(x) for x in spec["offsets_cents"])
        if len(offs) != 12:
            raise ValueError("offsets_cents 必须是 12 个数")
        return Temperament(spec.get("name", "custom"), offs, ref_midi, ref_hz)
    return Temperament.preset(spec.get("name", "equal"), ref_hz, ref_midi)
