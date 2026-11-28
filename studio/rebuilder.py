"""离线外发重建器（设计室侧，交付前运行）。

路线选择：A —— 只依据获准的外壳与接口声明重建统一实心替身。
  * 读取完整模型 OBJ 中【被声明】的壳体对象，其余对象（私有夹具、反向面、
    未引用顶点）在进入重建前即被丢弃，不接触、不复制；
  * 壳体重新体素化为统一实心场；声明的公共接口（通孔/凹槽/服务孔）以解析
    SDF 强制开出——这正是路线 A "可能堵住深槽" 的对策：声明即保证；
  * 交付前逐项审计：接口保持、无封闭空腔、闭合单连通、无反向面、无名字、
    无未引用顶点；成品为标准二进制 STL，普通网格读取器可开。
"""
from __future__ import annotations

import json
import math
import os

from geom.grid import mesh_from_grid
from geom.mesh import (coincident_reversed_pairs, component_count, edge_report,
                       read_obj, sha256_file, write_stl_binary)
from geom.slicer import grid_from_mesh
from studio import leakscan

REBUILD_PITCH = 0.35   # 重建体素步长 (mm)
AUDIT_TOL = 0.6        # 接口审计容差 (mm)：体素步长 + 曲面网误差


# ---------------------------------------------------------------- 坐标工具

AXES = {"x": 0, "y": 1, "z": 2}


def _hole_3d(hole):
    """孔声明 -> (axis, c1, c2)：轴线两异轴坐标。"""
    axis = AXES[hole["axis"]]
    return axis, hole["at"][0], hole["at"][1]


def _groove_box(g, bounds):
    """凹槽声明 -> 开凿盒 (x0,y0,z0,x1,y1,z1)。open: 'z+' 等。"""
    (x0, y0, x1, y1) = g["rect"]
    lo, hi = bounds
    face = g["open"]
    bottom = g["bottom"]
    if face == "z+":
        return (x0, y0, bottom, x1, y1, hi[2] + 0.5)
    if face == "z-":
        return (x0, y0, lo[2] - 0.5, x1, y1, bottom)
    raise ValueError("unsupported open face " + face)


# ---------------------------------------------------------------- 场探测

def _field_at(grid, p):
    i = int((p[0] - grid.ox) / grid.dx - 0.5)
    j = int((p[1] - grid.oy) / grid.dy - 0.5)
    k = int((p[2] - grid.oz) / grid.dz - 0.5)
    if 0 <= i < grid.nx and 0 <= j < grid.ny and 0 <= k < grid.nz:
        return grid.field[grid.idx(i, j, k)]
    return 1.0


def _ray_to_solid(grid, p, direction, max_dist=20.0, step=0.1):
    """自 p 沿 direction 步进，返回首次进入实体的距离；未命中返回 None。"""
    d = 0.0
    while d < max_dist:
        q = (p[0] + direction[0] * d, p[1] + direction[1] * d,
             p[2] + direction[2] * d)
        if _field_at(grid, q) < 0:
            return d
        d += step
    return None


def _measure_opening(grid, axis, c1, c2, mid):
    """在轴线上、mid 高度处，沿两异轴量取开口直径。"""
    others = [a for a in (0, 1, 2) if a != axis]
    dias = []
    for o in others:
        p = [0.0, 0.0, 0.0]
        p[axis] = mid
        p[others[0]] = c1
        p[others[1]] = c2
        d = [0.0, 0.0, 0.0]
        d[o] = 1.0
        r1 = _ray_to_solid(grid, tuple(p), tuple(d))
        r2 = _ray_to_solid(grid, tuple(p), tuple(-x for x in d))
        if r1 is None or r2 is None:
            return None
        dias.append(r1 + r2)
    return sum(dias) / len(dias)


def _axis_material_span(grid, axis, c1, c2, r_probe):
    """沿孔轴扫描：返回环探针 r_probe 处≥3/4 方向有料的轴向区间 (lo,hi)；无则 None。"""
    others = [a for a in (0, 1, 2) if a != axis]
    lo = [grid.ox, grid.oy, grid.oz][axis]
    hi = lo + ([grid.nx, grid.ny, grid.nz][axis] - 1) * [grid.dx, grid.dy, grid.dz][axis]
    inside = []
    a = lo + 0.5
    while a < hi - 0.5:
        solid_cnt = 0
        for m in range(8):
            ang = 2 * math.pi * m / 8
            p = [0.0, 0.0, 0.0]
            p[axis] = a
            p[others[0]] = c1 + r_probe * math.cos(ang)
            p[others[1]] = c2 + r_probe * math.sin(ang)
            if _field_at(grid, tuple(p)) < 0:
                solid_cnt += 1
        if solid_cnt >= 6:
            inside.append(a)
        a += 0.2
    if not inside:
        return None
    return (inside[0], inside[-1])


# ---------------------------------------------------------------- 主流程

def rebuild(part_dir, out_dir, pitch=REBUILD_PITCH, verbose=True):
    spec_path = os.path.join(part_dir, "public_spec.json")
    obj_path = os.path.join(part_dir, "full_model.obj")
    with open(spec_path, "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    part = spec["part"]

    # 1) 只提取声明的壳体对象；其余对象不进入重建
    objects, loose = read_obj(obj_path)
    shell = None
    dropped_objs = 0
    for name, mesh in objects:
        if name == spec["shell_object"]:
            shell = mesh
        else:
            dropped_objs += 1
    if shell is None:
        raise RuntimeError("声明的壳体对象 %s 不在模型中" % spec["shell_object"])
    closed, bad = edge_report(shell)
    if not closed:
        raise RuntimeError("声明壳体不闭合 (bad=%d)" % bad)
    if verbose:
        print("  [%s] 提取声明壳体：%d 面；丢弃未声明对象 %d 个、未引用顶点 %d 个"
              % (part, len(shell.tris), dropped_objs, loose))

    # 2) 壳体 -> 统一实心体素场
    grid = grid_from_mesh(shell, pitch, pad=1.5)
    bounds = (grid.ox + 1.5, grid.oy + 1.5, grid.oz + 1.5), \
             (grid.ox + (grid.nx - 1) * grid.dx - 1.5,
              grid.oy + (grid.ny - 1) * grid.dy - 1.5,
              grid.oz + (grid.nz - 1) * grid.dz - 1.5)
    mlo, mhi = bounds

    # 3) 一致性核对：声明接口 vs 壳体实际（开凿前）
    itf = spec["interfaces"]
    consistency = []
    for hole in itf.get("holes", []) + itf.get("service_holes", []):
        axis, c1, c2 = _hole_3d(hole)
        span = _axis_material_span(grid, axis, c1, c2, hole["dia"] / 2.0 + 0.5)
        entry = {"id": hole["id"], "declared_dia": hole["dia"], "shell_dia": None}
        if span is None:
            entry["note"] = "壳体中未见此孔，按声明强制开出"
            consistency.append((entry, True))
            continue
        meas = _measure_opening(grid, axis, c1, c2, (span[0] + span[1]) / 2)
        entry["shell_dia"] = None if meas is None else round(meas, 3)
        if meas is None:
            entry["note"] = "壳体中未见此孔，按声明强制开出"
            consistency.append((entry, True))
        elif abs(meas - hole["dia"]) > 1.0:
            entry["note"] = "声明与壳体不一致"
            consistency.append((entry, False))
        else:
            entry["note"] = "一致"
            consistency.append((entry, True))

    # 4) 按声明强制开出公共接口（解析 SDF，亚体素精度）
    for hole in itf.get("holes", []):
        axis, c1, c2 = _hole_3d(hole)
        grid.carve_sdf_cylinder(axis, c1, c2, hole["dia"] / 2.0,
                                mlo[axis] - 0.5, mhi[axis] + 0.5)
    for hole in itf.get("service_holes", []):
        axis, c1, c2 = _hole_3d(hole)
        grid.carve_sdf_cylinder(axis, c1, c2, hole["dia"] / 2.0,
                                mlo[axis] - 0.5, mhi[axis] + 0.5)
    for g in itf.get("grooves", []):
        grid.carve_sdf_box(_groove_box(g, (mlo, mhi)))

    # 5) 交付前审计
    audits = {"interfaces": [], "consistency": [e for e, _ok in consistency]}
    ok_all = all(ok for _e, ok in consistency)

    for hole in itf.get("holes", []) + itf.get("service_holes", []):
        axis, c1, c2 = _hole_3d(hole)
        # 孔的实际穿料区间（开凿后由环探针确定），审计只在该区间内采样
        span = _axis_material_span(grid, axis, c1, c2, hole["dia"] / 2.0 + 0.5)
        if span is None:
            audits["interfaces"].append(
                {"id": hole["id"], "kind": "hole", "ok": False,
                 "note": "开凿后未找到穿料区间"})
            ok_all = False
            continue
        others = [a for a in (0, 1, 2) if a != axis]
        heights = [span[0] + t * (span[1] - span[0])
                   for t in (0.15, 0.35, 0.5, 0.65, 0.85)]
        open_ok = True
        for h in heights:
            p = [0.0, 0.0, 0.0]
            p[axis] = h
            p[others[0]] = c1
            p[others[1]] = c2
            if _field_at(grid, tuple(p)) < 0:
                open_ok = False
        mid = (span[0] + span[1]) / 2
        ring_ok = True
        for m in range(8):
            ang = 2 * math.pi * m / 8
            p = [0.0, 0.0, 0.0]
            p[axis] = mid
            p[others[0]] = c1 + (hole["dia"] / 2 + 0.5) * math.cos(ang)
            p[others[1]] = c2 + (hole["dia"] / 2 + 0.5) * math.sin(ang)
            if _field_at(grid, tuple(p)) > 0:
                ring_ok = False
        meas = _measure_opening(grid, axis, c1, c2, mid)
        ok = open_ok and ring_ok and meas is not None \
            and abs(meas - hole["dia"]) <= AUDIT_TOL
        audits["interfaces"].append(
            {"id": hole["id"], "kind": "hole", "open": open_ok, "walled": ring_ok,
             "measured_dia": None if meas is None else round(meas, 3),
             "declared_dia": hole["dia"], "ok": ok})
        ok_all = ok_all and ok

    for g in itf.get("grooves", []):
        (x0, y0, x1, y1) = g["rect"]
        depth_ok, open_ok = True, True
        found = []
        for sx in (0.25, 0.5, 0.75):
            for sy in (0.25, 0.5, 0.75):
                px = x0 + sx * (x1 - x0)
                py = y0 + sy * (y1 - y0)
                d = _ray_to_solid(grid, (px, py, mhi[2] + 0.4), (0, 0, -1),
                                  max_dist=mhi[2] - mlo[2] + 1.0)
                if d is None:
                    depth_ok = False
                    continue
                zbot = mhi[2] + 0.4 - d
                found.append(round(zbot, 3))
                if abs(zbot - g["bottom"]) > AUDIT_TOL:
                    depth_ok = False
                # 槽底以上不得有残留（开口通畅）
                d2 = _ray_to_solid(grid, (px, py, mhi[2] + 0.4), (0, 0, -1),
                                   max_dist=mhi[2] + 0.4 - g["bottom"] - AUDIT_TOL)
                if d2 is not None:
                    open_ok = False
        ok = depth_ok and open_ok and len(found) == 9
        audits["interfaces"].append(
            {"id": g["id"], "kind": "groove", "open": open_ok,
             "bottom_z": sorted(set(found)), "declared_bottom": g["bottom"],
             "ok": ok})
        ok_all = ok_all and ok

    for mnt in itf.get("mounts", []):
        axis = AXES[mnt["plane"]["axis"]]
        coord = mnt["plane"]["coord"]
        side = 1.0 if mnt["plane"]["side"] == "+" else -1.0
        others = [a for a in (0, 1, 2) if a != axis]
        (u0, v0, u1, v1) = mnt["region"]
        flat_ok = True
        zs = []
        for su in (0.2, 0.4, 0.6, 0.8):
            for sv in (0.2, 0.4, 0.6, 0.8):
                p = [0.0, 0.0, 0.0]
                p[axis] = coord + side * 0.6
                p[others[0]] = u0 + su * (u1 - u0)
                p[others[1]] = v0 + sv * (v1 - v0)
                # 跳过落在孔盘内的采样点（孔轴与安装面同向时，孔心即平面内 at 坐标）
                in_hole = False
                for hole in itf.get("holes", []):
                    haxis = AXES[hole["axis"]]
                    if haxis != axis:
                        continue
                    du = p[others[0]] - hole["at"][0]
                    dv = p[others[1]] - hole["at"][1]
                    if du * du + dv * dv < (hole["dia"] / 2 + 1.0) ** 2:
                        in_hole = True
                if in_hole:
                    continue
                d = [0.0, 0.0, 0.0]
                d[axis] = -side
                dist = _ray_to_solid(grid, tuple(p), tuple(d), max_dist=1.8)
                if dist is None:
                    flat_ok = False
                    continue
                zf = coord + side * 0.6 - side * dist
                zs.append(round(zf, 3))
                if abs(zf - coord) > AUDIT_TOL:
                    flat_ok = False
        ok = flat_ok and len(zs) >= 8
        audits["interfaces"].append(
            {"id": mnt["id"], "kind": "mount", "flat": flat_ok,
             "plane_z_samples": sorted(set(zs)), "declared": coord, "ok": ok})
        ok_all = ok_all and ok

    voids = grid.enclosed_void_count()
    audits["enclosed_void_voxels"] = voids
    ok_all = ok_all and voids == 0

    # 6) 提取闭合网格并做成品检查
    mesh, style = mesh_from_grid(grid)
    closed, _bad = edge_report(mesh)
    comps = component_count(mesh)
    pairs = coincident_reversed_pairs(mesh)
    audits["mesh"] = {"style": style, "triangles": len(mesh.tris),
                      "closed": closed, "components": comps,
                      "coincident_reversed_pairs": pairs}
    ok_all = ok_all and closed and comps == 1 and pairs == 0

    os.makedirs(out_dir, exist_ok=True)
    stl_path = os.path.join(out_dir, "%s.stl" % part)
    write_stl_binary(mesh, stl_path)

    # 7) 对写出的成品文件再做一次独立泄漏扫描
    scan = leakscan.scan(stl_path)
    ok_all = ok_all and scan["clean"]

    audits["ok"] = ok_all
    audits["sha256"] = sha256_file(stl_path)
    audits["leak_scan"] = {"clean": scan["clean"], "leaks": scan["leaks"]}
    audit_path = os.path.join(out_dir, "%s.audit.json" % part)
    with open(audit_path, "w", encoding="utf-8") as fh:
        json.dump(audits, fh, ensure_ascii=False, indent=2, sort_keys=True)

    if verbose:
        print("  [%s] 体素场 %dx%dx%d@%.2fmm -> %s (%d 面, %s)"
              % (part, grid.nx, grid.ny, grid.nz, pitch,
                 os.path.basename(stl_path), len(mesh.tris), style))
        for it in audits["interfaces"]:
            print("    接口 %-4s %-6s ok=%s" % (it["id"], it["kind"], it["ok"]))
        print("    封闭空腔体素=%d  连通分量=%d  反向面=%d  泄漏扫描=%s"
              % (voids, comps, pairs, "干净" if scan["clean"] else scan["leaks"]))
        print("    审计总评: %s" % ("通过" if ok_all else "未通过"))
    return audits


def rebuild_all(models_root, out_dir):
    results = {}
    for part in sorted(os.listdir(models_root)):
        part_dir = os.path.join(models_root, part)
        if not os.path.isdir(part_dir):
            continue
        print("[重建] %s" % part)
        results[part] = rebuild(part_dir, out_dir)
    return results


if __name__ == "__main__":
    rebuild_all("out/models", "out/outbound")
