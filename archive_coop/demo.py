"""任务规定的全套演练（普通入口亦可逐项手工执行）。

顺序：
0. 保障单：两种互斥保证各封一卷，说明为什么不能对同一卷同时许诺；签收/退件。
1. 一卷两页，取足额份恢复两页。
2. 把两张必要份换成同一份的两次移交回单（封套在途）-> 开封必须失败。
3. 还原足额材料；补发换代后只把第二页所有副本换成旧页
   -> 第一页仍可作核准原页交回，但不能签“两页均已恢复”。
4. 旧版份额照实登记；拿另一卷的新份补本卷必须被拒。
5. 西库全失联 / 东库整批交管两种灾备态势，对两种配置分别演练。
6. 普通入口再封一本不同卷；重开程序（新进程）后凭恢复单查回具体交页。
"""

import os
import shutil
import subprocess
import sys

from .cabinet import IssuingCabinet
from .policy import SITE_RECOVERY, SPLIT_SITE
from .storage import Storage
from .workbench import RecoveryError, Workbench

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    mark = "✔" if ok else "✘"
    print(f"  [{mark}] {name}" + (f" —— {detail}" if detail else ""))


def section(title: str):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def env_of(storage: Storage, volume_id: str, holder: str, generation: int = None) -> dict:
    ledger = storage.load_ledger()
    for env_id in ledger["custodians"][holder]["envelopes"]:
        env = storage.read_envelope(env_id)
        if env["volume_id"] == volume_id and (generation is None or env["generation"] == generation):
            return env
    raise KeyError(f"{holder} 柜中找不到卷 {volume_id} 的封套")


def all_envs(storage: Storage, volume_id: str) -> list[dict]:
    """扫描封套目录，包含已 superseded、已移出柜存清单的旧代封套。"""
    out = []
    env_dir = os.path.join(storage.root, "envelopes")
    for name in os.listdir(env_dir):
        if not name.endswith(".json"):
            continue
        env = storage.read_envelope(name[:-5])
        if env["volume_id"] == volume_id:
            out.append(env)
    return out


def run_demo(root: str):
    if os.path.exists(root):
        shutil.rmtree(root)
    storage = Storage(root)
    cabinet = IssuingCabinet(storage)
    bench = Workbench(storage, cabinet)

    # ------------------------------------------------------------------
    section("0. 保障单：两种保证互斥，档主签收或退件")
    pages = [(1, "东库西库共同保管的第一页"), (2, "东库西库共同保管的第二页")]
    cabinet.seal_volume("V1", pages, SITE_RECOVERY)
    from .policy import build_guarantee
    g1 = build_guarantee("V1", SITE_RECOVERY, 2)
    print("  V1 保障单（" + g1["title"] + "）：")
    for line in g1["lines"]:
        print("    · " + line)
    cabinet.record_guarantee_decision(g1, "signed")
    check("V1 保障单已签收", storage.load_ledger()["guarantees"]["V1"]["status"] == "signed")

    cabinet.seal_volume("V1S", pages, SPLIT_SITE)
    g2 = build_guarantee("V1S", SPLIT_SITE, 2)
    print("  V1S 保障单（" + g2["title"] + "）：")
    for line in g2["lines"]:
        print("    · " + line)
    cabinet.record_guarantee_decision(g2, "rejected")
    check("档主可退件（V1S 退回，不用该配置封此卷）",
          storage.load_ledger()["guarantees"]["V1S"]["status"] == "rejected")
    # 退件不影响 V1S 已存在的封卷事实；演练继续用已签收的 V1 做主流程
    check("两种保证对同一卷方向相反，不能同时许诺（SITE_RECOVERY 东库可开/代管不可防；SPLIT_SITE 反之）",
          g1["lines"][2] != g2["lines"][2])

    # ------------------------------------------------------------------
    section("1. 足额份恢复一卷两页")
    order = bench.recover("V1", ["东甲", "西甲"])
    check("两份不同份位到场，开封成功", order["all_pages_recovered"] is True)
    check("两页均为核准原页", [p["page_no"] for p in order["delivered_pages"]] == [1, 2])
    check("交回的是实际页文本",
          order["delivered_pages"][0]["text"] == pages[0][1]
          and order["delivered_pages"][1]["text"] == pages[1][1])
    check("共同底线：单人（东甲）只有 1 份 < 门限 2，不能开卷",
          _recover_fails(bench, "V1", ["东甲"]))
    order_id_v1 = order["order_id"]
    print(f"    恢复单号：{order_id_v1}")

    # ------------------------------------------------------------------
    section("2. 两张必要份 → 同一份位的两次移交回单（在途），开封必须失败")
    env_a = env_of(storage, "V1", "东甲")
    env_b = env_of(storage, "V1", "西甲")
    rcp1 = cabinet.start_handover(env_a["envelope_id"], "东甲", "东乙")
    rcp2 = cabinet.start_handover(env_b["envelope_id"], "西甲", "西乙")
    print(f"    回单1 {rcp1['receipt_id']}（份位 {rcp1['index']}，状态 {rcp1['state']}）")
    print(f"    回单2 {rcp2['receipt_id']}（份位 {rcp2['index']}，状态 {rcp2['state']}）")
    check("两只盒子在途，双方柜存里都已没有这两个封套",
          len(bench.present_envelopes("V1", ["东甲", "东乙", "西甲", "西乙"])) == 2)  # 剩下东乙、西乙原有封套
    # 值班员试图把“回单”当材料提交：回单不是封套，柜内读取/核验直接拒绝
    rejected_receipt = False
    try:
        cabinet.combine_for_recovery("V1", [rcp1["receipt_id"], rcp2["receipt_id"]])
    except (ValueError, FileNotFoundError):
        rejected_receipt = True
    check("回单不能作为有效材料提交", rejected_receipt)
    # 即便按回单指向的在途封套提交，也因状态 in_transit 被拒，门限不足而失败
    rejected_intransit = False
    try:
        bench.recover("V1", [], force_envelope_ids=[env_a["envelope_id"], env_b["envelope_id"]])
    except RecoveryError as exc:
        rejected_intransit = "在途" in str(exc) or "in_transit" in str(exc) or "不足" in str(exc)
        print(f"    恢复台答复：{exc}")
    check("在途封套提交被拒且门限不足，开封失败", rejected_intransit)
    # 还原：撤销两笔交接，封套回原柜
    cabinet.cancel_handover(rcp1["receipt_id"])
    cabinet.cancel_handover(rcp2["receipt_id"])
    check("撤销交接后材料还原，足额恢复再次成功",
          bench.recover("V1", ["东甲", "西甲"])["all_pages_recovered"] is True)
    # 同份位重复也不能凑数（两只盒子/两名到场人规则）
    dup = False
    try:
        cabinet.combine_for_recovery("V1", [env_a["envelope_id"], env_a["envelope_id"]])
    except ValueError as exc:
        dup = "重复" in str(exc)
    check("同一份位交两次不被当成两份材料", dup)

    # ------------------------------------------------------------------
    section("3. 补发换代后，第二页全部副本换成旧页")
    # 先用足额份补发（门限 2），生成第 2 代份额与页标签
    gen1_envs = [env_of(storage, "V1", "东甲", 1)["envelope_id"],
                 env_of(storage, "V1", "西甲", 1)["envelope_id"]]
    cabinet.reissue("V1", gen1_envs)
    vol = storage.load_ledger()["volumes"]["V1"]
    check("补发后卷升到第 2 代", vol["generation"] == 2)
    old_envs = [e for e in all_envs(storage, "V1") if e["generation"] == 1]
    check("旧版份额照实登记：4 个旧封套全部 superseded，且记录现持有人",
          len(old_envs) == 4 and all(e["status"] == "superseded" for e in old_envs))
    for e in old_envs:
        hist = [h["event"] for h in e["history"]]
        check(f"旧封套 {e['envelope_id']}（份位 {e['index']}）留痕 {hist[-1]}",
              hist[-1] == "superseded")
    check("旧代份额不能开新卷",
          _combine_fails(cabinet, "V1", [e["envelope_id"] for e in old_envs[:2]]))

    # 只把第二页的所有副本换成旧代页（第一页不动）
    snapshot = bench.swap_page_with_old_generation("V1", 2)
    print(f"    第 2 页现存副本已全部替换为旧代快照：{snapshot}")
    order2 = bench.recover("V1", ["东甲", "西甲"])
    check("第一页仍通过核对，作为核准原页交回",
          [p["page_no"] for p in order2["delivered_pages"]] == [1]
          and order2["delivered_pages"][0]["text"] == pages[0][1])
    check("第二页所有副本核对失败（旧标签），列入 failed_pages",
          [p["page_no"] for p in order2["failed_pages"]] == [2])
    check("不能签“两页均已恢复”",
          order2["all_pages_recovered"] is False and "不能签" in order2["signed_statement"])
    print(f"    恢复台结论：{order2['signed_statement']}")

    # ------------------------------------------------------------------
    section("4. 不能拿另一卷的新份补足本卷")
    cabinet.seal_volume("V2", [(1, "另一本卷的唯一一页")], SITE_RECOVERY)
    v2_env = env_of(storage, "V2", "东甲")
    cross = False
    try:
        cabinet.reissue("V1", [env_of(storage, "V1", "东甲", 2)["envelope_id"], v2_env["envelope_id"]])
    except ValueError as exc:
        cross = "另一卷" in str(exc)
        print(f"    补发柜答复：{exc}")
    check("跨卷份额被发行柜拒绝", cross)
    cross2 = False
    try:
        cabinet.combine_for_recovery("V1", [env_of(storage, "V1", "东甲", 2)["envelope_id"], v2_env["envelope_id"]])
    except ValueError as exc:
        cross2 = "另一卷" in str(exc)
    check("跨卷份额在恢复合份时同样被拒", cross2)

    # ------------------------------------------------------------------
    section("5. 灾备演练：西库全失联 / 东库整批临时交管")
    for vid, policy_name in (("V1", SITE_RECOVERY), ("V1S", SPLIT_SITE)):
        print(f"\n  -- 卷 {vid}（{policy_name}）--")
        r_west = bench.scenario_west_offline(vid)
        r_escrow = bench.scenario_east_escrow(vid)
        print(f"    西库全失联：到场份位 {r_west['present_indices']}，{r_west['conclusion']}")
        print(f"    东库交管：代管方份位 {r_escrow['present_indices']}，{r_escrow['conclusion']}")
        if policy_name == SITE_RECOVERY:
            check(f"{vid} 西库断线时东库可自力恢复", r_west["can_open"] is True)
            check(f"{vid} 东库整批交管时代管方可凑齐（该配置不防代管）", r_escrow["can_open"] is True)
        else:
            check(f"{vid} 西库断线时东库无法自力恢复", r_west["can_open"] is False)
            check(f"{vid} 东库整批交管时代管方份位不足，不能开卷", r_escrow["can_open"] is False)
        # 两保证互斥：同一卷不可能既“东库单独可开”又“东库被整体拿走也安全”
        east_alone = r_west["can_open"]
        escrow_safe = not r_escrow["can_open"]
        check(f"{vid} 两种保证互斥，未同时许诺（东库自力恢复={east_alone}，防代管={escrow_safe}）",
              not (east_alone and escrow_safe))

    # 交接尚未签收时的灾备承诺：在途材料两边都不可用
    env_dj = env_of(storage, "V1", "东甲", 2)
    cabinet.start_handover(env_dj["envelope_id"], "东甲", "东乙")
    present_during_handover = bench.present_envelopes("V1", ["东甲", "东乙"])
    indices = sorted(e["index"] for e in present_during_handover)
    check("交接未签收期间，在途份位不计入任何一方有效柜存（东库当前有效份位：%s）" % indices,
          env_dj["index"] not in indices)
    print("    即：在途材料未签收前，只能按“缺这一份”承诺灾备，不能提前计入。")
    cabinet.cancel_handover(storage.load_ledger()["pending_handover"][-1]["receipt_id"])

    # ------------------------------------------------------------------
    section("6. 重开程序（新进程）后凭恢复单查回交给档主的页")
    root_abs = os.path.abspath(root)
    # 用普通入口在新进程里再封一本不同卷并恢复，验证普通入口可用
    cmd = [sys.executable, "-m", "archive_coop.cli", "--root", root_abs,
           "seal", "V3", "--pages", "1=普通入口封入的页面", "--policy", SITE_RECOVERY]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    cmd = [sys.executable, "-m", "archive_coop.cli", "--root", root_abs,
           "guarantee", "V3", "--sign"]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    cmd = [sys.executable, "-m", "archive_coop.cli", "--root", root_abs,
           "recover", "V3", "--custodians", "东乙", "西乙"]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    import json as _json
    v3_order = _json.loads(out.stdout)
    check("普通入口新进程封卷并恢复成功", v3_order["all_pages_recovered"] is True)
    # 再开一个全新进程，仅凭旧恢复单号查 V1 当时交回的页
    cmd = [sys.executable, "-m", "archive_coop.cli", "--root", root_abs, "order", order_id_v1]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    old_order = _json.loads(out.stdout)
    check("重开程序后凭恢复单查回 V1 交页",
          old_order["order_id"] == order_id_v1
          and [p["text"] for p in old_order["delivered_pages"]] == [pages[0][1], pages[1][1]])

    # ------------------------------------------------------------------
    section("演练结果汇总")
    failed = [r for r in results if not r[1]]
    for name, ok, _ in results:
        print(f"  {PASS if ok else FAIL}  {name}")
    print(f"\n共 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项。")
    if failed:
        sys.exit(1)


def _recover_fails(bench: Workbench, volume_id: str, custodians: list[str]) -> bool:
    try:
        bench.recover(volume_id, custodians)
    except RecoveryError:
        return True
    return False


def _combine_fails(cabinet: IssuingCabinet, volume_id: str, env_ids: list[str]) -> bool:
    try:
        cabinet.combine_for_recovery(volume_id, env_ids)
    except ValueError:
        return True
    return False
