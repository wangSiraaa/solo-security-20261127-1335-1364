"""设计室建模器：程序化生成本次演练的全部教具。

每个教具产出：
  full_model.obj   —— 完整模型（公开壳体 + 私有夹具对象 + 同坐标反向面 + 未引用顶点）
  public_spec.json —— 公开规格（壳体对象名 + 获准外发的接口声明：安装面/通孔/凹槽/服务孔）

私有内构只出现在 OBJ 的未声明对象里；公开规格是外发重建的唯一依据。
"""
from __future__ import annotations

import json
import math
import os
import random

from geom.grid import Grid, mesh_from_grid
from geom.mesh import Mesh, edge_report, write_obj

STUDIO_PITCH = 0.25  # 设计室建模体素步长 (mm)


# ---------------------------------------------------------------- 基础网格图元

def box_mesh(x0, y0, z0, x1, y1, z1):
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    t = [(0, 2, 1), (0, 3, 2),
         (4, 5, 6), (4, 6, 7),
         (0, 1, 5), (0, 5, 4),
         (2, 3, 7), (2, 7, 6),
         (0, 4, 7), (0, 7, 3),
         (1, 2, 6), (1, 6, 5)]
    return Mesh(v, t)


def cylinder_mesh(axis, c1, c2, r, a0, a1, n=24):
    """轴对齐圆柱闭合网格。(c1,c2)：其余两轴圆心坐标（axis=0->(y,z), 1->(x,z), 2->(x,y)）。"""
    others = [a for a in (0, 1, 2) if a != axis]
    verts = []

    def put(a, b, c):
        p = [0.0, 0.0, 0.0]
        p[axis] = a
        p[others[0]] = b
        p[others[1]] = c
        return tuple(p)

    ring0, ring1 = [], []
    for i in range(n):
        ang = 2.0 * math.pi * i / n
        ca, sa = math.cos(ang), math.sin(ang)
        ring0.append(len(verts))
        verts.append(put(a0, c1 + r * ca, c2 + r * sa))
        ring1.append(len(verts))
        verts.append(put(a1, c1 + r * ca, c2 + r * sa))
    cb = len(verts)
    verts.append(put(a0, c1, c2))
    ct = len(verts)
    verts.append(put(a1, c1, c2))
    tris = []
    for i in range(n):
        j = (i + 1) % n
        tris.append((ring0[i], ring0[j], ring1[j]))
        tris.append((ring0[i], ring1[j], ring1[i]))
        tris.append((cb, ring0[j], ring0[i]))
        tris.append((ct, ring1[i], ring1[j]))
    mesh = Mesh(verts, tris)
    if axis == 1:  # (x,z) 映射为奇排列，翻转回外法线
        from geom.mesh import flip_orientation
        mesh = flip_orientation(mesh)
    return mesh


def reversed_copy(mesh):
    from geom.mesh import flip_orientation
    return Mesh(list(mesh.verts), list(mesh.tris)), flip_orientation(mesh)


def junk_verts(seed, count, box):
    rnd = random.Random(seed)
    (x0, y0, z0, x1, y1, z1) = box
    return [(rnd.uniform(x0, x1), rnd.uniform(y0, y1), rnd.uniform(z0, z1))
            for _ in range(count)]


# ---------------------------------------------------------------- CSG 壳体

def build_shell(solids, voids, bounds):
    lo, hi = bounds
    grid = Grid.for_bounds(lo, hi, STUDIO_PITCH, pad=1.0)
    for s in solids:
        kind = s[0]
        if kind == "box":
            grid.fill_box(s[1], solid=True)
        elif kind == "cyl":
            grid.fill_cylinder(s[1], s[2], s[3], s[4], s[5], s[6], solid=True)
    for v in voids:
        kind = v[0]
        if kind == "box":
            grid.fill_box(v[1], solid=False)
        elif kind == "cyl":
            grid.fill_cylinder(v[1], v[2], v[3], v[4], v[5], v[6], solid=False)
    shell, style = mesh_from_grid(grid)
    closed, bad = edge_report(shell)
    if not closed:
        raise RuntimeError("壳体不闭合 bad=%d" % bad)
    return shell


# ---------------------------------------------------------------- 教具 1：滑槽演示块（4 个变体）

AID1_BOUNDS = ((-1.0, -1.0, -1.0), (61.0, 41.0, 31.0))


def aid1_csg(snap_dia=6.2, service_hole=False):
    solids = [("box", (0, 0, 0, 60, 40, 30))]
    voids = [
        ("box", (-1, 8, 22, 61, 18, 31)),                    # 顶部通槽（可触及凹槽）
        ("cyl", 2, 15.0, 30.0, 3.25, -1.0, 31.0),           # H1 直销孔
        ("cyl", 2, 45.0, 30.0, snap_dia / 2.0, -1.0, 31.0),  # H2 卡销孔
    ]
    if service_hole:
        voids.append(("cyl", 0, 36.0, 15.0, 2.0, -1.0, 61.0))  # 获准对外的服务孔
    return solids, voids


def aid1_spec(part="aid1", snap_dia=6.2, service_hole=False):
    spec = {
        "part": part, "units": "mm", "shell_object": "PUBLIC_SHELL",
        "interfaces": {
            "holes": [
                {"id": "H1", "axis": "z", "at": [15.0, 30.0], "dia": 6.5},
                {"id": "H2", "axis": "z", "at": [45.0, 30.0], "dia": snap_dia},
            ],
            "grooves": [
                {"id": "G1", "kind": "slot", "open": "z+",
                 "rect": [0.0, 8.0, 60.0, 18.0], "bottom": 22.0},
            ],
            "service_holes": [],
            "mounts": [
                {"id": "M1",
                 "plane": {"axis": "z", "coord": 0.0, "side": "-"},
                 "region": [2.0, 2.0, 58.0, 38.0],
                 "holes": ["H1", "H2"]},
            ],
        },
    }
    if service_hole:
        spec["interfaces"]["service_holes"].append(
            {"id": "S1", "axis": "x", "at": [36.0, 15.0], "dia": 4.0})
    return spec


def aid1_private(variant):
    """各变体的私有内构（绝不进入公开规格）。"""
    objs = []
    if variant in ("A", "C", "E"):
        cavity = box_mesh(20, 20, 8, 40, 30, 18)            # 内部薄壳：封闭内腔
        rib = box_mesh(22, 24.5, 9, 38, 25.5, 17)           # 内部加强肋
        _keep, rib_mirror = reversed_copy(rib)              # 同坐标反向面
        objs.append(("JIG_internal_cavity", cavity))
        objs.append(("JIG_rib", rib))
        objs.append(("JIG_rib_mirror", rib_mirror))
        if variant == "A":
            # 内部校准通道（全封闭于实体内，未获准对外）
            objs.append(("calibration_channel",
                         cylinder_mesh(0, 36.0, 15.0, 2.0, 15.0, 45.0)))
        if variant == "E":
            objs.append(("calibration_channel",
                         cylinder_mesh(0, 36.0, 15.0, 2.0, 15.0, 45.0)))
        loose = junk_verts(7, 40, (5, 5, 5, 55, 35, 25))
    elif variant == "B":
        cross1 = box_mesh(25, 20, 8, 27, 30, 18)
        cross2 = box_mesh(22, 24, 8, 38, 26, 18)
        _keep, cross2_flip = reversed_copy(cross2)
        objs.append(("FIXTURE_beta_cross1", cross1))
        objs.append(("FIXTURE_beta_cross2", cross2))
        objs.append(("FIXTURE_beta_cross2_flip", cross2_flip))
        loose = junk_verts(11, 25, (5, 5, 5, 55, 35, 25))
    else:
        raise ValueError(variant)
    return objs, loose


# ---------------------------------------------------------------- 教具 2：带深槽的演示支架

AID2_BOUNDS = ((-1.0, -1.0, -1.0), (51.0, 41.0, 41.0))


def aid2_csg():
    solids = [
        ("box", (0, 0, 0, 50, 40, 6)),        # 底板
        ("box", (0, 32, 6, 50, 40, 40)),      # 立板（背面 y=40 为第二安装面）
        ("box", (10, 20, 6, 40, 20.8, 18)),   # 薄肋（0.8mm，定向失效演示件）
    ]
    voids = [
        ("box", (21, 31, 15, 29, 41, 41)),              # 深槽：顶开 25mm 深，贯通立板
        ("cyl", 2, 10.0, 15.0, 3.25, -1.0, 7.0),        # H1 底板直销孔
        ("cyl", 2, 40.0, 15.0, 3.10, -1.0, 7.0),        # H2 底板卡销孔
        ("cyl", 1, 15.0, 20.0, 3.25, 31.0, 41.0),       # H3 立板直销孔
        ("cyl", 1, 35.0, 20.0, 3.10, 31.0, 41.0),       # H4 立板卡销孔
    ]
    return solids, voids


def aid2_spec():
    return {
        "part": "aid2", "units": "mm", "shell_object": "PUBLIC_SHELL",
        "interfaces": {
            "holes": [
                {"id": "H1", "axis": "z", "at": [10.0, 15.0], "dia": 6.5},
                {"id": "H2", "axis": "z", "at": [40.0, 15.0], "dia": 6.2},
                {"id": "H3", "axis": "y", "at": [15.0, 20.0], "dia": 6.5},
                {"id": "H4", "axis": "y", "at": [35.0, 20.0], "dia": 6.2},
            ],
            "grooves": [
                {"id": "G1", "kind": "slot", "open": "z+",
                 "rect": [21.0, 32.0, 29.0, 40.0], "bottom": 15.0},
            ],
            "service_holes": [],
            "mounts": [
                {"id": "M1",
                 "plane": {"axis": "z", "coord": 0.0, "side": "-"},
                 "region": [2.0, 2.0, 48.0, 38.0],
                 "holes": ["H1", "H2"]},
                {"id": "M2",
                 "plane": {"axis": "y", "coord": 40.0, "side": "+"},
                 "region": [2.0, 7.0, 48.0, 39.0],
                 "holes": ["H3", "H4"]},
            ],
        },
    }


def aid2_private():
    pocket = box_mesh(10, 33, 10, 40, 37, 14)          # 立板内部型腔薄壳
    prib = box_mesh(24, 33.5, 10.5, 26, 36.5, 13.5)
    _keep, prib_dup = reversed_copy(prib)
    objs = [("FIXTURE_pocket", pocket),
            ("FIXTURE_pocket_rib", prib),
            ("FIXTURE_pocket_rib_dup", prib_dup)]
    loose = junk_verts(23, 30, (5, 5, 2, 45, 35, 35))
    return objs, loose


# ---------------------------------------------------------------- 任务文件（工坊参数，设计室退出后才提供）

def job_files():
    return {
        "aid1": {"part": "aid1", "stl": "out/outbound/aid1.stl",
                 "spec": "out/models/aid1/public_spec.json",
                 "orientation": "I", "mount": "M1",
                 "layer_mm": 0.4, "min_wall_mm": 1.2,
                 "pins": [{"hole": "H1", "type": "straight"},
                          {"hole": "H2", "type": "snap"}]},
        "aid1e": {"part": "aid1e", "stl": "out/outbound/aid1e.stl",
                  "spec": "out/models/aid1e/public_spec.json",
                  "orientation": "I", "mount": "M1",
                  "layer_mm": 0.4, "min_wall_mm": 1.2,
                  "pins": [{"hole": "H1", "type": "straight"},
                           {"hole": "H2", "type": "snap"}]},
        "aid2_m1": {"part": "aid2", "stl": "out/outbound/aid2.stl",
                    "spec": "out/models/aid2/public_spec.json",
                    "orientation": "I", "mount": "M1",
                    "layer_mm": 0.4, "min_wall_mm": 1.2,
                    "pins": [{"hole": "H1", "type": "straight"},
                             {"hole": "H2", "type": "snap"}]},
        "aid2_m2": {"part": "aid2", "stl": "out/workshop/aid2_m1.restored.stl",
                    "spec": "out/models/aid2/public_spec.json",
                    "orientation": "rotx_m90", "mount": "M2",
                    "layer_mm": 0.4, "min_wall_mm": 1.2,
                    "pins": [{"hole": "H3", "type": "straight"},
                             {"hole": "H4", "type": "snap"}]},
    }


# ---------------------------------------------------------------- 总装

def _write_part(out_root, part, shell, private_objs, loose, spec):
    d = os.path.join(out_root, "models", part)
    os.makedirs(d, exist_ok=True)
    objects = [("PUBLIC_SHELL", shell)] + private_objs
    write_obj(os.path.join(d, "full_model.obj"), objects, loose)
    with open(os.path.join(d, "public_spec.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=2, sort_keys=True)
    n_private = sum(len(o.tris) for _n, o in private_objs)
    print("  [%s] 壳体 %d 三角面；私有对象 %d 个（%d 面）；未引用顶点 %d 个"
          % (part, len(shell.tris), len(private_objs), n_private, len(loose)))


def build_all(out_root):
    print("[设计室] 生成教具完整模型与公开规格 ...")
    # aid1 四个变体：A 基准 / B 同公开异私有 / C 内部通道改为对外服务孔 / E 卡销孔超差
    shell_a = build_shell(*aid1_csg(6.2, False), AID1_BOUNDS)
    shell_c = build_shell(*aid1_csg(6.2, True), AID1_BOUNDS)
    shell_e = build_shell(*aid1_csg(5.0, False), AID1_BOUNDS)

    pa, la = aid1_private("A")
    _write_part(out_root, "aid1", shell_a, pa, la, aid1_spec("aid1", 6.2, False))
    pb, lb = aid1_private("B")
    _write_part(out_root, "aid1b", shell_a, pb, lb, aid1_spec("aid1b", 6.2, False))
    pc, lc = aid1_private("C")
    _write_part(out_root, "aid1c", shell_c, pc, lc, aid1_spec("aid1c", 6.2, True))
    pe, le = aid1_private("E")
    _write_part(out_root, "aid1e", shell_e, pe, le, aid1_spec("aid1e", 5.0, False))

    shell2 = build_shell(*aid2_csg(), AID2_BOUNDS)
    p2, l2 = aid2_private()
    _write_part(out_root, "aid2", shell2, p2, l2, aid2_spec())

    jdir = os.path.join(out_root, "jobs")
    os.makedirs(jdir, exist_ok=True)
    for name, job in job_files().items():
        with open(os.path.join(jdir, "%s.job.json" % name), "w", encoding="utf-8") as fh:
            json.dump(job, fh, ensure_ascii=False, indent=2, sort_keys=True)
    print("[设计室] 完成。任务参数文件已存于 %s（设计室退出后由工坊启用）" % jdir)


if __name__ == "__main__":
    build_all("out")
