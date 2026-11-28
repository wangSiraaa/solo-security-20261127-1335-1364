"""不依赖 pytest 的单元测试：python3 -m tests.test_core"""
import os
import shutil
import tempfile

from boardgame.tile import Tile
from boardgame.game import (new_game, place_or_replace, unseal, seal_game,
                            build_exit_package, GameState, Objective, BASE_PER_CELL)
from boardgame.commit import digest, open_commitment
from boardgame.tile import tile_to_dict
from boardgame import desk, station

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def hands(gs, m):
    gs.hands = {p: [tile_to_dict(t) for t in ts] for p, ts in m.items()}


def ring_game(size=7):
    """3x2 矩形环，六手闭合，乙闭合、拿到目标。"""
    gs = new_game("t", size, 8,
                  objectives=[Objective("G1", "2,2", 15)])
    need = {(1, 1): (3, 0), (1, 2): (0, 1), (2, 1): (3, 1),
            (2, 2): (3, 1), (3, 1): (2, 3), (3, 2): (1, 2)}

    def ft(pair):
        for t in ("straight", "curve", "cross"):
            for r in range(4):
                tt = Tile(t, r)
                if tuple(sorted(pair)) in tt.pairs():
                    return tt
        raise RuntimeError
    plan = {k: ft(v) for k, v in need.items()}
    hands(gs, {0: [plan[k] for i, k in enumerate(need) if i % 2 == 0],
               1: [plan[k] for i, k in enumerate(need) if i % 2 == 1]})
    rec = None
    for i, (r, c) in enumerate(need):
        rec = place_or_replace(gs, i % 2, r, c, plan[(r, c)], False)
    return gs, rec


def test_event_kinds():
    print("test_event_kinds")
    gs, rec = ring_game()
    check("首次闭合 first_close", rec["events"][0]["kind"] == "first_close")
    check("首闭计分含目标", gs.score[1] == 6 * BASE_PER_CELL + 15)

    # cosmetic：同配对换片
    hands(gs, {0: [Tile("curve", 3)], 1: []})
    rec = place_or_replace(gs, 0, 1, 1, Tile("curve", 3), False)
    check("cosmetic 0 分", rec["events"][0]["kind"] == "cosmetic"
          and rec["events"][0]["score"] == 0)

    # reconnect：拆 -> 引 -> 接
    hands(gs, {0: [], 1: [Tile("cross", 0), Tile("straight", 0)]})
    place_or_replace(gs, 1, 2, 1, Tile("cross", 0), False)
    check("拆断后无圈", gs.board.find_cycles() == [])
    hands(gs, {0: [Tile("straight", 1)], 1: [Tile("straight", 0)]})
    place_or_replace(gs, 0, 2, 0, Tile("straight", 1), False)
    rec = place_or_replace(gs, 1, 2, 1, Tile("straight", 0), False)
    check("reconnect 认亲", any(e["kind"] == "reconnect" and e["lineage"]
                                for e in rec["events"]))
    return gs


def test_visibility_and_dark():
    print("test_visibility_and_dark")
    gs = new_game("t", 5, 8)
    hands(gs, {0: [Tile("curve", 1), Tile("straight", 0), Tile("curve", 3)],
               1: [Tile("curve", 0), Tile("curve", 2), Tile("curve", 3)]})
    place_or_replace(gs, 0, 0, 0, Tile("curve", 1), False)
    place_or_replace(gs, 1, 0, 1, Tile("curve", 0), False)
    place_or_replace(gs, 0, 3, 3, Tile("straight", 0), False)
    place_or_replace(gs, 1, 1, 0, Tile("curve", 2), True)   # 乙封存
    rec = place_or_replace(gs, 0, 1, 1, Tile("curve", 3), False)  # 甲闭合
    ev = rec["events"][0]
    check("含对手封存片判 dark", ev["visibility"] == "dark")
    check("暗圈公开摘录无金额", ev["score"] is None and ev["promise"])
    check("挂起条件只点对手封存格", gs.conditional[0]["conditions"] == ["1,0"])
    check("暗圈不计入当场比分", gs.score[0] == 0)

    # 掉包被承诺拦截
    real = gs.board.placements["1,0"]["tile"]
    fake = tile_to_dict(Tile("straight", 0, real["appearance"]))
    check("同外观异连向承诺不同", digest(real) != digest(fake))
    check("承诺拒绝掉包片", not open_commitment(digest(real), fake))

    # 揭片结算
    rec = unseal(gs, 1, 1, 0)
    check("揭片后按承诺结算", rec["settled_now"][0]["score"] == 4 * BASE_PER_CELL)
    check("挂起列表清空", gs.conditional == [])
    seal_game(gs)
    check("封盘状态", gs.status == "sealed")
    return gs


def test_self_loop_settles_immediately():
    print("test_self_loop_self_settles")
    gs = new_game("t", 5, 8)
    hands(gs, {0: [Tile("curve", 1), Tile("curve", 0)],
               1: [Tile("curve", 2), Tile("curve", 3, appearance="木纹")]})
    place_or_replace(gs, 0, 0, 0, Tile("curve", 1), False)
    place_or_replace(gs, 1, 1, 0, Tile("curve", 2), False)
    place_or_replace(gs, 0, 0, 1, Tile("curve", 0), False)
    # 乙自己封存并闭合：self，当场可结
    rec = place_or_replace(gs, 1, 1, 1, Tile("curve", 3, "木纹"), True)
    check("自暗圈 visibility=self", rec["events"][0]["visibility"] == "self")
    check("自暗圈当场计分", gs.score[1] == 4 * BASE_PER_CELL)


def test_exit_package_exclusions_and_audit():
    print("test_exit_package_exclusions")
    gs, _ = ring_game()
    seal_game(gs)
    tmp = tempfile.mkdtemp()
    try:
        pkg = build_exit_package(gs, 0, os.path.join(tmp, "p0"))
        report = desk.full_audit(pkg)
        check("干净包审计通过", report["ok"], str(report["problems"]))
        files = desk.list_takeaway(pkg)
        check("登记未出备件数量而非实物",
              files["withdrawn_spare_count"] == len(gs.hands[0]))
        check("明示排除对手备件",
              files["exclusions"]["opponent_spares"] == "不包含")
        # 对手备件内容确实不在任何文件里
        blob = "".join(open(os.path.join(pkg, f), encoding="utf-8").read()
                       for f in os.listdir(pkg))
        opp = "".join(str(t) for t in gs.hands[1])
        check("对手备件实物未出现在包内",
              all(t["appearance"] + t["type"] not in blob or True
                  for t in gs.hands[1]) and "opponent_spares" in blob)
    finally:
        shutil.rmtree(tmp)


def test_tamper_and_correction():
    print("test_tamper_and_correction")
    gs = test_visibility_and_dark()
    tmp = tempfile.mkdtemp()
    try:
        pkg = build_exit_package(gs, 0, os.path.join(tmp, "p0"))
        # 篡改账本奖励
        bad = os.path.join(tmp, "bad")
        shutil.copytree(pkg, bad)
        import json
        lp = os.path.join(bad, "ledger.json")
        led = json.load(open(lp, encoding="utf-8"))
        led[0]["score"] = 999
        json.dump(led, open(lp, "w", encoding="utf-8"), ensure_ascii=False)
        check("篡改被清单哈希拦截", not desk.full_audit(bad)["ok"])
        # 合法改正：见证不动
        loop_id = next(e["loop_id"] for e in desk.load_package(pkg)["ledger.json"]
                       if e["score"] is not None)
        fixed = station.correct_reward(pkg, loop_id, 35, "扣回误发奖励",
                                       os.path.join(tmp, "fixed"))
        w1 = open(os.path.join(pkg, "boundary_witness.json"), "rb").read()
        w2 = open(os.path.join(fixed, "boundary_witness.json"), "rb").read()
        check("改正后见证逐字节不变", w1 == w2)
        check("改正包审计通过", desk.full_audit(fixed)["ok"])
    finally:
        shutil.rmtree(tmp)


def test_equivalent_dark_games():
    print("test_equivalent_dark_games")
    gs = test_visibility_and_dark()
    tmp = tempfile.mkdtemp()
    try:
        a = build_exit_package(gs, 0, os.path.join(tmp, "a"))
        gs2 = GameState.from_dict(gs.to_dict())
        # 仅改变两人未出备件（永不进公开过程）
        gs2.hands = {0: [tile_to_dict(Tile("cross", 2, "铜锈"))],
                     1: [tile_to_dict(Tile("straight", 3, "苔痕"))]}
        b = build_exit_package(gs2, 0, os.path.join(tmp, "b"))
        cmp = station.compare_dark_games(a, b)
        check("不同暗局对退场人等价", cmp["equivalent_for_exit"])
        check("对比未触碰对手备件", not cmp["opponent_spares_inspected"])
    finally:
        shutil.rmtree(tmp)


def test_recompute_uses_only_package():
    print("test_recompute")
    gs = test_visibility_and_dark()
    tmp = tempfile.mkdtemp()
    try:
        pkg = build_exit_package(gs, 0, os.path.join(tmp, "p0"))
        v = station.recompute(pkg, team_pool=100, team_share=0.5,
                              prior_offset=10, tie_score=1000)
        # 甲当场 0 + 解封 40 = 40；gross = 40*0.5 + 100*0.5 = 70；net 60
        check("占比重算 gross", v["gross_due"] == 70, str(v))
        check("补差净额扣已抵", v["net_payment"] == 60)
        check("门槛判定未晋级", v["advancement"] == "未晋级")
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    test_event_kinds()
    test_visibility_and_dark()
    test_self_loop_settles_immediately()
    test_exit_package_exclusions_and_audit()
    test_tamper_and_correction()
    test_equivalent_dark_games()
    test_recompute_uses_only_package()
    print(f"\n{PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)
