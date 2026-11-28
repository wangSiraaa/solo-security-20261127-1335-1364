"""把渲染、视觉、认证串成一条可复读流水线，并支持翻转/裁边两种异常重交。"""
from __future__ import annotations

from pathlib import Path

from .certify import Certification, certify, pair_cameras
from .model import Leg, Scene
from .render import render_scene
from .vision import crop_edge_cells, extract_traces, load_frame


def run_vision(scene: Scene, workdir: str | Path,
               crop: tuple[str, int] | None = None) -> tuple[dict, list, list]:
    paths = render_scene(scene, workdir)
    notes = {"rendered": paths, "crop": crop, "frames": {}}
    frames = []
    for camera in (scene.camera_a, scene.camera_b):
        src = paths[camera.name]
        assume = None
        if crop is not None and camera.name == "B":
            side, cells = crop
            dst = str(Path(workdir) / f"{scene.scene_id}_camB_cropped.png")
            crop_edge_cells(src, dst, side, cells)
            src = dst
            # 裁掉顶部标定条时由交单注明回读方向。
            assume = scene.camera_b.flipped if side == "top" else None
        frame = load_frame(src, camera.name, assume_flip=assume)
        frames.append(frame)
        notes["frames"][camera.name] = {
            "detected_flip": frame.detected_flip,
            "restored": frame.restored,
            "calibration_present": frame.cal_present,
            "pulse_col": frame.pulse_col,
            "grid_size": [frame.width, frame.height],
        }
    traces = [extract_traces(f) for f in frames]
    return notes, traces[0], traces[1]


def certify_scene(scene: Scene, workdir: str | Path,
                  crop: tuple[str, int] | None = None) -> tuple[Certification, dict]:
    notes, traces_a, traces_b = run_vision(scene, workdir, crop)
    events = pair_cameras(traces_a, traces_b, scene.line_offset_rows)
    result = certify(events, scene.legs, scene.line_offset_rows, notes["frames"])
    return result, notes
