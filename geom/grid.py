"""有界体素标量场：绘制器（盒/圆柱）、解析 SDF 开槽打孔、洪泛检空腔、曲面网提取闭合网格。

场约定：field < 0 为实体，field > 0 为空。接口特征（孔/槽）用解析 SDF 以亚体素精度写入，
外壳等二值来源为 ±1。曲面网 (surface nets) 从场中提取闭合三角面。
"""
from __future__ import annotations

import math
from array import array
from collections import deque

from .mesh import Mesh, edge_report, ensure_outward


class Grid:
    def __init__(self, nx, ny, nz, origin, pitch):
        self.nx, self.ny, self.nz = int(nx), int(ny), int(nz)
        if isinstance(pitch, (tuple, list)):
            self.dx, self.dy, self.dz = pitch
        else:
            self.dx = self.dy = self.dz = float(pitch)
        self.ox, self.oy, self.oz = origin
        n = self.nx * self.ny * self.nz
        self.field = array("f", [1.0]) * n

    # ---------------------------------------------------------- 基础
    def idx(self, i, j, k):
        return i + self.nx * (j + self.ny * k)

    def solid(self, i, j, k):
        return self.field[i + self.nx * (j + self.ny * k)] < 0.0

    def bounds_mm(self):
        return ((self.ox, self.oy, self.oz),
                (self.ox + self.nx * self.dx,
                 self.oy + self.ny * self.dy,
                 self.oz + self.nz * self.dz))

    @classmethod
    def for_bounds(cls, lo, hi, pitch, pad):
        dx = pitch[0] if isinstance(pitch, (tuple, list)) else pitch
        dy = pitch[1] if isinstance(pitch, (tuple, list)) else pitch
        dz = pitch[2] if isinstance(pitch, (tuple, list)) else pitch
        ox, oy, oz = lo[0] - pad, lo[1] - pad, lo[2] - pad
        nx = int(math.ceil((hi[0] - lo[0] + 2 * pad) / dx)) + 1
        ny = int(math.ceil((hi[1] - lo[1] + 2 * pad) / dy)) + 1
        nz = int(math.ceil((hi[2] - lo[2] + 2 * pad) / dz)) + 1
        return cls(nx, ny, nz, (ox, oy, oz), (dx, dy, dz))

    def _span_fill(self, k, j, i0, i1, val):
        """向第 (k,j) 行的体素 [i0,i1] 写入 val（C 速度切片赋值）。"""
        i0 = max(0, i0)
        i1 = min(self.nx - 1, i1)
        if i1 < i0:
            return
        base = self.nx * (j + self.ny * k)
        self.field[base + i0: base + i1 + 1] = array("f", [val]) * (i1 - i0 + 1)

    def _irange(self, a0, a1, o, d):
        """mm 区间 [a0,a1] 覆盖的体素下标范围（按体素中心判定，带浮点余量）。"""
        i0 = int(math.ceil((a0 - o) / d - 0.5 - 1e-9))
        i1 = int(math.floor((a1 - o) / d - 0.5 + 1e-9))
        return i0, i1

    # ---------------------------------------------------------- 二值绘制器（供建模/壳体填充）
    def fill_box(self, box, solid=True):
        (x0, y0, z0, x1, y1, z1) = box
        val = -1.0 if solid else 1.0
        i0, i1 = self._irange(x0, x1, self.ox, self.dx)
        j0, j1 = self._irange(y0, y1, self.oy, self.dy)
        k0, k1 = self._irange(z0, z1, self.oz, self.dz)
        j0 = max(0, j0); j1 = min(self.ny - 1, j1)
        k0 = max(0, k0); k1 = min(self.nz - 1, k1)
        for k in range(k0, k1 + 1):
            for j in range(j0, j1 + 1):
                self._span_fill(k, j, i0, i1, val)

    def fill_cylinder(self, axis, c1, c2, r, a0, a1, solid=True):
        """轴对齐圆柱。axis: 0=x,1=y,2=z；(c1,c2) 为另两轴圆心；[a0,a1] 轴向范围。"""
        val = -1.0 if solid else 1.0
        r2 = r * r
        if axis == 2:   # 轴 = z，截面在 xy
            k0, k1 = self._irange(a0, a1, self.oz, self.dz)
            j0, j1 = self._irange(c2 - r, c2 + r, self.oy, self.dy)
            k0 = max(0, k0); k1 = min(self.nz - 1, k1)
            j0 = max(0, j0); j1 = min(self.ny - 1, j1)
            for k in range(k0, k1 + 1):
                for j in range(j0, j1 + 1):
                    dy = self.oy + (j + 0.5) * self.dy - c2
                    rem = r2 - dy * dy
                    if rem < 0:
                        continue
                    hw = math.sqrt(rem)
                    i0, i1 = self._irange(c1 - hw, c1 + hw, self.ox, self.dx)
                    self._span_fill(k, j, i0, i1, val)
        elif axis == 1:  # 轴 = y，截面在 xz
            j0, j1 = self._irange(a0, a1, self.oy, self.dy)
            k0, k1 = self._irange(c2 - r, c2 + r, self.oz, self.dz)
            j0 = max(0, j0); j1 = min(self.ny - 1, j1)
            k0 = max(0, k0); k1 = min(self.nz - 1, k1)
            for j in range(j0, j1 + 1):
                for k in range(k0, k1 + 1):
                    dz = self.oz + (k + 0.5) * self.dz - c2
                    rem = r2 - dz * dz
                    if rem < 0:
                        continue
                    hw = math.sqrt(rem)
                    i0, i1 = self._irange(c1 - hw, c1 + hw, self.ox, self.dx)
                    self._span_fill(k, j, i0, i1, val)
        else:            # 轴 = x，截面在 yz
            i0, i1 = self._irange(a0, a1, self.ox, self.dx)
            k0, k1 = self._irange(c2 - r, c2 + r, self.oz, self.dz)
            k0 = max(0, k0); k1 = min(self.nz - 1, k1)
            for k in range(k0, k1 + 1):
                dz = self.oz + (k + 0.5) * self.dz - c2
                rem = r2 - dz * dz
                if rem < 0:
                    continue
                hw = math.sqrt(rem)
                j0, j1 = self._irange(c1 - hw, c1 + hw, self.oy, self.dy)
                j0 = max(0, j0); j1 = min(self.ny - 1, j1)
                for j in range(j0, j1 + 1):
                    self._span_fill(k, j, i0, i1, val)

    # ---------------------------------------------------------- 解析 SDF 开凿（亚体素精度）
    def carve_sdf_box(self, box, margin=2.0):
        """以盒状空腔开凿：field = max(field, -sdf)。box=(x0,y0,z0,x1,y1,z1)。"""
        (x0, y0, z0, x1, y1, z1) = box
        cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
        hx, hy, hz = (x1 - x0) / 2, (y1 - y0) / 2, (z1 - z0) / 2
        i0, i1 = self._irange(x0 - margin, x1 + margin, self.ox, self.dx)
        j0, j1 = self._irange(y0 - margin, y1 + margin, self.oy, self.dy)
        k0, k1 = self._irange(z0 - margin, z1 + margin, self.oz, self.dz)
        i0 = max(0, i0); i1 = min(self.nx - 1, i1)
        j0 = max(0, j0); j1 = min(self.ny - 1, j1)
        k0 = max(0, k0); k1 = min(self.nz - 1, k1)
        f = self.field
        nx, ny = self.nx, self.ny
        for k in range(k0, k1 + 1):
            pz = self.oz + (k + 0.5) * self.dz
            qz = abs(pz - cz) - hz
            for j in range(j0, j1 + 1):
                py = self.oy + (j + 0.5) * self.dy
                qy = abs(py - cy) - hy
                base = nx * (j + ny * k)
                for i in range(i0, i1 + 1):
                    px = self.ox + (i + 0.5) * self.dx
                    qx = abs(px - cx) - hx
                    ax, ay, az = max(qx, 0.0), max(qy, 0.0), max(qz, 0.0)
                    outside = math.sqrt(ax * ax + ay * ay + az * az)
                    sdf = outside + min(max(qx, qy, qz), 0.0)
                    idx = base + i
                    if -sdf > f[idx]:
                        f[idx] = -sdf

    def carve_sdf_cylinder(self, axis, c1, c2, r, a0, a1, margin=2.0):
        """以轴对齐圆柱空腔开凿（通孔/服务孔）。"""
        lo = [self.ox, self.oy, self.oz]
        d = [self.dx, self.dy, self.dz]
        n = [self.nx, self.ny, self.nz]
        c = [0.0, 0.0, 0.0]
        others = [a for a in range(3) if a != axis]
        c[others[0]] = c1
        c[others[1]] = c2
        rng = []
        for a in range(3):
            if a == axis:
                rng.append(self._irange(a0 - margin, a1 + margin, lo[a], d[a]))
            else:
                rng.append(self._irange(c[a] - r - margin, c[a] + r + margin, lo[a], d[a]))
        f = self.field
        nx, ny = self.nx, self.ny
        o0, o1 = others
        am = (a0 + a1) / 2.0
        hm = (a1 - a0) / 2.0
        for k in range(max(0, rng[2][0]), min(n[2] - 1, rng[2][1]) + 1):
            pz = self.oz + (k + 0.5) * self.dz
            for j in range(max(0, rng[1][0]), min(n[1] - 1, rng[1][1]) + 1):
                py = self.oy + (j + 0.5) * self.dy
                base = nx * (j + ny * k)
                for i in range(max(0, rng[0][0]), min(n[0] - 1, rng[0][1]) + 1):
                    px = self.ox + (i + 0.5) * self.dx
                    p = (px, py, pz)
                    dv = p[o0] - c[o0]
                    dw = p[o1] - c[o1]
                    radial = math.sqrt(dv * dv + dw * dw) - r
                    axial = abs(p[axis] - am) - hm
                    ar, aa = max(radial, 0.0), max(axial, 0.0)
                    sdf = min(max(radial, axial), 0.0) + math.sqrt(ar * ar + aa * aa)
                    idx = base + i
                    if -sdf > f[idx]:
                        f[idx] = -sdf

    # ---------------------------------------------------------- 空腔检测（洪泛）
    def enclosed_void_count(self):
        """从边界洪泛非实体体素；返回不可达（被封闭）的空体素数。"""
        nx, ny, nz = self.nx, self.ny, self.nz
        n = nx * ny * nz
        f = self.field
        seen = bytearray(n)
        dq = deque()
        # 边界种子
        for k in range(nz):
            for j in range(ny):
                for i in (0, nx - 1):
                    idx = i + nx * (j + ny * k)
                    if f[idx] > 0 and not seen[idx]:
                        seen[idx] = 1
                        dq.append(idx)
        for k in range(nz):
            for i in range(nx):
                for j in (0, ny - 1):
                    idx = i + nx * (j + ny * k)
                    if f[idx] > 0 and not seen[idx]:
                        seen[idx] = 1
                        dq.append(idx)
        for j in range(ny):
            for i in range(nx):
                for k in (0, nz - 1):
                    idx = i + nx * (j + ny * k)
                    if f[idx] > 0 and not seen[idx]:
                        seen[idx] = 1
                        dq.append(idx)
        nxy = nx * ny
        while dq:
            idx = dq.popleft()
            for nb in (idx - 1, idx + 1, idx - nx, idx + nx, idx - nxy, idx + nxy):
                if 0 <= nb < n and not seen[nb] and f[nb] > 0:
                    seen[nb] = 1
                    dq.append(nb)
        void = 0
        for idx in range(n):
            if f[idx] > 0 and not seen[idx]:
                void += 1
        return void

    # ---------------------------------------------------------- 曲面网提取
    def surface_nets(self):
        """从标量场提取闭合三角网格（法线朝外由调用方保证/校验）。"""
        nx, ny, nz = self.nx, self.ny, self.nz
        f = self.field
        cnx, cny, cnz = nx - 1, ny - 1, nz - 1
        nxy = nx * ny
        cellvert = array("i", [-1]) * (cnx * cny * cnz)
        verts = []
        # 角点偏移 (di,dj,dk)，角序：低位=x, 中位=y, 高位=z
        corner_off = ((0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0),
                      (0, 0, 1), (1, 0, 1), (0, 1, 1), (1, 1, 1))
        edges = ((0, 1), (2, 3), (4, 5), (6, 7),   # x 向
                 (0, 2), (1, 3), (4, 6), (5, 7),   # y 向
                 (0, 4), (1, 5), (2, 6), (3, 7))   # z 向
        ox, oy, oz = self.ox, self.oy, self.oz
        dx, dy, dz = self.dx, self.dy, self.dz
        for k in range(cnz):
            for j in range(cny):
                rowbase = j * nx + k * nxy
                ci = j * cnx + k * cnx * cny
                for i in range(cnx):
                    b = rowbase + i
                    v0 = f[b]; v1 = f[b + 1]
                    v2 = f[b + nx]; v3 = f[b + nx + 1]
                    v4 = f[b + nxy]; v5 = f[b + nxy + 1]
                    v6 = f[b + nx + nxy]; v7 = f[b + nx + 1 + nxy]
                    s = ((v0 < 0) + (v1 < 0) + (v2 < 0) + (v3 < 0)
                         + (v4 < 0) + (v5 < 0) + (v6 < 0) + (v7 < 0))
                    if s == 0 or s == 8:
                        continue
                    vals = (v0, v1, v2, v3, v4, v5, v6, v7)
                    sx = sy = sz = 0.0
                    cnt = 0
                    for (ca, cb) in edges:
                        va, vb = vals[ca], vals[cb]
                        if (va < 0) == (vb < 0):
                            continue
                        t = va / (va - vb)
                        oa = corner_off[ca]
                        ob = corner_off[cb]
                        # 体素 (i,j,k) 的中心在 origin+(i+0.5)*pitch 处
                        sx += ox + (i + oa[0] + t * (ob[0] - oa[0]) + 0.5) * dx
                        sy += oy + (j + oa[1] + t * (ob[1] - oa[1]) + 0.5) * dy
                        sz += oz + (k + oa[2] + t * (ob[2] - oa[2]) + 0.5) * dz
                        cnt += 1
                    cellvert[ci + i] = len(verts)
                    verts.append((sx / cnt, sy / cnt, sz / cnt))
        tris = []

        def emit(q):
            # q: 4 个单元顶点索引（可能含 -1 表示网格外）
            if -1 in q:
                return
            tris.append((q[0], q[1], q[2]))
            tris.append((q[0], q[2], q[3]))

        cv = cellvert
        # x 向棱：相邻单元 (i, j-1, k-1),(i, j, k-1),(i, j, k),(i, j-1, k)
        for k in range(nz):
            for j in range(ny):
                b = j * nx + k * nxy
                for i in range(nx - 1):
                    fa, fb = f[b + i], f[b + i + 1]
                    if (fa < 0) == (fb < 0):
                        continue
                    q = (cv_get(cv, cnx, cny, i, j - 1, k - 1),
                         cv_get(cv, cnx, cny, i, j, k - 1),
                         cv_get(cv, cnx, cny, i, j, k),
                         cv_get(cv, cnx, cny, i, j - 1, k))
                    if fa >= 0:
                        q = (q[0], q[3], q[2], q[1])
                    emit(q)
        # y 向棱：相邻单元 (i, j, k-1),(i-1, j, k-1),(i-1, j, k),(i, j, k)
        # （从 +y 看屏幕右为 -x，此顺序与 x/z 向同为自实体向空侧的逆时针）
        for k in range(nz):
            for j in range(ny - 1):
                b = j * nx + k * nxy
                for i in range(nx):
                    fa, fb = f[b + i], f[b + i + nx]
                    if (fa < 0) == (fb < 0):
                        continue
                    q = (cv_get(cv, cnx, cny, i, j, k - 1),
                         cv_get(cv, cnx, cny, i - 1, j, k - 1),
                         cv_get(cv, cnx, cny, i - 1, j, k),
                         cv_get(cv, cnx, cny, i, j, k))
                    if fa >= 0:
                        q = (q[0], q[3], q[2], q[1])
                    emit(q)
        # z 向棱：相邻单元 (i-1, j-1, k),(i, j-1, k),(i, j, k),(i-1, j, k)
        for k in range(nz - 1):
            for j in range(ny):
                b = j * nx + k * nxy
                for i in range(nx):
                    fa, fb = f[b + i], f[b + i + nxy]
                    if (fa < 0) == (fb < 0):
                        continue
                    q = (cv_get(cv, cnx, cny, i - 1, j - 1, k),
                         cv_get(cv, cnx, cny, i, j - 1, k),
                         cv_get(cv, cnx, cny, i, j, k),
                         cv_get(cv, cnx, cny, i - 1, j, k))
                    if fa >= 0:
                        q = (q[0], q[3], q[2], q[1])
                    emit(q)
        return Mesh(verts, tris)

    def blocky_mesh(self):
        """保底方案：直接发射体素边界面（保证闭合、朝外）。"""
        verts, tris = [], []
        nx, ny, nz = self.nx, self.ny, self.nz
        ox, oy, oz = self.ox, self.oy, self.oz
        dx, dy, dz = self.dx, self.dy, self.dz
        f = self.field
        # 每个方向：4 个角偏移（相对体素下角 (i,j,k)，mm 单位），绕向朝外
        dirs = [
            ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
            ((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
            ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
            ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
            ((0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),
            ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
        ]
        for k in range(nz):
            for j in range(ny):
                base = nx * (j + ny * k)
                for i in range(nx):
                    if f[base + i] >= 0:
                        continue
                    for (di, dj, dk), quad in dirs:
                        ii, jj, kk = i + di, j + dj, k + dk
                        nb_solid = (0 <= ii < nx and 0 <= jj < ny and 0 <= kk < nz
                                    and f[ii + nx * (jj + ny * kk)] < 0)
                        if nb_solid:
                            continue
                        q = []
                        for (ci, cj, ck) in quad:
                            q.append(len(verts))
                            verts.append((ox + (i + ci) * dx,
                                          oy + (j + cj) * dy,
                                          oz + (k + ck) * dz))
                        tris.append((q[0], q[1], q[2]))
                        tris.append((q[0], q[2], q[3]))
        return Mesh(verts, tris)


def cv_get(cv, cnx, cny, i, j, k):
    if i < 0 or j < 0 or k < 0 or i >= cnx or j >= cny:
        return -1
    cnz = len(cv) // (cnx * cny)
    if k >= cnz:
        return -1
    return cv[i + cnx * (j + cny * k)]


def mesh_from_grid(grid: Grid, verify=True):
    """曲面网提取 + 朝外校正；失败时退回块状网格。返回 (mesh, style)。"""
    mesh = grid.surface_nets()
    closed, _ = edge_report(mesh)
    style = "surfacenets"
    if not closed:
        mesh = grid.blocky_mesh()
        style = "blocky-fallback"
    mesh = ensure_outward(mesh)
    if verify:
        closed, bad = edge_report(mesh)
        if not closed:
            raise RuntimeError("网格提取失败：不闭合 (bad edges=%d)" % bad)
    return mesh, style
