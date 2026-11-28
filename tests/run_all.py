#!/usr/bin/env python3
"""端到端场景测试：外发重建 + 独立验件 全流程。

场景覆盖：
  S1 完整模型四类藏匿渠道全部检出（内部薄壳/反向面/组件名/未引用顶点）
  S2 外发件审计全过且泄漏扫描干净
  S3 公开外形一致、私有夹具不同的两份输入 -> 外发材料逐字节相同
  S4 内部通道改为获准对外服务孔 -> 外发件与切片轮廓随之改变
  S5 教具1：能印能装（SHIP）；超差变体被检具拒收（不采信声明公差）
  S6 教具2：薄壁让一个安装方向失效；还原件仅转动摆放方向，另一座照常交付
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from geom.mesh import ray_intersections, read_stl, sha256_file
from geom.slicer import MeshSlicer
from studio import leakscan, modeler, rebuilder
from workshop import station

OUT = "out"
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    print("  [%s] %s %s" % (mark, name, ("-- " + detail) if detail else ""))


def main():
    # ---------------------------------------------------------- S1 建模 + 输入泄漏扫描
    print("=" * 70)
    print("S1 设计室建模；扫描完整模型（应检出全部四类藏匿渠道）")
    print("=" * 70)
    modeler.build_all(OUT)

    scan_in = leakscan.scan(os.path.join(OUT, "models", "aid1", "full_model.obj"))
    print(leakscan.report_str(scan_in))
    check("S1 输入含内部薄壳", scan_in["internal_shells"])
    check("S1 输入含同坐标反向面", scan_in["coincident_reversed_pairs"] > 0)
    check("S1 输入含组件名字", len(scan_in["object_names"]) > 1)
    check("S1 输入含未引用顶点", scan_in["unreferenced_vertices"] > 0)

    # ---------------------------------------------------------- S2 外发重建
    print("=" * 70)
    print("S2 离线外发重建（仅依据获准外壳与接口）")
    print("=" * 70)
    audits = rebuilder.rebuild_all(os.path.join(OUT, "models"),
                                   os.path.join(OUT, "outbound"))
    for part, au in sorted(audits.items()):
        check("S2 外发件审计通过: %s" % part, au["ok"])
        check("S2 外发件泄漏扫描干净: %s" % part, au["leak_scan"]["clean"])

    # ---------------------------------------------------------- S3 外发材料比对
    print("=" * 70)
    print("S3 公开一致/私有不同 -> 外发材料逐字节相同")
    print("=" * 70)
    sha_a = sha256_file(os.path.join(OUT, "outbound", "aid1.stl"))
    sha_b = sha256_file(os.path.join(OUT, "outbound", "aid1b.stl"))
    sha_c = sha256_file(os.path.join(OUT, "outbound", "aid1c.stl"))
    spec_a = json.load(open(os.path.join(OUT, "models", "aid1", "public_spec.json")))
    spec_b = json.load(open(os.path.join(OUT, "models", "aid1b", "public_spec.json")))
    spec_a.pop("part")
    spec_b.pop("part")
    check("S3 两输入公开规格一致", spec_a == spec_b)
    check("S3 外发 STL 逐字节相同", sha_a == sha_b, "sha256=%s..." % sha_a[:16])
    check("S3 服务孔变体外发件不同", sha_a != sha_c)

    # ---------------------------------------------------------- S4 服务孔改变切片所见
    print("=" * 70)
    print("S4 内部通道改为获准对外服务孔 -> 切片所见应变")
    print("=" * 70)
    mesh_a = read_stl(os.path.join(OUT, "outbound", "aid1.stl"))
    mesh_c = read_stl(os.path.join(OUT, "outbound", "aid1c.stl"))
    # 射线沿服务孔轴线：A 应被实体截停（2 次交点），C 应贯通（0 次）
    hits_a = ray_intersections(mesh_a, (-5.0, 36.0, 15.0), (1, 0, 0), t_max=100)
    hits_c = ray_intersections(mesh_c, (-5.0, 36.0, 15.0), (1, 0, 0), t_max=100)
    check("S4 基准件通道被填实", len(hits_a) == 2, "交点 %d 个" % len(hits_a))
    check("S4 服务孔变体贯通", len(hits_c) == 0, "交点 %d 个" % len(hits_c))
    # z=15 层轮廓面积对比
    sa = MeshSlicer(mesh_a, 0.4, z0=0.0)
    sc = MeshSlicer(mesh_c, 0.4, z0=0.0)
    k = int(15.0 / 0.4 - 0.5)
    area_a = sum(abs(a) for _p, a in sa.loops_at(k)[0])
    area_c = sum(abs(a) for _p, a in sc.loops_at(k)[0])
    check("S4 z=15 层轮廓面积显著变化", abs(area_a - area_c) > 150.0,
          "Δ=%.1fmm²" % abs(area_a - area_c))

    # ---------------------------------------------------------- S5 教具1 验件
    print("=" * 70)
    print("S5 工坊验件：教具1 应放行；卡销孔超差变体应拒收")
    print("=" * 70)
    rep1 = station.run_job(os.path.join(OUT, "jobs", "aid1.job.json"))
    check("S5 教具1 放行", rep1["decision"] == "SHIP",
          "；".join(rep1["reasons"]))
    rep1e = station.run_job(os.path.join(OUT, "jobs", "aid1e.job.json"))
    check("S5 超差变体拒收", rep1e["decision"] == "REJECT")
    snap_bad = [p for p in rep1e.get("pins", []) if p["type"] == "snap"]
    check("S5 拒收原因为插销实测干涉",
          bool(snap_bad) and not snap_bad[0]["ok"]
          and snap_bad[0]["insert_max_violation"] > 0)

    # ---------------------------------------------------------- S6 教具2 定向失效
    print("=" * 70)
    print("S6 教具2：薄壁使一个安装方向失效；还原件转向后另一座照常交付")
    print("=" * 70)
    rep2m1 = station.run_job(os.path.join(OUT, "jobs", "aid2_m1.job.json"))
    check("S6 M1 方向因薄壁拒收", rep2m1["decision"] == "REJECT"
          and not rep2m1["wall"]["ok"],
          "薄壁层数 %d" % rep2m1["wall"]["n_flagged"])
    check("S6 M1 分层件仍可复原", rep2m1["restore"]["closed"]
          and rep2m1["restore"]["min_iou"] >= 0.9)
    rep2m2 = station.run_job(os.path.join(OUT, "jobs", "aid2_m2.job.json"))
    check("S6 还原件转向 M2 后放行", rep2m2["decision"] == "SHIP",
          "；".join(rep2m2["reasons"]))

    # ---------------------------------------------------------- 汇总
    print("=" * 70)
    n_pass = sum(1 for _n, ok, _d in RESULTS if ok)
    print("汇总：%d/%d 项通过" % (n_pass, len(RESULTS)))
    for name, ok, detail in RESULTS:
        if not ok:
            print("  未过：%s %s" % (name, detail))
    print("=" * 70)
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
