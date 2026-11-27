"""迁移演练场景：可确定性重放的完整故事线。

单据清单：
- R-OLD-BLANK   旧版签发、空白期限、签发即起算   -> 两版分歧，自动处置被冻结
- R-OLD-EXPLICIT 旧版签发、显式期限、签发即起算  -> 两版一致，常规单得到保全
- R-NEW-EXPLICIT 新版签发、显式到期日            -> 两版一致，直接用日期
- R-TR-1        旧版、显式期限、转交才起算、未转交 -> 条件可表达，离线可消费
- R-TR-2        旧版、空白期限、转交才起算、未转交 -> 条件无法表达，舍弃自动化
- R-GROWN       旧版签发；合同后来增长出 v2       -> 只认签发时 v1 快照
- R-NRN         普通入口补入的带生效日续约（v2）
"""
from __future__ import annotations

from datetime import date

from ..hashing import digest
from ..model import (Contract, CustodyOrder, Handover, PermitOperation,
                     PortablePermit, Refusal, StoreLogEntry)
from .station import Station
from .stores import ReceivingStore

STATION_KEY = "受托站演示密钥-local-only"

# 三个只读库能保证的存期（物理/合同上限）
HORIZONS = {
    "STORE-A": None,                       # 无上限
    "STORE-B": date(2025, 6, 15),          # 只能保证到 2025-06-15
    "STORE-C": date(2027, 12, 31),
}
RETIRE_ON = date(2025, 6, 1)


def seal_order(order: CustodyOrder) -> CustodyOrder:
    return CustodyOrder(**{**order.__dict__,
                           "seal": "SEAL-" + digest(order.sealing_payload())[:24]})


def build_contracts() -> dict[tuple[str, int], Contract]:
    return {
        ("C-FRAME", 1): Contract("C-FRAME", 1, date(2024, 1, 1), 180),
        # 签发后增长出的版本：默认期限被拉长，日期口径也更新
        ("C-FRAME", 2): Contract("C-FRAME", 2, date(2026, 1, 1), 365),
        ("C-FRAME", 3): Contract("C-FRAME", 3, date(2025, 9, 1), 180),
    }


def build_orders() -> dict[str, CustodyOrder]:
    raw = [
        CustodyOrder("R-OLD-BLANK", "v1", "C-FRAME", 1, date(2025, 1, 1),
                     None, None, "issue", "客户资料保管单（空白期限）", ""),
        CustodyOrder("R-OLD-EXPLICIT", "v1", "C-FRAME", 1, date(2025, 1, 1),
                     90, None, "issue", "客户资料保管单（90 天）", ""),
        CustodyOrder("R-NEW-EXPLICIT", "v2", "C-FRAME", 3, date(2025, 9, 1),
                     None, date(2025, 11, 1), "issue", "客户资料保管单（显式到期）", ""),
        CustodyOrder("R-TR-1", "v1", "C-FRAME", 1, date(2025, 1, 1),
                     90, None, "transfer", "客户资料保管单（转交起算 90 天）", ""),
        CustodyOrder("R-TR-2", "v1", "C-FRAME", 1, date(2025, 1, 1),
                     None, None, "transfer", "客户资料保管单（空白+转交起算）", ""),
        CustodyOrder("R-GROWN", "v1", "C-FRAME", 1, date(2025, 1, 1),
                     None, None, "issue", "客户资料保管单（空白期限；合同后来增长）", ""),
    ]
    orders = {o.order_id: seal_order(o) for o in raw}
    # 续约单：普通入口、带生效日、新版字典、显式期限
    orders["R-NRN"] = seal_order(CustodyOrder(
        "R-NRN", "v2", "C-FRAME", 3, date(2025, 12, 1), 180, None, "issue",
        "续约单（生效日 2025-12-01，180 天）", ""))
    return orders


def build_handovers() -> dict[str, list[Handover]]:
    def issue(o):
        return Handover(f"HO-{o}-I", date(2025, 1, 1), "issue", None, "受托站")
    out = {oid: [issue(oid)] for oid in
           ("R-OLD-BLANK", "R-OLD-EXPLICIT", "R-TR-1", "R-TR-2", "R-GROWN")}
    out["R-NEW-EXPLICIT"] = [
        Handover("HO-RNEW-I", date(2025, 9, 1), "issue", None, "受托站")]
    out["R-NRN"] = [Handover("HO-RNRN-I", date(2025, 12, 1), "issue", None, "受托站")]
    return out


def _is_refusal(r) -> bool:
    return isinstance(r, Refusal)


def run_demo(verbose: bool = True) -> Station:
    def say(msg):
        if verbose:
            print(msg)

    station = Station(STATION_KEY, HORIZONS)
    contracts, orders, handovers = build_contracts(), build_orders(), build_handovers()
    for c in contracts.values():
        station.register_contract(c)
    for oid, hs in handovers.items():
        station.register_handovers(oid, hs, RETIRE_ON)

    say("=" * 72)
    say("步骤 1  旧解释器退场：双解释器并行求值，按对象签出便携许可、封存证据")
    say("=" * 72)
    first_batch = ["R-OLD-BLANK", "R-OLD-EXPLICIT", "R-NEW-EXPLICIT",
                   "R-TR-1", "R-TR-2", "R-GROWN"]
    permits = station.retire_interpreter([orders[i] for i in first_batch], RETIRE_ON)
    for p in permits:
        ev = station.evidence[p.evidence_id].payload
        v1, v2 = ev["legacy_v1"], ev["new_v2"]
        say(f"\n[{p.order_id}] 证据 {p.evidence_id}")
        say(f"  v1 旧字典: status={v1['status']} end={v1['term_end']}（{v1['reason']}）")
        say(f"  v2 新字典: status={v2['status']} end={v2['term_end']}（{v2['reason']}）")
        say(f"  许可操作: {[o.op + ':' + str(o.arg) for o in p.operations] or '无'}")
        if p.dropped_automation:
            say(f"  舍弃自动化: {list(p.dropped_automation)}")
        if p.ratification_required:
            say("  -> 存在分歧/无法表达：冻结自动化，进入客户追认队列")

    say("\n" + "=" * 72)
    say("步骤 2  分批送往三个只读接收库")
    say("=" * 72)
    r = station.dispatch("R-OLD-EXPLICIT", "STORE-A", date(2025, 6, 2))
    say(f"R-OLD-EXPLICIT -> STORE-A : {r.action if isinstance(r, StoreLogEntry) else r.reason}")
    r = station.dispatch("R-NEW-EXPLICIT", "STORE-C", date(2025, 9, 2))
    say(f"R-NEW-EXPLICIT -> STORE-C : {r.action if isinstance(r, StoreLogEntry) else r.reason}")
    r = station.dispatch("R-OLD-BLANK", "STORE-A", date(2025, 6, 2))
    say(f"R-OLD-BLANK    -> STORE-A : {r.action if isinstance(r, StoreLogEntry) else r.reason}")
    r = station.dispatch("R-TR-1", "STORE-A", date(2025, 6, 2))
    say(f"R-TR-1         -> STORE-A : {r.action if isinstance(r, StoreLogEntry) else r.reason}")
    r = station.dispatch("R-TR-2", "STORE-A", date(2025, 6, 2))
    say(f"R-TR-2         -> STORE-A : {r.action if isinstance(r, StoreLogEntry) else r.reason}")

    say("\n-- R-GROWN：只换成增长后的合同版本会怎样？--")
    r_b = station.dispatch("R-GROWN", "STORE-B", date(2025, 6, 2))
    say(f"R-GROWN -> STORE-B（保证存期仅到 2025-06-15）: "
        f"{r_b.action if isinstance(r_b, StoreLogEntry) else '拒绝：' + r_b.reason}")
    r_a = station.dispatch("R-GROWN", "STORE-A", date(2025, 6, 3))
    say(f"R-GROWN -> STORE-A（按签发时 C-FRAME v1 的 180 天，到期 2025-06-30）: "
        f"{r_a.action if isinstance(r_a, StoreLogEntry) else r_a.reason}")
    say(f"  客户拿到的单据: {[p.kind + '(' + p.proposal_id + ')' for p in station.proposals.values()]}")

    say("\n" + "=" * 72)
    say("步骤 3  库房离线：只消费已明示许可")
    say("=" * 72)
    r = station.offline_dispose("R-OLD-EXPLICIT", "STORE-A", date(2025, 2, 1))
    say(f"R-OLD-EXPLICIT 未到期尝试处置: "
        f"{r.action if isinstance(r, StoreLogEntry) else '拒绝：' + r.reason}")
    r = station.offline_dispose("R-OLD-EXPLICIT", "STORE-A", date(2025, 4, 10))
    say(f"R-OLD-EXPLICIT 到期后处置    : "
        f"{r.action if isinstance(r, StoreLogEntry) else '拒绝：' + r.reason}")
    r = station.offline_dispose("R-NEW-EXPLICIT", "STORE-C", date(2025, 11, 5))
    say(f"R-NEW-EXPLICIT 到期后处置    : "
        f"{r.action if isinstance(r, StoreLogEntry) else '拒绝：' + r.reason}")
    r = station.offline_dispose("R-OLD-BLANK", "STORE-A", date(2025, 12, 31))
    say(f"R-OLD-BLANK 离线自行处置     : "
        f"{r.action if isinstance(r, StoreLogEntry) else '拒绝：' + r.reason}（离线不得自行批准新动作）")

    say("\n" + "=" * 72)
    say("步骤 4  演练一次未预料的转交（2025-10-05）")
    say("=" * 72)
    t1 = station.simulate_transfer("R-TR-1", "STORE-A", date(2025, 10, 5),
                                   "原受托方", "STORE-A")
    say(f"R-TR-1（显式 90 天，公式可表达）: "
        f"{t1.action if isinstance(t1, StoreLogEntry) else '冻结：' + t1.reason}")
    if isinstance(t1, StoreLogEntry):
        say(f"  库内派生到期: {t1.detail['derived_until']}（10-05 + 90 天 = 2026-01-03）")
    t2 = station.simulate_transfer("R-TR-2", "STORE-A", date(2025, 10, 5),
                                   "原受托方", "STORE-A")
    say(f"R-TR-2（空白期限，签出时无法表达）: "
        f"{t2.action if isinstance(t2, StoreLogEntry) else '冻结：' + t2.reason}")

    say("\n-- 客户对 R-TR-2 追认：站内用封存的原材料复核，而不是退役字典补算 --")
    rc = next(c for c in station.cases.values() if c.order_id == "R-TR-2"
              and c.status == "open")
    new_permit = station.ratify(rc.case_id, date(2025, 10, 10), "approved")
    say(f"追认案 {rc.case_id} 已批准，补发许可 {new_permit.permit_id}，"
        f"到期 {new_permit.operations[0].arg}"
        "（封存规则 + 已发生的 10-05 转交事实重新求值：10-05 + 180 天 = 2026-04-03，"
        "一次性写成绝对日期，不再依赖退役字典）")

    say("\n" + "=" * 72)
    say("步骤 5  普通入口补一条带生效日的续约，再查每一库执行过什么")
    say("=" * 72)
    renewal = station.intake_renewal(orders["R-NRN"], date(2025, 11, 20))
    say(f"R-NRN 续约经普通入口受理，许可 {renewal.permit_id}，"
        f"操作 {[o.op + ':' + str(o.arg) for o in renewal.operations]}")
    r = station.dispatch("R-NRN", "STORE-C", date(2025, 12, 1))
    say(f"R-NRN -> STORE-C : {r.action if isinstance(r, StoreLogEntry) else r.reason}")

    say("\n各库哈希链审计：")
    for sid, audit in station.audit_stores().items():
        say(f"  {sid}: chain_valid={audit['chain_valid']} "
            f"动作={[e['action'] + '/' + e['order_id'] for e in audit['entries']]}")
    say(f"\n事件日志根哈希: {station.root_hash}")
    return station


def extra_guards(verbose: bool = True) -> dict:
    """不进入主日志的隔离检查：签章绑定与许可防篡改。"""
    def say(msg):
        if verbose:
            print(msg)

    orders = build_orders()
    station = Station(STATION_KEY, {"SANDBOX": None})
    for c in build_contracts().values():
        station.register_contract(c)
    for oid, hs in build_handovers().items():
        station.register_handovers(oid, hs, RETIRE_ON)
    station.retire_interpreter([orders["R-OLD-EXPLICIT"]], RETIRE_ON)
    permit = station.permits["R-OLD-EXPLICIT"]
    sandbox = ReceivingStore("SANDBOX", None)

    # 1) 同文本、同签章形态但不同单据号：许可不得复用
    other = orders["R-OLD-EXPLICIT"]
    impostor = CustodyOrder("R-FAKE", other.interpreter_version, other.contract_id,
                            other.contract_version, other.issued_on, other.term_days,
                            other.until_date, other.start_trigger, other.text,
                            # 复制同一枚签章
                            other.seal)
    r1 = sandbox.receive(RETIRE_ON, "R-FAKE", impostor.seal, permit, STATION_KEY)
    say(f"隔离检查 A 同文同章别单复用许可: {r1.reason if isinstance(r1, Refusal) else '放行(错误!)'}")

    # 2) 篡改许可操作后封印失效
    tampered = PortablePermit(
        permit.permit_id, permit.order_id, permit.order_seal, permit.evidence_id,
        permit.issued_on,
        tuple(list(permit.operations) + [PermitOperation(
            "dispose-after", "2000-01-01", ("伪造提前处置",))]),
        permit.ratification_required, permit.dropped_automation,
        permit.scope_note, permit.station_seal)
    r2 = sandbox.receive(RETIRE_ON, "R-OLD-EXPLICIT", orders["R-OLD-EXPLICIT"].seal,
                         tampered, STATION_KEY)
    say(f"隔离检查 B 篡改许可扩大范围    : {r2.reason if isinstance(r2, Refusal) else '放行(错误!)'}")
    return {"cross_order_blocked": isinstance(r1, Refusal),
            "tamper_blocked": isinstance(r2, Refusal)}
