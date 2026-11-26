"""离线审计取证台 —— 全场景演示与断言。

运行：python3 demo.py
输出：runs/ 下各案件目录与证书；控制台打印中文结论。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from dataclasses import asdict

from auditdesk import (CertError, Certifier, Chain, ChainError, EscrowLocker,
                       GENESIS, LedgerBuilder, RECEIPT_DELIVERED,
                       RECEIPT_VERIFIED, CaseStore, WitnessStation)
from auditdesk import crypto
from auditdesk.models import Bundle, Page
from auditdesk.render import render_cert, render_facts

RUNS = Path("runs")
EAST, WEST, DESK = "东站", "西站", "取证台"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    flag = "PASS" if cond else "FAIL"
    print(f"[{flag}] {name}" + (f" —— {detail}" if detail else ""))


def try_cert(fn, *args, **kw):
    try:
        return fn(*args, **kw), None
    except CertError as e:
        return None, str(e)


def new_case(name: str):
    d = RUNS / name
    if d.exists():
        shutil.rmtree(d)
    store = CaseStore(d / "case")
    chain = Chain(store)
    certifier = Certifier(store, chain)
    return store, chain, certifier


def make_stations(name: str, store: CaseStore | None = None):
    east = WitnessStation(EAST, RUNS / name / "stations")
    west = WitnessStation(WEST, RUNS / name / "stations")
    if store is not None:
        store.register_pubkey(east.key_id, east.pub_pem.decode())
        store.register_pubkey(west.key_id, west.pub_pem.decode())
    return east, west


def build_common_seven(builder: LedgerBuilder, ts0="2026-11-26T0"):
    """前七页：两站一致的正常共同历史，每页 2 条条目。"""
    prev, pages, entries = GENESIS, [], []
    for no in range(1, 8):
        es = [builder.add_entry(f"班次{no}", f"值班员{(no % 2) + 1}",
                                f"第{no}页日志A", f"{ts0}{no}:00"),
              builder.add_entry(f"班次{no}", f"值班员{(no % 2) + 1}",
                                f"第{no}页日志B", f"{ts0}{no}:30")]
        page = builder.seal_page(no, f"班次{no}", es, prev,
                                 f"{ts0}{no}:59")
        pages.append(page)
        entries.extend(es)
        prev = page.page_digest
    return pages, entries


# ----------------------------------------------------------------------
# 场景一：分叉场次 —— 第七页双站见证，第八页东西各签一支
# ----------------------------------------------------------------------
def build_fork_world(case_name: str, with_p9: bool = False) -> dict:
    """构造一个分叉世界（每次重放都重建，保证本地柜未被前一次清理污染）。"""
    store, chain, certifier = new_case(case_name)
    east, west = make_stations(case_name, store)
    builder = LedgerBuilder(store)
    pages, _ = build_common_seven(builder)
    p7 = pages[-1]
    for p in pages:
        east.receive_verified(p, chain, f"2026-11-26T{p.page_no}:59",
                              note="共同历史验页")
        west.receive_verified(p, chain, f"2026-11-26T{p.page_no}:59",
                              note="共同历史验页")

    e_jia = builder.add_entry("夜班", "客户甲", "甲的交接单",
                              "2026-11-26T21:05", entry_id="E-JIA")
    p8_east = builder.extend(8, "夜班东", [e_jia], p7, "2026-11-26T21:10")
    e_yi = builder.add_entry("夜班", "客户乙", "乙的交接单",
                             "2026-11-26T22:05", entry_id="E-YI")
    p8_west = builder.extend(8, "夜班西", [e_yi], p7, "2026-11-26T22:10")
    r_e8 = east.receive_verified(p8_east, chain, "2026-11-26T21:15",
                                 note="东站本地收件：甲的交接")
    r_w8 = west.receive_verified(p8_west, chain, "2026-11-26T22:15",
                                 note="西站本地收件：乙的交接")

    p9_west = None
    if with_p9:
        e9 = builder.add_entry("夜班西", "值班员2", "西站第9页",
                               "2026-11-26T23:05", entry_id="E-W9")
        p9_west = builder.extend(9, "夜班西", [e9], p8_west,
                                 "2026-11-26T23:10")
        west.receive_verified(p9_west, chain, "2026-11-26T23:15")
    return dict(store=store, chain=chain, certifier=certifier,
                east=east, west=west, builder=builder, pages=pages,
                p7=p7, p8_east=p8_east, p8_west=p8_west, p9_west=p9_west,
                r_e8=r_e8, r_w8=r_w8)


def scenario_fork() -> dict:
    print("\n" + "#" * 70 + "\n# 场景一：分叉场次（第七页共同，第八页东甲/西乙两支）\n" + "#" * 70)
    ctx = build_fork_world("01_fork", with_p9=True)
    store, chain, certifier = ctx["store"], ctx["chain"], ctx["certifier"]
    east, west = ctx["east"], ctx["west"]
    builder = ctx["builder"]
    pages, p7 = ctx["pages"], ctx["p7"]
    p8_east, p8_west, p9_west = ctx["p8_east"], ctx["p8_west"], ctx["p9_west"]

    check("分叉两页前页相同", p8_east.prev_digest == p8_west.prev_digest == p7.page_digest)
    check("分叉两页摘要不同", p8_east.page_digest != p8_west.page_digest)

    # 两站视角：各持一支链头，谁也不否认对方
    views = chain.station_views()
    check("东站链头只有东8页", views[EAST].tips == [p8_east.page_digest],
          f"链头页号 {views[EAST].tip_nos}")
    check("西站链头只有西9页", views[WEST].tips == [p9_west.page_digest],
          f"链头页号 {views[WEST].tip_nos}")
    lca = chain.last_common_history(p8_east.page_digest, p8_west.page_digest)
    check("最后共同历史停在第七页", lca["last_common_no"] == 7,
          f"共同到第 {lca['last_common_no']} 页")

    # 断线恢复：重复页、缺中间页、同前页双后续都能处理
    dup_stats = store.import_bundle(store.export_bundle())
    check("重复页/重复签收按内容去重",
          dup_stats["pages"] == 0 and dup_stats["receipts"] == 0)

    # 重启：新进程对象打开同一目录，恢复最后共同历史并验证柜台条目
    store2 = CaseStore(RUNS / "01_fork" / "case")
    chain2 = Chain(store2)
    lca2 = chain2.last_common_history(p8_east.page_digest, p8_west.page_digest)
    check("重启后仍能查出共同历史到第七页", lca2["last_common_no"] == 7)
    proof = chain2.inclusion_proof("E-JIA", p8_east.page_digest)
    check("重启后可验证柜台具体条目 E-JIA 归属东8页", proof["page_no"] == 8)

    # 晚到且更长的一支，不能抹掉先到的一支
    views2 = chain.station_views()
    check("链更长/日期更晚不抹掉东支",
          views2[EAST].tips == [p8_east.page_digest]
          and views2[WEST].tips == [p9_west.page_digest])
    # 再次重启：长度不等的两支，共同历史仍停在第七页
    store3 = CaseStore(RUNS / "01_fork" / "case")
    chain3 = Chain(store3)
    lca3 = chain3.last_common_history(p8_east.page_digest, p9_west.page_digest)
    check("长度不等的两支共同历史仍是第七页", lca3["last_common_no"] == 7)

    # 缺中间页：新案件只导入第9页时验前缀必须失败
    gap_store = CaseStore(RUNS / "01_fork" / "gap_case")
    gap_store.import_bundle(Bundle(pages=[asdict(p9_west)], entries=[],
                                   receipts=[], pubkeys={}))
    try:
        Chain(gap_store).verify_prefix(p9_west.page_digest)
        check("缺中间页时拒绝验前缀", False)
    except ChainError:
        check("缺中间页时拒绝验前缀", True)

    # 断线恢复补页：把缺的 1..8 页与条目补导入后，同一链头立即复核通过
    missing_pages = [asdict(p) for p in store.all_pages()
                     if p.page_digest != p9_west.page_digest]
    backfill = Chain(gap_store)
    gap_store.import_bundle(Bundle(
        pages=missing_pages, entries=list(store.entries.values()),
        receipts=[], pubkeys={}))
    replayed = backfill.verify_prefix(p9_west.page_digest)
    check("补页后前缀恢复并可完整回放",
          [p.page_no for p in replayed] == list(range(1, 10)))

    # 跨进程签名复核：重开案件库，逐张 VERIFIED 签收用 OpenSSL 独立验签
    reopened = CaseStore(RUNS / "01_fork" / "case")
    rc = Certifier(reopened, Chain(reopened))
    for r in reopened.all_receipts():
        rc.verify_receipt(r)  # 任一失败即抛 CertError
    check("全部站内签收可在新进程中用登记公钥独立验签", True)
    # 篡改任一签收签名即验不过
    tampered_r = next(iter(reopened.all_receipts()))
    raw = bytearray.fromhex(tampered_r.signature)
    raw[0] ^= 0xFF
    check("被涂改的签收验签失败",
          not crypto.verify(
              reopened.pubkeys[tampered_r.key_id].encode(),
              tampered_r.signed_message(), bytes(raw)))

    # 任何一张本地凭证都不能证明两件事的全局顺序
    cert_jia, err_jia = try_cert(certifier.issue_local_receipt,
                                 EAST, p8_east.page_digest,
                                 "客户甲向经办人证明东站确曾收过这一页")
    check("东站可给甲出单站本地收件证明", cert_jia is not None)
    if cert_jia:
        print(render_cert(cert_jia))
    co8, err_co8 = try_cert(certifier.issue_co_witness,
                            EAST, WEST, p8_east.page_digest)
    check("第八页不能出双站共同见证", co8 is None, err_co8)
    co7, err_co7 = try_cert(certifier.issue_co_witness,
                            EAST, WEST, p7.page_digest)
    check("第七页正常双站共同见证可出证", co7 is not None)

    # 第三方寄存柜：完整前七页 + 东站对第七页的签收；没有西7签收、没有东8
    locker = _build_locker(ctx)
    check("寄存柜内无西站第七页签收",
          Chain(locker.as_store()).find_receipt_for_page(
              WEST, p7.page_digest, RECEIPT_VERIFIED) is None)
    check("寄存柜内无东站第八页",
          locker.as_store().get_page(p8_east.page_digest) is None)

    ctx["locker"] = locker
    return ctx


# ----------------------------------------------------------------------
# 场景二：夜里只派一名取证人 —— 去东站 / 去西站，两种原件遗失重放
# ----------------------------------------------------------------------
def _build_locker(world: dict) -> EscrowLocker:
    """寄存柜：完整前七页 + 东站对第七页的签收；无西7签收、无东8。"""
    store, chain = world["store"], world["chain"]
    east, pages, p7 = world["east"], world["pages"], world["p7"]
    locker = EscrowLocker(RUNS / "01_fork" / f"locker_{id(world) % 100000}")
    r_p7 = chain.find_receipt_for_page(EAST, p7.page_digest, RECEIPT_VERIFIED)
    locker.deposit("两站共同历史", Bundle(
        pages=[asdict(p) for p in pages],
        entries=list(store.entries.values()),
        receipts=[asdict(r_p7)],
        pubkeys={east.key_id: east.pub_pem.decode()},
    ))
    return locker


def scenario_replay(fork_ctx: dict) -> None:
    print("\n" + "#" * 70 + "\n# 场景二：单点取证 + 两站清柜 + 两种遗失重放\n" + "#" * 70)
    p7, p8e, p8w = (fork_ctx["p7"], fork_ctx["p8_east"], fork_ctx["p8_west"])

    def replay(trip: str) -> tuple[list, list]:
        # 每次重放重建一个干净的分叉世界（确定性输入，摘要一致）
        world = build_fork_world(f"01_fork/replay_world_{trip}")
        east, west = world["east"], world["west"]
        locker = _build_locker(world)

        retrieved = (east.export_cabinet() if trip == EAST
                     else west.export_cabinet())
        east.wipe_cabinet()
        west.wipe_cabinet()
        check(f"[{trip}取证] 两站本地柜已清空，仅{trip}原件被取证人带走",
              not list(east.cabinet.glob("*")) and not list(west.cabinet.glob("*")))

        r_store = CaseStore(RUNS / "01_fork" / f"replay_{trip}")
        r_chain = Chain(r_store)
        r_cert = Certifier(r_store, r_chain)
        # 复查人手里只有：寄存柜材料 + 取回来的那一柜
        r_store.import_bundle(locker.as_store().export_bundle())
        stats = r_store.import_bundle(retrieved)
        print(f"  导入取回柜：页 {stats['pages']} 张，签收 {stats['receipts']} 份，"
              f"公钥 {stats['pubkeys']} 把")

        facts_jia, facts_yi = [], []
        cert_jia, err = try_cert(r_cert.issue_local_receipt,
                                 EAST, p8e.page_digest, "甲的局部交接举证")
        facts_jia.append(("东站确曾收过甲的第八页", cert_jia is not None,
                          "东8 VERIFIED 签收在取回柜内" if cert_jia
                          else f"东8原件未取回、已随清柜灭失：{err}"))
        prov7, _ = try_cert(r_cert.issue_provisional, EAST, WEST, p7.page_digest)
        co7, err_co7 = try_cert(r_cert.issue_co_witness,
                                EAST, WEST, p7.page_digest)
        facts_yi.append(("前七页链本身完整可复核",
                         _prefix_ok(r_chain, p7.page_digest),
                         "寄存柜存有完整前七页"))
        facts_yi.append(("东站对第七页的 VERIFIED 签收",
                         r_chain.find_receipt_for_page(
                             EAST, p7.page_digest, RECEIPT_VERIFIED) is not None,
                         "寄存柜内"))
        facts_yi.append(("西站对第七页的 VERIFIED 签收",
                         r_chain.find_receipt_for_page(
                             WEST, p7.page_digest, RECEIPT_VERIFIED) is not None,
                         "西站柜内取回" if trip == WEST else "未取回，已随清柜灭失"))
        facts_yi.append(("出具第七页双站共同见证书", co7 is not None,
                         "双站 VERIFIED 齐备" if co7 else err_co7))
        facts_jia.append(("能证明甲、乙两页的全局先后顺序", False,
                          "两张都是本地凭证，离线架构下无全局顺序可证"))
        facts_yi.append(("能证明西站第八页（乙）发生过",
                         r_chain.find_receipt_for_page(
                             WEST, p8w.page_digest, RECEIPT_VERIFIED) is not None,
                         "西站柜内取回" if trip == WEST else "未取回，不可证"))
        print(render_facts(f"重放：取证人去了{trip} —— 客户甲可举证事实", facts_jia))
        print(render_facts(f"重放：取证人去了{trip} —— 客户乙可举证事实", facts_yi))
        if cert_jia:
            print(render_cert(cert_jia))
        if co7:
            print(render_cert(co7))
        elif prov7:
            print(render_cert(prov7))
        return facts_jia, facts_yi

    matrix = {}
    for trip in (EAST, WEST):
        matrix[trip] = replay(trip)
    return matrix


def _prefix_ok(chain: Chain, tip: str) -> bool:
    try:
        chain.verify_prefix(tip)
        return True
    except ChainError:
        return False


# ----------------------------------------------------------------------
# 场景三：正常场次 —— 前七页一致，双站共同见证必须能出证；调包中间页即失效
# ----------------------------------------------------------------------
def scenario_normal_and_tamper() -> None:
    print("\n" + "#" * 70 + "\n# 场景三：正常场次出证 / 调换中间页后不能再签原顺序\n" + "#" * 70)
    store, chain, certifier = new_case("02_normal")
    east, west = make_stations("02_normal", store)
    builder = LedgerBuilder(store)
    pages, entries = build_common_seven(builder)
    for p in pages:
        east.receive_verified(p, chain, p.ts)
        west.receive_verified(p, chain, p.ts)

    co7, err = try_cert(certifier.issue_co_witness,
                        EAST, WEST, pages[-1].page_digest)
    check("正常场次双站共同见证可出证", co7 is not None, err or "")
    if co7:
        # 客户凭实际寄存材料复查：证书签名可独立验证
        verdict = Certifier.verify_cert(store, co7)
        check("客户复查证书签名有效", verdict["valid"])
        print(render_cert(co7))

    # 柜台具体条目验证
    proof = chain.inclusion_proof(entries[6].entry_id, pages[-1].page_digest)
    check("柜台条目可定位到具体页", proof["page_no"] == 4
          and proof["entry_digest"] == entries[6].digest())

    # 调换中间一页：复制正常案件，抽换第4页原件，后续页 prev 指针立即悬空
    tam_dir = RUNS / "02_normal" / "tampered_case"
    shutil.copytree(RUNS / "02_normal" / "case", tam_dir)
    t_store = CaseStore(tam_dir)
    t_chain = Chain(t_store)
    evil = _add_evil_entry(t_store)
    old_p4 = next(p for p in t_store.all_pages() if p.page_no == 4)
    tampered = Page(page_no=4, shift="班次4",
                    prev_digest=old_p4.prev_digest,
                    entry_digests=[evil.digest()]).seal("2026-11-26T04:59")
    (t_store.dir / "pages" / f"{old_p4.page_digest}.json").unlink()
    t_store.add_page(tampered)
    p7_digest = pages[-1].page_digest
    try:
        t_chain.verify_prefix(p7_digest)
        check("调换中间页后原顺序链复核失败", False)
    except ChainError as e:
        check("调换中间页后原顺序链复核失败", True, str(e))

    # 共同见证在调包案件中同样不能签发（前缀断、且无调包页的双站签收）
    t_cert = Certifier(t_store, t_chain)
    co_tamper, err_t = try_cert(t_cert.issue_co_witness,
                                EAST, WEST, p7_digest)
    check("调包后第七页共同见证被拒", co_tamper is None, err_t)
    # 调包页是新摘要，任何见证站都没见过，拿不到单站收件证明
    r_tamper, err_r = try_cert(t_cert.issue_local_receipt,
                               EAST, tampered.page_digest, "调包页举证")
    check("调包页拿不到任何单站收件证明", r_tamper is None, err_r)


def _add_evil_entry(store: CaseStore):
    from auditdesk.models import Entry
    e = Entry(entry_id="E-EVIL", shift="班次4", author="伪造者",
              payload="被调包的伪造日志", ts="2026-11-26T04:05")
    store.add_entry(e)
    return e


# ----------------------------------------------------------------------
# 场景四：恢复链本身 —— 第二站只签“包裹已送达、尚未验页”
# ----------------------------------------------------------------------
def scenario_delivered() -> None:
    print("\n" + "#" * 70 + "\n# 场景四：DELIVERED 签收 —— 单站事实可办，共同见证不得提前\n" + "#" * 70)
    store, chain, certifier = new_case("03_delivered")
    east, west = make_stations("03_delivered", store)
    builder = LedgerBuilder(store)
    pages, _ = build_common_seven(builder)
    p7 = pages[-1]
    for p in pages:
        east.receive_verified(p, chain, p.ts)
    # 西站恢复链本身：只登记包裹送达，未拆验
    r_del = west.receive_delivered("2026-11-26T20:00", page=p7)
    store.add_receipt(r_del)

    # 单站可证的事实仍应能办
    local, err = try_cert(certifier.issue_local_receipt,
                          EAST, p7.page_digest, "东站单站事实照常办理")
    check("VERIFIED 站单站证明可出", local is not None)
    delivery, err2 = try_cert(certifier.issue_delivery_only,
                              WEST, r_del.receipt_id)
    check("西站可出仅送达证明", delivery is not None, err2 or "")
    if delivery:
        print(render_cert(delivery))
        verdict = Certifier.verify_cert(store, delivery)
        check("仅送达证明签名可复查", verdict["valid"])

    # 共同见证不能提前签发
    co, err3 = try_cert(certifier.issue_co_witness, EAST, WEST, p7.page_digest)
    check("一 VERIFIED 一 DELIVERED 时共同见证被拒", co is None, err3)
    prov, err4 = try_cert(certifier.issue_provisional,
                          EAST, WEST, p7.page_digest)
    check("此时只能出待补临证书", prov is not None and WEST in prov["facts"]["lack"])

    # 西站补验后升级，共同见证才能签发
    for p in pages:
        west.receive_verified(p, chain, "2026-11-26T21:00",
                              note="补验升级")
    co2, err5 = try_cert(certifier.issue_co_witness,
                         EAST, WEST, p7.page_digest)
    check("补验 VERIFIED 后共同见证方可签发", co2 is not None, err5 or "")


# ----------------------------------------------------------------------
def main() -> None:
    if RUNS.exists():
        shutil.rmtree(RUNS)
    ctx = scenario_fork()
    scenario_replay(ctx)
    scenario_normal_and_tamper()
    scenario_delivered()

    print("\n" + "=" * 70 + "\n断言汇总\n" + "=" * 70)
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        print(f"  [{'✓' if ok else '✗'}] {name}" + (f"（{detail}）" if detail and not ok else ""))
    print(f"\n共 {len(results)} 项，通过 {passed}，失败 {len(results) - passed}")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
