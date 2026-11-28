"""外借导出程序：两条路线，输入都是授权后的 LoanedMaterial。

路线 A（公开可重排采样库）
  - 仅导出授权键 × 授权力度层，配 SFZ 映射与 JSON 清单
  - 不输出任何留厂尾音资产；需要余韵时用本地短 WAV 重制
  - 相邻力度层的交叠过渡在授权范围内重新混音；交界处某层被撤，
    对应过渡换材料或删除，其它键/层不受影响
  - 同授权、同源材料导出结果字节确定（用于“两份母库不可区分”证明）

路线 B（培训合约：有限调域连续响应片段）
  - 按合约调域预渲染连续力度响应片段（力度采样更密）
  - 允许在 max_cents_shift 内离线重定音高；超出即拒绝并提示回厂
"""
from __future__ import annotations

import hashlib
import json
import os

from . import synth
from .bank import LoanedMaterial, N_LAYERS
from .chords import CHORD_SHAPES, enumerate_chords
from .dsp import resample_linear, write_wav
from .tuning import note_name, ratio_from_cents


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _mix(a: list[float], b: list[float], wa: float, wb: float) -> list[float]:
    n = min(len(a), len(b))
    return [a[i] * wa + b[i] * wb for i in range(n)]


def _build_bridges(mat: LoanedMaterial, midi: int,
                   layers: list[int]) -> list[dict]:
    """在授权力度层之间重制交叠过渡（只碰授权材料）。"""
    bridges = []
    for li in range(len(layers) - 1):
        l0, l1 = layers[li], layers[li + 1]
        if l1 != l0 + 1:
            continue  # 中间层被撤：旧过渡随撤权一并消失，不做跨缺口拼接
        v0, v1 = synth.LAYER_CENTERS[l0], synth.LAYER_CENTERS[l1]
        mid = (v0 + v1) // 2
        w = (mid - v0) / (v1 - v0)
        blended = _mix(mat.bodies[(midi, l0)], mat.bodies[(midi, l1)],
                       1.0 - w, w)
        bridges.append({
            "file": f"bridge_{l0}_{l1}.wav",
            "velocity": mid,
            "between": [l0, l1],
            "audio": blended,
        })
    return bridges


def export_route_a(mat: LoanedMaterial, out_dir: str) -> dict:
    os.makedirs(os.path.join(out_dir, "samples"), exist_ok=True)
    lic = mat.license
    manifest = {
        "route": "A_public_rearrangeable",
        "sr": mat.sr,
        "license": lic.to_dict(),
        "notes": {},
        "chords": {},
        "hashes": {},
    }
    sfz_lines = ["// 公开可重排采样库（授权范围外的键不存在）"]
    for midi in sorted(lic.notes):
        layers = sorted(lic.notes[midi])
        ndir = os.path.join(out_dir, "samples", str(midi))
        os.makedirs(ndir, exist_ok=True)
        entry = {"name": note_name(midi), "layers": {}, "bridges": []}
        for layer in layers:
            fname = f"{layer}.wav"
            fpath = os.path.join(ndir, fname)
            write_wav(fpath, mat.sr, mat.bodies[(midi, layer)])
            rel = f"samples/{midi}/{fname}"
            manifest["hashes"][rel] = _sha256_file(fpath)
            lo, hi = synth.LAYER_RANGES[layer]
            entry["layers"][str(layer)] = {
                "file": rel, "velocity_lo": lo, "velocity_hi": hi,
            }
            sfz_lines.append(
                f"<region> sample={rel} lokey={midi} hikey={midi} "
                f"lovel={lo} hivel={hi}")
        for br in _build_bridges(mat, midi, layers):
            fpath = os.path.join(ndir, br["file"])
            write_wav(fpath, mat.sr, br["audio"])
            rel = f"samples/{midi}/{br['file']}"
            manifest["hashes"][rel] = _sha256_file(fpath)
            l0, l1 = br["between"]
            entry["bridges"].append({
                "file": rel, "at_velocity": br["velocity"],
                "between": [l0, l1],
            })
        # 重定音高只写在映射层（SFZ tune 单位为音分），不复制留厂尾音
        entry["rearrange"] = {
            "retune_by_cents_supported": True,
            "release_tail_included": False,
        }
        manifest["notes"][str(midi)] = entry

    authorized = set(lic.notes)
    manifest["chords"]["selectable"] = enumerate_chords(authorized)
    with open(os.path.join(out_dir, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    with open(os.path.join(out_dir, "instrument.sfz"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(sfz_lines) + "\n")
    return manifest


# ---------------- 路线 B ----------------

B_VELOCITY_GRID = [24, 48, 72, 96, 120]


def export_route_b(mat: LoanedMaterial, out_dir: str) -> dict:
    os.makedirs(os.path.join(out_dir, "segments"), exist_ok=True)
    lic = mat.license
    lo, hi = lic.contract_range
    manifest = {
        "route": "B_contract_continuous",
        "sr": mat.sr,
        "contract_range": [lo, hi],
        "max_cents_shift": lic.max_cents_shift,
        "license": lic.to_dict(),
        "segments": {},
        "chords": {},
        "hashes": {},
    }
    for midi in sorted(lic.notes):
        if not (lo <= midi <= hi):
            continue
        sdir = os.path.join(out_dir, "segments", str(midi))
        os.makedirs(sdir, exist_ok=True)
        steps = []
        for vel in B_VELOCITY_GRID:
            # 连续力度：用授权层的交叠权重混合；缺层时退到最近授权层
            weights = synth.layer_blend(vel)
            weights = [(li, w) for li, w in weights
                       if lic.layer_authorized(midi, li)]
            if not weights:
                nearest = min(lic.notes[midi],
                              key=lambda li: abs(synth.LAYER_CENTERS[li] - vel))
                weights = [(nearest, 1.0)]
            wsum = sum(w for _, w in weights)
            audio = [0.0] * len(mat.bodies[(midi,
                                             lic.notes[midi][0])])
            for li, w in weights:
                src = mat.bodies[(midi, li)]
                n = min(len(audio), len(src))
                for i in range(n):
                    audio[i] += src[i] * (w / wsum)
            fname = f"v{vel}.wav"
            fpath = os.path.join(sdir, fname)
            write_wav(fpath, mat.sr, audio)
            rel = f"segments/{midi}/{fname}"
            manifest["hashes"][rel] = _sha256_file(fpath)
            steps.append({"velocity": vel, "file": rel})
        manifest["segments"][str(midi)] = {
            "name": note_name(midi),
            "velocity_grid": B_VELOCITY_GRID,
            "steps": steps,
            "reference_pitch_hz": mat.meta["notes"][str(midi)]["base_hz"],
            "baked_cents": mat.meta["notes"][str(midi)]["cents"],
        }
    manifest["chords"]["selectable"] = enumerate_chords(set(lic.notes))
    with open(os.path.join(out_dir, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    return manifest


def export_both(mat: LoanedMaterial, root: str) -> dict:
    a = export_route_a(mat, os.path.join(root, "route_a"))
    b = export_route_b(mat, os.path.join(root, "route_b"))
    return {"route_a": a, "route_b": b}


def retune_segment(segment: list[float], cents_shift: float,
                   max_cents: float) -> list[float]:
    """B 路离线移频（重采样）。超出合约调域时拒绝，须回厂重渲染。"""
    if abs(cents_shift) > max_cents + 1e-9:
        raise PermissionError(
            f"移频 {cents_shift:+.1f} 音分超出合约调域 ±{max_cents:.0f}，"
            "该片段须回厂重渲染")
    # 升 c 音分 => 播放速率乘 2^(c/1200)，同内容缓冲长度按其倒数缩短
    rate_ratio = ratio_from_cents(-cents_shift)
    return resample_linear(segment, rate_ratio)
