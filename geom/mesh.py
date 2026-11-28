"""三角网格基础库：STL/OBJ 读写、完整性检查、刚体变换、射线求交。

仅使用 Python 标准库。坐标单位毫米 (mm)。
"""
from __future__ import annotations

import hashlib
import math
import struct
from collections import defaultdict

EPS = 1e-9


class Mesh:
    """三角网格：顶点表 + 三角形索引表。"""

    __slots__ = ("verts", "tris")

    def __init__(self, verts=None, tris=None):
        self.verts = verts if verts is not None else []  # [(x,y,z), ...]
        self.tris = tris if tris is not None else []     # [(a,b,c), ...]

    def bounds(self):
        xs = [v[0] for v in self.verts]
        ys = [v[1] for v in self.verts]
        zs = [v[2] for v in self.verts]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def transformed(self, mat, off=(0.0, 0.0, 0.0)):
        """mat: 3x3 行主序；off: 平移。返回新网格。"""
        (m0, m1, m2) = mat
        vs = [(
            m0[0] * x + m0[1] * y + m0[2] * z + off[0],
            m1[0] * x + m1[1] * y + m1[2] * z + off[1],
            m2[0] * x + m2[1] * y + m2[2] * z + off[2],
        ) for (x, y, z) in self.verts]
        return Mesh(vs, list(self.tris))


# ---------------------------------------------------------------- STL 读写

def write_stl_binary(mesh: Mesh, path: str, header: bytes = b"public sanitized part"):
    """标准二进制 STL：80 字节中性头 + 三角形数 + 50 字节/面。任何普通网格读取器可开。"""
    hdr = header[:80].ljust(80, b"\0")
    with open(path, "wb") as fh:
        fh.write(hdr)
        fh.write(struct.pack("<I", len(mesh.tris)))
        for (a, b, c) in mesh.tris:
            v0 = mesh.verts[a]
            v1 = mesh.verts[b]
            v2 = mesh.verts[c]
            ux, uy, uz = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
            wx, wy, wz = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
            nx, ny, nz = uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx
            ln = math.sqrt(nx * nx + ny * ny + nz * nz)
            if ln > EPS:
                nx, ny, nz = nx / ln, ny / ln, nz / ln
            else:
                nx, ny, nz = 0.0, 0.0, 0.0
            fh.write(struct.pack("<3f", nx, ny, nz))
            fh.write(struct.pack("<3f", *v0))
            fh.write(struct.pack("<3f", *v1))
            fh.write(struct.pack("<3f", *v2))
            fh.write(struct.pack("<H", 0))


def _weld(verts, tris, ndigits=6):
    """按坐标（四舍五入）合并重复顶点，重建索引。"""
    remap = {}
    new_v = []
    new_t = []
    for (a, b, c) in tris:
        tri = []
        for o in (a, b, c):
            key = tuple(round(c_, ndigits) for c_ in verts[o])
            idx = remap.get(key)
            if idx is None:
                idx = len(new_v)
                remap[key] = idx
                new_v.append(verts[o])
            tri.append(idx)
        new_t.append(tuple(tri))
    return Mesh(new_v, new_t)


def read_stl(path: str) -> Mesh:
    """读取二进制或 ASCII STL（严格按记录数判定格式）；顶点按坐标焊接。"""
    with open(path, "rb") as fh:
        data = fh.read()
    is_bin = False
    if len(data) >= 84:
        (n,) = struct.unpack_from("<I", data, 80)
        if 84 + 50 * n == len(data):
            is_bin = True
    if is_bin:
        (n,) = struct.unpack_from("<I", data, 80)
        verts, tris = [], []
        off = 84
        for _ in range(n):
            vals = struct.unpack_from("<12f", data, off)
            off += 50
            base = len(verts)
            verts.append((vals[3], vals[4], vals[5]))
            verts.append((vals[6], vals[7], vals[8]))
            verts.append((vals[9], vals[10], vals[11]))
            tris.append((base, base + 1, base + 2))
        return _weld(verts, tris)
    # ASCII
    text = data.decode("utf-8", "replace")
    verts, tris, cur = [], [], []
    for line in text.splitlines():
        w = line.strip().split()
        if len(w) == 4 and w[0] == "vertex":
            cur.append((float(w[1]), float(w[2]), float(w[3])))
            if len(cur) == 3:
                base = len(verts)
                verts.extend(cur)
                tris.append((base, base + 1, base + 2))
                cur = []
    return _weld(verts, tris)


# ---------------------------------------------------------------- OBJ 读写

def write_obj(path: str, objects, loose_verts=()):
    """写出多对象 OBJ。objects: [(名字, Mesh), ...]；loose_verts: 不被任何面引用的孤立顶点。"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# full studio model (含私有内构，不得外发)\n")
        offset = 1
        for name, mesh in objects:
            fh.write("o %s\n" % name)
            for v in mesh.verts:
                fh.write("v %.6f %.6f %.6f\n" % v)
            for (a, b, c) in mesh.tris:
                fh.write("f %d %d %d\n" % (a + offset, b + offset, c + offset))
            offset += len(mesh.verts)
        if loose_verts:
            fh.write("# unreferenced vertices below\n")
            for v in loose_verts:
                fh.write("v %.6f %.6f %.6f\n" % v)


def read_obj(path: str):
    """读取 OBJ -> (objects, loose_count)。
    objects: [(名字, Mesh)]，每个 Mesh 顶点已局部重映射（未引用顶点被剔除）；
    loose_count: 全局顶点表中未被任何面引用的顶点数。"""
    verts = []
    objects = []          # [name, [tri,...]]
    cur_name, cur_tris = "default", []
    used = set()

    def flush():
        nonlocal cur_tris
        if cur_tris:
            objects.append((cur_name, cur_tris))
        cur_tris = []

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("o ") or line.startswith("g "):
                flush()
                cur_name = line[2:].strip() or "unnamed"
            elif line.startswith("v "):
                p = line.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("f "):
                idx = []
                for tok in line.split()[1:]:
                    i = int(tok.split("/")[0])
                    if i < 0:
                        i = len(verts) + 1 + i
                    idx.append(i - 1)
                for k in range(1, len(idx) - 1):  # 扇形三角化
                    tri = (idx[0], idx[k], idx[k + 1])
                    cur_tris.append(tri)
                    used.update(tri)
    flush()
    loose = [i for i in range(len(verts)) if i not in used]
    out = []
    for name, tris in objects:
        remap, local_v, local_t = {}, [], []
        for (a, b, c) in tris:
            tri = []
            for o in (a, b, c):
                if o not in remap:
                    remap[o] = len(local_v)
                    local_v.append(verts[o])
                tri.append(remap[o])
            local_t.append(tuple(tri))
        out.append((name, Mesh(local_v, local_t)))
    return out, len(loose)


# ---------------------------------------------------------------- 完整性检查

def edge_report(mesh: Mesh):
    """返回 (closed, n_bad)。闭合 ⇔ 每条无向边恰好被两个三角形以相反方向使用。"""
    directed = defaultdict(int)
    for (a, b, c) in mesh.tris:
        directed[(a, b)] += 1
        directed[(b, c)] += 1
        directed[(c, a)] += 1
    bad = 0
    for (a, b), n in directed.items():
        if directed.get((b, a), 0) != n:
            bad += 1
    return bad == 0, bad


def component_count(mesh: Mesh) -> int:
    parent = list(range(len(mesh.verts)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b, c) in mesh.tris:
        ra, rb, rc = find(a), find(b), find(c)
        parent[ra] = rc
        parent[rb] = rc
    roots = set()
    for (a, b, c) in mesh.tris:
        roots.add(find(a))
    return len(roots)


def coincident_reversed_pairs(mesh: Mesh, ndigits=4):
    """同坐标反向面：顶点集合相同（坐标四舍五入）但绕向相反的面片对数。"""
    groups = defaultdict(list)
    for t in mesh.tris:
        key = tuple(sorted(tuple(round(c, ndigits) for c in mesh.verts[i]) for i in t))
        groups[key].append(t)
    pairs = 0
    for tris in groups.values():
        if len(tris) < 2:
            continue
        canons = set()
        for t in tris:
            rots = [(t[0], t[1], t[2]), (t[1], t[2], t[0]), (t[2], t[0], t[1])]
            canons.add(min(rots))
        # 同一点集若出现两种规范绕向，则存在反向对
        if len(canons) > 1:
            pairs += 1
        elif len(tris) > 2:
            pairs += 1
    return pairs


def unreferenced_vertex_count(mesh: Mesh) -> int:
    used = set()
    for (a, b, c) in mesh.tris:
        used.add(a)
        used.add(b)
        used.add(c)
    return len(mesh.verts) - len(used)


def signed_volume(mesh: Mesh) -> float:
    v = 0.0
    for (a, b, c) in mesh.tris:
        p, q, r = mesh.verts[a], mesh.verts[b], mesh.verts[c]
        v += (p[0] * (q[1] * r[2] - q[2] * r[1])
              - p[1] * (q[0] * r[2] - q[2] * r[0])
              + p[2] * (q[0] * r[1] - q[1] * r[0])) / 6.0
    return v


def flip_orientation(mesh: Mesh) -> Mesh:
    return Mesh(list(mesh.verts), [(a, c, b) for (a, b, c) in mesh.tris])


def ensure_outward(mesh: Mesh) -> Mesh:
    """按有符号体积保证法线朝外。"""
    if signed_volume(mesh) < 0:
        return flip_orientation(mesh)
    return mesh


def ray_intersections(mesh: Mesh, origin, direction, t_max=1e9):
    """Möller–Trumbore：返回沿射线的全部交点 t（升序）。"""
    ox, oy, oz = origin
    dx, dy, dz = direction
    hits = []
    for (a, b, c) in mesh.tris:
        v0 = mesh.verts[a]
        v1 = mesh.verts[b]
        v2 = mesh.verts[c]
        e1x, e1y, e1z = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
        e2x, e2y, e2z = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
        px, py, pz = dy * e2z - dz * e2y, dz * e2x - dx * e2z, dx * e2y - dy * e2x
        det = e1x * px + e1y * py + e1z * pz
        if -EPS < det < EPS:
            continue
        inv = 1.0 / det
        tx, ty, tz = ox - v0[0], oy - v0[1], oz - v0[2]
        u = (tx * px + ty * py + tz * pz) * inv
        if u < -1e-9 or u > 1.0 + 1e-9:
            continue
        qx, qy, qz = ty * e1z - tz * e1y, tz * e1x - tx * e1z, tx * e1y - ty * e1x
        vv = (dx * qx + dy * qy + dz * qz) * inv
        if vv < -1e-9 or u + vv > 1.0 + 1e-9:
            continue
        t = (e2x * qx + e2y * qy + e2z * qz) * inv
        if EPS < t < t_max:
            hits.append(t)
    hits.sort()
    return hits


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 常用朝向（24 种轴对齐旋转中的子集）

ORIENTATIONS = {
    "I": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "rotx_m90": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),   # (x,y,z)->(x, z,-y)
    "rotx_p90": ((1, 0, 0), (0, 0, -1), (0, 1, 0)),   # (x,y,z)->(x,-z, y)
    "roty_m90": ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
    "rotz_p90": ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    "rotz_180": ((-1, 0, 0), (0, -1, 0), (0, 0, 1)),
}


def apply_mat(mat, v):
    return (
        mat[0][0] * v[0] + mat[0][1] * v[1] + mat[0][2] * v[2],
        mat[1][0] * v[0] + mat[1][1] * v[1] + mat[1][2] * v[2],
        mat[2][0] * v[0] + mat[2][1] * v[1] + mat[2][2] * v[2],
    )
