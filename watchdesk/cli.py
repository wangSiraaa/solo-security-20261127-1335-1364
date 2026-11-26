from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from .core import Desk, DeskError, compute_coverage, processed_progress
from .handoff import (
    audit_pairs, dispatch_alert, execute_handoff, plan_handoff,
    revoke_delivery_signoff, sign_delivery, signout_coverage,
)
from .operations import (
    acknowledge, enable_rule, heartbeat, init_desk, load_events,
    register_rule_file, stop_rule, tick,
)
from .reporting import dumps, render_coverage


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="watchdesk", description="监测覆盖交班台：用证据而不是绿灯判断守住的区间")
    p.add_argument("--state", default=os.environ.get("WATCHDESK_STATE", ".watchdesk"), help="台账目录")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="初始化本地虚拟时钟和台账")
    s.add_argument("--start", default="2026-11-26T12:00:00+08:00")
    s.add_argument("--beat-seconds", type=int, default=60)
    s.add_argument("--force", action="store_true")

    s = sub.add_parser("tick", help="推进虚拟时钟拍号")
    s.add_argument("--beats", type=int, default=1)

    s = sub.add_parser("register", help="登记规则文件及 SHA256")
    s.add_argument("file")
    s.add_argument("--version")

    sub.add_parser("rules", help="查询已登记/启用的规则版本和启停回执")

    s = sub.add_parser("enable", help="规则启用回执")
    s.add_argument("rule_id")
    s.add_argument("--version", required=True)
    s.add_argument("--by", required=True)
    s.add_argument("--receipt", required=True)
    s.add_argument("--from-beat", type=int, required=True)
    s.add_argument("--desk")
    s.add_argument("--shift")

    s = sub.add_parser("stop", help="规则停止回执")
    s.add_argument("rule_id")
    s.add_argument("--through-beat", type=int, required=True)
    s.add_argument("--by", required=True)
    s.add_argument("--receipt", required=True)
    s.add_argument("--version")

    s = sub.add_parser("heartbeat", aliases=["hb"], help="登记心跳；心跳不能替代处理回执")
    s.add_argument("--by", required=True)
    s.add_argument("--receipt", required=True)
    s.add_argument("--beat", type=int, required=True)
    s.add_argument("--desk")
    s.add_argument("--shift")

    s = sub.add_parser("load", help="装载 JSON/JSONL 事件小段；设备只允许三类事件")
    s.add_argument("file")

    s = sub.add_parser("ack", help="登记逐拍实际处理回执，并驱动规则上下文")
    s.add_argument("--seq", type=int)
    s.add_argument("--beat", type=int)
    s.add_argument("--through-seq", type=int)
    s.add_argument("--rule", action="append", dest="rule_ids")
    s.add_argument("--by", required=True)
    s.add_argument("--receipt", required=True)
    s.add_argument("--shift")
    s.add_argument("--desk")
    s.add_argument("--note")

    s = sub.add_parser("plan", help="生成交班/携带计划")
    s.add_argument("--at-beat", type=int, required=True)
    s.add_argument("--slots", type=int, default=1)
    s.add_argument("--by", required=True)
    s.add_argument("--from-shift")
    s.add_argument("--from-desk")
    s.add_argument("--to-shift")
    s.add_argument("--to-desk")
    s.add_argument("--to-owner")
    s.add_argument("--emergency", action="store_true")
    s.add_argument("--policy", choices=["risk-first", "coverage-first"], default="risk-first")
    s.add_argument("--select", action="append", default=[])

    s = sub.add_parser("execute", help="执行一次交班携带；未携带上下文自动断链并签出已守前缀")
    s.add_argument("--plan", required=True)
    s.add_argument("--receipt", required=True)
    s.add_argument("--by", required=True)
    s.add_argument("--at-beat", type=int)
    s.add_argument("--carry", action="append", default=[])
    s.add_argument("--to-owner")
    s.add_argument("--to-desk")
    s.add_argument("--to-shift")
    s.add_argument("--no-auto-sign", action="store_true")

    s = sub.add_parser("signout", help="按证据签出合法覆盖区间；缺口拍生成回单")
    s.add_argument("rule_id")
    s.add_argument("--from-beat", type=int, required=True)
    s.add_argument("--to-beat", type=int, required=True)
    s.add_argument("--shift", required=True)
    s.add_argument("--by", required=True)
    s.add_argument("--receipt", required=True)
    s.add_argument("--desk")
    s.add_argument("--full-only", action="store_true")

    s = sub.add_parser("coverage", help="查询逐拍覆盖证据")
    s.add_argument("rule_id", nargs="?")
    s.add_argument("--from-beat", type=int, default=1)
    s.add_argument("--to-beat", type=int, default=3)

    s = sub.add_parser("progress", help="从 append-only ledger 重放/还原处理进度")
    s = sub.add_parser("rebuild", help="重放 ledger 并输出还原后的处理进度")

    s = sub.add_parser("contexts", help="查询未结上下文和携带责任")

    s = sub.add_parser("audit", help="事后核对原始事件对；不补造现场提醒")
    s.add_argument("--rule", default="pair2")
    s.add_argument("--no-save", action="store_true")

    s = sub.add_parser("dispatch", help="记录提醒消息已派发")
    s.add_argument("alert_id")
    s.add_argument("--channel", default="sms-and-board")
    s.add_argument("--by", required=True)
    s.add_argument("--message-id")
    s.add_argument("--beat", type=int)

    s = sub.add_parser("deliver", help="消息接收组正式签收提醒")
    s.add_argument("alert_id")
    s.add_argument("--group", required=True)
    s.add_argument("--received-by", required=True)
    s.add_argument("--receipt", required=True)
    s.add_argument("--channel", default="duty-board")
    s.add_argument("--beat", type=int)

    s = sub.add_parser("revoke-delivery", help="只撤掉接收组的正式签收；命中记录保留")
    s.add_argument("signoff_id")
    s.add_argument("--by", required=True)
    s.add_argument("--reason", required=True)
    s.add_argument("--receipt", required=True)
    s.add_argument("--beat", type=int)

    sub.add_parser("report", help="输出完整中文交班报告")

    s = sub.add_parser("scenario", help="运行内置教学/验收场景")
    s.add_argument("name", choices=[
        "emergency-matrix", "success", "missing-middle", "revoke-delivery", "version-switch", "all"])
    s.add_argument("--root", default="/tmp/watchdesk-scenarios")

    return p


def _print(obj: Any, as_json: bool, text: str | None = None) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))
    elif text is not None:
        print(text)
    else:
        print(dumps(obj, False))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "scenario":
            from .scenarios import run_scenario
            result = run_scenario(args.name, Path(args.root))
            print(result)
            return 0

        desk = Desk(args.state)
        if args.cmd == "init":
            rec = init_desk(args.state, args.start, args.beat_seconds, args.force)
            _print(rec, args.json, f"已初始化值班台：{args.state}；虚拟起点 {args.start}")
            return 0

        if not desk.ledger.exists():
            raise DeskError("值班台尚未初始化，请先运行 init")

        if args.cmd == "tick":
            rec = tick(desk, args.beats)
            _print(rec, args.json, f"虚拟时钟推进到第 {rec['beat']} 拍：{rec['ts']}")
        elif args.cmd == "rules":
            st = desk.state()
            if args.json:
                print(json.dumps({"files": st["rule_files"], "versions": list(st["rule_versions"].values()),
                                  "enablements": st["enablements"]}, ensure_ascii=False, indent=2))
            else:
                for f in st["rule_files"]:
                    print(f"文件 {f['file_id']} 版本 {f['version']} SHA256={f['sha256'][:16]}…")
                for en in st["enablements"]:
                    state = f"至第{en['through_beat']}拍停止({en.get('stop_receipt_id')})" if en.get("through_beat") else "未收停止回执"
                    print(f"启用 {en['rule_id']}@{en['version']} 第{en['from_beat']}拍起，启用回执={en['start_receipt_id']}，{state}")
        elif args.cmd == "register":
            rec = register_rule_file(desk, args.file, args.version)
            _print(rec, args.json, f"已登记规则文件 {rec['data']['file_id']} 版本 {rec['data']['version']}，SHA256={rec['data']['sha256'][:12]}…")
        elif args.cmd == "enable":
            rec = enable_rule(desk, args.rule_id, args.version, args.by, args.receipt,
                              args.from_beat, args.desk, args.shift)
            _print(rec, args.json, f"启用回执 {args.receipt}：{args.rule_id}@{args.version} 从第 {args.from_beat} 拍生效")
        elif args.cmd == "stop":
            rec = stop_rule(desk, args.rule_id, args.through_beat, args.by, args.receipt, args.version)
            _print(rec, args.json, f"停止回执 {args.receipt}：{args.rule_id} 值守至第 {args.through_beat} 拍")
        elif args.cmd in {"heartbeat", "hb"}:
            rec = heartbeat(desk, args.by, args.receipt, args.beat, args.desk, args.shift)
            _print(rec, args.json, f"心跳 {args.receipt} 已登记（第 {args.beat} 拍）；它只是存活证据，不证明处理进度")
        elif args.cmd == "load":
            events = load_events(desk, args.file)
            _print(events, args.json, "装载事件：" + "；".join(f"seq{e['seq']}/拍{e['beat']}/{e['label']}" for e in events))
        elif args.cmd == "ack":
            result = acknowledge(desk, by=args.by, receipt_id=args.receipt, beat=args.beat,
                                 seq=args.seq, through_seq=args.through_seq, rule_ids=args.rule_ids,
                                 shift=args.shift, desk_name=args.desk, note=args.note)
            bits = [f"{x['rule_id']}->{x['status']}" + (f"/{x['alert_id']}" if x.get('alert_id') else "") for x in result["results"]]
            _print(result, args.json, f"处理回执 {args.receipt} 已登记：" + "，".join(bits))
        elif args.cmd == "plan":
            plan = plan_handoff(desk, at_beat=args.at_beat, slots=args.slots, by=args.by,
                                from_shift=args.from_shift, from_desk=args.from_desk,
                                to_shift=args.to_shift, to_desk=args.to_desk, to_owner=args.to_owner,
                                emergency=args.emergency, policy=args.policy, select=args.select)
            _print(plan, args.json, f"{plan['id']}: {plan['headline']}")
        elif args.cmd == "execute":
            exe = execute_handoff(desk, plan_id=args.plan, receipt_id=args.receipt, by=args.by,
                                  at_beat=args.at_beat, carry=args.carry or None, to_owner=args.to_owner,
                                  to_desk=args.to_desk, to_shift=args.to_shift,
                                  auto_sign_prefix=not args.no_auto_sign)
            _print(exe, args.json, f"{exe['id']}: 携带 {exe['carried_context_ids']}；未携带 {exe['uncarried_context_ids']}")
        elif args.cmd == "signout":
            result = signout_coverage(desk, rule_id=args.rule_id, start_beat=args.from_beat,
                                       end_beat=args.to_beat, shift=args.shift, by=args.by,
                                       receipt_id=args.receipt, desk_name=args.desk,
                                       allow_partial=not args.full_only)
            text = f"签出 {result['certificate_ids']}；合法区间 {result['intervals']}；缺口回单 {result['gap_ids']}"
            _print(result, args.json, text)
            if result["gap_beats"] and args.full_only:
                return 2
        elif args.cmd == "coverage":
            st = desk.state()
            if args.rule_id:
                cov = compute_coverage(st, args.rule_id, args.from_beat, args.to_beat)
                _print(cov, args.json, render_coverage(cov))
            else:
                result = {rid: compute_coverage(st, rid, args.from_beat, args.to_beat)
                          for rid in sorted(st["rule_versions"] and {e["rule_id"] for e in st["enablements"]})}
                if args.json:
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print("\n\n".join(render_coverage(c) for c in result.values()))
        elif args.cmd in {"progress", "rebuild"}:
            records = desk.read_records()
            st = desk.state()
            progress = processed_progress(st)
            result = {"ledger_records": len(records), "rebuilt": True, "progress": progress}
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"已从 {len(records)} 条不可变台账记录还原处理进度：")
                for p in progress:
                    print(f"  {p['rule_id']}: 拍{p['acked_beats']}，through_seq={p['last_claimed_through_seq']}，连续高水位拍={p['contiguous_beat_hwm']}")
        elif args.cmd == "contexts":
            st = desk.state()
            ctxs = list(st["contexts"].values())
            _print(ctxs, args.json, "\n".join(
                f"{c['id']} {c['rule_id']} {c['status']} {c.get('custody')} owner={c.get('current_owner')}" for c in ctxs
            ) or "无上下文")
        elif args.cmd == "audit":
            result = audit_pairs(desk, args.rule, save=not args.no_save)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                for f in result["findings"]:
                    print(f"{f['id']}: {f['classification']}；{f}")
                if not result["findings"]:
                    print("未发现原始事件对")
        elif args.cmd == "dispatch":
            rec = dispatch_alert(desk, alert_id=args.alert_id, channel=args.channel, by=args.by,
                                 notification_id=args.message_id, beat=args.beat)
            _print(rec, args.json, f"已派发 {rec['data']['notification_id']}（{args.channel}）")
        elif args.cmd == "deliver":
            so = sign_delivery(desk, alert_id=args.alert_id, group=args.group,
                               received_by=args.received_by, receipt_id=args.receipt,
                               beat=args.beat, channel=args.channel)
            _print(so, args.json, f"正式签收 {so['id']}：接收组 {args.group} / {args.received_by}")
        elif args.cmd == "revoke-delivery":
            result = revoke_delivery_signoff(desk, signoff_id=args.signoff_id, by=args.by,
                                             reason=args.reason, receipt_id=args.receipt, beat=args.beat)
            _print(result, args.json, f"已撤销售收 {args.signoff_id}；命中仍可查，但不得写成已正式通知该组")
        elif args.cmd == "report":
            st = desk.state()
            if args.json:
                print(json.dumps(st, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                from .reporting import render_desk
                print(render_desk(st))
        return 0
    except DeskError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
