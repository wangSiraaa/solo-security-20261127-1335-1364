# -*- coding: utf-8 -*-
"""test_chat.py —— 核心性质单元测试（unittest，标准库）。"""
import os
import shutil
import tempfile
import unittest

from secret_chat import (DeskError, ForgottenError, Relay, ServiceDesk,
                         Terminal, TerminalError, UnknownItemError)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="sc_")
        self.desk = ServiceDesk(os.path.join(self.root, "desk"))
        self.relay = Relay(os.path.join(self.root, "relay"))
        self.sheets = os.path.join(self.root, "sheets")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def terms(self, names=("alice", "bob"), window=16):
        home = os.path.join(self.root, "dev")
        return [Terminal(n, os.path.join(home, n), self.desk, window) for n in names]

    def new_terminal(self, name):
        return Terminal(name, os.path.join(self.root, "dev2", name), self.desk)


class TestBasics(Base):
    def test_four_acts_are_separate(self):
        alice, bob = self.terms()
        alice.create_session(bob, "S1", "EPHEMERAL", self.sheets)
        env = alice.send("S1", "第一条")
        self.relay.send("bob", env)
        (got,) = self.relay.take("bob")
        # 未收信不能签已读
        with self.assertRaises(TerminalError):
            bob.mark_read("S1", 0)
        plain = bob.receive(got)
        self.assertEqual(plain, "第一条")
        # 已读只是回条登记，柜内不产生信件档案
        before = len(self.desk.state["letters"])
        bob.mark_read("S1", 0)
        self.assertEqual(len(self.desk.state["letters"]), before)
        self.assertEqual(len(self.desk.state["receipts"]), 1)
        # EPHEMERAL 策略：柜内始终没有信件封件
        self.assertEqual(before, 0)

    def test_out_of_order_window(self):
        alice, bob = self.terms(window=8)
        alice.create_session(bob, "S2", "EPHEMERAL", self.sheets)
        envs = [alice.send("S2", "m%d" % i) for i in range(5)]
        for e in envs:
            self.relay.send("bob", e)
        order = [4, 0, 3, 1, 2]  # 完全乱序
        for idx in order:
            bob.receive(envs[idx])
        self.assertEqual([bob.inbox["S2"][str(i)] for i in range(5)],
                         ["m%d" % i for i in range(5)])
        # 窗口外拒绝：新终端窗口=2，跳号 5 超出
        a2, b2 = self.terms(("alice2", "bob2"), window=2)
        a2.create_session(b2, "S2b", "EPHEMERAL", self.sheets)
        far_envs = [a2.send("S2b", "n%d" % i) for i in range(4)]
        with self.assertRaises(TerminalError):
            b2.receive(far_envs[3])  # 0 号未到先跳 3 号，gap=3 > 窗口 2

    def test_replay_is_idempotent(self):
        alice, bob = self.terms()
        alice.create_session(bob, "S3", "EPHEMERAL", self.sheets)
        env = alice.send("S3", "x")
        bob.receive(env)
        self.assertEqual(bob.receive(env), "x")  # 重放不报错、不重复记账

    def test_tamper_detected(self):
        alice, bob = self.terms()
        alice.create_session(bob, "S4", "EPHEMERAL", self.sheets)
        env = alice.send("S4", "secret")
        env["ct"] = env["ct"][:-2] + ("AA" if not env["ct"].endswith("AA") else "BB")
        with self.assertRaises(TerminalError):
            bob.receive(env)


class TestForgetting(Base):
    def test_tombstone_blocks_everything(self):
        alice, bob = self.terms(window=8)
        alice.create_session(bob, "S5", "ARCHIVE_ALL", self.sheets)
        envs = [alice.send("S5", "old%d" % i) for i in range(4)]
        # bob 只收到 0 和 2（1 缺失），窗口为 1 保留了开件位置
        bob.receive(envs[0])
        bob.receive(envs[2])
        self.assertEqual(bob.pending_gaps("S5"), [1])
        # 先放弃缺号、销毁整段
        bob.forget_prefix("S5", 2)
        # 迟到的 1 号信再也开不了
        with self.assertRaises(ForgottenError):
            bob.receive(envs[1])
        # 柜面也拒开、拒存
        with self.assertRaises(ForgottenError):
            bob.open_archived("S5", 1)
        with self.assertRaises(ForgottenError):
            self.desk.deposit_letter("S5", 1, b"fake", "x")
        # 后续新信仍可正常收发
        env_new = alice.send("S5", "new-round")
        self.assertEqual(bob.receive(env_new), "new-round")

    def test_account_material_cannot_reopen_forgotten(self):
        alice, bob = self.terms()
        alice.create_session(bob, "S6", "ARCHIVE_ALL", self.sheets)
        envs = [alice.send("S6", "昨晚商议%d" % i) for i in range(3)]
        for e in envs:
            bob.receive(e)
        bob.forget_prefix("S6", 2)
        # 拿到"整套账户材料"（即 setup 包裹所需的恢复手续）的攻击者
        with open(os.path.join(self.sheets,
                               "recovery_S6_bob.txt"), encoding="utf-8") as f:
            ark_share = f.read().strip().split("ARK_SHARE=")[1]
        attacker = self.new_terminal("bob")
        attacker.import_from_desk("S6", ark_share=ark_share)
        # 状态恢复了，但遗忘段的本地明文没有回来，柜面同样拒开
        self.assertEqual(attacker.inbox.get("S6", {}), {})
        with self.assertRaises(ForgottenError):
            attacker.open_archived("S6", 0)

    def test_read_receipt_cannot_reconstruct_body(self):
        alice, bob = self.terms()
        alice.create_session(bob, "S7", "EPHEMERAL", self.sheets)
        env = alice.send("S7", "机密正文")
        bob.receive(env)
        rcpt = bob.mark_read("S7", 0)
        # 回条里没有正文字节；柜面登记项只有证明指纹
        self.assertNotIn("机密正文".encode().hex(), str(rcpt))
        logged = self.desk.state["receipts"][0]
        self.assertNotIn("body", logged)


class TestMigration(Base):
    def _build_talk(self, policy):
        alice, bob = self.terms()
        alice.create_session(bob, "M-" + policy, policy, self.sheets)
        envs = [alice.send("M-" + policy, "last-night-%d" % i) for i in range(4)]
        return alice, bob, envs

    def test_case1_missing_fills_before_wipe_archive(self):
        alice, bob, envs = self._build_talk("ARCHIVE_ALL")
        bob.receive(envs[0]); bob.receive(envs[2])  # 缺 1、3
        # 缺信先补到（窗口里保留的开件位置正好对上晚到的信）
        bob.receive(envs[1]); bob.receive(envs[3])
        self.assertEqual(bob.view_inbox("M-ARCHIVE_ALL"),
                         {str(i): "last-night-%d" % i for i in range(4)})
        cid, mkey = bob.export_continuation("M-ARCHIVE_ALL")
        bob.wipe()
        bob2 = self.new_terminal("bob")
        info = bob2.import_from_desk("M-ARCHIVE_ALL",
                                     continuation_id=cid, migration_key=mkey)
        # 接续只搬密钥状态，不带明文；昨晚 4 封原信凭柜内归档逐件还原
        self.assertEqual(info["inbox_seqs"], [])
        for i in range(4):
            letter, _ = bob2.open_archived("M-ARCHIVE_ALL", i)
            self.assertEqual(letter["body"], "last-night-%d" % i)
        # 新通信继续：alice 再发，bob2 能收
        env = alice.send("M-ARCHIVE_ALL", "今天的新话")
        self.assertEqual(bob2.receive(env), "今天的新话")
        # bob2 也能发，alice 能收（双向链各自独立）
        reply = bob2.send("M-ARCHIVE_ALL", "收到")
        self.assertEqual(alice.receive(reply), "收到")

    def test_case2_wipe_first_ephemeral_old_gone_new_continues(self):
        alice, bob, envs = self._build_talk("EPHEMERAL")
        bob.receive(envs[0]); bob.receive(envs[2])
        # 先清机，缺号永不补；导出时窗口内缺格本地销毁、链根保留
        cid, mkey = bob.export_continuation("M-EPHEMERAL")
        bob.wipe()
        bob2 = self.new_terminal("bob")
        bob2.import_from_desk("M-EPHEMERAL", continuation_id=cid, migration_key=mkey)
        # 旧明文已随清机消失；柜内也没有任何 EPHEMERAL 信件可补
        self.assertEqual(bob2.inbox["M-EPHEMERAL"], {})
        for i in range(4):
            with self.assertRaises(UnknownItemError):
                bob2.open_archived("M-EPHEMERAL", i)
        # 迟到旧信：缺格已销毁，开不出来
        with self.assertRaises(ForgottenError):
            bob2.receive(envs[1])
        # 新一轮通信不受影响（alice 沿原发送链发 4 号，bob2 的接收链根正好接上）
        env = alice.send("M-EPHEMERAL", "新一轮")
        self.assertEqual(bob2.receive(env), "新一轮")
        reply = bob2.send("M-EPHEMERAL", "收到")
        self.assertEqual(alice.receive(reply), "收到")

    def test_archive_setup_recovery_restores_old_letters(self):
        alice, bob, envs = self._build_talk("ARCHIVE_ALL")
        for e in envs:
            bob.receive(e)
        bob.wipe()  # 不导出 continuation，只剩恢复手续单 + 柜内归档
        bob2 = self.new_terminal("bob")
        with open(os.path.join(self.sheets,
                               "recovery_M-ARCHIVE_ALL_bob.txt"), encoding="utf-8") as f:
            ark_share = f.read().strip().split("ARK_SHARE=")[1]
        bob2.import_from_desk("M-ARCHIVE_ALL", ark_share=ark_share)
        letter0, receipt = bob2.open_archived("M-ARCHIVE_ALL", 0)
        self.assertEqual(letter0["body"], "last-night-0")
        # 重复申请只查回原交接
        _, receipt2 = bob2.open_archived("M-ARCHIVE_ALL", 0)
        self.assertEqual(receipt["receipt_id"], receipt2["receipt_id"])
        # setup 恢复出的是初始链状态；双方在场再握手后，新通信从 0 号重新起算
        # （alice 旧机仍在，持有同会话 ark_wrap 可收取再握手包裹）
        alice.export_rehandshake("M-ARCHIVE_ALL", bob2)
        bob2.accept_rehandshake("M-ARCHIVE_ALL")
        alice.accept_rehandshake("M-ARCHIVE_ALL")
        fresh = bob2.send("M-ARCHIVE_ALL", "换机后新话")
        self.assertEqual(alice.receive(fresh), "换机后新话")
        back = alice.send("M-ARCHIVE_ALL", "确认")
        self.assertEqual(bob2.receive(back), "确认")

    def test_independent_directory_reopen(self):
        alice, bob, envs = self._build_talk("ARCHIVE_ALL")
        bob.receive(envs[0])
        old_home = bob.home
        cid, mkey = bob.export_continuation("M-ARCHIVE_ALL")
        bob.wipe()
        self.assertFalse(os.path.exists(os.path.join(old_home, "sessions.json")))
        new_home = os.path.join(self.root, "brand_new_device", "bob")
        bob2 = Terminal("bob", new_home, self.desk)
        bob2.import_from_desk("M-ARCHIVE_ALL", continuation_id=cid, migration_key=mkey)
        self.assertTrue(os.path.exists(os.path.join(new_home, "sessions.json")))
        # 独立目录重开后仍凭柜内归档还原旧信
        letter, _ = bob2.open_archived("M-ARCHIVE_ALL", 0)
        self.assertEqual(letter["body"], "last-night-0")


class TestCrossSession(Base):
    def test_same_number_letter_from_other_session_rejected(self):
        alice, bob = self.terms()
        alice.create_session(bob, "S-A", "ARCHIVE_ALL", self.sheets)
        alice.create_session(bob, "S-B", "ARCHIVE_ALL", self.sheets)
        env_a = alice.send("S-A", "甲段零号")
        env_b = alice.send("S-B", "乙段零号")  # 同号、另一会话
        bob.receive(env_a)
        bob.receive(env_b)
        # 调包：把 S-A 柜内 0 号封件换成 S-B 的同号封件（模拟柜内介质被换）
        blob_b = self.desk.state["letters"]["S-B/0"]["blob"]
        self.desk.state["letters"]["S-A/0"]["blob"] = blob_b
        with self.assertRaises(TerminalError):
            bob.open_archived("S-A", 0)
        # 走正式恢复手续：合法开件材料重封正确原件入柜
        bob.restore_archived("S-A", 0, "甲段零号", alice.id)
        letter, receipt_new = bob.open_archived("S-A", 0)
        self.assertEqual(letter["body"], "甲段零号")
        # 乙段同号信依然只能由乙段身份开启
        letter_b, _ = bob.open_archived("S-B", 0)
        self.assertEqual(letter_b["body"], "乙段零号")

    def test_double_present_handoff_missing_letter(self):
        alice, bob = self.terms()
        alice.create_session(bob, "S-C", "ARCHIVE_ALL", self.sheets)
        envs = [alice.send("S-C", "v%d" % i) for i in range(3)]
        bob.receive(envs[0]); bob.receive(envs[2])  # 1 号一直缺失
        # 双机在场：用 alice 的发送归档补交 1 号
        self.assertTrue(self.desk.has_letter("S-C", 1))
        letter, receipt = bob.open_archived("S-C", 1)
        self.assertEqual(letter["body"], "v1")
        # 对端已领走同一原件：再次申请只查回原交接
        _, receipt2 = bob.open_archived("S-C", 1)
        self.assertEqual(receipt["receipt_id"], receipt2["receipt_id"])

    def test_non_member_cannot_open(self):
        alice, bob = self.terms()
        Terminal("carol", os.path.join(self.root, "dev", "carol"), self.desk)
        alice.create_session(bob, "S-D", "ARCHIVE_ALL", self.sheets)
        alice.send("S-D", "private")
        with self.assertRaises(DeskError):
            self.desk.open_letter("S-D", 0, "carol")


if __name__ == "__main__":
    unittest.main(verbosity=2)
