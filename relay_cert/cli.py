"""命令行：渲染、认证、选码（方案甲）、见证（方案乙）、计分、端到端演示。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .certify import certify, pair_cameras
from .codebook import design_codebook
from .model import PALETTE
from .pipeline import certify_scene
from .render import render_scene
from .scenarios import build_scene
from .score import decide
from .vision import crop_edge_cells, extract_traces, load_frame
from .witness import apply_witness, choose_section


def _dump(obj, path: str | Path | None) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=lambda o: o.to_dict())
    if path:
        Path(path).write_text(text)
    print(text)


def cmd_render(args: argparse.Namespace) -> None:
    scene = build_scene(args.kind)
    _dump(render_scene(scene, args.outdir), args.json)


def cmd_certify(args: argparse.Namespace) -> None:
    scene = build_scene(args.kind)
    crop = (args.crop_side, args.crop_cells) if args.crop_side else None
    result, notes = certify_scene(scene, args.workdir, crop)
    payload = {"pipeline_notes": notes, "certification": result.to_dict()}
    _dump(payload, args.json)


def cmd_codebook(args: argparse.Namespace) -> None:
    colors = list(PALETTE.keys())[:args.palette]
    plan = design_codebook(colors, args.length, args.need, args.level,
                           spare_per_selected=args.spares)
    _dump(plan.to_dict(), args.json)


def cmd_witness(args: argparse.Namespace) -> None:
    scene = build_scene("ambiguous")
    result, _ = certify_scene(scene, Path(args.workdir) / "witness")
    section = choose_section(result)
    if section is None:
        raise SystemExit("没有可取景且能消歧的截面")
    outcome = None
    if args.observe is not None:
        event_id, cell, color = args.observe.split(":")
        outcome = apply_witness(result, event_id, int(cell), color)
    _dump({"section": section, "outcome": outcome}, args.json)


def cmd_score(args: argparse.Namespace) -> None:
    scene = build_scene(args.kind)
    result, _ = certify_scene(scene, Path(args.workdir) / "score")
    penalties = json.loads(args.penalties)
    roster = decide(result.to_dict(), penalties, args.quota)
    _dump(roster, args.json)


def cmd_demo(args: argparse.Namespace) -> None:
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {}

    # 1) 清晰场：全部组合唯一，名次可逐队署名。
    clean_scene = build_scene("clean")
    clean, clean_notes = certify_scene(clean_scene, out / "clean")
    report["clean"] = {
        "flip_notes": clean_notes["frames"],
        "combinations": len(clean.combinations),
        "certified_rankings": clean.certified_rankings,
    }

    # 2) 争议场：两种完整身份组合，认证台只署共同成立的名次，并给互斥证据。
    amb_scene = build_scene("ambiguous")
    amb, amb_notes = certify_scene(amb_scene, out / "ambiguous")
    report["ambiguous"] = {
        "combinations": len(amb.combinations),
        "disputed_events": amb.disputed,
        "exclusions_on_dispute": [
            {"event": e.event.event_id, "exclusions": e.exclusions}
            for e in amb.events if e.event.event_id in amb.disputed
        ],
        "witness_options": amb.witness_options,
        "certified_rankings": amb.certified_rankings,
    }

    # 3) 方案甲：有限色格 + 临时补杆数，双面可判编码。
    plan = design_codebook(list(PALETTE.keys())[:5], length=5, need=8,
                           level="two_camera", spare_per_selected=1)
    report["plan_A_codebook"] = plan.to_dict()

    # 4) 方案乙：保留旧杆带，选一处真能消歧的有限见证截面。
    section = choose_section(amb)
    report["plan_B_witness"] = section

    # 5) 翻转场：B 回读方向翻转，只影响一对擦肩杆；标定条检出并镜像还原。
    flip_scene = build_scene("flip")
    flip, flip_notes = certify_scene(flip_scene, out / "flip")
    report["flip_recovery"] = {
        "camera_notes": flip_notes["frames"],
        "combinations_after_restore": len(flip.combinations),
        "disputed_after_restore": flip.disputed,
    }

    # 6) 计分室：罚秒与名额到场才开封；第一名确定、第二名待核仍按名额继续编排。
    penalties = {"甲": 0.0, "乙": 0.0}
    roster = decide(amb.to_dict(), penalties, quota=1)
    report["score_room_first_place_only"] = roster

    # 只改一个接棒罚秒，第一名仍确定，第二名待核的结论应保持。
    penalties_adj = {"甲": 0.0, "乙": 0.5}
    roster_adj = decide(amb.to_dict(), penalties_adj, quota=1)
    report["score_room_one_penalty_changed"] = roster_adj

    # 7) 外部裁判用普通图像程序裁掉边上一格再交新单（右侧裁一格）。
    crop_scene = build_scene("crop")
    crop_result, crop_notes = certify_scene(crop_scene, out / "crop",
                                            crop=("right", 1))
    report["external_crop_resubmit"] = {
        "camera_notes": crop_notes["frames"],
        "combinations": len(crop_result.combinations),
        "disputed_events": crop_result.disputed,
        "certified_rankings": crop_result.certified_rankings,
    }

    text = json.dumps(report, ensure_ascii=False, indent=2,
                      default=lambda o: o.to_dict())
    (out / "demo_report.json").write_text(text)
    print(text)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="接力赛终点认证台（无人体特征）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("render", help="画两面相机窄条时序图")
    sp.add_argument("--kind", choices=["clean", "ambiguous", "flip", "crop"], required=True)
    sp.add_argument("--outdir", required=True)
    sp.add_argument("--json")
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("certify", help="从 PNG 与登记做身份认证")
    sp.add_argument("--kind", choices=["clean", "ambiguous", "flip", "crop"], required=True)
    sp.add_argument("--workdir", required=True)
    sp.add_argument("--crop-side", choices=["left", "right", "top", "bottom"])
    sp.add_argument("--crop-cells", type=int, default=1)
    sp.add_argument("--json")
    sp.set_defaults(func=cmd_certify)

    sp = sub.add_parser("codebook", help="方案甲：选双面可判杆带编码")
    sp.add_argument("--palette", type=int, default=5)
    sp.add_argument("--length", type=int, default=5)
    sp.add_argument("--need", type=int, default=8)
    sp.add_argument("--level", choices=["two_camera", "rotation_mask"],
                    default="two_camera")
    sp.add_argument("--spares", type=int, default=1)
    sp.add_argument("--json")
    sp.set_defaults(func=cmd_codebook)

    sp = sub.add_parser("witness", help="方案乙：选见证截面并可回报见证色")
    sp.add_argument("--workdir", required=True)
    sp.add_argument("--observe", help="形如 E1:3:R，事件:格:见到的颜色")
    sp.add_argument("--json")
    sp.set_defaults(func=cmd_witness)

    sp = sub.add_parser("score", help="计分室开封罚秒与名额后出名单")
    sp.add_argument("--kind", choices=["clean", "ambiguous", "flip", "crop"], required=True)
    sp.add_argument("--workdir", required=True)
    sp.add_argument("--penalties", required=True, help='JSON，如 {"甲":0.0,"乙":0.5}')
    sp.add_argument("--quota", type=int, default=1)
    sp.add_argument("--json")
    sp.set_defaults(func=cmd_score)

    sp = sub.add_parser("demo", help="端到端演示，覆盖翻转、裁边、两套赛前方案")
    sp.add_argument("--outdir", default="demo_out")
    sp.set_defaults(func=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)
