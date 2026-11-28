"""泄漏扫描器：检查一份网格文件是否夹带私有信息。

四类藏匿渠道（即使完全藏在实体内部也算已交出）：
  1. 内部薄壳   —— 封闭在实体内部的空腔/型腔表面（体素洪泛检出）
  2. 同坐标反向面 —— 顶点集合相同、绕向相反的面片对
  3. 组件名字   —— OBJ 的 o/g 名称、STL 头中的非中性文字
  4. 未引用顶点 —— 顶点表中不被任何面引用的点（可编码任意数据）

对输入（完整模型）扫描应全部报警；对外发件扫描应全部干净。
"""
from __future__ import annotations

import os

from geom.mesh import (Mesh, coincident_reversed_pairs, component_count,
                       edge_report, read_obj, read_stl,
                       unreferenced_vertex_count)
from geom.slicer import grid_from_mesh

SCAN_PITCH = 0.5  # 审计体素步长（粗一些，速度快）


def scan(path):
    rep = {"file": path}
    if path.endswith(".obj"):
        objects, loose = read_obj(path)
        names = [n for n, _m in objects]
        verts, tris = [], []
        for _n, m in objects:
            base = len(verts)
            verts.extend(m.verts)
            tris.extend([(a + base, b + base, c + base) for (a, b, c) in m.tris])
        mesh = Mesh(verts, tris)
        rep["format"] = "obj"
        rep["object_names"] = names
        rep["unreferenced_vertices"] = loose
        rep["header_text"] = None
    else:
        mesh = read_stl(path)
        with open(path, "rb") as fh:
            hdr = fh.read(80).rstrip(b"\0").decode("ascii", "replace")
        rep["format"] = "stl"
        rep["object_names"] = []
        rep["unreferenced_vertices"] = 0  # STL 为三角面汤，无顶点表，天然无未引用顶点
        rep["header_text"] = hdr

    closed, bad = edge_report(mesh)
    rep["closed"] = closed
    rep["bad_edges"] = bad
    rep["components"] = component_count(mesh)
    rep["coincident_reversed_pairs"] = coincident_reversed_pairs(mesh)
    rep["triangles"] = len(mesh.tris)

    grid = grid_from_mesh(mesh, SCAN_PITCH, pad=1.0)
    voids = grid.enclosed_void_count()
    rep["enclosed_void_voxels"] = voids
    rep["internal_shells"] = voids > 0

    leaks = []
    if rep["object_names"]:
        leaks.append("组件名字: %s" % ",".join(rep["object_names"]))
    if rep["unreferenced_vertices"]:
        leaks.append("未引用顶点 x%d" % rep["unreferenced_vertices"])
    if rep["coincident_reversed_pairs"]:
        leaks.append("同坐标反向面 x%d" % rep["coincident_reversed_pairs"])
    if rep["internal_shells"]:
        leaks.append("内部薄壳(封闭空腔体素 x%d)" % voids)
    if rep["header_text"] and rep["header_text"] not in ("public sanitized part",):
        leaks.append("STL头非中性: %r" % rep["header_text"])
    rep["leaks"] = leaks
    rep["clean"] = not leaks and closed and rep["components"] == 1
    return rep


def report_str(rep):
    lines = ["扫描 %s" % rep["file"]]
    lines.append("  格式=%s 三角面=%d 闭合=%s 连通分量=%d"
                 % (rep["format"], rep["triangles"], rep["closed"], rep["components"]))
    if rep["leaks"]:
        for lk in rep["leaks"]:
            lines.append("  [泄漏] %s" % lk)
    else:
        lines.append("  [干净] 未发现私有信息夹带")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print(report_str(scan(p)))
