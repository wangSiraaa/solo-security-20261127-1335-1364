"""工厂母库与授权模型。

母库磁盘布局（工厂侧，不整体外发）::

    bank/
      meta.json                 # 音域、力度层、键区、音分表
      body/<midi>/<layer>.wav   # 每层力度的原始琴条样本
      tail/<midi>.wav           # 释放尾音资产

授权清单 license.json 只描述“哪些键 × 哪些力度层 × 是否含尾音”可以外借。
本模块的关键纪律：母库可以有两份且保留音不同，但读取授权材料的接口
(open_loaned_materials) 只暴露白名单内容，且不触碰任何留厂资产。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from . import synth
from .dsp import read_wav, write_wav
from .tuning import Temperament, note_name

BANK_MIDI_LO, BANK_MIDI_HI = 55, 84          # G3 .. C6
N_LAYERS = len(synth.LAYER_CENTERS)          # 4


def build_factory_bank(root: str, temperament: Temperament | None = None,
                       variant_tag: str = "A",
                       retained_notes: list[int] | None = None) -> dict:
    """渲染一座完整工厂母库。

    variant_tag / retained_notes 仅影响“保留音”（留厂、不在外借清单上的音），
    用于验证两份母库外借产物不可区分。
    """
    os.makedirs(os.path.join(root, "body"), exist_ok=True)
    os.makedirs(os.path.join(root, "tail"), exist_ok=True)
    temp = temperament or Temperament.preset("equal")
    retained = set(retained_notes or [])
    meta = {
        "kind": "factory_bank",
        "variant": variant_tag,
        "sr": synth.SR,
        "midi_lo": BANK_MIDI_LO,
        "midi_hi": BANK_MIDI_HI,
        "layers": synth.LAYER_CENTERS,
        "layer_ranges": synth.LAYER_RANGES,
        "zones": {z: {"ratios": cfg["ratios"], "tau2": cfg["tau2"]}
                  for z, cfg in synth.ZONES.items()},
        # 工厂侧外发放行表：保留音不在其中，授权清单单方面索要也会被拒
        "released_notes": [],
        # 工厂侧尾音放行表：释放尾音须留厂，默认任何外借都拿不到
        "released_tails": [],
        "notes": {},
    }
    released = []
    for midi in range(BANK_MIDI_LO, BANK_MIDI_HI + 1):
        base_hz = temp.target_hz(midi)
        # 保留音可以带不同的工厂偏差；外借音一律同一张确定性音分表
        if midi in retained:
            c = synth.factory_cents(midi) + (7.0 if variant_tag == "B" else 0.0)
        else:
            c = synth.factory_cents(midi)
        meta["notes"][str(midi)] = {
            "name": note_name(midi),
            "base_hz": base_hz,
            "cents": c,
            "zone": synth.zone_for_midi(midi),
            "retained": midi in retained,
        }
        bdir = os.path.join(root, "body", str(midi))
        os.makedirs(bdir, exist_ok=True)
        for li, vel in enumerate(synth.LAYER_CENTERS):
            audio = synth.render_bar(base_hz, velocity=vel, cents=c, midi=midi,
                                     seed=midi * 1009 + vel)
            write_wav(os.path.join(bdir, f"{li}.wav"), synth.SR, audio)
        tail = synth.render_tail(base_hz, cents=c, midi=midi, seed=midi * 77 + 3)
        write_wav(os.path.join(root, "tail", f"{midi}.wav"), synth.SR, tail)
        if midi not in retained:
            released.append(midi)
    meta["released_notes"] = sorted(released)
    with open(os.path.join(root, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, sort_keys=True)
    return meta


def load_bank_meta(root: str) -> dict:
    with open(os.path.join(root, "meta.json"), encoding="utf-8") as f:
        return json.load(f)


@dataclass
class License:
    """外借授权：notes 为 {midi: 授权力度层集合}；tails 为含尾音的键集合。"""
    name: str
    notes: dict[int, list[int]] = field(default_factory=dict)
    tails: set[int] = field(default_factory=set)
    original_samples: set[int] = field(default_factory=set)
    max_cents_shift: float = 50.0   # B 路允许的离线移频上限
    contract_range: tuple[int, int] = (BANK_MIDI_LO, BANK_MIDI_HI)

    def layer_authorized(self, midi: int, layer: int) -> bool:
        return layer in self.notes.get(midi, [])

    def note_authorized(self, midi: int) -> bool:
        return bool(self.notes.get(midi))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "notes": {str(k): sorted(v) for k, v in sorted(self.notes.items())},
            "tails": sorted(self.tails),
            "original_samples": sorted(self.original_samples),
            "max_cents_shift": self.max_cents_shift,
            "contract_range": list(self.contract_range),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "License":
        return cls(
            name=d["name"],
            notes={int(k): list(v) for k, v in d["notes"].items()},
            tails=set(d.get("tails", [])),
            original_samples=set(d.get("original_samples", [])),
            max_cents_shift=float(d.get("max_cents_shift", 50.0)),
            contract_range=tuple(d.get("contract_range",
                                       [BANK_MIDI_LO, BANK_MIDI_HI])),
        )

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2,
                      sort_keys=True)

    @classmethod
    def load(cls, path: str) -> "License":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def revoke_layer(self, midi: int, layer: int) -> bool:
        """撤掉交界处一层授权；返回是否实际发生变化。"""
        cur = self.notes.get(midi, [])
        if layer not in cur:
            return False
        self.notes[midi] = [x for x in cur if x != layer]
        return True

    def without_layer(self, midi: int, layer: int) -> "License":
        copy = License.from_dict(self.to_dict())
        copy.revoke_layer(midi, layer)
        return copy


@dataclass
class LoanedMaterial:
    """授权检查后实际可打开的外借材料：只含白名单内的样本数据。"""
    meta: dict
    license: License
    bodies: dict          # (midi, layer) -> list[float]
    tails: dict           # midi -> list[float]（仅授权尾音）
    sr: int

    def has(self, midi: int, layer: int) -> bool:
        return (midi, layer) in self.bodies


def open_loaned_materials(root: str, lic: License) -> LoanedMaterial:
    """严格按授权清单从母库读取。任何越权读取都会抛 AccessViolation。"""
    meta = load_bank_meta(root)
    bodies: dict[tuple[int, int], list[float]] = {}
    tails: dict[int, list[float]] = {}
    for midi, layers in lic.notes.items():
        if not (BANK_MIDI_LO <= midi <= BANK_MIDI_HI):
            raise PermissionError(f"音键 {midi} 不在母库范围内")
        if midi not in meta.get("released_notes", []):
            raise PermissionError(f"音键 {midi} 为留厂资产，禁止外借")
        for layer in layers:
            if not (0 <= layer < N_LAYERS):
                raise PermissionError(f"力度层 {layer} 越界")
            p = os.path.join(root, "body", str(midi), f"{layer}.wav")
            _, data = read_wav(p)
            bodies[(midi, layer)] = data
    for midi in lic.tails:
        if not lic.note_authorized(midi):
            raise PermissionError(f"尾音授权必须先有样本授权: {midi}")
        if midi not in meta.get("released_tails", []):
            raise PermissionError(f"该键释放尾音为留厂资产，禁止外借: {midi}")
        _, data = read_wav(os.path.join(root, "tail", f"{midi}.wav"))
        tails[midi] = data
    # original_samples 是更窄的额外许可，这里仅做登记校验，不复制数据路径
    for midi in lic.original_samples:
        if midi not in lic.notes:
            raise PermissionError(f"原采样授权不能脱离样本授权存在: {midi}")
    return LoanedMaterial(meta=meta, license=lic, bodies=bodies, tails=tails,
                          sr=synth.SR)
