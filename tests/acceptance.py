"""端到端验收演示（对应题目验收清单）。

角色：
  编纂方    Signer + Compiler，产出 v1/v2/v3 修订与版本凭据
  检索台    Server（不受信），保存全库快照并答页
  查词席    Client + 仅信任的验签键，独立验页
  排字工坊  Client + Manuscript，离线核验、注稿注入

运行：python3 -m tests.acceptance
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from adp import (
    Entry, Server, Client, Manuscript, NetworkTransport, OfflineTransport,
    Signer, Verifier, Compiler, VerificationError, StaleCursorError,
)
from adp.fixtures import (
    KEY_ID, CURATED_V1, build_revisions, synthetic_entries,
    V2_INSERTED_SENSE, V2_INSERTED_WORD, V3_GLOSS_PATCH,
)
from adp.credential import EMPTY_ROOT_HEX

PASS, FAIL, INFO = "✓", "✗", "•"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    mark = PASS if ok else FAIL
    print(f"  {mark} {name}" + (f" —— {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="adp_acceptance_"))
    print(f"工作目录: {workdir}")

    # ------------------------------------------------ 0. 三方建置
    section("0. 建置：编纂方签发、检索台托管、查词席只存验签键")
    signer = Signer.generate(KEY_ID)
    vk_pem = signer.public_key_pem()
    verifier = Verifier(vk_pem, KEY_ID)
    compiler = Compiler(signer)
    revs = build_revisions(compiler)
    v1, v2, v3 = revs["v1"], revs["v2"], revs["v3"]

    full_size = len(synthetic_entries()) + len(CURATED_V1)
    check("合成词库超过一千条", full_size > 1000,
          f"v1 共 {full_size} 条；v2 共 {len(v2.entries)} 条；v3 共 {len(v3.entries)} 条")
    check("三次修订根哈希互不相同",
          len({v1.credential.root_hash_hex, v2.credential.root_hash_hex,
               v3.credential.root_hash_hex}) == 3)
    check("空库根哈希为约定零值（独立可复算）",
          v1.credential.root_hash_hex != EMPTY_ROOT_HEX)

    # 快照只交给检索台；客户端目录里只有验签键和落盘页面
    server = Server()
    for rev in (v1, v2, v3):
        server.load_snapshot(rev.snapshot_json())
    client = Client(verifier)
    transport = NetworkTransport(server)

    # 验签键单独落盘（查词席信任锚）
    trust_dir = workdir / "trust"
    trust_dir.mkdir()
    (trust_dir / "vk.pem").write_bytes(vk_pem)

    # ------------------------------------------------ 1. 正确首页与续页
    section("1. 检索台交出正确首页与续页（前缀“花”，每页 2 条）")
    r1 = client.fetch_page(transport, "花", 2, want_version=1)
    page1 = [Entry.from_json(d) for d in r1["entries"]]
    check("首页含 花/0、花/1",
          [e.key for e in page1] == [("花", 0), ("花", 1)])
    check("首页声明还有续页并给出绑定 v1 的游标",
          r1["has_more"] and r1["next_cursor"]["version"] == 1)
    r2 = client.fetch_page(transport, "花", 2, cursor=r1["next_cursor"])
    page2 = [Entry.from_json(d) for d in r2["entries"]]
    check("续页恰好交出剩余义项 花/2，并在页缝处正确终止",
          [e.key for e in page2] == [("花", 2)] and not r2["has_more"])

    # 同前缀用不同页长翻完，结果一致
    walked = client.fetch_all(NetworkTransport(_server_with(v1)),
                              "花", 1, want_version=1)
    check("页长 1 翻完“花”得到相同三个义项且有序",
          [e.key for e in walked] == [("花", 0), ("花", 1), ("花", 2)])

    # ------------------------------------------------ 2. 攻击一：抽走夹在同形词中的义项
    section("2. 攻击：在交付副本里抽去夹在两个同形词中的义项")
    # 选前缀“行”，三个义项；制造一张页长足够的正确页，再抽掉中间义项
    honest = server.get_page(1, "行", 10)
    tampered = dict(honest)
    tampered["entries"] = [d for d in honest["entries"]
                           if (d["word"], d["sense"]) != ("行", 1)]
    try:
        Client(verifier).verify_response(tampered)
        check("抽义项后的页面被拒绝", False, "验证竟然通过了")
    except VerificationError as exc:
        check("抽义项后的页面被拒绝", True, str(exc))

    # 更强攻击：删词后重建整棵树（根哈希自洽），但无签发键 => 凭据失效
    stripped = [e for e in v1.entries if e.key != ("行", 1)]
    rogue_rev = Compiler(Signer.generate("rogue")).publish(99, stripped,
                                                            "2026-12-01")
    rogue = Server()
    rogue.load_snapshot(rogue_rev.snapshot_json())
    try:
        Client(verifier).verify_response(rogue.get_page(99, "行", 10))
        check("无签发键的重建树被拒绝", False)
    except PermissionError as exc:
        check("无签发键的重建树被拒绝", True, str(exc).split('（')[0])

    # ------------------------------------------------ 3. 攻击二：旧首页 + 新版本合法页拼接
    section("3. 攻击：给旧首页续上一张新版本来的合法页")
    old_resp = Client(verifier).fetch_page(
        NetworkTransport(_server_with(v1)), "花", 1, want_version=1)
    old_cursor = old_resp["next_cursor"]
    check("旧游标记录版本 v1", old_cursor["version"] == 1)
    try:
        # 服务器直接拒绝跨版游标
        server.get_page(2, "花", 1, cursor=old_cursor)
        check("检索台拒绝跨版续页", False)
    except ValueError as exc:
        check("检索台拒绝跨版续页", True, str(exc))
    # 即使服务器被攻破强行拼包，客户端独立验证同样拦截
    spliced = server.get_page(2, "花", 1)
    spliced["cursor"] = old_cursor
    try:
        Client(verifier).verify_response(spliced)
        check("查词席独立拒绝拼接页", False)
    except StaleCursorError as exc:
        check("查词席独立拒绝拼接页", True, str(exc))

    # 降级防护：见过 v2 后再收 v1
    c2 = Client(verifier)
    c2.fetch_page(transport, "花", 2, want_version=2)
    try:
        c2.verify_response(server.get_page(1, "花", 2))
        check("旧版本降级被拒绝", False)
    except VerificationError as exc:
        check("旧版本降级被拒绝", True, str(exc))

    # ------------------------------------------------ 4. 关服务器离线核验 + 另一段继续出稿
    section("4. 排字工坊关掉服务器：离线核验真实交件，未受影响段继续出稿")
    handoff = workdir / "handoff"
    handoff.mkdir()
    # 模拟事先落盘的交件：前缀“木*”的两张页
    srv = _server_with(v1)
    pages = [
        srv.get_page(1, "木", 2),
        srv.get_page(1, "木", 2, cursor=srv.get_page(1, "木", 2)["next_cursor"]),
    ]
    for i, p in enumerate(pages):
        (handoff / f"page_{i:03d}.json").write_text(
            json.dumps(p, ensure_ascii=False), encoding="utf-8")

    workshop = Client(Verifier((trust_dir / "vk.pem").read_bytes(), KEY_ID))
    offline = OfflineTransport(handoff)
    ms = Manuscript()
    injected = 0
    first = workshop.fetch_page(offline, "木", 2, want_version=1)
    injected += ms.inject_page(
        [Entry.from_json(d) for d in first["entries"]], first["version"])
    second = workshop.fetch_page(offline, "木", 2,
                                 cursor=first["next_cursor"], want_version=1)
    injected += ms.inject_page(
        [Entry.from_json(d) for d in second["entries"]], second["version"])
    check("离线交件全部通过独立验页", True,
          f"并入 {injected} 个义项：{sorted(ms.notes)}")

    # 另一前缀（不同段）继续出带注稿——坏页不能污染已印注释
    good = workshop.fetch_page(transport, "仪表", 5, want_version=1)
    before = dict(ms.notes)
    ms.inject_page([Entry.from_json(d) for d in good["entries"]], 1)
    bad = server.get_page(1, "杜鹃", 5)
    bad["entries"][0]["gloss"] = "被恶意替换的解释"
    try:
        workshop.verify_response(bad)
        check("缺项/坏页被拦下且未污染稿件", False)
    except VerificationError as exc:
        unaffected = all(ms.get(k[0], k[1]) == v for k, v in before.items())
        check("缺项/坏页被拦下且未污染稿件", unaffected, str(exc))
    check("已印注释保持原值",
          ms.get("木简", 0)["gloss"] == "古代用来写字的木片"
          and ms.get("仪表", 1)["gloss"].startswith("测定温度"))

    # ------------------------------------------------ 5. 恢复同版页，仅改所用义项，注音/解释独立变动
    section("5. 同版恢复与字段独立变动（v3 只改 行/2 的解释，注音不动）")
    v3_note = next(e for e in v3.entries if e.key == ("行", 2))
    v1_note = next(e for e in v1.entries if e.key == ("行", 2))
    check("v3 中 行/2 注音未变", v3_note.pron == v1_note.pron == "xíng")
    check("v3 中 行/2 解释已独立变动", v3_note.gloss != v1_note.gloss
          and v3_note.gloss.startswith("（v3 改注）"))
    # 同形词的其他义项完全不动
    check("行/0、行/1 在 v3 中逐字段不变",
          next(e for e in v3.entries if e.key == ("行", 0)) ==
          next(e for e in v1.entries if e.key == ("行", 0))
          and next(e for e in v3.entries if e.key == ("行", 1)) ==
          next(e for e in v1.entries if e.key == ("行", 1)))
    # 工坊用同版页恢复并注入，稿件取到新解释；独立再演示只改读音的版本
    resp_v3 = Client(verifier).fetch_page(transport, "行", 10, want_version=3)
    ms2 = Manuscript()
    ms2.inject_page([Entry.from_json(d) for d in resp_v3["entries"]], 3)
    check("v3 页面可验证并注入工坊稿件",
          ms2.get("行", 2)["gloss"] == V3_GLOSS_PATCH[3])
    pron_only = [
        Entry(e.word, e.sense,
              "háng2" if e.key == ("行", 1) else e.pron, e.gloss)
        for e in v1.entries
    ]
    v4 = compiler.publish(4, pron_only, "2026-11-25T09:00:00Z")
    s4 = _server_with(v4)
    resp_v4 = Client(verifier).fetch_page(
        NetworkTransport(s4), "行", 10, want_version=4)
    ms3 = Manuscript()
    ms3.inject_page([Entry.from_json(d) for d in resp_v4["entries"]], 4)
    check("只改注音的修订：读音变而解释不变",
          ms3.get("行", 1)["pron"] == "háng2"
          and ms3.get("行", 1)["gloss"] == "行列；行业"
          and ms3.get("行", 0)["gloss"] == "行走；走")

    # ------------------------------------------------ 6. 新版插入词可被重新发起的查询检出
    section("6. v2 插入的新义项与新词必须能被新查询真正检出")
    c3 = Client(verifier)
    hua = c3.fetch_all(transport, "花", 2, want_version=2)
    check("重新查询“花”检出 v2 新义项 花/3",
          ("花", 3) in [e.key for e in hua],
          f"共 {len(hua)} 个义项")
    gang = c3.fetch_all(transport, "花岗石", 2, want_version=2)
    check("重新查询检出 v2 新词“花岗石”",
          [e.key for e in gang] == [("花岗石", 0)])
    # v1 老查词席（只见过 v1）查不到新词，且 v2 证明与 v1 根不同，无法混用
    old_client = Client(verifier)
    old_gang = old_client.fetch_all(
        NetworkTransport(_server_with(v1)), "花岗石", 2, want_version=1)
    check("v1 中查询新词得到可验证的空结果", old_gang == [])

    # ------------------------------------------------ 7. 边界情形的一致解释
    section("7. 空前缀、跨段最后一页、多义词跨页")
    c4 = Client(verifier)
    all_walk = c4.fetch_all(transport, "", 50, want_version=2)
    check("空前缀 = 全库分页，条目数与凭据一致且不依赖服务器自报",
          len(all_walk) == v2.credential.entry_count == len(v2.entries))
    # 跨内部节点段的最后一页：取一个必然跨段的前缀并翻到最后
    pages_seen, cursor, last_resp = 0, None, None
    while True:
        resp = server.get_page(2, "术语", 37, cursor)
        Client(verifier).verify_response(resp)
        pages_seen += 1
        last_resp = resp
        if not resp["has_more"]:
            break
        cursor = resp["next_cursor"]
    check("跨段最后一页 has_more=False 且无 next_cursor",
          pages_seen > 1 and not last_resp["has_more"]
          and last_resp["next_cursor"] is None)
    # 多义项恰被页缝切开
    cut = Client(verifier).fetch_all(transport, "长", 1, want_version=1)
    check("多义词“长”以页长 1 翻页，三个义项齐全且有序",
          [e.key for e in cut] == [("长", 0), ("长", 1), ("长", 2)])

    # ------------------------------------------------ 8. 证明体积
    section("8. 短页证明体积应明显小于完整词库")
    full_bytes = len(json.dumps(v2.snapshot_json(), ensure_ascii=False))
    short = server.get_page(2, "花", 2)
    proof_bytes = len(json.dumps(short, ensure_ascii=False))
    ratio = proof_bytes / full_bytes
    print(f"  {INFO} 全库快照 JSON：{full_bytes:,} 字节")
    print(f"  {INFO} 一张短页（条目+证明+凭据）：{proof_bytes:,} 字节，占比 {ratio:.2%}")
    # 再看不同页长下证明只随页面规模缓慢增长
    sizes = {}
    for lim in (2, 8, 32):
        sizes[lim] = len(json.dumps(server.get_page(2, "术语", lim),
                                    ensure_ascii=False))
    print(f"  {INFO} 页长 2/8/32 的答页体积：{sizes[2]:,} / {sizes[8]:,} / {sizes[32]:,} 字节")
    check("短页证明显著小于全库（< 10%）", ratio < 0.10, f"{ratio:.2%}")

    # ------------------------------------------------ 汇总
    section("汇总")
    failed = [n for n, ok, _ in results if not ok]
    for n, ok, detail in results:
        print(f"  {PASS if ok else FAIL} {n}")
    print(f"\n共 {len(results)} 项，通过 {len(results) - len(failed)} 项，"
          f"失败 {len(failed)} 项")
    shutil.rmtree(workdir, ignore_errors=True)
    return 0 if not failed else 1


def _server_with(rev) -> Server:
    s = Server()
    s.load_snapshot(rev.snapshot_json())
    return s


if __name__ == "__main__":
    raise SystemExit(main())
