"""平面截网格 -> 逐层轮廓环；轮廓环 -> 行跨度/位图；位图形态学（大整数行，C 速度）。

切片采用半开区间规则处理共面/顶点退化；轮廓按端点吸附连接成环。
位图每行是一个 Python 大整数（bit i = 像素 i），腐蚀/膨胀用移位与与/或实现。
"""
from __future__ import annotations

import math
from collections import defaultdict

from .grid import Grid

SNAP = 1e-6  # 端点吸附精度 (mm)
EPS_ROW = 1e-6  # 行/层归属计算的浮点余量：宁多算一行（守卫会拒绝），不可漏行


# ---------------------------------------------------------------- 网格切片

class MeshSlicer:
    """对网格按固定层厚切片。z_k = z0 + (k+0.5)*layer_h。"""

    def __init__(self, mesh, layer_h, z0=0.0, nz=None):
        self.mesh = mesh
        self.h = float(layer_h)
        self.z0 = float(z0)
        zs = [v[2] for v in mesh.verts]
        zmin, zmax = min(zs), max(zs)
        if nz is None:
            nz = max(1, int((zmax - self.z0) / self.h + 0.5))
        self.nz = nz
        # 三角形按覆盖层分桶（带浮点余量，段内守卫最终裁决）
        self.bins = [[] for _ in range(nz)]
        for ti, (a, b, c) in enumerate(mesh.tris):
            za, zb, zc = mesh.verts[a][2], mesh.verts[b][2], mesh.verts[c][2]
            lo, hi = min(za, zb, zc), max(za, zb, zc)
            k0 = int(math.ceil((lo - self.z0) / self.h - 0.5 - EPS_ROW))
            k1 = int(math.floor((hi - self.z0) / self.h - 0.5 + EPS_ROW))
            k0 = max(0, k0)
            k1 = min(nz - 1, k1)
            for k in range(k0, k1 + 1):
                self.bins[k].append(ti)

    def z_of(self, k):
        return self.z0 + (k + 0.5) * self.h

    def segments_at(self, k):
        z = self.z_of(k)
        mesh = self.mesh
        segs = []
        for ti in self.bins[k]:
            (a, b, c) = mesh.tris[ti]
            pts = []
            for (p, q) in ((a, b), (b, c), (c, a)):
                vp, vq = mesh.verts[p], mesh.verts[q]
                zp, zq = vp[2], vq[2]
                if (zp < z) != (zq < z):
                    t = (z - zp) / (zq - zp)
                    pts.append((vp[0] + t * (vq[0] - vp[0]),
                                vp[1] + t * (vq[1] - vp[1])))
            if len(pts) == 2:
                segs.append((pts[0], pts[1]))
        return segs

    def loops_at(self, k):
        return join_loops(self.segments_at(k))


def join_loops(segments):
    """把散乱线段按端点吸附连接成闭合环。返回 (loops, n_dropped)。
    loops: [(pts, signed_area)]，面积>0 为外环（逆时针）。"""
    pid_of = {}
    pts = []
    adj = defaultdict(list)  # pid -> [(pid2, edge_id)]

    def intern(p):
        key = (round(p[0] / SNAP), round(p[1] / SNAP))
        pid = pid_of.get(key)
        if pid is None:
            pid = len(pts)
            pid_of[key] = pid
            pts.append(p)
        return pid

    edges = []
    for (p, q) in segments:
        a, b = intern(p), intern(q)
        if a == b:
            continue
        eid = len(edges)
        edges.append((a, b))
        adj[a].append((b, eid))
        adj[b].append((a, eid))

    used = [False] * len(edges)
    loops = []
    dropped = 0
    for eid0 in range(len(edges)):
        if used[eid0]:
            continue
        a0, b0 = edges[eid0]
        used[eid0] = True
        loop = [a0, b0]
        cur, prev = b0, a0
        closed = False
        for _ in range(len(edges) + 2):
            nxt = None
            for (cand, eid) in adj[cur]:
                if not used[eid]:
                    nxt = (cand, eid)
                    break
            if nxt is None:
                closed = (cur == a0)
                break
            cand, eid = nxt
            used[eid] = True
            if cand == a0:
                closed = True
                break
            loop.append(cand)
            prev, cur = cur, cand
        if closed and len(loop) >= 3:
            poly = [pts[i] for i in loop]
            area = 0.0
            for m in range(len(poly)):
                x0, y0 = poly[m]
                x1, y1 = poly[(m + 1) % len(poly)]
                area += x0 * y1 - x1 * y0
            loops.append((poly, area / 2.0))
        else:
            dropped += 1
    return loops, dropped


# ---------------------------------------------------------------- 环 -> 行跨度

def row_spans(loops, y):
    """水平线 y 与全部环求交，偶奇规则配成跨度列表 [(xa,xb), ...]。"""
    xs = []
    for (poly, _area) in loops:
        n = len(poly)
        for m in range(n):
            x0, y0 = poly[m]
            x1, y1 = poly[(m + 1) % n]
            if (y0 <= y) != (y1 <= y):
                xs.append(x0 + (x1 - x0) * (y - y0) / (y1 - y0))
    xs.sort()
    out = []
    for m in range(0, len(xs) - 1, 2):
        out.append((xs[m], xs[m + 1]))
    return out


def fill_grid_rows(grid, k, loops):
    """把 z 第 k 层的轮廓环按偶奇规则填入体素行（实体=-1）。"""
    # 边按行分桶
    buckets = defaultdict(list)
    for (poly, _a) in loops:
        n = len(poly)
        for m in range(n):
            x0, y0 = poly[m]
            x1, y1 = poly[(m + 1) % n]
            if y0 == y1:
                continue
            lo, hi = min(y0, y1), max(y0, y1)
            j0 = int(math.ceil((lo - grid.oy) / grid.dy - 0.5 - EPS_ROW))
            j1 = int(math.floor((hi - grid.oy) / grid.dy - 0.5 + EPS_ROW))
            for j in range(max(0, j0), min(grid.ny - 1, j1) + 1):
                y = grid.oy + (j + 0.5) * grid.dy
                if (y0 <= y) != (y1 <= y):
                    x = x0 + (x1 - x0) * (y - y0) / (y1 - y0)
                    buckets[j].append(x)
    for j, xs in buckets.items():
        xs.sort()
        for m in range(0, len(xs) - 1, 2):
            i0 = int(math.ceil((xs[m] - grid.ox) / grid.dx - 0.5))
            i1 = int(math.ceil((xs[m + 1] - grid.ox) / grid.dx - 0.5)) - 1
            grid._span_fill(k, j, i0, i1, -1.0)


def grid_from_mesh(mesh, pitch, pad):
    """闭合网格 -> 体素场（逐层切片填充）。"""
    lo, hi = mesh.bounds()
    grid = Grid.for_bounds(lo, hi, pitch, pad)
    slicer = MeshSlicer(mesh, grid.dz, z0=grid.oz, nz=grid.nz)
    for k in range(grid.nz):
        loops, _dropped = slicer.loops_at(k)
        if loops:
            fill_grid_rows(grid, k, loops)
    return grid


def point_in_loops(loops, x, y):
    """偶奇规则判定点是否在材料内。"""
    inside = False
    for (poly, _a) in loops:
        n = len(poly)
        for m in range(n):
            x0, y0 = poly[m]
            x1, y1 = poly[(m + 1) % n]
            if (y0 <= y) != (y1 <= y):
                xc = x0 + (x1 - x0) * (y - y0) / (y1 - y0)
                if xc > x:
                    inside = not inside
    return inside


def dist_to_loops(loops, x, y):
    """点到最近轮廓边的距离 (mm)。"""
    best = float("inf")
    for (poly, _a) in loops:
        n = len(poly)
        for m in range(n):
            x0, y0 = poly[m]
            x1, y1 = poly[(m + 1) % n]
            dx, dy = x1 - x0, y1 - y0
            l2 = dx * dx + dy * dy
            if l2 <= 0.0:
                t = 0.0
            else:
                t = ((x - x0) * dx + (y - y0) * dy) / l2
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            px, py = x0 + t * dx, y0 + t * dy
            d = math.hypot(x - px, y - py)
            if d < best:
                best = d
    return best


def clearance_from_loops(loops, x, y):
    """自 (x,y) 到最近材料的距离；点在材料内返回 0。矢量量取，无位图量化偏差。"""
    if not loops:
        return float("inf")
    if point_in_loops(loops, x, y):
        return 0.0
    return dist_to_loops(loops, x, y)


# ---------------------------------------------------------------- 位图（大整数行）

class Bitmap:
    """nx*ny 二值图；rows[j] 的第 i 位 = 像素 (i,j)。"""

    def __init__(self, nx, ny):
        self.nx, self.ny = nx, ny
        self.rows = [0] * ny
        self.full = (1 << nx) - 1

    @classmethod
    def from_loops(cls, loops, ox, oy, pitch, nx, ny):
        bm = cls(nx, ny)
        buckets = defaultdict(list)
        for (poly, _a) in loops:
            n = len(poly)
            for m in range(n):
                x0, y0 = poly[m]
                x1, y1 = poly[(m + 1) % n]
                if y0 == y1:
                    continue
                lo, hi = min(y0, y1), max(y0, y1)
                j0 = int(math.ceil((lo - oy) / pitch - 0.5 - EPS_ROW))
                j1 = int(math.floor((hi - oy) / pitch - 0.5 + EPS_ROW))
                for j in range(max(0, j0), min(ny - 1, j1) + 1):
                    y = oy + (j + 0.5) * pitch
                    if (y0 <= y) != (y1 <= y):
                        x = x0 + (x1 - x0) * (y - y0) / (y1 - y0)
                        buckets[j].append(x)
        for j, xs in buckets.items():
            xs.sort()
            row = 0
            for m in range(0, len(xs) - 1, 2):
                i0 = int(math.ceil((xs[m] - ox) / pitch - 0.5))
                i1 = int(math.ceil((xs[m + 1] - ox) / pitch - 0.5)) - 1
                i0 = max(0, i0)
                i1 = min(nx - 1, i1)
                if i1 >= i0:
                    row |= ((1 << (i1 - i0 + 1)) - 1) << i0
            bm.rows[j] = row
        return bm

    def count(self):
        c = 0
        for r in self.rows:
            c += r.bit_count()
        return c

    def get(self, i, j):
        if i < 0 or j < 0 or i >= self.nx or j >= self.ny:
            return 0
        return (self.rows[j] >> i) & 1

    def copy(self):
        bm = Bitmap(self.nx, self.ny)
        bm.rows = list(self.rows)
        return bm

    # ---- 形态学（8 邻域，结构元近似八边形）----
    def erode(self, n=1):
        bm = self.copy()
        for _ in range(n):
            r = bm.rows
            out = [0] * bm.ny
            full = bm.full
            for j in range(bm.ny):
                up = r[j - 1] if j > 0 else 0
                dn = r[j + 1] if j < bm.ny - 1 else 0
                c = r[j]
                v = (c & ((c << 1) & full) & (c >> 1)
                     & up & ((up << 1) & full) & (up >> 1)
                     & dn & ((dn << 1) & full) & (dn >> 1))
                out[j] = v
            bm.rows = out
        return bm

    def dilate(self, n=1):
        bm = self.copy()
        for _ in range(n):
            r = bm.rows
            out = [0] * bm.ny
            full = bm.full
            for j in range(bm.ny):
                up = r[j - 1] if j > 0 else 0
                dn = r[j + 1] if j < bm.ny - 1 else 0
                c = r[j]
                v = (c | ((c << 1) & full) | (c >> 1)
                     | up | ((up << 1) & full) | (up >> 1)
                     | dn | ((dn << 1) & full) | (dn >> 1))
                out[j] = v & full
            bm.rows = out
        return bm

    def opening(self, n):
        return self.erode(n).dilate(n)

    def andnot(self, other):
        bm = Bitmap(self.nx, self.ny)
        bm.rows = [a & ~b for a, b in zip(self.rows, other.rows)]
        return bm

    def union(self, other):
        bm = Bitmap(self.nx, self.ny)
        bm.rows = [a | b for a, b in zip(self.rows, other.rows)]
        return bm

    def intersect(self, other):
        bm = Bitmap(self.nx, self.ny)
        bm.rows = [a & b for a, b in zip(self.rows, other.rows)]
        return bm

    def clearance(self, x_mm, y_mm, ox, oy, pitch, max_r_mm=8.0, ndir=16):
        """自 (x,y) 向 ndir 个方向步进，返回到最近实体像素的距离 (mm)。
        全部方向在 max_r 内无实体 -> 返回 max_r_mm。"""
        best = max_r_mm
        step = pitch * 0.5
        for m in range(ndir):
            ang = 2.0 * math.pi * m / ndir
            ca, sa = math.cos(ang), math.sin(ang)
            d = step
            while d < best:
                i = int((x_mm + ca * d - ox) / pitch)
                j = int((y_mm + sa * d - oy) / pitch)
                if self.get(i, j):
                    if d < best:
                        best = d
                    break
                d += step
        return best
