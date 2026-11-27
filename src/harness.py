"""三册小文件的端到端演示：预约 / 上载 / 还原 / 退库 / 回收 / 重开。

跑法：python3 src/harness.py
"""

from __future__ import annotations

from plant import Plant, PlantError, swatch_bytes, sha_hex
from client import TenantClient

Q = 4 * 1024  # 每户小配额（页 400B，够放几页）


def hr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def show_balance(label, client):
    b = client.balance()
    print(f"  [{label}] 配额={b['quota_bytes']} 冻结={b['reserved']} "
          f"已用={b['committed']} 可退={b['refundable_bytes']}")


def show_admin(p, label):
    inv = p.admin_inventory()
    print(f"  [admin:{label}] 物理={inv['physical_used']}/{inv['physical_capacity']} "
          f"待回收={inv['pending_reclaim_bytes']} 逻辑合计={inv['logical_committed_total']} "
          f"物理节省={inv['physical_savings_total']}")
    return inv


def main():
    p = Plant(physical_capacity=4 * 1024, grace_seconds=60)
    p.register_tenant("甲", Q)
    p.register_tenant("乙", Q)
    p.register_tenant("丙", Q)
    a = TenantClient(p, "甲")
    b = TenantClient(p, "乙")
    c = TenantClient(p, "丙")

    # 三册：甲册 A（red/blue/green），乙册 B（red/yellow），丙册 C（pink/purple/pink）。
    # 甲与乙都含页 "red" —— 内容恰好相同（跨户碰巧相同）。
    # 丙册内含重复页（页1与页3都是 pink）—— 册内允许重复，逻辑只计一次。
    specs_a = [(1, "red"), (2, "blue"), (3, "green")]
    specs_b = [(1, "red"), (2, "yellow")]
    specs_c = [(1, "pink"), (2, "purple"), (3, "pink")]

    hr("1) 预约 + 上载（三册，甲乙碰巧同页 red）")
    ra = a.reserve("vol-a", specs_a)
    rb = b.reserve("vol-b", specs_b)
    rc = c.reserve("vol-c", specs_c)
    print(f"  甲预约 {ra['request_id']} 声明 {ra['declared_bytes']}B")
    print(f"  乙预约 {rb['request_id']} 声明 {rb['declared_bytes']}B")
    print(f"  丙预约 {rc['request_id']} 声明 {rc['declared_bytes']}B")

    for pn, spec in specs_a:
        r = a.upload(ra["request_id"], pn, spec)
        print(f"  甲 页{pn} -> {r['state']}  本户已有={r['already_in_this_tenant']} "
              f"可退={r['refundable_bytes_after']}")
    for pn, spec in specs_b:
        r = b.upload(rb["request_id"], pn, spec)
        print(f"  乙 页{pn} -> {r['state']}  本户已有={r['already_in_this_tenant']} "
              f"可退={r['refundable_bytes_after']}   (red 已是甲有，但乙无提示)")
    for pn, spec in specs_c:
        r = c.upload(rc["request_id"], pn, spec)
        print(f"  丙 页{pn} -> {r['state']}  本户已有={r['already_in_this_tenant']} "
              f"可退={r['refundable_bytes_after']}")

    show_balance("甲", a); show_balance("乙", b); show_balance("丙", c)
    inv = show_admin(p, "上载后")
    print("  物理实块（admin 可见跨户相等；客户不可见）：")
    for bl in inv["blobs"]:
        print(f"    sha={bl['sha'][:10]} len={bl['bytes_len']} refs={bl['refcount']} "
              f"state={bl['state']}")

    hr("2) 先还原乙的一册（快照字节 + 客户账）")
    before_b = b.restore("vol-b")
    before_bytes = {x["page_no"]: x["data"] for x in before_b["pages"]}
    before_bal = b.balance()
    print("  乙还原页：", [(x["page_no"], len(x["data"]), x["sha"][:8])
                          for x in before_b["pages"]])
    print("  乙字节 == swatch(red/yellow)?",
          before_bytes[1] == swatch_bytes("red") and
          before_bytes[2] == swatch_bytes("yellow"))
    show_balance("乙", b)

    hr("3) 只移除甲碰巧相同的那页 red；乙的字节及客户账应保持不变")
    del_res = a.delete_page("vol-a", 1)
    print(f"  甲删除页1(red) -> 退还 {del_res['refunded_bytes']}B")
    show_balance("甲", a)

    after_b = b.restore("vol-b")
    after_bytes = {x["page_no"]: x["data"] for x in after_b["pages"]}
    after_bal = b.balance()
    print("  乙还原字节与删除前完全一致:", before_bytes == after_bytes)
    print("  乙客户账与删除前完全一致:", before_bal == after_bal)
    show_balance("乙", b)
    inv = show_admin(p, "甲删红页后")
    print("  red 实块仍因乙引用而 live:",
          any(bl["sha"] == sha_hex(swatch_bytes("red")) and bl["state"] == "live"
              and bl["refcount"] >= 1 for bl in inv["blobs"]))

    # 甲删完后其册仅剩 blue/green；仍能还原
    print("  甲剩余可还原页：", [x["page_no"] for x in a.restore("vol-a")["pages"]])

    hr("4) 保留所有页，撤销乙对该版本的保留订约，宽限期后按真实合同变化")
    # 为做这一步，先让乙重新拥有 vol-b 的完整两页（上面没删乙的页，仍在）
    print("  乙当前册页：", [x["page_no"] for x in b.restore("vol-b")["pages"]])
    rel = b.release_retention("vol-b")
    print(f"  撤销保留订约 released_at={rel['released_at']} "
          f"grace_deadline={rel['grace_deadline']}")
    st = b.volume_state("vol-b")
    print("  宽限期内：contract_released=%s restorable=%s" %
          (st["contract_released"], st["restorable"]))
    print("  宽限期内仍可还原：", [x["page_no"] for x in b.restore("vol-b")["pages"]])
    show_balance("乙(宽限期内)", b)
    try:
        b.settle_release("vol-b")
    except PlantError as e:
        print("  宽限期内提前结算被拒：", e.code)

    p.tick(61)  # 过宽限期
    print(f"  时间推进到 now={p.now()}（已过宽限）")
    try:
        b.restore("vol-b")
        print("  ERROR: 过宽限仍还原成功（不应发生）")
    except PlantError as e:
        print("  过宽限期还原被拒：", e.code)
    settle = b.settle_release("vol-b")
    print(f"  按真实合同结算：退还 {settle['refunded_bytes']}B")
    show_balance("乙(结算后)", b)
    print("  乙册不再可恢复:", not b.volume_state("vol-b")["restorable"])
    # 甲/丙不受影响
    print("  甲仍可还原：", [x["page_no"] for x in a.restore("vol-a")["pages"]])

    hr("5) 管理员模拟一次未完成的回收后重开；凭请求号查可给下家的页")
    # 为了让甲产生可回收页：把甲剩余 blue/green 也删掉（删册）
    d = a.delete_volume("vol-a")
    print(f"  甲删册 vol-a -> 退还 {d['refunded_bytes']}B")
    inv = show_admin(p, "甲删册后")
    cands = p.conn.execute(
        "SELECT sha, request_id, state FROM reclaim_items ORDER BY id").fetchall()
    print("  回收候选：", [(x["sha"][:8], x["request_id"], x["state"]) for x in cands])

    job = p.make_reclaim_job("job-a")
    print(f"  建立回收作业 job-a：{job}")
    step = p.reclaim_step("job-a", count=1)  # 只完成 1 项 -> 故意未完成
    print(f"  执行部分回收：freed={len(step['freed'])} remaining={step['remaining']}")
    print("  作业状态：", p.reclaim_job_state("job-a")["state"])
    q1 = p.pages_available_to_next_tenant(ra["request_id"])
    print("  重开前，凭请求号查可给下家页：freed=%d 未释放=%d" %
          (q1["freed_count"], q1["not_yet_freed"]))

    reopened = p.reopen_reclaim("job-a")
    print(f"  重开回收：freed={len(reopened['freed'])} remaining={reopened['remaining']}")
    print("  作业状态：", p.reclaim_job_state("job-a")["state"])
    q2 = p.pages_available_to_next_tenant(ra["request_id"])
    print("  重开后，凭请求号查可给下家页：")
    print(f"    request_id={q2['request_id']} volume={q2['volume_id']} "
          f"freed={q2['freed_count']} 未释放={q2['not_yet_freed']}")
    for f in q2["freed_pages"]:
        print(f"      sha={f['sha'][:10]} released={f['released_bytes']}B")
    inv = show_admin(p, "回收完成后")
    print("  待回收空间已清零:", inv["pending_reclaim_bytes"] == 0)

    hr("6) 总容量不足时的受理（另一机房实例）")
    cap_demo()

    hr("7) 终态摘要")
    show_balance("甲", a); show_balance("乙", b); show_balance("丙", c)
    show_admin(p, "终态")
    print("  丙仍可还原：", [x["page_no"] for x in c.restore("vol-c")["pages"]])
    print("\n演示完成。")


def cap_demo():
    """总容量不足：部分页 pending，容量释放后重试；预约额度保持冻结到结算。"""
    p = Plant(physical_capacity=1200, grace_seconds=60)
    for t in ("甲", "乙", "丙"):
        p.register_tenant(t, Q)
    a = TenantClient(p, "甲"); b = TenantClient(p, "乙"); c = TenantClient(p, "丙")

    specs_a = [(1, "red"), (2, "blue")]        # 800B 新占盘
    specs_b = [(1, "red"), (2, "yellow")]      # red 复用 + yellow 400B -> 计 1200/1200
    ra = a.reserve("vol-a", specs_a); a.upload_all(ra, specs_a)
    rb = b.reserve("vol-b", specs_b); b.upload_all(rb, specs_b)
    show_admin(p, "甲乙上载后已满")

    specs_c = [(1, "pink"), (2, "purple")]     # 无空间
    rc = c.reserve("vol-c", specs_c)
    states = []
    for pn, spec in specs_c:
        r = c.upload(rc["request_id"], pn, spec)
        states.append((pn, r["state"]))
        print(f"  丙 页{pn} -> {r['state']}（容量不足即挂起，预约仍有效）")
    show_balance("丙(挂起中)", c)
    print("  挂起页不会扣 committed，冻结额度仍保留。")

    # 甲退库释放物理空间
    a.delete_volume("vol-a")
    print("  甲退库后待回收:", p.admin_inventory()["pending_reclaim_bytes"])
    p.make_reclaim_job("job-cap"); p.reopen_reclaim("job-cap")
    show_admin(p, "回收后")

    retried = c.retry_pending(rc["request_id"])
    print("  丙重试挂起页：", retried)
    show_balance("丙(重试后)", c)
    print("  丙可还原：", [x["page_no"] for x in c.restore("vol-c")["pages"]])


if __name__ == "__main__":
    main()