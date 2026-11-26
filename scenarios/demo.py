#!/usr/bin/env python3
"""End-to-end demonstration of the two-hall anonymous ballot exercise.

Run:  python3 -m scenarios.demo     (or: python3 scenarios/demo.py)

Walks the narrative in the brief on a throw-away directory, prints a step log,
and writes + verifies the final report bundle (ballots / tally / identity /
loss / chains).  Exit code is non-zero if verification fails (useful for CI).
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ballotbox import (  # noqa: E402
    Desk, Hall, build_report, verify_report, tokens,
)

root = tempfile.mkdtemp(prefix="ballotbox-demo-")


def line(msg=""):
    print(msg)


clock_t = [1000]


def clock():
    return clock_t[0]


def tick(dt=1):
    clock_t[0] += dt
    return clock_t[0]


# ---- world -------------------------------------------------------------------
desk = Desk(os.path.join(root, "desk"), clock=clock)
east = Hall("east", os.path.join(root, "halls", "east"), clock=clock)
west = Hall("west", os.path.join(root, "halls", "west"), clock=clock)
desk.register_hall(east)
desk.register_hall(west)
for m in ("甲", "乙", "丙", "丁", "戊"):
    desk.add_member(m)
event = desk.register_event("本场", choices=("赞成", "反对", "弃权"), deadline=5000)
old = desk.register_event("上一场", choices=("赞成", "反对"))

line(f"workdir: {root}")
line("")

# ---- 1. 会员台领证 -----------------------------------------------------------
tok_A = desk.issue("甲", "本场")
tok_B = desk.issue("乙", "本场")
tok_C = desk.issue("丙", "本场")
tok_D = desk.issue("丁", "本场")
tok_E = desk.issue("戊", "本场")
line("1) 领证: 甲乙丙丁戊 各领一张本场凭条（凭条本身不含会员号）")
line(f"   例: 甲 slip = {tok_A.slip[:52]}...")
line("")

# ---- 1b. 把基准凭条的活动改成上一场 -> 投递前发现证不符 ---------------------
old_tok = desk.issue("甲", "上一场")  # 甲 also attends the previous event
try:
    tokens.verify_slip(old_tok.slip, event)
    line("   !! previous-event slip accepted (BUG)")
except Exception as exc:  # EventMismatch
    line(f"1b) 用上一场凭条投本场 -> 投递前即拒绝: {type(exc).__name__}")
line("")

# ---- 2. 断线前分配接收权 -----------------------------------------------------
desk.grant_final_authority(tok_A.token_id, "east")  # 甲：东厅独占最终接收权
desk.keep_transfer(tok_B.token_id)                   # 乙：保留跨厅流转
line("2) 会员台: 甲->东厅独占最终接收权; 乙->保留跨厅流转; 丙丁戊暂未授予")
line("")

# ---- 3. 两厅同时断线（断线时刻）---------------------------------------------
tick(5)
desk.set_hall_online("east", False)
desk.set_hall_online("west", False)
line("3) 两厅进入断线时刻")
line("")

# ---- 4. 甲在东厅完成（独占接收权, 断线也出最终回执）------------------------
r = east.submit(tok_A.slip, "赞成", event)
line(f"4) 甲到东厅(持独占权, 断线) => ok={r.ok} kind={r.receipt.kind}")
line("   甲已预订且东厅可独立接收的服务办成（离线发收妥回执）")
line("")

# ---- 5. 断线时对甲挂失：只能明示待交接 --------------------------------------
lr = desk.report_loss(tok_A.token_id)
line(f"5) 断线时甲挂失 => effective={lr.effective} pending={lr.pending} "
     f"state={lr.state}")
line("   一厅独占接收权且仍离线，会员台只能明示挂失待其交接，不能声称即时生效")
line("")

# ---- 6. 乙在断线时到西厅（流转证, 仍失联 -> 不承诺）------------------------
r = west.submit(tok_B.slip, "反对", event)
line(f"6) 乙断线时到西厅(流转证, 仍失联) => ok={r.ok} reason={r.reason}")
line("   不能在仍失联时许诺最终结果，只能待确认")
line("")

# ---- 7. 东厅恢复联络并交接; 甲的挂失结论落定（票保留）----------------------
desk.set_hall_online("east", True)
east.sync()
line(f"7) 东厅恢复联络并交接 => 甲收妥回执={east.receipt_for(tok_A.token_id).kind}")
line(f"   甲挂失结论: state={desk.loss_conclusion(tok_A.token_id)['loss_state']}"
     f" (先收妥, 票保留)")
line("")

# ---- 8. 西厅恢复联络; 乙补投 -> 暂存 -> 截止前交接计入 ---------------------
desk.set_hall_online("west", True)
r2 = west.submit(tok_B.slip, "反对", event)
line(f"8) 乙恢复联络后到西厅 => ok={r2.ok} kind={r2.receipt.kind}")
desk.commit_handoff(tok_B.token_id, "west")
line(f"   截止前交接清楚 => 西厅计入: {west.recount()}")
line("")

# ---- 9. 丙: 先授予->东... 改为: 授予西厅, 先收妥再挂失 -> 票保留 -----------
desk.grant_final_authority(tok_C.token_id, "west")
r3 = west.submit(tok_C.slip, "赞成", event)
lr3 = desk.report_loss(tok_C.token_id)
line(f"9) 丙先收妥(kind={r3.receipt.kind}) 后挂失(state={lr3.state}) "
     f"-> 先收妥的票保留")
line("")

# ---- 10. 丁: 先挂失(流转证即时生效) -> 后续投递被作废 ----------------------
desk.keep_transfer(tok_D.token_id)
lrd = desk.report_loss(tok_D.token_id)
line(f"10) 丁先挂失(effective={lrd.effective})")
rd = east.submit(tok_D.slip, "弃权", event)
line(f"    丁随后投递 => ok={rd.ok} reason={rd.reason} (先挂失, 不得再投)")
line("")

# ---- 11. 戊: 未授予最终接收权 -> 相同投递只能待确认 ------------------------
re = east.submit(tok_E.slip, "赞成", event)
line(f"11) 戊既有凭条但东厅尚未取得其最终接收权 => ok={re.ok} reason={re.reason}")
line("    相同投递只能待确认")
line("")

# ---- 12. 关闭场次与最终回执 -------------------------------------------------
desk.close_event("本场")
line("")
line("12) 关闭场次，生成最终回执包")
out = os.path.join(root, "report")
paths = build_report(desk, out)
for k, p in paths.items():
    line(f"   - {k}: {p}")

verdict = verify_report(desk, out)
line("")
line(f"重新统计 = {verdict['recount']}")
line(f"核实结论 ok = {verdict['ok']}  problems = {verdict['problems']}")
line("")
line("厅内汇总:")
for hall in (east, west):
    line(f"  {hall.hall_id}: ballots_counted={hall.summary()['ballots_counted']} "
         f"pending={hall.summary()['ballots_pending']} "
         f"chain_ok={hall.summary()['chain']['ok']}")
line("")
line("挂失结论:")
for tok, who in ((tok_A, "甲"), (tok_B, "乙"), (tok_C, "丙"),
                 (tok_D, "丁"), (tok_E, "戊")):
    c = desk.loss_conclusion(tok.token_id)
    line(f"  {who}: loss_state={c['loss_state']:>9}  finalized={c['finalized']}  "
         f"hall={c['finalized_hall']}")

sys.exit(0 if verdict["ok"] else 1)
