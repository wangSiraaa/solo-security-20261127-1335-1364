"""命令行入口：本地重放迁移台。

用法：
  python -m custody.cli demo [--journal out/journal.json]   跑完整演练并落事件日志
  python -m custody.cli replay --journal out/journal.json   仅从日志重放并比对
  python -m custody.cli guards                               签章绑定/防篡改隔离检查
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .hashing import canon
from .migration.scenarios import (STATION_KEY, build_contracts, build_handovers,
                                  build_orders, extra_guards, run_demo)
from .migration.station import Station


def _factories():
    contracts, orders = build_contracts(), build_orders()

    def order_factory(order_id: str):
        return orders[order_id]

    def contract_factory(payload: dict):
        return contracts[(payload["contract_id"], payload["version"])]

    return order_factory, contract_factory


def cmd_demo(args) -> int:
    station = run_demo()
    if args.journal:
        os.makedirs(os.path.dirname(args.journal) or ".", exist_ok=True)
        station.save_journal(args.journal)
        print(f"\n事件日志已写入 {args.journal}")
    guards = extra_guards()
    assert all(guards.values()), "隔离检查未通过"
    print(f"隔离检查通过：跨单复用={guards['cross_order_blocked']}，"
          f"篡改拦截={guards['tamper_blocked']}")
    if args.journal:
        print(f"根哈希 {station.root_hash} 可用 `replay` 子命令复核")
    return 0


def cmd_replay(args) -> int:
    order_factory, contract_factory = _factories()
    replayed = Station.replay(args.journal, order_factory, contract_factory)
    # 与现场重跑同一故事线做双重比对
    live = run_demo(verbose=False)
    ok_hash = replayed.root_hash == live.root_hash
    ok_report = canon(replayed.report()) == canon(live.report())
    print(f"重放根哈希一致 : {ok_hash}  ({replayed.root_hash})")
    print(f"重放完整报告一致: {ok_report}")
    if not (ok_hash and ok_report):
        print("重放结果与现场运行不一致！", file=sys.stderr)
        return 1
    print("\n各库执行审计（重放结果）：")
    print(json.dumps(replayed.audit_stores(), ensure_ascii=False, indent=2))
    return 0


def cmd_guards(args) -> int:
    result = extra_guards()
    return 0 if all(result.values()) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="资料保管单迁移台（本地可重放）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_demo = sub.add_parser("demo", help="运行完整迁移演练")
    p_demo.add_argument("--journal", default="out/journal.json")
    p_demo.set_defaults(func=cmd_demo)

    p_replay = sub.add_parser("replay", help="从事件日志重放并与现场运行比对")
    p_replay.add_argument("--journal", default="out/journal.json")
    p_replay.set_defaults(func=cmd_replay)

    p_guards = sub.add_parser("guards", help="签章绑定与防篡改隔离检查")
    p_guards.set_defaults(func=cmd_guards)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
