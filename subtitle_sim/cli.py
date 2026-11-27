"""Command line interface for the subtitle simulcast exercise."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import engine
from .common import DIRECT_PROFILE, RELAY_PROFILE, SimError, Store
from .viewer import Viewer


def default_root() -> str:
    return os.environ.get("SIM_ROOT", "runtime/default")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="subtitle-sim", description="旧字幕席保密联播本地模拟")
    sub = parser.add_subparsers(dest="command", required=True)

    root_parent = argparse.ArgumentParser(add_help=False)
    # SUPPRESS lets --root appear either before or after nested actions without
    # an inner parser default overwriting an outer parser value.
    root_parent.add_argument("--root", default=argparse.SUPPRESS, help="本地状态根目录")

    p = sub.add_parser("init", parents=[root_parent], help="初始化小文件、钥匙、能力声明和同意记录")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("login", parents=[root_parent])
    p.add_argument("--seat", required=True)
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("issue-consent", parents=[root_parent])
    p.add_argument("--profile", choices=[DIRECT_PROFILE, RELAY_PROFILE], required=True)
    p.add_argument("--seats", required=True, help="逗号分隔，例如 new-1,old-2")
    p.add_argument("--consent-id")
    p.set_defaults(func=cmd_issue_consent)

    p = sub.add_parser("negotiate", parents=[root_parent])
    p.add_argument("--consent", required=True, help="可信通道交来的同意记录 JSON")
    p.set_defaults(func=cmd_negotiate)

    p = sub.add_parser("set-scene", parents=[root_parent], help="把某一端的能力声明改成另一场次")
    p.add_argument("--seat", required=True)
    p.add_argument("--scene", required=True)
    p.set_defaults(func=cmd_set_scene)

    p = sub.add_parser("suspend-session", parents=[root_parent])
    p.add_argument("--reason", required=True)
    p.add_argument("--replacement-seat", default="")
    p.set_defaults(func=cmd_suspend)

    p = sub.add_parser("send", parents=[root_parent], help="导播送字幕/改词")
    p.add_argument("--sequence", required=True)
    p.add_argument("--version", type=int, default=1)
    body = p.add_mutually_exclusive_group(required=True)
    body.add_argument("--text")
    body.add_argument("--body-file")
    note = p.add_mutually_exclusive_group()
    note.add_argument("--note")
    note.add_argument("--note-file")
    p.add_argument("--kind", choices=["ordinary-subtitle", "director-revision"], default=None)
    p.add_argument("--author", default=engine.DIRECTOR)
    p.set_defaults(func=cmd_send)

    for cmd, seat_kind, default_seat in (
        ("new-viewer", "new", "new-1"),
        ("old-viewer", "old", "old-1"),
    ):
        p = sub.add_parser(cmd, parents=[root_parent], help=f"{seat_kind} 席本地收看器")
        p.add_argument("--seat", default=default_seat)
        p.set_defaults(viewer_expected_kind=seat_kind)
        viewer_sub = p.add_subparsers(dest="viewer_action", required=True)
        lp = viewer_sub.add_parser("list", parents=[root_parent])
        lp.set_defaults(func=cmd_viewer_list)
        vp = viewer_sub.add_parser("view", parents=[root_parent])
        vp.add_argument("message")
        vp.set_defaults(func=cmd_viewer_view)
        rp = viewer_sub.add_parser("receipt", parents=[root_parent])
        rp.add_argument("message")
        rp.set_defaults(func=cmd_viewer_receipt)
        sp = viewer_sub.add_parser("state", parents=[root_parent])
        sp.set_defaults(func=cmd_viewer_state)
        wp = viewer_sub.add_parser("watch", parents=[root_parent], help="轮询本地信箱；--once 只扫一次")
        wp.add_argument("--once", action="store_true")
        wp.add_argument("--interval", type=float, default=1.0)
        wp.set_defaults(func=cmd_viewer_watch)

    p = sub.add_parser("turntable", parents=[root_parent], help="受托转台")
    tt_sub = p.add_subparsers(dest="turntable_action", required=True)
    pp = tt_sub.add_parser("process-job", parents=[root_parent])
    pp.add_argument("job")
    pp.set_defaults(func=cmd_turntable_process)
    ap = tt_sub.add_parser("audit", parents=[root_parent])
    ap.add_argument("--tail", type=int, default=20)
    ap.set_defaults(func=cmd_turntable_audit)
    cp = tt_sub.add_parser("show-cert", parents=[root_parent])
    cp.add_argument("cert")
    cp.set_defaults(func=cmd_turntable_show_cert)

    p = sub.add_parser("confirm-same-text", parents=[root_parent])
    p.add_argument("--sequence", required=True)
    p.add_argument("--version", type=int, required=True)
    p.set_defaults(func=cmd_confirm_same)

    p = sub.add_parser("confirm-relay", parents=[root_parent])
    p.add_argument("--sequence", required=True)
    p.add_argument("--version", type=int, required=True)
    p.set_defaults(func=cmd_confirm_relay)

    p = sub.add_parser("verify-proof", parents=[root_parent])
    p.add_argument("proof")
    p.add_argument("--keyring", help="可选：第三方持有的可信公钥 JSON；默认用 --root/trusted/keyring.json")
    p.set_defaults(func=cmd_verify_proof)

    p = sub.add_parser("status", parents=[root_parent])
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("accessctl", parents=[root_parent], help="供 simulcast-access 包装脚本使用")
    ac_sub = p.add_subparsers(dest="access_action", required=True)
    for name, func in (
        ("init", cmd_init),
        ("login", cmd_login),
        ("negotiate", cmd_negotiate),
        ("send", cmd_send),
        ("confirm-same-text", cmd_confirm_same),
        ("confirm-relay", cmd_confirm_relay),
        ("verify-proof", cmd_verify_proof),
        ("status", cmd_status),
        ("suspend-session", cmd_suspend),
        ("issue-consent", cmd_issue_consent),
        ("set-scene", cmd_set_scene),
    ):
        ap = ac_sub.add_parser(name, parents=[root_parent])
        _copy_leaf_args(name, ap)
        ap.set_defaults(func=func)

    return parser


def _copy_leaf_args(name: str, parser: argparse.ArgumentParser) -> None:
    # The wrapper-facing accessctl leaves intentionally expose the common args.
    if name == "init":
        parser.add_argument("--force", action="store_true")
    elif name == "login":
        parser.add_argument("--seat", required=True)
    elif name == "negotiate":
        parser.add_argument("--consent", required=True)
    elif name == "issue-consent":
        parser.add_argument("--profile", required=True)
        parser.add_argument("--seats", required=True)
        parser.add_argument("--consent-id")
    elif name == "set-scene":
        parser.add_argument("--seat", required=True)
        parser.add_argument("--scene", required=True)
    elif name == "suspend-session":
        parser.add_argument("--reason", required=True)
        parser.add_argument("--replacement-seat", default="")
    elif name == "send":
        parser.add_argument("--sequence", required=True)
        parser.add_argument("--version", type=int, default=1)
        g = parser.add_mutually_exclusive_group(required=True)
        g.add_argument("--text")
        g.add_argument("--body-file")
        n = parser.add_mutually_exclusive_group()
        n.add_argument("--note")
        n.add_argument("--note-file")
        parser.add_argument("--kind", choices=["ordinary-subtitle", "director-revision"], default=None)
        parser.add_argument("--author", default=engine.DIRECTOR)
    elif name in ("confirm-same-text", "confirm-relay"):
        parser.add_argument("--sequence", required=True)
        parser.add_argument("--version", type=int, required=True)
    elif name == "verify-proof":
        parser.add_argument("proof")
        parser.add_argument("--keyring", help="第三方持有的可信公钥 JSON")


def store_from_args(args: argparse.Namespace) -> Store:
    return Store(args.root)


def make_viewer(args: argparse.Namespace) -> Viewer:
    viewer = Viewer(store_from_args(args), args.seat)
    expected_kind = getattr(args, "viewer_expected_kind", None)
    if expected_kind and viewer.kind != expected_kind:
        raise SimError(
            f"{expected_kind} 席收看器不能装入 {viewer.kind} 型能力声明 ({args.seat})"
        )
    return viewer


def _read_text_arg_or_file(text: Optional[str], file: Optional[str]) -> Optional[str]:
    if file:
        return Path(file).read_text(encoding="utf-8").rstrip("\r\n")
    if text is not None:
        return text.rstrip("\r\n")
    return None


def _print_json(doc: Any) -> None:
    print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_init(args: argparse.Namespace) -> int:
    store = engine.init_store(args.root, force=args.force)
    print(f"已初始化本地联播模拟根目录: {store.root}")
    print(f"场次: {engine.SCENE}（另一场次 B-209 用于能力声明变更演练）")
    print("可信能力声明/同意记录目录: " + str(store.trusted_dir))
    print("方言读音表仅存于: " + str(store.turntable_private_dir))
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    doc = engine.login(store_from_args(args), args.seat)
    print(f"登录成功: {args.seat} / scene={doc['scene_id']} / {doc['login']}")
    print("注意: 这不能代替 negotiate 核对本期会话约定。")
    return 0


def cmd_issue_consent(args: argparse.Namespace) -> int:
    store = store_from_args(args)
    seats = [s.strip() for s in args.seats.split(",") if s.strip()]
    doc = engine.issue_consent(store, args.profile, seats, args.consent_id)
    path = store.consents_dir / f"{doc['consent_id']}.consent.json"
    print(f"场记同意记录已经模拟可信通道交付: {path}")
    print(f"约定: {doc['profile']} — {doc['profile_label']}")
    return 0


def cmd_negotiate(args: argparse.Namespace) -> int:
    store = store_from_args(args)
    session = engine.negotiate(store, args.consent)
    print("本期会话约定已核对并建立:")
    print(f"  session : {session['session_id']} (generation {session['generation']})")
    print(f"  scene   : {session['scene_id']}")
    print(f"  profile : {session['profile']}")
    print(f"  meaning : {session['profile_label']}")
    print("  roster  : " + ", ".join(f"{m['seat_id']}({m['kind']})" for m in session["roster"]))
    print(f"  endpoint acceptances: {len(session.get('endpoint_acceptances', []))} 份端点签名接受书")
    if "relay" in session:
        print("  relay   : " + json.dumps(session["relay"], ensure_ascii=False))
    return 0


def cmd_set_scene(args: argparse.Namespace) -> int:
    doc = engine.set_capability_scene(store_from_args(args), args.seat, args.scene)
    print(f"能力声明已通过模拟可信通道更换: {args.seat} -> scene {args.scene}")
    print(f"新摘要: {engine.doc_hash(doc)}")
    print("旧会话固定的是原能力声明摘要；继续发送/确认会被拒绝，必须先重建会话。")
    return 0


def cmd_suspend(args: argparse.Namespace) -> int:
    doc = engine.suspend_active_session(store_from_args(args), args.reason, args.replacement_seat)
    print("旧会话已停用，旧约定不得无声沿用:")
    print(f"  session: {doc['session_id']}")
    print(f"  reason : {doc['reason']}")
    if args.replacement_seat:
        print(f"  replacement: {args.replacement_seat}; 须重新取得同意并 negotiate")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    store = store_from_args(args)
    body = _read_text_arg_or_file(args.text, args.body_file)
    note = _read_text_arg_or_file(args.note, args.note_file) if hasattr(args, "note") else None
    meta = engine.send_message(
        store,
        sequence=args.sequence,
        version=args.version,
        body=body or "",
        note=note,
        kind=args.kind,
        author=args.author,
        body_source=args.body_file,
    )
    print(f"导播稿件已送入联播接入程序: {meta['message_id']} ({meta['kind']})")
    print(f"  body_sha256: {meta['body_sha256']}")
    if meta.get("note_sha256"):
        print(f"  note_sha256: {meta['note_sha256']}")
    print("  routing: " + json.dumps(meta["routing"], ensure_ascii=False))
    return 0


def cmd_viewer_list(args: argparse.Namespace) -> int:
    viewer = make_viewer(args)
    files = viewer.list_messages()
    if not files:
        print(f"{args.seat}: 信箱为空")
        return 0
    for p in files:
        print(p.name)
    return 0


def cmd_viewer_view(args: argparse.Namespace) -> int:
    viewer = make_viewer(args)
    result = viewer.view(args.message)
    print(result["screen"])
    print(f"[回单已签名保存] {result['receipt_path']}")
    return 0


def cmd_viewer_receipt(args: argparse.Namespace) -> int:
    store = store_from_args(args)
    viewer = make_viewer(args)
    message_id = args.message if args.message.startswith("MSG-") else None
    if message_id is None:
        p = viewer._resolve_message_file(args.message)
        message_id = p.name.replace(".msg", "")
    path = store.seat_receipts(args.seat) / f"{message_id}.receipt.json"
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_viewer_state(args: argparse.Namespace) -> int:
    viewer = make_viewer(args)
    _print_json(viewer.latest_state())
    return 0


def cmd_viewer_watch(args: argparse.Namespace) -> int:
    viewer = make_viewer(args)

    def poll_once() -> None:
        for path in viewer.list_messages():
            message_id = path.name[: -len(".msg")]
            receipt_path = viewer.store.seat_receipts(viewer.seat_id) / f"{message_id}.receipt.json"
            if receipt_path.exists():
                continue
            result = viewer.view(message_id)
            print(result["screen"])
            print(f"[自动收看并签名回单] {result['receipt_path']}", flush=True)

    poll_once()
    if args.once:
        return 0
    while True:
        time.sleep(args.interval)
        poll_once()


def cmd_turntable_process(args: argparse.Namespace) -> int:
    cert = engine.process_turntable_job(store_from_args(args), args.job)
    print(f"转台已终结第一段通道并生成旧席播稿: {cert['cert_id']}")
    print(f"  old_script_sha256: {cert['old_script_sha256']}")
    print(f"  逐段记录数: {len(cert['segments'])}")
    print("  证明只能用转台私钥验证；可用 turntable audit / verify-proof 查。")
    return 0


def cmd_turntable_audit(args: argparse.Namespace) -> int:
    store = store_from_args(args)
    path = store.audit_dir / "audit.jsonl"
    if not path.exists():
        print("（转台审计日志为空）")
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()[-args.tail :]
    for line in lines:
        entry = json.loads(line)
        if entry.get("actor") == "turntable" or entry.get("action", "").startswith("relay"):
            print(json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_turntable_show_cert(args: argparse.Namespace) -> int:
    cert, path = engine.load_conversion_cert(store_from_args(args), args.cert)
    print(f"转台证明有效: {path}")
    print(f"cert={cert['cert_id']} session={cert['session_id']} table={cert['dialect_table_id']}")
    print(f"turntable saw duty-only section: {cert['duty_only_section_seen']}")
    print("逐段转换记录（即转台本次实际打开过的内容）:")
    for seg in cert["segments"]:
        print(f"  [{seg['index']}] {seg['kind']}: {seg['input_text']!r} -> {seg['output_text']!r}")
    return 0


def cmd_confirm_same(args: argparse.Namespace) -> int:
    store = store_from_args(args)
    proof = engine.confirm_same_text(store, args.sequence, args.version)
    path = proof["_proof_path"]
    print("同文确认通过，联播回单/证明已保存:")
    print(f"  {path}")
    print(f"  body_sha256={proof['body_sha256']}")
    print(f"  {proof['claim']}")
    return 0


def cmd_confirm_relay(args: argparse.Namespace) -> int:
    store = store_from_args(args)
    proof = engine.confirm_relay_handover(store, args.sequence, args.version)
    print("转台两段通道交接核对通过:")
    print(f"  {proof['_proof_path']}")
    print(f"  {proof['claim']}")
    return 0


def cmd_verify_proof(args: argparse.Namespace) -> int:
    keyring = None
    if getattr(args, "keyring", None):
        keyring = json.loads(Path(args.keyring).read_text(encoding="utf-8"))
    result = engine.verify_saved_proof(store_from_args(args), args.proof, keyring)
    for check in result["checks"]:
        print(f"PASS {check['name']} {check.get('detail', '')}")
    print("PROOF_VALID")
    print(result["conclusion"])
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store = store_from_args(args)
    pointer = store.active_pointer()
    if not pointer:
        print("active_session: none (登录不等于已核对约定)")
    else:
        print(json.dumps(pointer, ensure_ascii=False, indent=2, sort_keys=True))
        if pointer.get("convention_valid"):
            session = store.load_session(pointer["active_session_id"])
            print(f"profile: {session['profile']} — {session['profile_label']}")
    for seat in ("new-1", "old-1", "old-2"):
        state_path = store.seat_state(seat) / "viewed.json"
        if state_path.exists():
            state = store.read_json(state_path)
            print(f"\n[{seat}] latest viewed:")
            print(json.dumps(state.get("latest_by_sequence", {}), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "root"):
        args.root = default_root()
    try:
        return int(args.func(args) or 0)
    except SimError as exc:
        print(f"拒绝: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"缺少文件: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - demo CLI should report cleanly
        print(f"内部错误: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def access_main() -> int:
    # simulcast-access wrapper: prepend accessctl.
    return main(["accessctl"] + sys.argv[1:])


def new_viewer_main() -> int:
    return main(["new-viewer"] + sys.argv[1:])


def old_viewer_main() -> int:
    args = sys.argv[1:]
    if "--seat" not in args:
        args = ["--seat", "old-1"] + args
    return main(["old-viewer"] + args)


if __name__ == "__main__":
    raise SystemExit(main())
