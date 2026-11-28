"""独立切片验件台（工坊侧，设计室退出后运行）。

输入：外发 STL + 工坊任务参数（摆放方向、层厚、最小可成型壁厚、两种插销）。
流程：来料检查 -> 定向 -> 逐层实际轮廓切片 -> 壁厚/贴合实测 -> 格点检具
      插销仿真（卡入/就位/取出）-> 可复原分层件 -> 还原复切比对 -> 判定。
判定只依据切片实测，不引用源模型宣布的公差。
"""
from __future__ import annotations

import json
import math
import os

from geom.grid import Grid, mesh_from_grid
from geom.mesh import (ORIENTATIONS, apply_mat, component_count, edge_report,
                       read_stl, sha256_file, write_stl_binary)
from geom.slicer import Bitmap, MeshSlicer, fill_grid_rows
from workshop import fixture

RASTER = 0.2           # 实测位图分辨率 (mm)：0.6mm 开运算半径 = 3 像素，对齐 1.2mm 最小壁厚
RESTORE_PITCH = 0.3    # 分层件还原体素步长 (mm)
FLUSH_LAYERS = 2       # 安装面贴合允许的最大悬空（层）
WALL_THIN_PX_MIN = 150 # 单层薄壁像素报警下限（排除尖角伪报）
IOU_MIN = 0.90         # 还原复切比对下限

AXES = {"x": 0, "y": 1, "z": 2}


# ---------------------------------------------------------------- 坐标工具

def _plane_normal(plane):
    n = [0.0, 0.0, 0.0]
    n[AXES[plane["axis"]]] = 1.0 if plane["side"] == "+" else -1.0
    return tuple(n)


def _plane_point(plane):
    p = [0.0, 0.0, 0.0]
    p[AXES[plane["axis"]]] = plane["coord"]
    return tuple(p)


def _hole_center3d(hole):
    axis = AXES[hole["axis"]]
    others = [a for a in (0, 1, 2) if a != axis]
    p = [0.0, 0.0, 0.0]
    p[others[0]] = hole["at"][0]
    p[others[1]] = hole["at"][1]
    return tuple(p), axis


def _uv_to_3d(plane, u, v):
    p = [0.0, 0.0, 0.0]
    axis = AXES[plane["axis"]]
    others = [a for a in (0, 1, 2) if a != axis]
    p[axis] = plane["coord"]
    p[others[0]] = u
    p[others[1]] = v
    return tuple(p)


def _point_in_interface_void(spec, p, bounds):
    """采样点是否落在某个声明接口的空腔里（孔盘/槽腔，含余量）。"""
    itf = spec["interfaces"]
    for hole in itf.get("holes", []) + itf.get("service_holes", []):
        c, axis = _hole_center3d(hole)
        d2 = 0.0
        for a in (0, 1, 2):
            if a == axis:
                continue
            d2 += (p[a] - c[a]) ** 2
        if d2 < (hole["dia"] / 2 + 0.8) ** 2:
            return True
    for g in itf.get("grooves", []):
        (x0, y0, x1, y1) = g["rect"]
        if g["open"] == "z+":
            box = (x0 - 0.5, y0 - 0.5, g["bottom"] - 0.5,
                   x1 + 0.5, y1 + 0.5, bounds[1][2] + 1.0)
        else:
            box = (x0 - 0.5, y0 - 0.5, bounds[0][2] - 1.0,
                   x1 + 0.5, y1 + 0.5, g["bottom"] + 0.5)
        if (box[0] <= p[0] <= box[3] and box[1] <= p[1] <= box[4]
                and box[2] <= p[2] <= box[5]):
            return True
    return False


# ---------------------------------------------------------------- 各项实测

def check_walls(bitmaps, min_wall_mm):
    """逐层薄壁检测：材料经 r=min_wall/2 开运算后消失的区域为薄壁。"""
    n = max(1, int(round(min_wall_mm / 2.0 / RASTER)))
    worst = {"thin_px": 0, "layer": -1}
    flagged = []
    for i, bm in enumerate(bitmaps):
        mat = bm.count()
        if mat == 0:
            continue
        thin = bm.andnot(bm.opening(n)).count()
        limit = max(WALL_THIN_PX_MIN, int(0.003 * mat))
        if thin > limit:
            flagged.append({"layer": i, "thin_px": thin, "material_px": mat})
        if thin > worst["thin_px"]:
            worst = {"thin_px": thin, "layer": i}
    return {"ok": not flagged, "erode_px": n, "worst": worst,
            "flagged_layers": flagged[:8], "n_flagged": len(flagged)}


def check_flush(bitmaps, ox, oy, mount, spec, mat, off, bounds):
    """安装面贴合：声明安装区采样点在首 FLUSH_LAYERS 层内应有料。"""
    (u0, v0, u1, v1) = mount["region"]
    total, good = 0, 0
    u = u0 + 1.0
    while u <= u1 - 1.0:
        v = v0 + 1.0
        while v <= v1 - 1.0:
            p = _uv_to_3d(mount["plane"], u, v)
            if not _point_in_interface_void(spec, p, bounds):
                q = apply_mat(mat, p)
                q = (q[0] + off[0], q[1] + off[1], q[2] + off[2])
                i = int((q[0] - ox) / RASTER)
                j = int((q[1] - oy) / RASTER)
                hit = False
                for k in range(min(FLUSH_LAYERS, len(bitmaps))):
                    if bitmaps[k].get(i, j):
                        hit = True
                        break
                total += 1
                good += 1 if hit else 0
            v += 2.0
        u += 2.0
    frac = good / total if total else 0.0
    return {"ok": frac >= 0.9, "samples": total, "contact": good,
            "fraction": round(frac, 4)}


# ---------------------------------------------------------------- 分层件

def write_layerpack(path, part, src_stl, src_sha, orientation, layer_h, slicer):
    layers = []
    for k in range(slicer.nz):
        loops, _d = slicer.loops_at(k)
        layers.append({"z": round(slicer.z_of(k), 4),
                       "loops": [[[round(x, 4), round(y, 4)] for (x, y) in poly]
                                 for (poly, _a) in loops]})
    pack = {"part": part, "source_stl": src_stl, "source_sha256": src_sha,
            "orientation": orientation, "layer_mm": layer_h, "layers": layers}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(pack, fh)
    return pack


def restore_layerpack(pack):
    """分层件 -> 闭合实体网格（体素堆叠 + 曲面网）。可复原性的核心。
    栅格原点对齐层边界：层 k 恰好占据体素层 k+3，还原实体 z 范围即 [0, 层数*层厚]。"""
    xs, ys = [], []
    for layer in pack["layers"]:
        for poly in layer["loops"]:
            for (x, y) in poly:
                xs.append(x)
                ys.append(y)
    layer_h = pack["layer_mm"]
    px, py = RESTORE_PITCH, RESTORE_PITCH
    ox = math.floor((min(xs) - 1.0) / px) * px
    oy = math.floor((min(ys) - 1.0) / py) * py
    oz = -3.0 * layer_h
    nx = int(math.ceil((max(xs) + 1.0 - ox) / px)) + 1
    ny = int(math.ceil((max(ys) + 1.0 - oy) / py)) + 1
    nz = len(pack["layers"]) + 6
    grid = Grid(nx, ny, nz, (ox, oy, oz), (px, py, layer_h))
    for k, layer in enumerate(pack["layers"]):
        loops = [(poly, 0.0) for poly in layer["loops"]]
        if not loops:
            continue
        kz = int(round((layer["z"] - oz) / layer_h - 0.5))
        fill_grid_rows(grid, kz, loops)
    mesh, style = mesh_from_grid(grid)
    return mesh, style


def restore_iou(pack, restored_mesh):
    """还原件按原层厚复切，与分层件逐层比对（位图交并比）。"""
    slicer = MeshSlicer(restored_mesh, pack["layer_mm"], z0=0.0,
                        nz=len(pack["layers"]))
    xs, ys = [], []
    for layer in pack["layers"]:
        for poly in layer["loops"]:
            for (x, y) in poly:
                xs.append(x)
                ys.append(y)
    ox, oy = min(xs) - 1.0, min(ys) - 1.0
    nx = int((max(xs) - min(xs) + 2.0) / RASTER) + 1
    ny = int((max(ys) - min(ys) + 2.0) / RASTER) + 1
    worst = 1.0
    for k, layer in enumerate(pack["layers"]):
        loops0 = [(poly, 0.0) for poly in layer["loops"]]
        bm0 = Bitmap.from_loops(loops0, ox, oy, RASTER, nx, ny)
        loops1, _d = slicer.loops_at(k)
        bm1 = Bitmap.from_loops(loops1, ox, oy, RASTER, nx, ny)
        inter = bm0.intersect(bm1).count()
        union = bm0.union(bm1).count()
        iou = inter / union if union else 1.0
        worst = min(worst, iou)
    return worst


# ---------------------------------------------------------------- 主流程

def run_job(job_path, verbose=True):
    with open(job_path, "r", encoding="utf-8") as fh:
        job = json.load(fh)
    name = os.path.splitext(os.path.basename(job_path))[0].replace(".job", "")
    out_dir = os.path.join("out", "workshop")
    os.makedirs(out_dir, exist_ok=True)

    rep = {"job": name, "decision": "REJECT", "reasons": []}

    def log(msg):
        if verbose:
            print(msg)

    def reject(reason):
        rep["reasons"].append(reason)
        log("  [拒收] " + reason)

    log("[验件] 任务 %s" % name)

    # ---- 来料检查
    stl_path = job["stl"]
    mesh = read_stl(stl_path)
    closed, bad = edge_report(mesh)
    comps = component_count(mesh)
    rep["incoming"] = {"closed": closed, "components": comps,
                       "triangles": len(mesh.tris)}
    log("  来料 %s：%d 面，闭合=%s，分量=%d"
        % (os.path.basename(stl_path), len(mesh.tris), closed, comps))
    if not closed or comps != 1:
        reject("来料网格不闭合或不单连通")
        _finish(rep, out_dir, name)
        return rep

    with open(job["spec"], "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    itf = spec["interfaces"]
    mount = next(m for m in itf["mounts"] if m["id"] == job["mount"])

    # ---- 定向与安装面就位
    mat = ORIENTATIONS[job["orientation"]]
    n_model = _plane_normal(mount["plane"])
    n_rot = apply_mat(mat, n_model)
    if any(abs(a - b) > 1e-6 for a, b in zip(n_rot, (0, 0, -1))):
        reject("摆放方向未使安装面 %s 朝下" % mount["id"])
        _finish(rep, out_dir, name)
        return rep
    p_rot = apply_mat(mat, _plane_point(mount["plane"]))
    off = (0.0, 0.0, -p_rot[2])
    mesh = mesh.transformed(mat, off)
    bounds = mesh.bounds()

    # ---- 孔位 -> 检具格点
    holes = {h["id"]: h for h in itf["holes"]}
    pins = []
    for pin_decl in job["pins"]:
        hole = holes[pin_decl["hole"]]
        c, _axis = _hole_center3d(hole)
        q = apply_mat(mat, c)
        fx, fy = q[0] + off[0], q[1] + off[1]
        gx, gy = round(fx / fixture.GRID_PITCH), round(fy / fixture.GRID_PITCH)
        err = math.hypot(fx - gx * fixture.GRID_PITCH, fy - gy * fixture.GRID_PITCH)
        if err > 0.25:
            reject("孔 %s 未落在检具格点上（偏差 %.2fmm）" % (hole["id"], err))
            continue
        pins.append({"hole": hole["id"], "type": pin_decl["type"],
                     "x": gx * fixture.GRID_PITCH, "y": gy * fixture.GRID_PITCH,
                     "declared_dia": hole["dia"]})
    if len(pins) != len(job["pins"]):
        _finish(rep, out_dir, name)
        return rep

    # ---- 逐层实际轮廓
    layer_h = job["layer_mm"]
    slicer = MeshSlicer(mesh, layer_h, z0=0.0)
    all_loops = []
    dropped = 0
    for k in range(slicer.nz):
        loops, d = slicer.loops_at(k)
        dropped += d
        all_loops.append(loops)
    rep["slices"] = {"layers": slicer.nz, "dropped_loops": dropped}
    log("  切片 %d 层（层厚 %.2fmm），丢弃残段 %d" % (slicer.nz, layer_h, dropped))
    if dropped:
        reject("切片存在无法闭合的轮廓")

    # ---- 位图化
    ox, oy = bounds[0][0] - 1.0, bounds[0][1] - 1.0
    nx = int((bounds[1][0] - bounds[0][0] + 2.0) / RASTER) + 1
    ny = int((bounds[1][1] - bounds[0][1] + 2.0) / RASTER) + 1
    bitmaps = [Bitmap.from_loops(loops, ox, oy, RASTER, nx, ny)
               for loops in all_loops]

    # ---- 最小可成型壁厚（实测，随摆放方向而变）
    wall = check_walls(bitmaps, job["min_wall_mm"])
    rep["wall"] = wall
    if wall["ok"]:
        log("  壁厚实测：通过（最小壁厚 %.2fmm，开运算半径 %d 像素）"
            % (job["min_wall_mm"], wall["erode_px"]))
    else:
        reject("薄壁实测不通过：%d 层越限（最严重第 %d 层，薄壁 %d 像素）"
               % (wall["n_flagged"], wall["worst"]["layer"], wall["worst"]["thin_px"]))

    # ---- 安装面贴合（实测）
    flush = check_flush(bitmaps, ox, oy, mount, spec, mat, off, bounds)
    rep["flush"] = flush
    if flush["ok"]:
        log("  安装面贴合：%.1f%% 采样点接触" % (100 * flush["fraction"]))
    else:
        reject("安装面贴合不足：仅 %.1f%%" % (100 * flush["fraction"]))

    # ---- 格点检具插销（实测；不读声明公差）
    pin_reps = []
    for pin in pins:
        pr = fixture.simulate_pin(all_loops, layer_h,
                                  pin["x"], pin["y"], pin["type"])
        pr["hole"] = pin["hole"]
        pr["measured_dia"] = fixture.measured_hole_dia(
            all_loops, layer_h, pin["x"], pin["y"])
        pr["declared_dia"] = pin["declared_dia"]
        pin_reps.append(pr)
        state = "合格" if pr["ok"] else "不合格"
        log("  插销 %s(%s)：插入干涉 %.2f / 就位 %.2f / 取出 %.2f -> %s"
            % (pin["hole"], pin["type"], pr["insert_max_violation"],
               pr["seated_max_violation"], pr["remove_max_violation"], state))
        log("      实测孔径 %.2fmm（声明 %.2fmm，仅供参考不作判定）"
            % (pr["measured_dia"] or -1, pin["declared_dia"]))
        if not pr["ok"]:
            reject("插销 %s 仿真不合格" % pin["hole"])
    rep["pins"] = pin_reps

    # ---- 可复原分层件
    pack_path = os.path.join(out_dir, "%s.layers.json" % name)
    src_sha = sha256_file(stl_path)
    pack = write_layerpack(pack_path, job["part"], stl_path, src_sha,
                           job["orientation"], layer_h, slicer)
    restored, style = restore_layerpack(pack)
    rclosed, _rb = edge_report(restored)
    rst_path = os.path.join(out_dir, "%s.restored.stl" % name)
    write_stl_binary(restored, rst_path)
    iou = restore_iou(pack, restored)
    rep["restore"] = {"style": style, "closed": rclosed, "min_iou": round(iou, 4),
                      "file": rst_path}
    log("  分层件 %s：还原闭合=%s，复切最小 IoU=%.3f"
        % (os.path.basename(pack_path), rclosed, iou))
    if not rclosed or iou < IOU_MIN:
        reject("分层件不可复原（闭合=%s, IoU=%.3f）" % (rclosed, iou))

    # ---- 检具可视化
    fx_mesh = fixture.build_fixture_mesh(
        [(p["x"], p["y"], p["type"]) for p in pins])
    fx_path = os.path.join(out_dir, "%s.fixture.stl" % name)
    write_stl_binary(fx_mesh, fx_path)

    if not rep["reasons"]:
        rep["decision"] = "SHIP"
        log("  [判定] 放行（SHIP）")
    else:
        log("  [判定] 拒收（REJECT）：%s" % "；".join(rep["reasons"]))
    _finish(rep, out_dir, name)
    return rep


def _finish(rep, out_dir, name):
    path = os.path.join(out_dir, "%s.report.json" % name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    import sys
    for jp in sys.argv[1:]:
        run_job(jp)
