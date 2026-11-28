"""独立格点检具：展示座 + 两种插销 + 卡入/取出过程仿真。

检具只依据：工坊提供的参数（摆放方向、层厚、最小壁厚、插销规格）
与【切片实测轮廓】。绝不引用源模型自己宣布的公差作为合格依据——
孔径、间隙、干涉全部由切片位图实测。
"""
from __future__ import annotations

import math

from geom.mesh import Mesh
from geom.slicer import clearance_from_loops

GRID_PITCH = 5.0  # 检具格点间距 (mm)

# 两种插销：sections = [(z0, z1, r0, r1), ...] 半径沿高度分段线性
PIN_TYPES = {
    "straight": {
        "L": 20.0, "flex": 0.0,
        "sections": [(0.0, 20.0, 2.9, 2.9)],            # Ø5.8 直销
    },
    "snap": {
        "L": 35.0, "flex": 0.6,                          # 弹性让位 δ
        "sections": [(0.0, 30.0, 2.8, 2.8),              # Ø5.6 杆身
                     (30.0, 31.5, 2.8, 3.4),             # 锥段
                     (31.5, 35.0, 3.4, 3.4)],            # Ø6.8 倒钩
    },
}


def pin_radius(ptype, h):
    spec = PIN_TYPES[ptype]
    if h < 0.0 or h > spec["L"]:
        return 0.0
    for (z0, z1, r0, r1) in spec["sections"]:
        if z0 <= h <= z1:
            if z1 <= z0:
                return r0
            return r0 + (r1 - r0) * (h - z0) / (z1 - z0)
    return 0.0


def pin_barb_start(ptype):
    """倒钩/锥段起始高度（超过杆身半径的位置）；直销返回 None。"""
    spec = PIN_TYPES[ptype]
    r_shank = spec["sections"][0][2]
    for (z0, _z1, r0, r1) in spec["sections"]:
        if max(r0, r1) > r_shank + 0.05:
            return z0
    return None


def pin_flex(ptype, h):
    """该高度允许的弹性干涉量：倒钩/锥段为 δ，其余为 0。"""
    barb = pin_barb_start(ptype)
    if barb is None:
        return 0.0
    return PIN_TYPES[ptype]["flex"] if h >= barb else 0.0


def pin_max_r(ptype):
    return max(max(r0, r1) for (_0, _1, r0, r1) in PIN_TYPES[ptype]["sections"])


# ---------------------------------------------------------------- 插销过程仿真

def simulate_pin(all_loops, layer_h, x_mm, y_mm, ptype):
    """在逐层矢量轮廓上仿真一次插销：插入 -> 就位 -> 卡持 -> 取出。

    all_loops: 各层轮廓环（z=0 为安装面，检具顶面）。
    返回实测报告 dict。全部结论来自切片轮廓实测，与源模型声明无关。
    """
    spec = PIN_TYPES[ptype]
    L = spec["L"]
    clear = [clearance_from_loops(loops, x_mm, y_mm) for loops in all_loops]
    material_layers = [i for i, loops in enumerate(all_loops) if loops]
    r_max = pin_max_r(ptype)
    # 插销附近的材料层（局部件顶）：只统计轮廓距针位 r_max+0.5 以内的层
    near_layers = [i for i in material_layers if clear[i] < r_max + 0.5]
    local_top_z = (max(near_layers) + 1) * layer_h if near_layers else 0.0

    def sweep(g0, g1, step):
        """相对扫掠：g 为件底与座顶的间隙。返回 (max_violation, where)。"""
        worst, where = -1e9, None
        g = g0
        while (step > 0 and g <= g1 + 1e-9) or (step < 0 and g >= g1 - 1e-9):
            for i in material_layers:
                h = (i + 0.5) * layer_h + g
                if h < 0.0 or h > L:
                    continue
                v = pin_radius(ptype, h) - clear[i] - pin_flex(ptype, h)
                if v > worst:
                    worst, where = v, (round(g, 2), i, round(h, 2))
            g += step
        return worst, where

    ins_v, ins_at = sweep(L, 0.0, -0.2)     # 插入
    seat_v, seat_at = sweep(0.0, 0.0, 0.2)  # 就位
    rem_v, rem_at = sweep(0.0, L, 0.2)      # 取出

    rep = {"type": ptype, "at": [round(x_mm, 3), round(y_mm, 3)],
           "insert_max_violation": round(ins_v, 3), "insert_at": ins_at,
           "seated_max_violation": round(seat_v, 3), "seated_at": seat_at,
           "remove_max_violation": round(rem_v, 3), "remove_at": rem_at}
    ok = ins_v <= 0.0 and seat_v <= 0.0 and rem_v <= 0.0

    barb = pin_barb_start(ptype)
    if barb is not None:
        # 卡持：倒钩必须露出局部件顶；孔沿卡唇取针位材料顶部 3 层的最紧间隙
        # （顶层孔口有曲面网倒圆，单量顶层会误判无唇）
        lip = min(clear[i] for i in near_layers[-3:]) if near_layers else float("inf")
        r_barb = pin_max_r(ptype)
        exposed = local_top_z <= barb + 1e-9
        retained = lip <= r_barb - 0.1
        rep["retention"] = {"barb_exposed": exposed,
                            "lip_clearance": round(lip, 3),
                            "barb_r": r_barb, "ok": exposed and retained}
        ok = ok and exposed and retained
    rep["ok"] = ok
    return rep


def measured_hole_dia(all_loops, layer_h, x_mm, y_mm):
    """实测孔径：针位材料层中的最小间隙半径 ×2（最收紧处，对插销最相关）。"""
    vals = []
    for loops in all_loops:
        if not loops:
            continue
        c = clearance_from_loops(loops, x_mm, y_mm)
        if c < 8.0:  # 只统计针位附近有材料的层
            vals.append(c)
    if not vals:
        return None
    return round(2.0 * min(vals), 3)


# ---------------------------------------------------------------- 检具网格（可视化/可打印）

def _profile_revolve(profile, n=24, at=(0.0, 0.0)):
    """(r,z) 母线绕 z 轴旋转成闭合网格，放置在 (at.x, at.y)。"""
    verts, tris = [], []
    rings = []
    for (r, z) in profile:
        if r <= 1e-9:
            rings.append([len(verts)])
            verts.append((at[0], at[1], z))
        else:
            ring = []
            for i in range(n):
                ang = 2.0 * math.pi * i / n
                ring.append(len(verts))
                verts.append((at[0] + r * math.cos(ang),
                              at[1] + r * math.sin(ang), z))
            rings.append(ring)
    for s in range(len(profile) - 1):
        ra, rb = rings[s], rings[s + 1]
        pa_is_axis = profile[s][0] <= 1e-9
        pb_is_axis = profile[s + 1][0] <= 1e-9
        if pa_is_axis and pb_is_axis:
            continue
        if pa_is_axis:
            for i in range(n):
                j = (i + 1) % n
                tris.append((ra[0], rb[j], rb[i]))
        elif pb_is_axis:
            for i in range(n):
                j = (i + 1) % n
                tris.append((ra[i], ra[j], rb[0]))
        else:
            for i in range(n):
                j = (i + 1) % n
                tris.append((ra[i], ra[j], rb[j]))
                tris.append((ra[i], rb[j], rb[i]))
    return Mesh(verts, tris)


def pin_profile(ptype):
    spec = PIN_TYPES[ptype]
    prof = [(0.0, 0.0)]
    for (z0, z1, r0, r1) in spec["sections"]:
        prof.append((r0, z0))
        if abs(r1 - r0) > 1e-9:
            prof.append((r1, z1))
        else:
            prof.append((r1, z1))
    prof.append((0.0, spec["L"]))
    # 去重相邻点
    out = [prof[0]]
    for p in prof[1:]:
        if abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9:
            out.append(p)
    return out


def build_fixture_mesh(pins, plate_t=4.0, margin=10.0):
    """pins: [(x, y, ptype)] -> 座板 + 插销的组合网格（可视化检具）。"""
    xs = [p[0] for p in pins]
    ys = [p[1] for p in pins]
    x0, x1 = min(xs) - margin, max(xs) + margin
    y0, y1 = min(ys) - margin, max(ys) + margin
    plate = Mesh(
        [(x0, y0, -plate_t), (x1, y0, -plate_t), (x1, y1, -plate_t), (x0, y1, -plate_t),
         (x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0)],
        [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
         (0, 1, 5), (0, 5, 4), (2, 3, 7), (2, 7, 6),
         (0, 4, 7), (0, 7, 3), (1, 2, 6), (1, 6, 5)])
    meshes = [plate]
    for (x, y, ptype) in pins:
        meshes.append(_profile_revolve(pin_profile(ptype), at=(x, y)))
    verts, tris = [], []
    for m in meshes:
        base = len(verts)
        verts.extend(m.verts)
        tris.extend([(a + base, b + base, c + base) for (a, b, c) in m.tris])
    return Mesh(verts, tris)
