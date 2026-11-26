"""Command line entry point: serve, replay, demo, smoke and recheck."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .engine import Engine
from .scenario import run_branch, run_empty_walkthrough, run_old_plan
from .server import create_handler, serve
from .store import Store


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _print_json(value: Any) -> None:
    print(_json_dumps(value))


def render_replay(result: dict[str, Any]) -> None:
    titles = {
        "reconnect": "岔站回放：乙柜中途回联",
        "offline": "岔站回放：乙柜一直离线",
    }
    if "case" in result:
        suffix = "（迟到回执反事实）" if result.get("late_ack") else ("（严格执行班初窗口）" if result["case"] in titles else "")
        print(f"\n=== {titles.get(result['case'], '旧站先办双柜再整体接回')}{suffix} ===")
    else:
        print("\n=== 两个空柜：持久停用、成功单、半张回条与重启复查 ===")

    for frame in result["frames"]:
        print(f"\n-- 第{frame['tick']}拍 | {frame['label']} --")
        for note in frame.get("notes", []):
            print(f"   · {note}")
        print("  柜格当前签发资格:")
        for code, auth in frame["authority"].items():
            print(
                f"    {code} {auth['station_label']} / G{auth['generation']} "
                f"[{auth['basis_label']}]"
            )
        if frame.get("orders"):
            print("  单据当刻可签性:")
            for order in frame["orders"]:
                common = order["common_station_label"]
                flags = []
                if order["new_can_complete"]:
                    flags.append("新站可办结")
                elif order["new_can_half"]:
                    flags.append("新站可取半张")
                elif order["new_can_resume"]:
                    flags.append("新站半张待补")
                elif order["new_can_claim"]:
                    flags.append("新站可领")
                if order["old_can_complete"]:
                    flags.append("旧站可办结")
                elif order["old_can_half"]:
                    flags.append("旧站可取半张")
                elif order["old_can_resume"]:
                    flags.append("旧站半张待补")
                elif order["old_can_claim"]:
                    flags.append("旧站可领")
                if not flags:
                    flags.append("当前无可推进站")
                deadline = "期限内" if order["within_deadline"] else "已过期"
                print(
                    f"    {order['order_id']} [{','.join(order['required'])}] "
                    f"{order['status']}/{deadline} 共同站={common or '分裂'} "
                    f"voucher={order['voucher_status'] or '-'} | {'、'.join(flags)}"
                )
        for attempt in frame.get("attempts", []):
            mark = "OK " if attempt["ok"] else "ERR"
            line = f"  动作[{mark}] {attempt['label']}"
            if not attempt["ok"]:
                line += f" -> {attempt['code']}: {attempt['message']}"
            elif attempt.get("brief"):
                brief = attempt["brief"]
                if isinstance(brief, dict) and brief.get("status"):
                    line += f" -> {brief['status']}"
                    if brief.get("new_acceptances"):
                        cabs = ",".join(x["cabinet"] for x in brief["new_acceptances"])
                        line += f", 新回条={cabs}"
                    if brief.get("resigned") is False:
                        line += ", 未重签"
            print(line)
        if frame.get("open_todos"):
            for todo in frame["open_todos"]:
                print(f"  TODO #{todo['id']} {todo.get('order_id')} 缺 {todo.get('cabinet')}: {todo['detail']}")

    if "checks" in result:
        passed = sum(1 for c in result["checks"] if c["ok"])
        print(f"\n断言检查: {passed}/{len(result['checks'])} 通过")
        for check in result["checks"]:
            if not check["ok"]:
                print(f"  FAIL {check['name']}: {check['detail']}")


def _temp_db() -> str:
    fd, path = tempfile.mkstemp(prefix="handback-drill-", suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def cmd_replay(args: argparse.Namespace) -> int:
    db_path = args.db or _temp_db()
    if args.case == "empty":
        result = run_empty_walkthrough(db_path)
    elif args.case == "old-plan":
        result = run_old_plan(db_path)
    else:
        result = run_branch(args.case, db_path, late_ack=args.late_ack)
    if args.json:
        _print_json(result)
    else:
        render_replay(result)
    if args.keep_db:
        print(f"\n持久数据库保留在: {db_path}")
    elif not args.db:
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(db_path + suffix).unlink(missing_ok=True)
            except OSError:
                pass
    return 0 if result.get("ok") else 1


def cmd_demo(args: argparse.Namespace) -> int:
    results = [
        run_empty_walkthrough(args.db or _temp_db()),
        run_branch("reconnect", _temp_db()),
        run_branch("offline", _temp_db()),
        run_old_plan(_temp_db()),
    ]
    for result in results:
        render_replay(result)
    ok = all(r.get("ok") for r in results)
    print("\n=== 演示结论 ===")
    print("1. 命令送达、镜像自称签发站，都不产生授权；只认持久停用回执或可信租约到期。")
    print("2. 甲柜回执只解决甲柜；两柜联签必须同一站在当刻有效代次取得两柜回条。")
    print("3. 半张回条和待办持久化；重启/恢复镜像后幂等重试，不重签已取得的回条。")
    print("4. 乙中途回联与一直离线的当刻差异已在上方逐拍列出。")
    print(f"\n总体结果: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def cmd_recheck(args: argparse.Namespace) -> int:
    store = Store(args.db)
    engine = Engine(store)
    result = {
        "ok": True,
        "db": str(Path(args.db).resolve()),
        "tick": engine.tick,
        "authority": engine.authority_snapshot()["authority"],
        "orders": [
            {
                "id": o["id"],
                "status": o["status"],
                "assigned_station": o["assigned_station"],
                "voucher": o["voucher"]["status"] if o["voucher"] else None,
            }
            for o in engine.list_orders()
        ],
        "open_todos": engine.todos(True),
    }
    _print_json(result)
    store.close()
    return 0


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def cmd_smoke(args: argparse.Namespace) -> int:
    """Start a real HTTP server on an ephemeral port and exercise a few calls."""

    db_path = args.db or _temp_db()
    store = Store(db_path)
    engine = Engine(store)
    engine.reset("empty")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(engine))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    failures = []

    def expect(name: str, condition: bool, detail: Any = ""):
        print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail if not condition else ''}")
        if not condition:
            failures.append(name)

    try:
        r = _http_json("POST", f"{base}/api/reset", {"scenario": "empty"})
        expect("reset", r.get("ok") is True)

        r = _http_json("POST", f"{base}/api/switch/A/deliver", {})
        auth = r["authority"]["A"]
        expect("delivered-but-no-receipt-keeps-old", auth["station"] == "old-station", auth)

        # Negative HTTP request: NEW cannot issue before durable receipt.
        _http_json("POST", f"{base}/api/orders", {
            "id": "O-SMOKE",
            "cabinets": ["A"],
            "delivery_site": "new-site",
            "deadline_tick": 1,
        })
        denied = _http_json("POST", f"{base}/api/orders/O-SMOKE/issue", {"station": "new-station"})
        expect("http-new-denied", denied.get("ok") is False and denied.get("error") == "station_not_authorized", denied)

        r = _http_json("POST", f"{base}/api/switch/A/succeed", {})
        expect("durable-switch-to-new", r["authority"]["A"]["station"] == "new-station")

        r = _http_json("POST", f"{base}/api/orders/O-SMOKE/claim", {"station": "new-station"})
        expect("claim-by-new", r.get("ok") is True and r["order"]["assigned_station"] == "new-station")

        r = _http_json("POST", f"{base}/api/orders/O-SMOKE/issue", {})
        expect("issue-completed", r.get("ok") is True and r["voucher"]["status"] == "completed")

        r = _http_json("POST", f"{base}/api/admin/restart", {})
        order = next(o for o in r["orders"] if o["id"] == "O-SMOKE")
        expect("restart-review-keeps-order", order["status"] == "completed")

        r = _http_json("POST", f"{base}/api/orders/O-SMOKE/issue", {})
        expect("idempotent-no-resign", r.get("already_completed") is True and r.get("resigned") is False)

        plan = _http_json("GET", f"{base}/api/plan")
        expect("plan-available", plan.get("ok") is True and "boundaries" in plan)
    finally:
        httpd.shutdown()
        httpd.server_close()
        store.close()
        if not args.db:
            for suffix in ("", "-wal", "-shm"):
                Path(db_path + suffix).unlink(missing_ok=True)

    print(f"\nHTTP smoke: {9 - len(failures)}/9 passed")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="drill", description="档案馆两站签发/交回本地演练后台")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="启动 JSON HTTP 后台")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.add_argument("--db", default="handback-drill.db")
    p_serve.set_defaults(func=lambda a: serve(a.db, a.host, a.port))

    p_replay = sub.add_parser("replay", help="运行内置剧本")
    p_replay.add_argument("case", choices=["reconnect", "offline", "empty", "old-plan"])
    p_replay.add_argument("--db", help="持久 SQLite 路径；默认临时库")
    p_replay.add_argument("--json", action="store_true", help="输出完整 JSON")
    p_replay.add_argument("--keep-db", action="store_true")
    p_replay.add_argument("--late-ack", action="store_true", help="允许班初已送达命令在回联时补回执的反事实")
    p_replay.set_defaults(func=cmd_replay)

    p_demo = sub.add_parser("demo", help="运行全部走查和两种岔站回放")
    p_demo.add_argument("--db", help="可选：空柜走查保留的数据库路径")
    p_demo.set_defaults(func=cmd_demo)

    p_smoke = sub.add_parser("smoke", help="通过真实 HTTP 请求执行少量调用并重启复查")
    p_smoke.add_argument("--db", help="可选：保留 smoke 数据库")
    p_smoke.set_defaults(func=cmd_smoke)

    p_recheck = sub.add_parser("recheck", help="读取持久库并打印重启复查结果")
    p_recheck.add_argument("--db", default="handback-drill.db")
    p_recheck.set_defaults(func=cmd_recheck)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
