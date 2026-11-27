"""普通入口：值班员命令行。

示例：
  python -m archive_coop.cli --root ./data seal V1 --pages 1=第一页 2=第二页 --policy SITE_RECOVERY
  python -m archive_coop.cli --root ./data guarantee V1 --sign
  python -m archive_coop.cli --root ./data status
  python -m archive_coop.cli --root ./data handover ENV_xxx --from 东甲 --to 东乙
  python -m archive_coop.cli --root ./data receive RCP_xxx --as 东乙
  python -m archive_coop.cli --root ./data recover V1 --custodians 东甲 西甲
  python -m archive_coop.cli --root ./data order ORD_xxxx
  python -m archive_coop.cli --root ./data demo        # 全流程演练（含全部规定动作）
"""

import argparse
import json
import sys

from .cabinet import IssuingCabinet
from .policy import POLICIES, build_guarantee
from .storage import Storage
from .workbench import RecoveryError, Workbench


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _get(storage: Storage):
    cabinet = IssuingCabinet(storage)
    bench = Workbench(storage, cabinet)
    return cabinet, bench


def cmd_seal(args):
    storage = Storage(args.root)
    cabinet, _ = _get(storage)
    pages = []
    for item in args.pages:
        no, _, text = item.partition("=")
        pages.append((int(no), text))
    if args.policy not in POLICIES:
        raise SystemExit(f"未知配置，可选：{list(POLICIES)}")
    cabinet.seal_volume(args.volume, pages, args.policy)
    guarantee = build_guarantee(args.volume, args.policy, len(pages))
    print("=== 保障单（封卷前递给档主，签收或退件）===")
    print(f"卷号：{guarantee['volume_id']}    配置：{guarantee['title']}")
    for line in guarantee["lines"]:
        print(f"  - {line}")
    print("保障单状态：issued（待档主决定）。用 guarantee --sign 签收，或 guarantee --reject 退件。")


def cmd_guarantee(args):
    storage = Storage(args.root)
    cabinet, _ = _get(storage)
    ledger = storage.load_ledger()
    if args.volume not in ledger["guarantees"]:
        # 允许对已封卷补建保障单
        vol = ledger["volumes"][args.volume]
        guarantee = build_guarantee(args.volume, vol["policy"], len(vol["page_numbers"]))
    else:
        guarantee = ledger["guarantees"][args.volume]
    if args.sign:
        decision = "signed"
    elif args.reject:
        decision = "rejected"
    else:
        _print(guarantee)
        return
    _print(cabinet.record_guarantee_decision(guarantee, decision))


def cmd_status(args):
    storage = Storage(args.root)
    ledger = storage.load_ledger()
    out = {"volumes": {}, "custodians": {}, "pending_handover": ledger["pending_handover"]}
    for name, info in ledger["custodians"].items():
        envelopes = []
        for env_id in info["envelopes"]:
            env = storage.read_envelope(env_id)
            envelopes.append(
                {
                    "envelope_id": env["envelope_id"],
                    "volume_id": env["volume_id"],
                    "index": env["index"],
                    "generation": env["generation"],
                    "status": env["status"],
                }
            )
        out["custodians"][name] = {"site": info["site"], "envelopes": envelopes}
    for vid, vol in ledger["volumes"].items():
        guarantee = ledger["guarantees"].get(vid)
        out["volumes"][vid] = {
            "policy": vol["policy"],
            "generation": vol["generation"],
            "pages": vol["page_numbers"],
            "guarantee": guarantee["status"] if guarantee else None,
        }
    _print(out)


def cmd_handover(args):
    storage = Storage(args.root)
    cabinet, _ = _get(storage)
    _print(cabinet.start_handover(args.envelope, args.frm, args.to))


def cmd_receive(args):
    storage = Storage(args.root)
    cabinet, _ = _get(storage)
    if args.cancel:
        _print(cabinet.cancel_handover(args.receipt))
    else:
        _print(cabinet.sign_handover(args.receipt, args.as_custodian))


def cmd_recover(args):
    storage = Storage(args.root)
    cabinet, bench = _get(storage)
    try:
        order = bench.recover(args.volume, args.custodians)
    except RecoveryError as exc:
        print(f"[恢复被拒] {exc}")
        sys.exit(2)
    _print(order)


def cmd_order(args):
    storage = Storage(args.root)
    _, bench = _get(storage)
    _print(bench.get_recovery_order(args.order_id))


def cmd_demo(args):
    from .demo import run_demo

    run_demo(args.root)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="archive_coop", description="档案合作社：发份柜与恢复工作台")
    p.add_argument("--root", default="./archive_data", help="本地档案库目录")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("seal", help="封卷并发行份额（先出保障单）")
    sp.add_argument("volume")
    sp.add_argument("--pages", nargs="+", required=True, help="形如 1=第一页正文 2=第二页正文")
    sp.add_argument("--policy", required=True, choices=list(POLICIES))
    sp.set_defaults(func=cmd_seal)

    gp = sub.add_parser("guarantee", help="查看/签收/退件保障单")
    gp.add_argument("volume")
    gp.add_argument("--sign", action="store_true")
    gp.add_argument("--reject", action="store_true")
    gp.set_defaults(func=cmd_guarantee)

    sub.add_parser("status", help="查看各柜持有与在途交接").set_defaults(func=cmd_status)

    hp = sub.add_parser("handover", help="发起交接（封套进入在途，出具回单）")
    hp.add_argument("envelope")
    hp.add_argument("--from", dest="frm", required=True)
    hp.add_argument("--to", required=True)
    hp.set_defaults(func=cmd_handover)

    rp = sub.add_parser("receive", help="接收方签收回单，或撤销交接")
    rp.add_argument("receipt")
    rp.add_argument("--as", dest="as_custodian")
    rp.add_argument("--cancel", action="store_true")
    rp.set_defaults(func=cmd_receive)

    cp = sub.add_parser("recover", help="以到场保管人的实有份恢复指定卷")
    cp.add_argument("volume")
    cp.add_argument("--custodians", nargs="+", required=True)
    cp.set_defaults(func=cmd_recover)

    op = sub.add_parser("order", help="凭恢复单号查回交给档主的页面")
    op.add_argument("order_id")
    op.set_defaults(func=cmd_order)

    dp = sub.add_parser("demo", help="运行任务规定的全套演练")
    dp.set_defaults(func=cmd_demo)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
