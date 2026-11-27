"""Command line entry: run a local demonstration or start the web UI."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .scenario import build_full_correction_demo, happy_path, incident_setup
from .service import CouponError, CouponService
from .store import Store


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False))


def run_demo(db_path: str, *, reset: bool = True) -> int:
    if db_path != ":memory:" and reset and Path(db_path).exists():
        Path(db_path).unlink()
    store = Store(db_path)
    service = CouponService(store=store)
    try:
        if reset and db_path != ":memory:":
            service.reset_data()
        result = build_full_correction_demo(service)
        state = service.get_state()

        print("=" * 78)
        print("福利社制券/库房/兑换本地演练")
        print("=" * 78)
        print("\n[1] 干净路径：独立来源 + 封条 + 柜台签收 + 顾客兑换")
        hp = result["happy_path"]
        print(f"  来料批次: {hp['material_lot_id']}")
        print(f"  独立来源: {hp['source_id']} / 段 {hp['segment_id']}")
        print(f"  封包/封条: {hp['stack_id']} / SEAL-HAPPY")
        print(f"  柜台回执: {hp['receipt_id']}")
        print(f"  兑换结果: {hp['redemption']['redemption_id']} - {hp['redemption']['message']}")
        print("  演示暗码（只在印制清单显示一次）:")
        for item in hp["print_manifest_once"]:
            print(f"    {item['ticket_no']}: {item['secret']}")

        inc = result["incident"]
        print("\n[2] 甲乙两机恢复同一备份")
        print("  甲机实名预订叠 STACK-A-RESERVED：印到一半、旧交接回执未全")
        print("  旧柜复核封条与甲机专属连续见证后，原批次可证")
        print("  后续旧交接单齐全 -> 已领取券补正持有事实，未领取券补齐交割")
        print(f"  A-RES-001 先兑现: {inc['a1_redemption']['redemption_id']}")

        print("\n[3] 后续发现乙机已从同一旧段印过 B-EVT-101..103")
        holds = inc["finding"].get("corrections", {})
        print(f"  已兑现、不得重发: {holds.get('redeemed_no_reissue', [])}")
        print(f"  已领取、冻结核查: {holds.get('claimed_frozen', [])}")
        print(f"  未领取、立即隔离: {holds.get('unclaimed_isolated', [])}")
        print("  不同公开票号不产生第二个独立兑奖权；暗码/哈希不进入公开状态。")

        print("\n[4] 恢复独立来源并更正柜台回执")
        print(f"  新来源: {inc['new_source_id']}")
        print(f"  回执 {inc['a_receipt_id']} 已改为 STACK-B-NEW")
        print("  STACK-A-RESERVED 仍可查验，但不再是 COUNTER-2 当前回执所领走的叠")
        print(f"  新叠兑换: {inc['n1_redemption']['redemption_id']}")

        print("\n[5] 服务当前决定")
        d = state["decision"]
        print(f"  优先事项: {d['priority']}")
        print(f"  第一站: {d['first_stop']}")
        print(f"  可交割叠: {', '.join(d['sendable_stack_ids']) or '(无)'}")
        print(f"  建议: {d['recommendation']}")
        print("\n本地网页入口: python run.py serve --db data/demo.db")
        print(f"数据已持久化: {db_path}")
        return 0
    finally:
        service.close()


def run_serve(db_path: str, host: str, port: int) -> int:
    from .web import create_server

    service = CouponService(db_path)
    httpd = create_server(service, host=host, port=port)
    print(f"Coupon demo listening on http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        httpd.server_close()
        service.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coupon-demo")
    sub = parser.add_subparsers(dest="command")

    demo = sub.add_parser("demo", help="run terminal demonstration")
    demo.add_argument("--db", default="data/demo.db")
    demo.add_argument("--no-reset", action="store_true")

    serve = sub.add_parser("serve", help="start local web UI/API")
    serve.add_argument("--db", default="data/demo.db")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        return run_demo(args.db, reset=not args.no_reset)
    if args.command == "serve":
        return run_serve(args.db, args.host, args.port)

    # Default local entry point starts the web UI on 127.0.0.1:8000.
    return run_serve("data/demo.db", "127.0.0.1", 8000)


if __name__ == "__main__":
    raise SystemExit(main())
