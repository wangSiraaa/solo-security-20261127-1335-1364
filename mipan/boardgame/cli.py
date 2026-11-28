"""本地命令行入口。

可继续走棋：play 是带存档的交互式 REPL；其余为一次性子命令。
"""

from __future__ import annotations

import argparse
import json
import sys

from .tile import Tile, TILE_TYPES, APPEARANCES
from .game import (new_game, place_or_replace, unseal, seal_game,
                   build_exit_package, GameState, Objective)
from . import desk, station, demo


def _print_board(gs: GameState, viewer: int) -> None:
    b = gs.board
    print(f"\n棋盘 {gs.name}（第 {gs.turn} 手，轮到 {gs.players[gs.next_player]}）")
    for r in range(b.size):
        line = []
        for c in range(b.size):
            p = b.placements.get(f"{r},{c}")
            if not p:
                line.append("  ·  ")
            elif p["sealed"]:
                mark = "?" if p["owner"] == viewer else "x"
                line.append(f" {p['tile']['appearance'][:1]}{mark} ")
            else:
                t = b.tile_at(r, c)
                line.append(f" {t.glyph()}{p['owner']}  ")
        print("".join(line))
    print("图例：?=本人封存片  x=对手封存片  数字=落片人；· 为空位")
    print(f"比分：甲 {gs.score[0]}  乙 {gs.score[1]}  "
          f"挂起暗圈 {len(gs.conditional)} 笔")


def _parse_tile(spec: str) -> Tile:
    parts = spec.split("/")
    if len(parts) < 2:
        raise argparse.ArgumentTypeError(
            "片格式：类型/旋转/外观，如 curve/2/木纹；straight/0/青石")
    typ, rot = parts[0], int(parts[1])
    appearance = parts[2] if len(parts) > 2 else "木纹"
    if typ not in TILE_TYPES or not 0 <= rot < 4 or appearance not in APPEARANCES:
        raise argparse.ArgumentTypeError("类型/旋转/外观取值非法")
    return Tile(typ, rot, appearance)


def cmd_init(args) -> None:
    objs = [Objective(id=o[0], target=o[1], reward=int(o[2]))
            for o in (x.split(":") for x in args.objective)]
    gs = new_game(args.name, args.size, args.hand, seed=args.seed, objectives=objs)
    gs.save(args.save)
    print(f"已开局 {args.name}（{args.size}x{args.size}，每手 {args.hand} 片）→ {args.save}")


def cmd_play(args) -> None:
    gs = GameState.load(args.save)
    viewer = args.player
    print(f"继续对局 {gs.name}。输入 help 查看指令，quit 存档退出。")
    while True:
        _print_board(gs, viewer)
        try:
            raw = input(f"{gs.players[gs.next_player]}> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not raw:
            continue
        parts = raw.split()
        cmd = parts[0]
        try:
            if cmd == "quit":
                break
            elif cmd == "help":
                print("place r c 类型/旋转/外观 [seal]   落片（seal=封存）\n"
                      "unseal r c                         揭自己的封存片\n"
                      "hand                               看本人备件\n"
                      "log                                看公开过程\n"
                      "seal                               封盘（需无封存/挂起）\n"
                      "exit <目录>                        生成本人退场带走件\n"
                      "quit                               存档退出")
            elif cmd == "hand":
                for i, t in enumerate(gs.hands[viewer]):
                    print(f"  [{i}] {Tile(**t).describe()}")
            elif cmd == "log":
                print(json.dumps(gs.turn_log[-5:], ensure_ascii=False, indent=2))
            elif cmd == "place":
                r, c = int(parts[1]), int(parts[2])
                tile = _parse_tile(parts[3])
                sealed = len(parts) > 4 and parts[4] == "seal"
                rec = place_or_replace(gs, gs.next_player, r, c, tile, sealed)
                for e in rec["events"]:
                    sc = "挂起(承诺)" if e["score"] is None else f"{e['score']} 分"
                    print(f"  {e['kind']} {e['visibility']} {e['loop_id'][:8]} {sc}")
            elif cmd == "unseal":
                rec = unseal(gs, gs.next_player, int(parts[1]), int(parts[2]))
                print("  解封结算：", rec["settled_now"])
            elif cmd == "seal":
                seal_game(gs)
                print("  已封盘")
            elif cmd == "exit":
                path = build_exit_package(gs, viewer, parts[1])
                print(f"  带走件已生成（不含对手备件/未出备件/未领目标）：{path}")
            else:
                print("未知指令，help 查看用法")
        except Exception as exc:  # 对局中不退出，只回显规则错误
            print(f"  ! {exc}")
    gs.save(args.save)
    print(f"已存档 → {args.save}")


def cmd_exit(args) -> None:
    gs = GameState.load(args.save)
    path = build_exit_package(gs, args.player, args.out)
    print(path)


def cmd_audit(args) -> None:
    report = desk.full_audit(args.pkg)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["ok"] else 1)


def cmd_takeaway(args) -> None:
    print(json.dumps(desk.list_takeaway(args.pkg), ensure_ascii=False, indent=2))


def cmd_recompute(args) -> None:
    verdict = station.recompute(args.pkg, team_pool=args.team_pool,
                                team_share=args.team_share,
                                prior_offset=args.prior, tie_score=args.tie)
    print(json.dumps(verdict, ensure_ascii=False, indent=2))


def cmd_correct(args) -> None:
    station.correct_reward(args.pkg, args.loop, args.score, args.reason, args.out)
    print(args.out)


def cmd_compare(args) -> None:
    print(json.dumps(station.compare_dark_games(args.a, args.b),
                     ensure_ascii=False, indent=2))


def cmd_demo(args) -> None:
    demo.run(args.out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mipan", description="双人协作谜盘记分器（Mode A）")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("init", help="开局")
    q.add_argument("name")
    q.add_argument("--size", type=int, default=5)
    q.add_argument("--hand", type=int, default=8)
    q.add_argument("--seed", type=int)
    q.add_argument("--objective", action="append", default=[],
                    help="可重复：id:r,c:reward，如 G1:1,1:15")
    q.add_argument("--save", required=True)
    q.set_defaults(func=cmd_init)

    q = sub.add_parser("play", help="交互式继续走棋")
    q.add_argument("--save", required=True)
    q.add_argument("--player", type=int, choices=(0, 1), default=0)
    q.set_defaults(func=cmd_play)

    q = sub.add_parser("exit-package", help="生成退场带走件")
    q.add_argument("--save", required=True)
    q.add_argument("--player", type=int, choices=(0, 1), required=True)
    q.add_argument("--out", required=True)
    q.set_defaults(func=cmd_exit)

    q = sub.add_parser("audit", help="只读退场证明台：审计并重算")
    q.add_argument("pkg")
    q.set_defaults(func=cmd_audit)

    q = sub.add_parser("takeaway", help="列出带走件与排除项")
    q.add_argument("pkg")
    q.set_defaults(func=cmd_takeaway)

    q = sub.add_parser("recompute", help="记分台：占比/补差/晋级")
    q.add_argument("pkg")
    q.add_argument("--team-pool", type=int, default=0)
    q.add_argument("--team-share", type=float, default=0.0)
    q.add_argument("--prior", type=int, default=0, help="退场前已抵奖励")
    q.add_argument("--tie", type=int, help="并列晋级门槛分")
    q.set_defaults(func=cmd_recompute)

    q = sub.add_parser("correct", help="见证不变，仅改一次已兑奖励")
    q.add_argument("pkg")
    q.add_argument("--loop", required=True)
    q.add_argument("--score", type=int, required=True)
    q.add_argument("--reason", required=True)
    q.add_argument("--out", required=True)
    q.set_defaults(func=cmd_correct)

    q = sub.add_parser("compare", help="两套暗局带走件等价对比")
    q.add_argument("a")
    q.add_argument("b")
    q.set_defaults(func=cmd_compare)

    q = sub.add_parser("demo", help="跑题面全幕剧本")
    q.add_argument("--out", default="data/demo_run")
    q.set_defaults(func=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
