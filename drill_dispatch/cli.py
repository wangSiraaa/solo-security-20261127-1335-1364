"""命令行入口：验收员查询、故障调整、跑演练、出结论。"""
from __future__ import annotations

import argparse
import json
import os

from . import inspector
from .drillmaster import DrillMaster
from .router import (
    CRASH_ONCE,
    NORMAL,
    TIMEOUT_ALWAYS,
    TIMEOUT_ONCE,
    Router,
)

FAULTS = {
    "normal": NORMAL,
    "timeout-once": TIMEOUT_ONCE,
    "timeout-always": TIMEOUT_ALWAYS,
    "crash-once": CRASH_ONCE,
}


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="drill-dispatch", description="值班发件安全演练工具"
    )
    parser.add_argument("--root", default=os.environ.get("DD_ROOT", "./runtime"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("cabinets", help="查询正式/演练柜实际办理的动作与计数")

    p_svc = sub.add_parser("service", help="查询单个服务")
    p_svc.add_argument("domain", choices=["prod", "drill"])
    p_svc.add_argument("name")

    p_job = sub.add_parser("job", help="查询单个作业的因果顺序与回条")
    p_job.add_argument("jobs_dir")
    p_job.add_argument("job_id")

    p_chain = sub.add_parser("verify-chain", help="验作业因果链与悬置意图")
    p_chain.add_argument("jobs_dir")
    p_chain.add_argument("job_id")

    p_fault = sub.add_parser("set-fault", help="调整某作业某步的模拟返回")
    p_fault.add_argument("job_id")
    p_fault.add_argument("step", choices=["take", "check", "slip"])
    p_fault.add_argument("mode", choices=list(FAULTS))

    p_clear = sub.add_parser("clear-fault", help="清除故障注入")
    p_clear.add_argument("job_id", nargs="?", default=None)

    sub.add_parser("evidence", help="核验封存证据哈希清单")

    p_count = sub.add_parser("counters", help="正式/演练柜计数对照")
    args = parser.parse_args(argv)

    if args.cmd == "cabinets":
        _print(inspector.query_all_cabinets(args.root))
    elif args.cmd == "service":
        _print(inspector.query_service(args.root, args.domain, args.name))
    elif args.cmd == "job":
        _print(inspector.query_job(args.jobs_dir, args.job_id))
    elif args.cmd == "verify-chain":
        _print(inspector.verify_job_chain(args.jobs_dir, args.job_id))
    elif args.cmd == "set-fault":
        Router(args.root).set_fault(args.job_id, args.step, FAULTS[args.mode])
        _print({"set": args.job_id, "step": args.step, "mode": args.mode})
    elif args.cmd == "clear-fault":
        Router(args.root).clear_faults(args.job_id)
        _print({"cleared": args.job_id or "ALL"})
    elif args.cmd == "evidence":
        problems = DrillMaster(args.root).verify_evidence()
        _print({"evidence_sound": not problems, "problems": problems})
    elif args.cmd == "counters":
        _print(DrillMaster(args.root).counter_snapshot())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
