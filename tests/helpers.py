"""测试共享夹具：工厂母库、外借授权、两根独立录音练习琴条。"""
from __future__ import annotations

import os
import tempfile

from glock import bank, synth
from glock.dsp import write_wav
from glock.tuning import Temperament

# 授权键（覆盖三个键区中的低/中区）
LICENSED_NOTES = [55, 60, 62, 64, 65, 67, 69, 72]
RETAINED_NOTES = [76, 79]          # 两份母库只在这些保留音上不同
LAYERS_ALL = [0, 1, 2, 3]

# 练习琴条：整数谐比泛音列，便于构造可预测的重合泛音与拍频
BAR_A_RATIOS = {1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0}
BAR_B_RATIOS = {1: 1.0, 2: 2.0, 3: 3.0}
BAR_B_RATIOS_SWAP2 = {1: 1.0, 2: 2.06, 3: 3.0}   # 第二泛音偏移样本


def make_license(max_shift: float = 50.0,
                 partial: dict[int, list[int]] | None = None) -> bank.License:
    notes = {m: list(LAYERS_ALL) for m in LICENSED_NOTES}
    if partial:
        for m, drop in partial.items():
            notes[m] = [x for x in notes[m] if x not in drop]
    return bank.License(
        name="training_room_loan",
        notes=notes,
        tails=set(),                       # 释放尾音一律留厂
        original_samples=set(),            # 原采样不授权
        max_cents_shift=max_shift,
        contract_range=(55, 72),
    )


def build_workspace(root: str | None = None) -> str:
    """渲染两座工厂母库与练习琴条；root 给定时确定性复用，不重复渲染。"""
    import tempfile
    if root is None:
        holder = tempfile.TemporaryDirectory()
        root = holder.name
        holders.append(holder)
    if (os.path.exists(os.path.join(root, "factoryA", "meta.json"))
            and os.path.exists(os.path.join(root, "bars", "barA.wav"))
            and os.path.exists(os.path.join(root, "bars", "barB.wav"))):
        return root
    equal = Temperament.preset("equal")
    bank.build_factory_bank(os.path.join(root, "factoryA"),
                            temperament=equal, variant_tag="A",
                            retained_notes=RETAINED_NOTES)
    bank.build_factory_bank(os.path.join(root, "factoryB"),
                            temperament=equal, variant_tag="B",
                            retained_notes=RETAINED_NOTES)
    _render_bars(root)
    return root


_CACHE = os.path.join(tempfile.gettempdir(), "glock_test_workspace")
holders: list = []


def build_once() -> "tempfile.TemporaryDirectory":
    """整套测试共享的一次性母库（放在固定临时目录，重复运行不重渲染）。"""

    class _Holder:
        def __init__(self, name):
            self.name = name

    build_workspace(_CACHE)
    return _Holder(_CACHE)


def BUILD_ROOT(root: str, *parts: str) -> str:
    return os.path.join(root, *parts)


def _render_bars(root: str) -> None:
    bardir = os.path.join(root, "bars")
    os.makedirs(bardir, exist_ok=True)
    # 两根独立录音：基音接近（B 比 A 高约 12 音分），力度响应、衰减不同
    a = synth.render_bar(220.0, velocity=72, midi=57, duration=2.2,
                         ratio_override=BAR_A_RATIOS, seed=991,
                         decay_scale=1.15)
    b = synth.render_bar(220.0 * (2 ** (12 / 1200)), velocity=108, midi=57,
                         duration=2.0, ratio_override=BAR_B_RATIOS, seed=417,
                         decay_scale=0.8)
    b_swap = synth.render_bar(220.0 * (2 ** (12 / 1200)), velocity=108,
                              midi=57, duration=2.0,
                              ratio_override=BAR_B_RATIOS_SWAP2, seed=417,
                              decay_scale=0.8)
    write_wav(os.path.join(bardir, "barA.wav"), synth.SR, a)
    write_wav(os.path.join(bardir, "barB.wav"), synth.SR, b)
    write_wav(os.path.join(bardir, "barB_swap2.wav"), synth.SR, b_swap)


def bar_paths(root: str) -> dict[str, str]:
    d = os.path.join(root, "bars")
    return {
        "a": os.path.join(d, "barA.wav"),
        "b": os.path.join(d, "barB.wav"),
        "b_swap": os.path.join(d, "barB_swap2.wav"),
    }


# 校音员后交的参考音与非等分音律目标
def temperament_spec(name: str = "meantone", ref_hz: float = 219.5) -> dict:
    return {"name": name, "reference_midi": 57, "reference_hz": ref_hz}


TEST_CHORDS = [
    {"root": 57, "shape": "maj"},     # 受影响：含 3:2 / 5:4（第二泛音参与）
    {"root": 57, "shape": "min"},     # 受影响：含 3:2
    {"root": 57, "shape": "aug"},     # 不受影响：只有 5:4 / 5:3，不涉及 2 号泛音
    {"root": 57, "shape": "dim"},     # 不受影响：只有 6:5
]
