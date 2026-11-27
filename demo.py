# -*- coding: utf-8 -*-
"""
demo.py —— 端到端演示（只读写 ./demo_run，与任何既有数据隔离）。

剧目：
  第一幕  两部终端离线各自发信，上线后按不同顺序收到对方旧信（乱序窗口）
  第二幕  发信/收信/已读/归档四件事互相独立；回条不含正文
  第三幕  换机 A：缺信先补到 → 旧信按来信顺序还原，新通信继续
  第四幕  换机 B：先清机（临时会话）→ 旧话彻底读不回，新通信继续
  第五幕  遗忘承诺：事后拿到整套账户材料的人也读不回已销毁段
  第六幕  双机在场的接收交接：补缺失信；别段同号信不能补入当段；
          恢复正确封件；重复申请只查回原交接
"""
import os
import shutil

from secret_chat import (DeskError, ForgottenError, Relay, ServiceDesk,
                         Terminal, TerminalError, UnknownItemError)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_run")


def banner(title):
    print("\n" + "=" * 72)
    print("  " + title)
    print("=" * 72)


def check(cond, text):
    print(("  [√] " if cond else "  [×] ") + text)
    if not cond:
        raise AssertionError(text)


def read_sheet(sheets_dir, sid, term):
    path = os.path.join(sheets_dir, "recovery_%s_%s.txt" % (sid, term))
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("ARK_SHARE="):
                return line.strip().split("=", 1)[1]
    raise RuntimeError("找不到恢复手续单")


def main():
    if os.path.exists(ROOT):
        shutil.rmtree(ROOT)
    desk = ServiceDesk(os.path.join(ROOT, "desk"))
    relay = Relay(os.path.join(ROOT, "relay"))
    sheets = os.path.join(ROOT, "recovery_sheets")
    old_home = os.path.join(ROOT, "devices")
    new_home = os.path.join(ROOT, "new_devices")  # 换机后从独立目录重新打开

    alice = Terminal("alice", os.path.join(old_home, "alice"), desk, window=16)
    bob = Terminal("bob", os.path.join(old_home, "bob"), desk, window=16)

    # 三段会话：一段临时、两段事先承诺归档（其中一段专用于跨段调包演示）
    alice.create_session(bob, "S-EPH", "EPHEMERAL", sheets)
    alice.create_session(bob, "S-ARC", "ARCHIVE_ALL", sheets)
    alice.create_session(bob, "S-OTHER", "ARCHIVE_ALL", sheets)

    # ---------------- 第一幕：离线各自发信 + 乱序收信 ----------------
    banner("第一幕 离线各自发信，上线后按不同顺序收到对方旧信")
    a_envs = [alice.send("S-EPH", "爱丽丝旧信-%d" % i) for i in range(3)]
    b_envs = [bob.send("S-EPH", "鲍勃旧信-%d" % i) for i in range(3)]
    for e in a_envs:
        relay.send("bob", e)
    for e in b_envs:
        relay.send("alice", e)

    bob_first = relay.take("bob", permutation=[2, 0, 1])      # bob: 2→0→1
    alice_first = relay.take("alice", permutation=[1, 0, 2])  # alice: 1→0→2
    bob.receive(bob_first[0])  # 先到 2 号
    print("  bob 先收到 2 号，窗口为尚缺来信保留开件位置：%s"
          % bob.pending_gaps("S-EPH"))
    check(bob.pending_gaps("S-EPH") == [0, 1], "缺号 0、1 的开件位置已保留")
    for env in bob_first[1:]:
        bob.receive(env)
    for env in alice_first:
        alice.receive(env)
    check([bob.view_inbox("S-EPH")[str(i)] for i in range(3)]
          == ["爱丽丝旧信-%d" % i for i in range(3)],
          "bob 按来信顺序还原：" + str(bob.view_inbox("S-EPH")))
    check([alice.view_inbox("S-EPH")[str(i)] for i in range(3)]
          == ["鲍勃旧信-%d" % i for i in range(3)],
          "alice 按来信顺序还原：" + str(alice.view_inbox("S-EPH")))

    # ---------------- 第二幕：四件事互相独立 ----------------
    banner("第二幕 发信 / 收信 / 已读 / 归档是四件不同的事")
    letters_before = len(desk.state["letters"])
    env = alice.send("S-ARC", "归档段密信")  # S-ARC 0 号：发送端先另封一份
    check(len(desk.state["letters"]) == letters_before + 1,
          "发送即逐件另封归档（sender-archive），与对端是否收/读无关")
    bob.receive(env)  # 同号幂等，不重复入柜
    receipts_before = len(desk.state["receipts"])
    rcpt = bob.mark_read("S-ARC", 0)
    check("归档段密信" not in str(rcpt), "已读回条里不含正文字节")
    check(len(desk.state["receipts"]) == receipts_before + 1
          and "body" not in desk.state["receipts"][-1],
          "柜面只登记回条证明指纹，没有、也无法据此补写正文")
    check(not desk.has_letter("S-EPH", 0), "临时会话柜内无任何信件封件")

    # ---------------- 第三幕：换机 A —— 缺信先补到 ----------------
    banner("第三幕 换机 A：缺信先补到（归档会话，旧信可还原）")
    arc_envs = [alice.send("S-ARC", "昨晚商议-%d" % i) for i in range(4)]  # S-ARC 1..4
    bob.receive(arc_envs[0]); bob.receive(arc_envs[2])  # 2、4 缺失
    print("  缺号位置：%s" % bob.pending_gaps("S-ARC"))
    bob.receive(arc_envs[1]); bob.receive(arc_envs[3])  # 缺信先补到
    check(bob.pending_gaps("S-ARC") == [], "缺号补齐，窗口里的开件位置全部销掉")
    cid_arc, mkey_arc = bob.export_continuation("S-ARC")

    # 同时为第四幕准备临时会话的换机材料（此时旧机还没清）
    eph_envs = [alice.send("S-EPH", "临时旧话-%d" % i) for i in range(3)]  # S-EPH 3..5
    bob.receive(eph_envs[0]); bob.receive(eph_envs[2])  # 4 号永不到达
    check(bob.pending_gaps("S-EPH") == [4], "临时会话 4 号开件位置暂留窗口")
    cid_eph, mkey_eph = bob.export_continuation("S-EPH")  # 缺格本地销毁，链根保留

    bob.wipe()
    check(not os.path.exists(os.path.join(old_home, "bob", "sessions.json")),
          "旧机交还清空：本地签章私钥、链密钥与明文全部删除")

    bob2 = Terminal("bob", os.path.join(new_home, "bob-arc"), desk)
    info = bob2.import_from_desk("S-ARC", continuation_id=cid_arc,
                                 migration_key=mkey_arc)
    print("  新机导入结果：%s" % info)
    restored = [bob2.open_archived("S-ARC", i)[0]["body"] for i in range(1, 5)]
    check(restored == ["昨晚商议-%d" % i for i in range(4)],
          "服务台按会话和缺号范围交回真正能打开的原信：" + str(restored))
    new_env = alice.send("S-ARC", "今天的新一轮")  # S-ARC 5 号
    check(bob2.receive(new_env) == "今天的新一轮", "新机收得到对端新信")
    reply = bob2.send("S-ARC", "新机已就位")
    check(alice.receive(reply) == "新机已就位", "新机发信、对端可收，双向新通信继续")

    # ---------------- 第四幕：换机 B —— 先清机 ----------------
    banner("第四幕 换机 B：先清机（临时会话，旧话读不回、新话继续）")
    bob3 = Terminal("bob", os.path.join(new_home, "bob-eph"), desk)
    bob3.import_from_desk("S-EPH", continuation_id=cid_eph, migration_key=mkey_eph)
    check(bob3.inbox["S-EPH"] == {}, "接续包裹不夹明文，本地旧话已随清机消失")
    for i in (3, 4, 5):
        try:
            bob3.open_archived("S-EPH", i)
            raise AssertionError("不应走到这里")
        except UnknownItemError:
            pass
    print("  [√] 柜内没有临时会话封件，三封临时旧话都无法补开")
    try:
        bob3.receive(eph_envs[1])  # 迟到的 4 号旧信
        raise AssertionError("不应走到这里")
    except ForgottenError:
        print("  [√] 迟到旧信的开件位置已在清机前销毁，开不出来")
    fresh = alice.send("S-EPH", "清机后的新话")  # S-EPH 6 号
    check(bob3.receive(fresh) == "清机后的新话", "接收链根保留，新一轮通信照常")

    # ---------------- 第五幕：遗忘承诺 vs 整套账户材料 ----------------
    banner("第五幕 已签出的遗忘承诺：拿到整套账户材料也读不回")
    f_envs = [alice.send("S-ARC", "待遗忘-%d" % i) for i in range(3)]  # S-ARC 6..8
    for e in f_envs:
        bob2.receive(e)
    bob2.forget_range("S-ARC", 6, 8)  # 只销毁这三封对应的状态
    ark_share = read_sheet(sheets, "S-ARC", "bob")
    attacker = Terminal("bob", os.path.join(ROOT, "attacker"), desk)
    attacker.import_from_desk("S-ARC", ark_share=ark_share)
    print("  [√] 攻击者凭恢复手续单拿到了会话状态（受托档案柜的恢复能力确实存在）")
    try:
        attacker.open_archived("S-ARC", 6)
        raise AssertionError("不应走到这里")
    except ForgottenError:
        print("  [√] 遗忘段 6 号：服务台依已签承诺拒开，整套账户材料也无用")
    letter, _ = attacker.open_archived("S-ARC", 1)  # 未承诺段仍可开
    check(letter["body"] == "昨晚商议-0", "未承诺销毁的旧信仍可正常还原")

    # ---------------- 第六幕：双机在场接收交接 + 调包检测 ----------------
    banner("第六幕 双机在场交接：补缺失信、同号异段调包必败、重复申请查原交接")
    h_envs = [alice.send("S-ARC", "交接段-%d" % i) for i in range(3)]  # S-ARC 9..11
    bob2.receive(h_envs[0]); bob2.receive(h_envs[2])  # 中段 10 号一直缺失
    check("10" not in bob2.inbox["S-ARC"], "10 号确属缺失")
    letter10, receipt10 = bob2.open_archived("S-ARC", 10)  # 发送端归档即合法材料
    check(letter10["body"] == "交接段-1", "双机在场：凭合法开件材料补交缺失原件")
    _, receipt10b = bob2.open_archived("S-ARC", 10)
    check(receipt10["receipt_id"] == receipt10b["receipt_id"],
          "对端已领走同一原件，再次申请只查回原交接 %s" % receipt10["receipt_id"])

    # 另一段会话里的同号（0 号）信：把柜内 S-ARC/10 封件调包成它
    other_env = alice.send("S-OTHER", "别段同号信")
    other_blob = desk.state["letters"]["S-OTHER/0"]["blob"]
    desk.state["letters"]["S-ARC/10"]["blob"] = other_blob  # 介质调包
    try:
        bob2.open_archived("S-ARC", 10)
        raise AssertionError("不应走到这里")
    except TerminalError:
        print("  [√] 别段同号信的封件 AAD 不匹配，不能补入当段")
    # 恢复正确封件：窗口里 10 号开件位置还在，重收原件取得明文，
    # 再走柜面正式恢复手续重封入柜（旧交接记录作废、留痕、可重新开件）
    recovered_plain = bob2.receive(h_envs[1])
    bob2.restore_archived("S-ARC", 10, recovered_plain, "alice")
    letter10c, receipt10c = bob2.open_archived("S-ARC", 10)
    check(letter10c["body"] == "交接段-1", "正确封件恢复后，当段原件重新可开")
    check(receipt10c["receipt_id"] != receipt10["receipt_id"],
          "恢复后重新出库，领的是新回执；旧交接已在流水中标作废")

    # 局外人开件被拒
    Terminal("mallory", os.path.join(ROOT, "mallory"), desk)
    try:
        desk.open_letter("S-ARC", 0, "mallory")
        raise AssertionError("不应走到这里")
    except DeskError:
        print("  [√] 非会话成员的开件申请被柜面拒绝")

    banner("服务台审计流水（节选）")
    with open(os.path.join(ROOT, "desk", "events.log"), encoding="utf-8") as f:
        for line in f:
            if any(k in line for k in ("TOMBSTONE", "OPEN_DENIED", "DUPLICATE",
                                       "READ_RECEIPT", "CHECKOUT", "KEY_ROTATED",
                                       "RESTORE")):
                print("  " + line.rstrip())

    print("\n全部剧目通过。介质仅使用题内程序生成的材料；"
          "新机目录 %s 与旧机目录 %s 相互独立。" % (new_home, old_home))


if __name__ == "__main__":
    main()
