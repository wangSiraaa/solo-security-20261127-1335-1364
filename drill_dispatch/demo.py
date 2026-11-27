#!/usr/bin/env python3
"""端到端安全演练（可重放）。

阶段：
  P1 正式柜一次成功取件开局（取件 -> 复检 -> 领物条）
  P2 正式作业超时重试：回条丢失，幂等重传，已发件计数只 +1
  P3 正式作业半途崩溃：重启捡起悬置 INTENT，同样不重发
  D0 封存正式域、快照替身柜、历史回条转只读证据
  D1 演练室整段重放：正式计数不动，演练内证据充分即办成
  D2 演练域引用缓存里的正式柜名：fail-closed
  D3 改单 A：来源凭据移到另一正式单号，旧回条不能当本单初态
  D4 改单 B：恢复来源，仅把演练柜合法接收者换成另一领用组，
     原组的可复原取件不得算已被原组领走
  D5 缺回执：持续超时安全停手 -> 解除故障后按幂等键拿回原回条
  D6 出演练结论（含可验证范围）
"""
from __future__ import annotations

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drill_dispatch.codec import read_json
from drill_dispatch.drillmaster import ClaimError, DrillMaster
from drill_dispatch.external import Cabinet, ReinspectStation
from drill_dispatch.router import (
    CRASH_ONCE,
    DomainGuardError,
    ProcessCrash,
    Router,
    TIMEOUT_ALWAYS,
    TIMEOUT_ONCE,
)
from drill_dispatch.runner import JobRejected, JobUnknown, Runner
from drill_dispatch import inspector

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime")
PROD_JOBS = os.path.join(ROOT, "prod", "jobs")
DRILL_JOBS = os.path.join(ROOT, "drill", "jobs")

STEP = 0


def h1(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def h2(title: str) -> None:
    print(f"\n--- {title} ---")


def check(cond: bool, msg: str) -> None:
    global STEP
    STEP += 1
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {STEP:02d} {msg}")
    if not cond:
        raise AssertionError(msg)


def counters(router_root: str = ROOT) -> dict:
    return DrillMaster(ROOT).counter_snapshot()


def prod_cabinet() -> Cabinet:
    """从磁盘重新加载正式柜（路由器调用后内存对象会过期）。"""
    return Cabinet(ROOT, "prod", "vault-main")


def prod_station() -> ReinspectStation:
    return ReinspectStation(ROOT, "prod", "reinspect-1")


def shadow_router(shadow_name: str) -> Router:
    """每个替身柜一套独立别名，避免演练作业串到别人的柜上。"""
    return Router(
        ROOT,
        domain_aliases={
            "vault-main": shadow_name,
            "reinspect-1": "shadow-reinspect",
        },
    )


def main() -> int:
    if os.path.exists(ROOT):
        shutil.rmtree(ROOT)

    # ================================================================
    # 开局准备：正式保管柜、复检站、柜内两件
    # ================================================================
    h1("开局：建立正式域（prod/ 与 drill/ 两个本地目录互不相交）")
    cabinet = Cabinet(ROOT, "prod", "vault-main")
    station = ReinspectStation(ROOT, "prod", "reinspect-1")
    cabinet.load_items(
        {
            "ITEM-A": {"allowed_receiver": "GROUP-A"},
            "ITEM-B": {"allowed_receiver": "GROUP-A"},
        }
    )
    router = Router(ROOT)
    print("正式柜初态：", prod_cabinet().item_view())
    check(prod_cabinet().state["dispatched"] == 0, "正式柜已发件计数初始为 0")

    # ----------------------------------------------------------------
    # P1 正式柜一次成功取件开局
    # ----------------------------------------------------------------
    h1("P1 正式柜一次成功取件：取件 -> 复检 -> 签出领物条")
    order_a = {
        "order_id": "WO-1001",
        "item_id": "ITEM-A",
        "receiver": "GROUP-A",
        "cabinet": "vault-main",
        "station": "reinspect-1",
        "purpose": "例行复检",
    }
    runner1 = Runner(ROOT, PROD_JOBS, "JOB-P1", order_a, "prod", router)
    result1 = runner1.run()
    print("作业结果：", result1)
    check(prod_cabinet().state["dispatched"] == 1, "成功取件后正式已发件计数 = 1")
    slip1 = runner1.journal.slip()
    check(slip1 is not None and slip1["domain"] == "prod", "正式领物条已签出")
    take_receipt_a = runner1._receipt_of("take")
    print("取件回条：", take_receipt_a["receipt_id"])

    # ----------------------------------------------------------------
    # P2 超时重试：回条丢失式超时，幂等重传只产生一张回条
    # ----------------------------------------------------------------
    h1("P2 正式作业复检超时：同意图重传，正式柜绝不多吐一张单")
    order_b = {
        "order_id": "WO-1002",
        "item_id": "ITEM-B",
        "receiver": "GROUP-A",
        "cabinet": "vault-main",
        "station": "reinspect-1",
        "purpose": "周期复检",
    }
    router.set_fault("JOB-P2", "check", TIMEOUT_ONCE)
    runner2 = Runner(ROOT, PROD_JOBS, "JOB-P2", order_b, "prod", router)
    result2 = runner2.run()
    print("作业结果：", result2)
    check(prod_station().state["seq"] == 2, "复检站两次调用只落 1 个序号（重传被幂等去重）")
    check(prod_cabinet().state["dispatched"] == 2, "正式已发件计数仅 +1（取件只办了一次）")
    take_actions = [a for a in prod_cabinet().actions() if a["kind"] == "take"]
    check(len(take_actions) == 2, "正式柜台账只有两条取件动作")

    # ----------------------------------------------------------------
    # P3 半途崩溃：重启恢复悬置 INTENT
    # ----------------------------------------------------------------
    h1("P3 正式作业半途崩溃：重启重放悬置意图，不重复发件")
    order_c = dict(order_b, order_id="WO-1003", item_id="ITEM-A",
                   purpose="复检后再领用演示（复用回柜件）")
    # ITEM-A 已被 P1 取走，先由正式流程退回，制造在柜初态
    prod_cabinet().give_back(
        {
            "idempotency_key": "manual-return-ITEM-A",
            "intent_digest": "manual",
            "ticket": {
                "order_id": "WO-1001", "item_id": "ITEM-A", "receiver": "GROUP-A"
            },
        }
    )
    router.set_fault("JOB-P3", "take", CRASH_ONCE)
    runner3 = Runner(ROOT, PROD_JOBS, "JOB-P3", order_c, "prod", router)
    crashed = False
    try:
        runner3.run()
    except ProcessCrash as crash:
        crashed = True
        print("捕获模拟崩溃：", crash)
    check(crashed, "取件落账后作业进程崩溃，OBSERVATION 未写")
    pending_before = inspector.query_job(PROD_JOBS, "JOB-P3")["pending_intents"]
    check(len(pending_before) == 1 and pending_before[0]["step"] == "take",
          "崩溃后日志里留着一条悬置 take INTENT")
    dispatched_at_crash = prod_cabinet().state["dispatched"]

    # “重启”：新建同一 job_id 的执行器，故障已消耗不会复现
    runner3r = Runner(ROOT, PROD_JOBS, "JOB-P3", order_c, "prod", router)
    resume = runner3r.resume_after_crash()
    print("恢复结果：", resume)
    check(len(resume["resumed_pending"]) == 1, "重启识别并捡起悬置交接")
    check(prod_cabinet().state["dispatched"] == dispatched_at_crash,
          "恢复后正式已发件计数不增长（没有二次发件）")
    check(runner3r.journal.get_state()["status"] == "COMPLETED", "崩溃作业最终办成")
    actions_p3 = prod_cabinet().actions()
    check(
        sorted(a["kind"] for a in actions_p3) == ["return", "take", "take", "take"],
        "P3 结束时正式柜台账：3 取（崩溃那笔仅一次）+ 1 退",
    )
    check(len({a["idem_key"] for a in actions_p3 if a["kind"] == "take"}) == 3,
          "3 个不同取件幂等键，崩溃那笔只落了一次账")

    prod_counters = None  # 封存后再拍，作为“正式计数不动”的比较基准

    # ================================================================
    # D0 封存正式域、快照、证据
    # ================================================================
    h1("D0 切域：封存正式出口，快照替身柜，历史回条转只读证据")
    dm = DrillMaster(ROOT)
    # 基线快照必须取在“演练初态”：把 P3 取走的 ITEM-A 退回后再拍
    prod_cabinet().give_back(
        {
            "idempotency_key": "manual-return-ITEM-A-2",
            "intent_digest": "manual",
            "ticket": {
                "order_id": "WO-1003", "item_id": "ITEM-A", "receiver": "GROUP-A"
            },
        }
    )
    snap = dm.take_baseline(prod_cabinet())
    check(
        sorted(a["kind"] for a in prod_cabinet().actions())
        == ["return", "return", "take", "take", "take"],
        "封存前正式柜台账：3 取 + 2 退，全程无重复发件条目",
    )
    print("基线快照：", snap["snapshot_id"][:16], "…，物件：",
          {k: v["status"] for k, v in snap["items"].items()})
    dm.seal_production()
    for jid in ("JOB-P1", "JOB-P2", "JOB-P3"):
        dm.import_evidence(PROD_JOBS, jid)
    check(not dm.verify_evidence(), "封存证据哈希清单完整、只读")
    try:
        Router(ROOT).call("prod", "JOB-X", "take", "vault-main", "take", {})
        check(False, "封存后正式调用应被拒绝")
    except DomainGuardError as e:
        check(True, f"正式出口已切断（{e}）")
    prod_counters = counters()
    print("正式域计数（封存基准）：", prod_counters)

    # ================================================================
    # D1 演练室整段重放
    # ================================================================
    h1("D1 演练室整段重放：正式计数不动，证据充分即办成")
    shadow1 = dm.build_shadow("shadow-vault-1")
    shadow_station = ReinspectStation(ROOT, "drill", "shadow-reinspect",
                                      genesis=snap["snapshot_id"])
    aliases = {"vault-main": "shadow-vault-1", "reinspect-1": "shadow-reinspect"}
    drill_router = Router(ROOT, domain_aliases=aliases)

    drill_order_a = dict(order_a, cabinet_shadow="shadow-vault-1")
    runner_d1 = dm.start_drill_job(
        DRILL_JOBS, "JOB-D1",
        dict(drill_order_a, station="reinspect-1", cabinet="vault-main"),
        drill_router, source_receipt=take_receipt_a,
    )
    d1 = runner_d1.run()
    print("演练作业结果：", d1)
    after_d1 = counters()
    check(after_d1["prod"] == prod_counters["prod"],
          "演练重放后正式柜 seq / 已发件计数完全不动")
    check(after_d1["drill"]["shadow-vault-1"]["dispatched"] == 1,
          "替身柜独立已发件计数 = 1（本域真实取件）")
    drill_receipt = runner_d1._receipt_of("take")
    check(drill_receipt["domain"] == "drill"
          and drill_receipt["receipt_id"].startswith("drill-"),
          "新回条带演练域标记，不能冒充历史见证")
    check(runner_d1.journal.slip()["based_on_receipts"]
          == [drill_receipt["receipt_id"],
              runner_d1._receipt_of("check")["receipt_id"]],
          "演练领物条只引用本作业自己拿到的回条")

    # 再重放一次同一演练作业：幂等，计数仍为 1
    runner_d1b = Runner(ROOT, DRILL_JOBS, "JOB-D1",
                        runner_d1.order, "drill", drill_router)
    runner_d1b.run()
    check(counters()["drill"]["shadow-vault-1"]["dispatched"] == 1,
          "同一演练作业再重放，替身柜也不重复发件")

    # ----------------------------------------------------------------
    # D2 缓存里的正式柜名未登记 -> fail-closed
    # ----------------------------------------------------------------
    h1("D2 演练作业携带缓存的正式柜名：fail-closed 拒绝")
    rogue_router = Router(ROOT, domain_aliases={"reinspect-1": "shadow-reinspect"})
    # 故意不登记 vault-main（模拟缓存残留正式名）；单号保持 WO-1001 以通过初态声明，
    # 让失败点精确落在域守卫上。
    rogue_order = dict(order_a, order_id="WO-1001",
                       cabinet="vault-main", cabinet_shadow="shadow-vault-1")
    try:
        rogue = dm.start_drill_job(DRILL_JOBS, "JOB-D2", rogue_order,
                                   rogue_router, source_receipt=take_receipt_a)
        rogue.run()
        check(False, "未登记正式柜名应被拦截")
    except DomainGuardError as e:
        check(True, f"域守卫拦截跨域取件（{e}）")
    check(counters()["prod"] == prod_counters["prod"], "被拦后正式计数依然不动")

    # ================================================================
    # D3 改单 A：来源凭据移到另一正式单号
    # ================================================================
    h1("D3 改单 A：来源凭据移到另一正式单号，旧回条不能当本单初态")
    shadow2 = dm.build_shadow("shadow-vault-2")
    router2 = shadow_router("shadow-vault-2")
    moved_order = dict(
        order_a, order_id="WO-3001", cabinet="vault-main",
        station="reinspect-1", cabinet_shadow="shadow-vault-2",
    )
    try:
        dm.start_drill_job(DRILL_JOBS, "JOB-D3", moved_order, router2,
                           source_receipt=take_receipt_a)
        check(False, "跨单复用来源回条应被初态声明拒绝")
    except ClaimError as e:
        check(True, f"初态声明被拒（{e}）")

    # 复制物同样不行：拿历史回条的 JSON 副本冒充
    forged = dict(take_receipt_a)
    forged["receipt_id"] = "forged-copy"
    try:
        dm.start_drill_job(DRILL_JOBS, "JOB-D3b", moved_order, router2,
                           source_receipt=forged)
        check(False, "复制/改写回条应被拒绝")
    except ClaimError as e:
        check(True, f"复制回条被拒（{e}）")

    # 新单不借历史回条、以快照在柜状态为初态，可以在替身柜真实办成
    fresh = dm.start_drill_job(DRILL_JOBS, "JOB-D3c", moved_order, router2)
    fresh_result = fresh.run()
    fresh_receipt = fresh._receipt_of("take")
    check(fresh_receipt["domain"] == "drill"
          and fresh_receipt["order_id"] == "WO-3001",
          "新单凭本域真实取件拿演练回条，与历史回条完全无关")
    check(take_receipt_a["order_id"] == "WO-1001",
          "原始回条仍只证明 WO-1001 那次领出了哪件")

    # ================================================================
    # D4 改单 B：仅更换演练柜合法接收者
    # ================================================================
    h1("D4 改单 B：恢复来源，仅把演练柜合法接收者换成 GROUP-B")
    shadow3 = dm.build_shadow("shadow-vault-3")
    router3 = shadow_router("shadow-vault-3")
    shadow3.replace_receiver("ITEM-A", "GROUP-B")
    check(read_json(os.path.join(ROOT, "drill", "shadow-vault-3", "state.json"))
          ["items"]["ITEM-A"]["allowed_receiver"] == "GROUP-B",
          "替身柜合法接收者已改为 GROUP-B（正式柜配属未动）")

    order_b_group_a = dict(
        order_a, order_id="WO-1001", cabinet="vault-main",
        station="reinspect-1", cabinet_shadow="shadow-vault-3",
        receiver="GROUP-A",
    )
    try:
        dm.start_drill_job(DRILL_JOBS, "JOB-D4a", order_b_group_a, router3,
                           source_receipt=take_receipt_a)
        check(False, "原组在改配后的替身柜上应被拒绝")
    except ClaimError as e:
        check(True, f"GROUP-A 初态声明不成立（{e}）")

    # 即便绕过声明直接打柜，柜端授权也拒绝原组
    direct = Runner(ROOT, DRILL_JOBS, "JOB-D4direct",
                    order_b_group_a, "drill", router3)
    try:
        direct.run()
        check(False, "柜端应拒绝非合法接收者")
    except JobRejected as e:
        check(True, f"替身柜柜端拒绝 GROUP-A（{e}）")

    # 新接收者 GROUP-B 可以真实办成；其回条不能算到 GROUP-A 头上
    order_b_group_b = dict(order_b_group_a, receiver="GROUP-B")
    runner_b = dm.start_drill_job(DRILL_JOBS, "JOB-D4b", order_b_group_b,
                                  router3)
    runner_b.run()
    gb_receipt = runner_b._receipt_of("take")
    check(gb_receipt["receiver"] == "GROUP-B"
          and gb_receipt["domain"] == "drill",
          "同一次可复原的取件由 GROUP-B 领走，回条记名 GROUP-B")
    # 历史证据里 GROUP-A 的正式回条仍是正式域那次的见证，两笔账互不冒充
    evidence_receipt = read_json(
        os.path.join(ROOT, "evidence", "JOB-P1", "slip.json")
    )
    check(evidence_receipt["receiver"] == "GROUP-A"
          and evidence_receipt["domain"] == "prod",
          "GROUP-A 历史见证原样封存，演练新收条无法顶替它")

    # ================================================================
    # D5 缺回执：持续超时 -> 安全停手 -> 恢复
    # ================================================================
    h1("D5 缺回执演练：持续超时安全停手，解除后按幂等键取回原回条")
    shadow4 = dm.build_shadow("shadow-vault-4")
    router4 = shadow_router("shadow-vault-4")
    router4.set_fault("JOB-D5", "take", TIMEOUT_ALWAYS)
    order_d5 = dict(order_a, order_id="WO-4001",
                    cabinet="vault-main", station="reinspect-1",
                    cabinet_shadow="shadow-vault-4")
    runner_d5 = dm.start_drill_job(DRILL_JOBS, "JOB-D5", order_d5, router4)
    unknown = False
    try:
        runner_d5.run()
    except JobUnknown as e:
        unknown = True
        print("安全停手：", e)
    check(unknown, "重试有界，缺回执时作业挂 UNKNOWN 而非重发")
    dispatched_before_fix = counters()["drill"].get("shadow-vault-4",
                                                    {"dispatched": 0})
    # 服务端可能已办过一次：查替身柜台账确认事实，而不是猜
    svc_view = inspector.query_service(ROOT, "drill", "shadow-vault-4")
    print("替身柜实际动作：", svc_view["actions"])
    router4.clear_faults("JOB-D5")
    runner_d5r = Runner(ROOT, DRILL_JOBS, "JOB-D5", order_d5, "drill",
                        router4)
    d5_resume = runner_d5r.resume_after_crash()
    print("恢复结果：", d5_resume)
    check(runner_d5r.journal.get_state()["status"] == "COMPLETED",
          "故障解除后悬置交接重放成功")
    check(counters()["drill"]["shadow-vault-4"]["dispatched"]
          == max(1, dispatched_before_fix["dispatched"]),
          "恢复全程替身柜至多一次真实发件")

    # ================================================================
    # D6 终检与结论
    # ================================================================
    h1("D6 终检：正式计数全程不动、各台账验链、出演练结论")
    final_counters = counters()
    print(json.dumps(final_counters, ensure_ascii=False, indent=2))
    check(final_counters["prod"] == prod_counters["prod"],
          "终态：正式柜 seq/已发件计数与封存前逐项相等")
    all_chains_ok = True
    for domain in ("prod", "drill"):
        base = os.path.join(ROOT, domain)
        for name in sorted(os.listdir(base)):
            if not os.path.isdir(os.path.join(base, name)) or name == "jobs":
                continue
            view = inspector.query_service(ROOT, domain, name)
            if view["chain_problems"]:
                all_chains_ok = False
                print("链问题：", domain, name, view["chain_problems"])
    check(all_chains_ok, "正式/演练全部服务哈希链完整")
    check(not dm.verify_evidence(), "证据封存清单复核无误")

    conclusion_path = os.path.join(ROOT, "drill_conclusion.md")
    inspector.write_conclusion(
        conclusion_path,
        [
            ("演练结论（给操作员）",
             "见下方各项计数与动作；正式域在封存点之后零写入，"
             "所有演练动作均发生在 drill/ 替身柜，回条带演练域标记。"),
            ("正式柜封存前计数", prod_counters["prod"]),
            ("演练结束时两域计数", final_counters),
            ("可验证范围",
             "1) 历史回条能且只能证明正式域当次实际领出的物件与领用组；\n"
             "2) 替身柜可受理改过的工单，但新收条带 drill 域标记，"
             "不能冒充历史见证；\n"
             "3) 超时/崩溃重放由幂等键与因果日志保证不重复发件，"
             "可凭台账逐条验链；\n"
             "4) 本结论不回答正式作业当时未做的新操作——"
             "那超出历史回条的见证范围；\n"
             "5) 缺回执时作业挂 UNKNOWN，须人工核查台账，禁止臆测重发。"),
            ("正式柜实际办理动作",
             inspector.query_service(ROOT, "prod", "vault-main")["actions"]),
            ("证据完整性", {"problems": dm.verify_evidence()}),
        ],
    )
    print("\n演练结论已写出：", conclusion_path)
    print("\n全部断言通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
