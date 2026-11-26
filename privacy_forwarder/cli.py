"""
Command-line front-end and scripted demonstration.

Commands (state is persisted to a JSON file between invocations):
  draw  --base L=甲单:#eef ... --window ... --privacy ...
  send  --policy partitioned|atomic [--deliver L=late,R=drop] [--due oid=kind:version]...
  click --x N --y N
  show  [--report|--frame]
  reset
  demo                      run the scripted demonstration
  serve  --port N           local HTTP API (same verbs)

No GUI and no external network are required.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

from .engine import (
    SOURCE_PRIVACY,
    Forwarder,
    Operation,
    Rect,
    check_click,
    default_regions,
    forwarder_from_dict,
    forwarder_to_dict,
    tile_key,
)

DEFAULT_STATE = os.environ.get("PF_STATE", ".privacy_forwarder_state.json")


# --------------------------------------------------------------------------- #
# State I/O
# --------------------------------------------------------------------------- #

def load_state(path: str) -> Forwarder:
    if not os.path.exists(path):
        fw = Forwarder(default_regions(), policy="partitioned")
        fw.composer.set_base("L", "甲单", "#e8eef7")
        fw.composer.set_base("R", "甲单", "#e8eef7")
        return fw
    with open(path, "r", encoding="utf-8") as f:
        return forwarder_from_dict(json.load(f))


def save_state(fw: Forwarder, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(forwarder_to_dict(fw), f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #

def _parse_kv(spec: str) -> tuple:
    if "=" not in spec:
        raise SystemExit(f"expected key=value, got {spec!r}")
    k, v = spec.split("=", 1)
    return k, v


def _parse_base(spec: str):
    # L=甲单:#eef  ->  rid, version, color  (only ':' separates the colour)
    rid, rest = _parse_kv(spec)
    if ":" not in rest:
        raise SystemExit(f"base spec needs rid=version:#rrggbb, got {spec!r}")
    version, color = rest.rsplit(":", 1)
    return rid, version, color


def _parse_rect_layer(spec: str, default_color: Optional[str] = None):
    # wid=x,y,w,h[:#rrggbb][:label]   (colour & label optional)
    k, rest = _parse_kv(spec)
    parts = rest.split(":")
    rect = Rect.from_str(parts[0])
    color = None
    label_parts = []
    if len(parts) > 1 and parts[1].startswith("#"):
        color = parts[1]
        label_parts = parts[2:]
    elif len(parts) > 1:
        label_parts = parts[1:]
    if color is None:
        color = default_color
    if color is None:
        raise SystemExit(f"layer spec needs a colour: {spec!r}")
    label = ":".join(label_parts) if label_parts else None
    return k, rect, color, label


def _parse_ops(specs: List[str]) -> List[Operation]:
    ops = []
    for s in specs:
        # oid=kind:version[:due]
        oid, rest = _parse_kv(s)
        chunks = rest.split(":")
        kind = chunks[0]
        version = chunks[1] if len(chunks) > 1 else "甲单"
        due = int(chunks[2]) if len(chunks) > 2 else None
        ops.append(Operation(oid, kind, version, due))
    return ops


# --------------------------------------------------------------------------- #
# Pretty printing
# --------------------------------------------------------------------------- #

RESET = "\x1b[0m" if sys.stdout.isatty() else ""
DIM = "\x1b[2m" if sys.stdout.isatty() else ""
BOLD = "\x1b[1m" if sys.stdout.isatty() else ""
_INV = "\x1b[37;40m" if sys.stdout.isatty() else ""


def _c(color_hex: str) -> str:
    if not sys.stdout.isatty():
        return ""
    try:
        r = int(color_hex[1:3], 16)
        g = int(color_hex[3:5], 16)
        b = int(color_hex[5:7], 16)
        return f"\x1b[48;2;{r};{g};{b}m"
    except Exception:
        return ""


def render_report(report: Dict, regions: Dict) -> str:
    lines = [f"{BOLD}实际显示视图 @ tick {report['tick']}{RESET}"]
    for rid in sorted(regions):
        region = regions[rid]
        r = report["regions"][rid]
        vis = "可见" if r["fully_visible"] else "不完整"
        lines.append(
            f"  {rid} 可见版本={r['version_seen']!r} 完整={vis} "
            f"遮罩={r['redactions']} 缺块={r['missing']} 旧块={r['stale']}"
        )
        grid = {}
        for t in r["tiles"]:
            ci, cj = t["tile"]
            grid[(ci, cj)] = t
        for cj in range(region.tiles_y):
            row = []
            for ci in range(region.tiles_x):
                t = grid.get((ci, cj), {"status": "missing"})
                if t["status"] == "missing":
                    cell = f"{DIM} ▢ {RESET}"
                elif t.get("source") == SOURCE_PRIVACY:
                    cell = f"{_INV} ▩ {RESET}"
                else:
                    lab = (t.get("label") or "")[:3]
                    bg = _c(t.get("color", "#000000"))
                    cell = f"{bg} {lab:<3}{RESET}"
                if t.get("status") == "stale":
                    cell = f"{DIM}{cell}{RESET}"
                row.append(cell)
            lines.append("    " + "".join(row))
    return "\n".join(lines)


def render_frame(frame: Dict, regions: Dict) -> str:
    lines = [f"{BOLD}发送端本拍合成帧（意图，不用于签收）{RESET}"]
    for rid in sorted(regions):
        region = regions[rid]
        lines.append(f"  {rid}")
        for cj in range(region.tiles_y):
            row = []
            for ci in range(region.tiles_x):
                t = frame[tile_key(rid, ci, cj)]
                if t["source"] == SOURCE_PRIVACY:
                    row.append(f"{_INV} ▩ ")
                else:
                    row.append(f"{_c(t['color'])} {(t.get('label') or '')[:3]:<3}")
            lines.append("    " + "".join(row) + RESET)
    return "\n".join(lines)


def render_decisions(decisions: List[Dict]) -> str:
    if not decisions:
        return "  (无到期操作)"
    marks = {
        "authorized": "✓ 可办理",
        "wait": "… 等齐（下一拍再查）",
        "void": "✗ 作废（旧画面必须丢弃）",
    }
    out = []
    for d in decisions:
        outcome = d.get("outcome")
        if outcome is None:
            outcome = "authorized" if d["authorized"] else "wait"
        mark = marks[outcome]
        seen = d.get("seen_versions", {})
        extra = f"  [L={seen.get('L')!r} R={seen.get('R')!r}]" if seen else ""
        out.append(f"  {mark}  {d['oid']} ({d['kind']}:{d['version']}) "
                   f"理由={d['reason']}{extra}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_draw(args, fw: Forwarder) -> int:
    for spec in args.base or []:
        rid, version, color = _parse_base(spec)
        fw.composer.set_base(rid, version, color)
    for spec in args.window or []:
        wid, rect, color, label = _parse_rect_layer(spec, default_color="#fff3cd")
        fw.composer.set_window(wid, rect, color, label)
    for spec in args.privacy or []:
        pid, rect, color, _ = _parse_rect_layer(spec, default_color="#ff00ff")
        fw.composer.set_privacy(pid, rect, color)
    for pid in args.remove_privacy or []:
        fw.composer.remove_privacy(pid)
    print("已更新绘层。")
    return 0


def cmd_send(args, fw: Forwarder) -> int:
    if args.policy:
        fw.policy = args.policy
    if args.delivery is not None:
        for spec in args.delivery:
            rid, mode = _parse_kv(spec)
            fw.delivery.set(rid, mode)
    ops = _parse_ops(args.due or [])
    result = fw.tick(ops)
    print(render_report(result["report"], fw.regions))
    print()
    inv = result["invalidation"]
    if inv["void_regions"]:
        print(f"  旧画面作废：{inv['void_regions']}（区内自相矛盾，必须丢弃）")
    if not inv["cross_region_same"]:
        seen = ", ".join(f"{r}={v!r}" for r, v in inv["versions"].items())
        print(f"  两区异版（{seen}）：跨区同版操作不可办，异版旧画面须刷新")
    if inv["incomplete_regions"]:
        print(f"  本拍未齐：{inv['incomplete_regions']}（等下一拍）")
    if inv["consistent"] and not inv["incomplete_regions"]:
        print("  实际视图各区域同版齐备。")
    print(render_decisions(result["decisions"]))
    return 0


def cmd_click(args, fw: Forwarder) -> int:
    # click is judged against what the endpoint physically most recently showed
    report = fw.endpoint.report(fw.tick_no)
    res = check_click(args.x, args.y, report, fw.regions)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("accepted") else 2


def cmd_show(args, fw: Forwarder) -> int:
    if args.frame:
        print(render_frame(fw.composer.compose(), fw.regions))
    else:
        print(render_report(fw.endpoint.report(fw.tick_no), fw.regions))
    return 0


def cmd_reset(args, fw: Forwarder) -> int:
    fw2 = Forwarder(default_regions(), policy="partitioned")
    fw2.composer.set_base("L", "甲单", "#e8eef7")
    fw2.composer.set_base("R", "甲单", "#e8eef7")
    save_state(fw2, args.state)
    print("已重置。")
    return 0


# --------------------------------------------------------------------------- #
# Scripted demonstration
# --------------------------------------------------------------------------- #

def _scenario_step(title: str) -> None:
    print()
    print("=" * 76)
    print(f"{BOLD}{title}{RESET}")
    print("=" * 76)


def _preswitch() -> Forwarder:
    """A terminal that has honestly displayed 甲单 / 甲单 at tick 1."""
    fw = Forwarder(default_regions(), policy="partitioned")
    fw.composer.set_base("L", "甲单", "#e8eef7")
    fw.composer.set_base("R", "甲单", "#e8eef7")
    fw.delivery.mode = {"L": "deliver", "R": "deliver"}
    fw.tick([])                      # tick 1: terminal now really shows 甲/甲
    return fw


def _clone(fw: Forwarder) -> Forwarder:
    return forwarder_from_dict(forwarder_to_dict(fw))


def _switch_layers(fw: Forwarder) -> None:
    """L's 送到前台 has switched 甲->乙; a private message covers R's confirm bar.

    R still carries the OLD 甲单 confirmation ("右区旧确认栏").
    """
    fw.composer.set_base("L", "乙单", "#fdecef")
    dest = fw.regions["R"].tile_rect(*fw.regions["R"].dest_tile())
    fw.composer.set_privacy("dm", dest)


def _checks(checks: list) -> int:
    bad = 0
    for label, cond in checks:
        mark = "PASS" if cond else "FAIL"
        if not cond:
            bad += 1
        print(f"    [{mark}] {label}")
    return bad


def _outcome(decisions: list, oid: str) -> str:
    d = next(d for d in decisions if d["oid"] == oid)
    return d.get("outcome", "authorized" if d["authorized"] else "wait")


def _auth(decisions: list, oid: str) -> bool:
    return next(d["authorized"] for d in decisions if d["oid"] == oid)


def _reason(decisions: list, oid: str) -> str:
    return next(d["reason"] for d in decisions if d["oid"] == oid)


def run_demo() -> int:
    total_fail = 0

    # ================================================================== #
    # 1) both regions same version & visible -> successful confirmation
    # ================================================================== #
    _scenario_step("1) 两区同版且可见 -> 甲单跨区确认成功")
    fw = _preswitch()
    r = fw.tick([Operation("甲-cross-ok", "cross", "甲单")])
    print(render_report(r["report"], fw.regions))
    print(render_decisions(r["decisions"]))
    total_fail += _checks([
        ("甲单跨区确认被签", _auth(r["decisions"], "甲-cross-ok")),
    ])

    # ================================================================== #
    # 2) THE CRUX TICK: L switched 甲->乙, DM covers R's confirm bar.
    #    Two due requests at tick end: 甲(cross) and 乙(left).
    #    Both policies fork from the SAME pre-switch terminal.
    # ================================================================== #
    base = _preswitch()
    _switch_layers(base)

    # ---- 2a) ATOMIC handoff: right not clean -> withhold whole frame ---
    _scenario_step("2a) 整帧交接：丢整帧 -> 终端停在旧甲/甲，甲单跨区可查；乙单左区迟到")
    fa = _clone(base)
    fa.policy = "atomic"
    fa.delivery.mode = {"L": "deliver", "R": "deliver"}
    r = fa.tick([Operation("甲-cross-A", "cross", "甲单"),
                 Operation("乙-left-A", "left", "乙单")])
    print(render_report(r["report"], fa.regions))
    print(render_decisions(r["decisions"]))
    total_fail += _checks([
        ("整帧未送出（dirty 全扣留）", r["dirty"] and not r["actions"]["sent"]),
        ("终端仍显示旧甲/甲（两区都旧但同版）",
         r["report"]["regions"]["L"]["version_seen"] == "甲单"
         and r["report"]["regions"]["R"]["version_seen"] == "甲单"),
        ("甲单跨区确认被签（基于旧甲/甲）", _auth(r["decisions"], "甲-cross-A")),
        ("乙单左区判为等齐，未签", _outcome(r["decisions"], "乙-left-A") == "wait"),
    ])

    # ---- 2b) PARTITIONED handoff i: deliver L(乙) now, ship redaction on R
    _scenario_step("2b) 分区交接(i)：先交乙单左区；右区被私信遮住 -> 不能跨区确认")
    fp = _clone(base)
    fp.policy = "partitioned"
    fp.delivery.mode = {"L": "deliver", "R": "deliver"}
    r = fp.tick([Operation("甲-cross-P", "cross", "甲单"),
                 Operation("乙-left-P", "left", "乙单")])
    print(render_report(r["report"], fp.regions))
    print(render_decisions(r["decisions"]))
    total_fail += _checks([
        ("乙单左区已办", _auth(r["decisions"], "乙-left-P")),
        ("甲单跨区判为等齐（右区被遮属临时，等下一拍）",
         _outcome(r["decisions"], "甲-cross-P") == "wait"),
        ("右区确实出现私信遮挡", r["report"]["regions"]["R"]["redactions"] > 0),
    ])

    # ---- 2c) PARTITIONED handoff ii: R keeps OLD 甲单 block (drop) ----
    _scenario_step("2c) 分区交接(ii)：右区留有旧版甲单 -> 仍不能跨区确认")
    fq = _clone(base)
    fq.policy = "partitioned"
    fq.delivery.mode = {"L": "deliver", "R": "drop"}
    r = fq.tick([Operation("甲-cross-Q", "cross", "甲单"),
                 Operation("乙-cross-Q", "cross", "乙单"),
                 Operation("乙-left-Q", "left", "乙单")])
    print(render_report(r["report"], fq.regions))
    print(render_decisions(r["decisions"]))
    total_fail += _checks([
        ("乙单左区已办", _auth(r["decisions"], "乙-left-Q")),
        ("乙单跨区判为作废（两区完整却异版，旧甲块必须丢）",
         _outcome(r["decisions"], "乙-cross-Q") == "void"),
        ("甲单跨区判为作废（同一矛盾屏）",
         _outcome(r["decisions"], "甲-cross-Q") == "void"),
        ("右区留有旧块（stale>0）", r["report"]["regions"]["R"]["stale"] > 0),
    ])

    # ================================================================== #
    # 3) next tick: (A) DM disappears  vs  (B) DM keeps covering.
    #    Both re-query what the customer actually sees *then*.
    # ================================================================== #
    # 3A: DM disappears, desktop advanced to 乙/乙 -> cross(乙) now provable
    _scenario_step("3A) 私信下一拍消失 + 桌面推进为乙/乙：再查客户当时实际看见什么")
    f3a = _clone(fp)
    f3a.composer.remove_privacy("dm")
    f3a.composer.set_base("R", "乙单", "#fdecef")
    f3a.delivery.mode = {"L": "deliver", "R": "deliver"}
    r = f3a.tick([Operation("乙-cross-gone", "cross", "乙单"),
                  Operation("乙-left-gone", "left", "乙单")])
    print(render_report(r["report"], f3a.regions))
    print(render_decisions(r["decisions"]))
    total_fail += _checks([
        ("此刻两区都是乙单且可见",
         r["report"]["regions"]["L"]["version_seen"] == "乙单"
         and r["report"]["regions"]["R"]["version_seen"] == "乙单"),
        ("乙单跨区确认被签", _auth(r["decisions"], "乙-cross-gone")),
    ])

    # 3B: DM still covering next tick -> still blocked (actual view re-queried)
    _scenario_step("3B) 私信继续遮挡：再查客户当时实际看见什么（仍被遮）")
    f3b = _clone(fp)
    f3b.composer.set_base("R", "乙单", "#fdecef")   # desktop advanced, DM still up
    f3b.delivery.mode = {"L": "deliver", "R": "deliver"}
    r = f3b.tick([Operation("乙-cross-still", "cross", "乙单")])
    print(render_report(r["report"], f3b.regions))
    print(render_decisions(r["decisions"]))
    total_fail += _checks([
        ("右区仍被私信遮住", r["report"]["regions"]["R"]["redactions"] > 0),
        ("乙单跨区确认未签（确认栏看不见）",
         not _auth(r["decisions"], "乙-cross-still")),
    ])

    # ================================================================== #
    # 4) desktop restored to 乙/乙, but R's receipt stuck on OLD 甲单
    #    -> must NOT sign this version's cross-region op
    # ================================================================== #
    _scenario_step("4) 桌面还原乙/乙，但右区回执停在旧版甲单 -> 不得签乙单跨区")
    f4 = _preswitch()                       # R holds clean old 甲单 blocks
    f4.composer.set_base("L", "乙单", "#fdecef")
    f4.composer.set_base("R", "乙单", "#fdecef")
    f4.policy = "partitioned"
    f4.delivery.mode = {"L": "deliver", "R": "drop"}   # R receipt frozen
    r = f4.tick([Operation("乙-cross-stale", "cross", "乙单"),
                 Operation("甲-cross-stale", "cross", "甲单"),
                 Operation("乙-left-stale", "left", "乙单")])
    print(render_report(r["report"], f4.regions))
    print(render_decisions(r["decisions"]))
    total_fail += _checks([
        ("右区保留旧甲单块", r["report"]["regions"]["R"]["version_seen"] == "甲单"),
        ("乙单跨区判为作废（乙/甲 两区自相矛盾，旧块必须丢）",
         _outcome(r["decisions"], "乙-cross-stale") == "void"),
        ("甲单跨区亦判为作废（两区完整却异版，整屏矛盾）",
         _outcome(r["decisions"], "甲-cross-stale") == "void"),
        ("乙单左区可办", _auth(r["decisions"], "乙-left-stale")),
    ])

    # ================================================================== #
    # 5) move ONLY the privacy layer over the destination:
    #    the new frame must not still ship the destination as clickable px
    # ================================================================== #
    _scenario_step("5) 只把私密层移到目的地：新帧不得再送出目的地像素")
    f5 = _preswitch()
    f5.composer.set_base("L", "乙单", "#fdecef")
    f5.composer.set_base("R", "乙单", "#fdecef")
    dest = f5.regions["R"].dest_tile()
    dest_rect = f5.regions["R"].tile_rect(*dest)
    f5.composer.set_privacy("dm-dest", dest_rect)
    f5.delivery.mode = {"L": "deliver", "R": "deliver"}
    intended = f5.composer.compose()
    dest_key = tile_key("R", *dest)
    print(render_frame(intended, f5.regions))
    r = f5.tick([Operation("乙-cross-dest", "cross", "乙单")])
    dest_tile = next(t for t in r["report"]["regions"]["R"]["tiles"]
                     if tuple(t["tile"]) == dest)
    print(render_decisions(r["decisions"]))
    total_fail += _checks([
        ("意图帧中目的地已是遮罩", intended[dest_key]["source"] == SOURCE_PRIVACY),
        ("实际视图目的地不是可点像素", dest_tile.get("source") == SOURCE_PRIVACY),
        ("乙单跨区确认未签（目的地被遮）",
         not _auth(r["decisions"], "乙-cross-dest")),
    ])

    # ================================================================== #
    # 6) a PUBLIC popup covers the confirm bar (not the private layer).
    #    Pixels ship fine and versions agree, but the 确认 affordance is
    #    physically hidden -> cross must still be refused.
    # ================================================================== #
    _scenario_step("6) 公开弹窗（如续费框）上移盖住确认栏：像素可发，但确认按钮不可点")
    f6 = _preswitch()
    f6.composer.set_base("R", "甲单", "#e8eef7")
    rdest = f6.regions["R"].tile_rect(*f6.regions["R"].dest_tile())
    f6.composer.set_window("renew", rdest, "#fff3cd", "续费")
    f6.delivery.mode = {"L": "deliver", "R": "deliver"}
    r = f6.tick([Operation("甲-cross-win", "cross", "甲单"),
                 Operation("甲-left-win", "left", "甲单")])
    print(render_report(r["report"], f6.regions))
    print(render_decisions(r["decisions"]))
    total_fail += _checks([
        ("右区无遮罩、版本一致（公开像素正常送达）",
         r["report"]["regions"]["R"]["redactions"] == 0
         and r["report"]["regions"]["R"]["version_seen"] == "甲单"),
        ("确认栏被公开弹窗盖住", not r["report"]["regions"]["R"]["dest"]["base_visible"]),
        ("甲单跨区未签（确认按钮不可点）",
         not _auth(r["decisions"], "甲-cross-win")),
        ("甲单左区仍可办", _auth(r["decisions"], "甲-left-win")),
    ])

    print()
    print("=" * 76)
    if total_fail == 0:
        print(f"{BOLD}演示全部断言通过：签收只依据每拍实际显示视图。{RESET}")
    else:
        print(f"{BOLD}{total_fail} 项断言失败{RESET}")
    print("历史仅记入 history 供事后追查，从不用于签收。")
    return 0 if total_fail == 0 else 1


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #

def _make_handler(state_path: str):
    """Build a request handler class bound to a state file (used by serve/test)."""
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _fw(self) -> Forwarder:
            return load_state(state_path)

        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"ok": True})
            if self.path == "/show":
                fw = load_state(state_path)
                return self._send(200, fw.endpoint.report(fw.tick_no))
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return self._send(400, {"error": "invalid json"})
            fw = self._fw()
            try:
                if self.path == "/draw":
                    for b in payload.get("base", []):
                        fw.composer.set_base(b["rid"], b["version"], b["color"])
                    for w in payload.get("window", []):
                        fw.composer.set_window(w["wid"], Rect(*w["rect"]),
                                               w["color"], w.get("label"), w.get("z", 10))
                    for p in payload.get("privacy", []):
                        fw.composer.set_privacy(p["pid"], Rect(*p["rect"]),
                                                p.get("color", "#ff00ff"), p.get("z", 100))
                    for pid in payload.get("remove_privacy", []):
                        fw.composer.remove_privacy(pid)
                    save_state(fw, state_path)
                    return self._send(200, {"ok": True})
                if self.path == "/send":
                    if payload.get("policy"):
                        fw.policy = payload["policy"]
                    for rid, mode in payload.get("delivery", {}).items():
                        fw.delivery.set(rid, mode)
                    ops = [Operation.from_dict(o) for o in payload.get("ops", [])]
                    result = fw.tick(ops)
                    save_state(fw, state_path)
                    return self._send(200, result)
                if self.path == "/click":
                    report = fw.endpoint.report(fw.tick_no)
                    res = check_click(int(payload["x"]), int(payload["y"]),
                                      report, fw.regions)
                    return self._send(200 if res.get("accepted") else 409, res)
                if self.path == "/show":
                    report = fw.endpoint.report(fw.tick_no)
                    return self._send(200, report)
                if self.path == "/reset":
                    save_state(Forwarder(default_regions(), "partitioned"), state_path)
                    return self._send(200, {"ok": True})
                return self._send(404, {"error": "not found"})
            except (KeyError, ValueError) as e:
                return self._send(400, {"error": str(e)})

    return Handler


def run_server(port: int, state_path: str) -> int:
    from http.server import HTTPServer

    srv = HTTPServer(("127.0.0.1", port), _make_handler(state_path))
    print(f"privacy forwarder listening on http://127.0.0.1:{port}  (state={state_path})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


# --------------------------------------------------------------------------- #
# Argument parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="privacy_forwarder",
                                description="Local privacy-screen forwarder")
    p.add_argument("--state", default=DEFAULT_STATE)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("draw", help="set base/window/privacy layers")
    d.add_argument("--base", action="append", metavar="RID=VER:#rrggbb")
    d.add_argument("--window", action="append", metavar="WID=x,y,w,h:#rrggbb[:label]")
    d.add_argument("--privacy", action="append", metavar="PID=x,y,w,h:#rrggbb")
    d.add_argument("--remove-privacy", action="append", metavar="PID")
    d.set_defaults(func=cmd_draw)

    s = sub.add_parser("send", help="refresh once, deliver, report, decide")
    s.add_argument("--policy", choices=["partitioned", "atomic"])
    s.add_argument("--delivery", action="append", metavar="RID=deliver|drop|hold|late")
    s.add_argument("--due", action="append", metavar="OID=kind:version[:due_tick]")
    s.add_argument("--report", action="store_true")
    s.set_defaults(func=cmd_send)

    c = sub.add_parser("click", help="click against actual displayed view")
    c.add_argument("--x", type=int, required=True)
    c.add_argument("--y", type=int, required=True)
    c.set_defaults(func=cmd_click)

    sh = sub.add_parser("show", help="show actual view or intended frame")
    sh.add_argument("--frame", action="store_true")
    sh.set_defaults(func=cmd_show)

    rs = sub.add_parser("reset", help="reset state")
    rs.set_defaults(func=cmd_reset)

    dm = sub.add_parser("demo", help="run scripted demonstration")
    dm.set_defaults(func=None)

    sv = sub.add_parser("serve", help="run local HTTP API")
    sv.add_argument("--port", type=int, default=8765)
    sv.set_defaults(func=None)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "demo":
        return run_demo()
    if args.cmd == "serve":
        return run_server(args.port, args.state)

    fw = load_state(args.state)
    rc = args.func(args, fw)
    save_state(fw, args.state)
    return rc


if __name__ == "__main__":
    sys.exit(main())
