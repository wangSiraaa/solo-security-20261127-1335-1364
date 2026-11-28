"""渲染端：按真值场景画两面相机的窄条时序图。

约定（格坐标）：
* 顶部两行为本场标定条：第一行是非回文色序（正反向可辨）+ 橙色定位，
  第二行是判线脉冲橙格；两面相机物理上对同一条脉冲成像。
* 每条迹是一条竖直色带，横向为色带格，纵向为时间（行）。
* B 相机从相反方向看，正常成像时列序与 A 镜像；回读翻转故障时不再镜像。
* B 所在地面判线相对 A 有可核行数偏移 line_offset_rows。
* 两迹占同一格 -> 灰色混色 X；本迹被遮的一格 -> 黑格 K。
认证端只拿到 PNG 与登记，不接触本模块的真值。
"""
from __future__ import annotations

import json
from pathlib import Path

from .model import (
    BG_COLOR,
    CAL_COLOR,
    CAL_SEQ,
    CELL,
    MASK_COLOR,
    MIX_COLOR,
    PALETTE,
    CameraTruth,
    Scene,
    TraceSpec,
)
from .png_io import write_png


def _image_col(camera: CameraTruth, scene: Scene, physical_col: int) -> int:
    """物理格列 -> 该相机读出的图像列。B 正常为镜像，回读翻转时不镜像。"""
    if camera.name == "A":
        return physical_col
    return physical_col if camera.flipped else scene.width - 1 - physical_col


def _trace_color(spec: TraceSpec, code: list[str], local_j: int) -> str:
    if spec.mask_cell is not None and local_j == spec.mask_cell:
        return "K"
    return code[(local_j + spec.rotation) % len(code)]


def build_grid(scene: Scene, camera: CameraTruth) -> list[list[str]]:
    width, height = scene.width, scene.cal_top_rows + scene.height
    grid = [["W"] * width for _ in range(height)]

    # 标定条第一行固定在物理方向上（非回文色序），不随相机镜像；
    # 这样回读翻转的相机里它才真的是反的，可被认证端检出并还原。
    for c in range(width):
        grid[0][_image_col(camera, scene, c)] = "O"
    for j, key in enumerate(CAL_SEQ):
        grid[0][_image_col(camera, scene, 1 + j)] = key
    # 第二行：判线脉冲固定在物理列 0；翻转相机里它会出现在图像最右侧。
    grid[1][_image_col(camera, scene, 0)] = "O"

    line_shift = scene.line_offset_rows if camera.name == "B" else 0
    occupancy: dict[tuple[int, int], str] = {}
    for spec in camera.traces:
        code = scene.leg_by_id(spec.leg_id).code
        top = scene.cal_top_rows + spec.row0 + line_shift
        for local_j in range(len(code)):
            col = _image_col(camera, scene, spec.col + local_j)
            key = _trace_color(spec, code, local_j)
            for row in range(top, top + spec.length):
                if not (0 <= row < height and 0 <= col < width):
                    continue
                pos = (col, row)
                if pos in occupancy and occupancy[pos] != key:
                    occupancy[pos] = "X"
                else:
                    occupancy[pos] = key
    for (col, row), key in occupancy.items():
        grid[row][col] = key
    return grid


_KEY_RGB = {**PALETTE, "K": MASK_COLOR, "X": MIX_COLOR, "O": CAL_COLOR, "W": BG_COLOR}


def grid_to_pixels(grid: list[list[str]]) -> list[list[tuple[int, int, int]]]:
    pixels: list[list[tuple[int, int, int]]] = []
    for row in grid:
        band = []
        for key in row:
            band.extend([_KEY_RGB[key]] * CELL)
        for _ in range(CELL):
            pixels.append(list(band))
    return pixels


def render_scene(scene: Scene, outdir: str | Path) -> dict[str, str]:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for camera in (scene.camera_a, scene.camera_b):
        grid = build_grid(scene, camera)
        rel = out / f"{scene.scene_id}_cam{camera.name}.png"
        write_png(rel, grid_to_pixels(grid))
        paths[camera.name] = str(rel)
    meta = out / f"{scene.scene_id}_scene.json"
    meta.write_text(json.dumps(_scene_to_dict(scene), ensure_ascii=False, indent=2))
    paths["scene"] = str(meta)
    return paths


def _spec_to_dict(spec: TraceSpec) -> dict:
    return {
        "event_id": spec.event_id,
        "leg_id": spec.leg_id,
        "col": spec.col,
        "row0": spec.row0,
        "length": spec.length,
        "rotation": spec.rotation,
        "mask_cell": spec.mask_cell,
    }


def _scene_to_dict(scene: Scene) -> dict:
    def cam(c: CameraTruth) -> dict:
        return {"name": c.name, "flipped": c.flipped, "traces": [_spec_to_dict(t) for t in c.traces]}

    return {
        "scene_id": scene.scene_id,
        "width": scene.width,
        "height": scene.height,
        "line_offset_rows": scene.line_offset_rows,
        "cal_top_rows": scene.cal_top_rows,
        "legs": [leg.to_dict() for leg in scene.legs],
        "camera_a": cam(scene.camera_a),
        "camera_b": cam(scene.camera_b),
    }
