"""剧本演示：把题面要求逐幕走一遍。

幕次：
1. 公开成圈（first_close），两人当场各领至少一项应得分
2. 换片只改外观（cosmetic，0 分）
3. 拆圈再接（reconnect）
4. 封存支路：自暗圈本人可核可结；含对手封存片的暗圈挂起（核一圈可结、一圈有条件）
5. 外观相同、连向不同暗区的两片临时交换：承诺拦住掉包
6. 揭片解封，挂起圈凭承诺结算
7. 退场打包：带走件不含对手备件/未出备件/未领目标
8. 只读退场台重算
9. 记分台收到占比等参数，办补差/晋级
10. 保持见证不变，仅改一次已兑奖励，旧包审计失败、新包通过
11. 两套不同暗局（仅对手备件不同）的整套带走件等价对比
"""

from __future__ import annotations

import json
import os
import shutil

from .tile import Tile
from .game import (new_game, place_or_replace, unseal, seal_game,
                   build_exit_package, Objective, BASE_PER_CELL)
from .commit import digest, open_commitment
from .tile import tile_to_dict
from . import desk, station


def _hand(*tiles: Tile) -> list[dict]:
    return [tile_to_dict(t) for t in tiles]


def _give_hands(gs, hands: dict[int, list[Tile]]) -> None:
    gs.hands = {pid: _hand(*ts) for pid, ts in hands.items()}


def banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def _show(rec: dict) -> None:
    for e in rec.get("events", []):
        score = "挂起(承诺)" if e["score"] is None else f"{e['score']} 分"
        print(f"    -> {e['kind']:11s} {e['visibility']:6s} 圈 {e['loop_id'][:8]} {score}")


def run(workdir: str) -> None:
    if os.path.exists(workdir):
        shutil.rmtree(workdir)
    os.makedirs(workdir)

    # ---------- 对局一：公开圈 + 换片三态 + 自暗圈 ----------
    banner("对局一  落片、换片三态、自暗圈（两人当场各有得分）")
    gs = new_game("对局一", size=7, hand_size=8,
                  objectives=[Objective(id="G1", target="2,2", reward=15)])
    # 3x2 矩形环在 r1..3 / c1..2，六片各需的配对
    ring = {(1, 1): (3, 0), (1, 2): (0, 1), (2, 1): (3, 1),
            (2, 2): (3, 1), (3, 1): (2, 3), (3, 2): (1, 2)}

    def _ft(pair):
        for t in ("straight", "curve", "cross"):
            for r in range(4):
                tt = Tile(t, r)
                if tuple(sorted(pair)) in tt.pairs():
                    return tt
        raise RuntimeError("no tile")

    plan = {k: _ft(v) for k, v in ring.items()}
    tiles_p0 = [plan[k] for i, k in enumerate(ring) if i % 2 == 0]
    tiles_p1 = [plan[k] for i, k in enumerate(ring) if i % 2 == 1]
    # 追加 cosmetic / 拆圈 / 引片 / 重接所需备件
    tiles_p0 += [Tile("curve", 3, "木纹"), Tile("straight", 1, "苔痕"),
                 Tile("straight", 0, "铜锈")]
    tiles_p1 += [Tile("cross", 0, "铜锈"), Tile("straight", 0, "铜锈")]
    _give_hands(gs, {0: tiles_p0, 1: tiles_p1})
    for i, (r, c) in enumerate(ring):
        actor = i % 2
        rec = place_or_replace(gs, actor, r, c, plan[(r, c)], sealed=False)
    print("  六片落完，3x2 矩形环闭合（目标 G1 在圈内）：")
    _show(rec)
    assert gs.score[1] == 6 * BASE_PER_CELL + 15

    banner("幕二  cosmetic：同构换片，圈边集合不变，0 分")
    # (1,1) 原片 curve r1，换同配对的 curve r3
    r5 = place_or_replace(gs, 0, 1, 1, Tile("curve", 3, "木纹"), sealed=False)
    _show(r5)
    assert all(e["kind"] == "cosmetic" for e in r5["events"])

    banner("幕三  reconnect：乙拆断原圈，甲引断头到新格，乙重接认亲")
    r6 = place_or_replace(gs, 1, 2, 1, Tile("cross", 0, "铜锈"), sealed=False)
    print("    cross 换入 (2,1)，原环拆断，本手无新圈")
    assert not gs.board.find_cycles()
    r7 = place_or_replace(gs, 0, 2, 0, Tile("straight", 1, "苔痕"), sealed=False)
    print("    甲在 (2,0) 落直通片，把断头引向新格（仍未成圈）")
    r8 = place_or_replace(gs, 1, 2, 1, Tile("straight", 0, "铜锈"), sealed=False)
    _show(r8)
    assert any(e["kind"] == "reconnect" for e in r8["events"])

    banner("幕四  自暗圈：甲把封存片放进自己的圈，本人可核可结、对手只见承诺")
    # 在 (4,1)(4,2)(5,1)(5,2) 再做一圈，(5,2) 为甲的封存片
    _give_hands(gs, {
        0: [Tile("straight", 1, "木纹"), Tile("curve", 2, "苔痕")],
        1: [Tile("curve", 1, "苔痕"), Tile("curve", 0, "铜锈"),
            Tile("curve", 3, "青石"), Tile("curve", 2, "木纹")],
    })
    place_or_replace(gs, 0, 4, 1, Tile("straight", 1, "木纹"), sealed=False)
    place_or_replace(gs, 1, 5, 1, Tile("curve", 1, "苔痕"), sealed=False)
    place_or_replace(gs, 0, 4, 2, Tile("curve", 2, "苔痕"), sealed=False)
    r9 = place_or_replace(gs, 1, 5, 2, Tile("curve", 2, "木纹"), sealed=True)
    _show(r9)
    assert gs.score[1] >= 4 * BASE_PER_CELL  # 乙当场领到自暗圈分
    print(f"  当前比分：甲 {gs.score[0]} 分，乙 {gs.score[1]} 分（两人均已当场领取）")

    pkg1 = build_exit_package(gs, 0, os.path.join(workdir, "pkg_p0_game1"))
    build_exit_package(gs, 1, os.path.join(workdir, "pkg_p1_game1"))
    gs.save(os.path.join(workdir, "game1.json"))

    # ---------- 对局二：含对手封存片的暗圈（可结/有条件各一圈） ----------
    banner("对局二  外观相同连向不同暗区：一圈当场可结，一圈有条件挂起")
    gs2 = new_game("对局二", size=5, hand_size=8)
    _give_hands(gs2, {
        # 两片外观同为"木纹"：配对不同
        0: [Tile("curve", 1, "木纹"), Tile("straight", 0, "铜锈"),
            Tile("curve", 3, "青石"), Tile("curve", 1, "木纹")],
        1: [Tile("curve", 0, "青石"), Tile("curve", 2, "木纹"),
            Tile("curve", 3, "苔痕")],
    })
    place_or_replace(gs2, 0, 0, 0, Tile("curve", 1, "木纹"), sealed=False)
    place_or_replace(gs2, 1, 0, 1, Tile("curve", 0, "青石"), sealed=False)
    # 甲在远处落一片调整轮换相位（不参与本圈）
    place_or_replace(gs2, 0, 3, 3, Tile("straight", 0, "铜锈"), sealed=False)
    # 乙在 (1,0) 放封存片（甲的对手暗区），外观也是木纹
    place_or_replace(gs2, 1, 1, 0, Tile("curve", 2, "木纹"), sealed=True)
    r9 = place_or_replace(gs2, 0, 1, 1, Tile("curve", 3, "青石"), sealed=False)
    print("  甲闭合的圈经过乙的封存格：对甲为 dark，金额封进承诺、挂起")
    _show(r9)
    assert gs2.conditional and gs2.conditional[0]["conditions"] == ["1,0"]

    # 同盘另做一圈全公开圈，乙先落、甲闭合，甲当场可结
    _give_hands(gs2, {
        0: [Tile("curve", 2, "苔痕"), Tile("curve", 3, "铜锈")],
        1: [Tile("curve", 1, "铜锈"), Tile("curve", 0, "苔痕")],
    })
    place_or_replace(gs2, 1, 3, 0, Tile("curve", 1, "铜锈"), sealed=False)
    place_or_replace(gs2, 0, 4, 0, Tile("curve", 2, "苔痕"), sealed=False)
    place_or_replace(gs2, 1, 3, 1, Tile("curve", 0, "苔痕"), sealed=False)
    r11 = place_or_replace(gs2, 0, 4, 1, Tile("curve", 3, "铜锈"), sealed=False)
    print("  另一侧公开圈由甲当场结算：")
    _show(r11)
    assert gs2.score[0] > 0

    banner("幕五  临时交换两片外观相同、连向不同暗区的支路：承诺拦截")
    cell = "1,0"
    real = gs2.board.placements[cell]["tile"]
    swapped = tile_to_dict(Tile("straight", 0, "木纹"))  # 同外观，配对变了
    gs2.board.placements[cell]["tile"] = swapped
    print(f"    原片承诺 {digest(real)[:12]}… 掉包片承诺 {digest(swapped)[:12]}…")
    print(f"    open_commitment(原承诺, 掉包片) = {open_commitment(digest(real), swapped)}")
    assert not open_commitment(digest(real), swapped)
    gs2.board.placements[cell]["tile"] = real  # 换回，等待正常揭片

    banner("幕六  乙揭片：承诺核对通过，挂起暗圈转为当场结算")
    r12 = unseal(gs2, 1, 1, 0)
    print(f"    解封结算：{r12['settled_now']}")
    assert r12["settled_now"] and not gs2.conditional
    seal_game(gs2)
    pkg2a = build_exit_package(gs2, 0, os.path.join(workdir, "pkg_p0_game2"))
    gs2.save(os.path.join(workdir, "game2.json"))

    # 等价暗局：公开过程完全相同，只是两人手里未出的备件不同（永不公开）
    gs2b = new_game("对局二", size=5, hand_size=8)
    gs2b.board = gs2.board
    gs2b.turn_log, gs2b.events = gs2.turn_log, gs2b.events
    gs2b.events = gs2.events
    gs2b.score, gs2b.conditional = dict(gs2.score), []
    gs2b.objectives, gs2b.turn, gs2b.status = list(gs2.objectives), gs2.turn, gs2.status
    gs2b.hands = {0: [tile_to_dict(Tile("cross", 2, "铜锈"))],
                  1: [tile_to_dict(Tile("straight", 3, "苔痕"))]}
    pkg2b = build_exit_package(gs2b, 0, os.path.join(workdir, "pkg_p0_game2_alt"))

    # ---------- 退场台审计 ----------
    banner("幕七/八  退场带走件 + 只读退场证明台")
    info = desk.list_takeaway(pkg1)
    print("  带走文件：", ", ".join(info["files"]))
    print("  明确排除：", info["exclusions"])
    audit1 = desk.full_audit(pkg1)
    audit2 = desk.full_audit(pkg2a)
    print(f"  对局一甲包审计：{'通过' if audit1['ok'] else audit1['problems']}")
    print(f"  对局二甲包审计：{'通过' if audit2['ok'] else audit2['problems']}")
    assert audit1["ok"] and audit2["ok"]

    # ---------- 记分台 ----------
    banner("幕九  记分台此时才收到占比/已抵奖励/并列规则，办补差与晋级")
    verdict = station.recompute(pkg2a, team_pool=200, team_share=0.4,
                                prior_offset=18, tie_score=60)
    print(json.dumps(verdict, ensure_ascii=False, indent=2))

    banner("幕十  保持见证不变，仅改一次已兑奖励")
    loop_id = next(e["loop_id"] for e in desk.load_package(pkg2a)["ledger.json"]
                   if e["score"] is not None)
    # 该圈重算基础分 40；以"误发 5 分、扣回"为由改正为 35，留痕但不动见证
    fixed = station.correct_reward(pkg2a, loop_id, new_score=35,
                                   reason="该圈曾误发 5 分目标奖励，目标实际不在圈内，扣回",
                                   out_dir=os.path.join(workdir, "pkg_p0_game2_fixed"))
    # 见证文件必须逐字节不变
    w1 = open(os.path.join(pkg2a, "boundary_witness.json"), "rb").read()
    w2 = open(os.path.join(fixed, "boundary_witness.json"), "rb").read()
    print(f"  boundary_witness 逐字节不变：{w1 == w2}")
    # 篡改旧包的已兑奖励（不动见证），审计必须失败
    tampered = os.path.join(workdir, "pkg_p0_game2_tampered")
    shutil.copytree(pkg2a, tampered)
    led_path = os.path.join(tampered, "ledger.json")
    led = json.load(open(led_path, encoding="utf-8"))
    led[0]["score"] = 999
    json.dump(led, open(led_path, "w", encoding="utf-8"), ensure_ascii=False)
    bad = desk.full_audit(tampered)
    print(f"  仅改奖励后的旧包审计：{'通过(异常!)' if bad['ok'] else '被清单哈希拦截'}")
    assert not bad["ok"]
    assert desk.full_audit(fixed)["ok"]

    banner("幕十一  两套不同暗局的带走件等价对比（不看对手备件）")
    cmp = station.compare_dark_games(pkg2a, pkg2b)
    print(json.dumps(cmp, ensure_ascii=False, indent=2))
    assert cmp["equivalent_for_exit"] and not cmp["opponent_spares_inspected"]

    banner("全部幕次通过")
    print(f"  产物目录：{workdir}")
    print("  - game1.json / game2.json 可继续走棋的本地存档")
    print("  - pkg_* 退场带走件；*_fixed 为奖励改正后的新包；*_tampered 为反面对照")
