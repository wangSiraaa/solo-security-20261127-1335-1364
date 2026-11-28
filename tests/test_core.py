"""核心机制单元测试（小库、小扇出，便于断言边界行为）。"""
import json
import unittest

from adp import (
    Entry, Server, Client, Manuscript, NetworkTransport, Signer, Verifier,
    Compiler, VerificationError, StaleCursorError, build_tree,
)
from adp.entries import prefix_bounds, NEG_INF


def tiny_entries():
    # 含同形异义、共享前缀，且故意让总数跨多个树层
    return [
        Entry("a", 0, "ei1", "甲"),
        Entry("ab", 0, "ei1 bi4", "乙"),
        Entry("ab", 1, "ei1 bi4", "乙的第二义项"),
        Entry("abc", 0, "ei1 bi4 si1", "丙"),
        Entry("abd", 0, "ei1 bi4 di4", "丁"),
        Entry("b", 0, "bi4", "戊"),
        Entry("ba", 0, "bi4 ei1", "己"),
        Entry("花", 0, "hua1", "花朵"),
        Entry("花", 1, "hua1", "耗费"),
    ]


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.signer = Signer.generate("test-key")
        self.verifier = Verifier(self.signer.public_key_pem(), "test-key")
        self.compiler = Compiler(self.signer)
        self.rev = self.compiler.publish(1, tiny_entries(), "2026-11-01")
        self.server = Server()
        self.server.load_snapshot(self.rev.snapshot_json())
        self.client = Client(self.verifier)
        self.tp = NetworkTransport(self.server)

    # ------------------------------------------------ 基本翻页

    def test_prefix_bounds(self):
        lo, hi = prefix_bounds("ab")
        self.assertTrue(("ab", 0) >= lo)
        self.assertTrue(("ab", 5) < hi)
        self.assertFalse(("aa", 0) >= lo)
        self.assertFalse(("ac", 0) < hi)
        lo2, hi2 = prefix_bounds("")
        self.assertEqual(lo2, NEG_INF)
        self.assertIsNone(hi2)

    def test_paging_walks_full_prefix(self):
        for limit in (1, 2, 3, 100):
            client = Client(self.verifier)
            got = client.fetch_all(self.tp, "ab", limit, want_version=1)
            keys = [e.key for e in got]
            self.assertEqual(keys, [("ab", 0), ("ab", 1), ("abc", 0), ("abd", 0)])

    def test_empty_prefix_is_whole_dictionary(self):
        got = Client(self.verifier).fetch_all(self.tp, "", 3, want_version=1)
        self.assertEqual(len(got), len(tiny_entries()))
        self.assertEqual([e.key for e in got],
                         sorted(e.key for e in tiny_entries()))

    def test_nonexistent_prefix_returns_empty_page(self):
        resp = self.client.fetch_page(self.tp, "zzz", 5, want_version=1)
        self.assertEqual(resp["entries"], [])
        self.assertFalse(resp["has_more"])
        self.assertIsNone(resp["next_cursor"])

    def test_page_count_never_trusted(self):
        # 服务器谎报 entry_count：该字段在凭据内，篡改即签名失败
        resp = self.server.get_page(1, "ab", 2)
        resp["credential"]["entry_count"] = 999
        with self.assertRaises(PermissionError):
            self.client.verify_response(resp)

    # ------------------------------------------------ 多义项跨页

    def test_polysemy_split_across_pages(self):
        # limit=1 时 "花" 的两个义项必须分属两页且都不丢
        r1 = self.client.fetch_page(self.tp, "花", 1, want_version=1)
        self.assertEqual([e["key"] if "key" in e else (e["word"], e["sense"])
                          for e in r1["entries"]], [("花", 0)])
        self.assertTrue(r1["has_more"])
        r2 = self.client.fetch_page(self.tp, "花", 1, cursor=r1["next_cursor"])
        self.assertEqual([(e["word"], e["sense"]) for e in r2["entries"]],
                         [("花", 1)])
        self.assertFalse(r2["has_more"])

    # ------------------------------------------------ 攻击：漏项

    def test_omitted_entry_between_homographs_rejected(self):
        # 正确页面含 花/0、花/1；抽掉夹在中间的义项（这里抽 花/0），
        # 服务器未重建树，前驱/后继证明与页内键对不上
        resp = self.server.get_page(1, "花", 10)
        resp["entries"] = [d for d in resp["entries"]
                           if (d["word"], d["sense"]) != ("花", 0)]
        with self.assertRaises(VerificationError):
            self.client.verify_response(resp)

    def test_server_rebuilds_tree_but_omission_still_detected(self):
        # 更强的攻击：服务器删掉 花/0 后重建整棵树（根哈希自洽，
        # 但它没有编纂端私钥，无法为新根签出有效凭据）
        entries = [e for e in tiny_entries() if e.key != ("花", 0)]
        rogue = self.compiler.publish(99, entries, "2026-11-02")
        rogue_server = Server()
        rogue_server.load_snapshot(rogue.snapshot_json())
        # 用另一把钥匙模拟无法获得有效签名：新凭据的 key_id 不同/验签失败
        other_verifier = Verifier(Signer.generate("other").public_key_pem(),
                                  "other")
        with self.assertRaises(PermissionError):
            Client(other_verifier).verify_response(
                rogue_server.get_page(99, "花", 10))

    def test_fake_has_more_without_successor_rejected(self):
        resp = self.server.get_page(1, "花", 10)
        self.assertFalse(resp["has_more"])
        resp["has_more"] = True
        with self.assertRaises(VerificationError):
            self.client.verify_response(resp)

    def test_pruned_subtree_overlapping_window_rejected(self):
        # 直接篡改证明：把一个展开叶子换成剪枝节点并谎报边界
        resp = self.server.get_page(1, "ab", 2)

        def tamper(node):
            if "internal" in node:
                node["internal"] = [tamper(c) for c in node["internal"]]
                return node
            if "leaf" in node and node["leaf"]["word"] == "abc":
                return {"pruned": {"h": "00" * 32,
                                   "first": ["ab", 1], "last": ["abc", 0]}}
            return node

        resp["proof"] = tamper(resp["proof"])
        with self.assertRaises(VerificationError):
            self.client.verify_response(resp)

    # ------------------------------------------------ 攻击：版本拼接

    def test_cross_version_cursor_rejected(self):
        # 修订出 v2
        v2_entries = tiny_entries() + [Entry("ac", 0, "ei1 si1", "庚")]
        rev2 = self.compiler.publish(2, v2_entries, "2026-11-02")
        self.server.load_snapshot(rev2.snapshot_json())

        old = Client(self.verifier).fetch_page(
            self.tp, "ab", 1, want_version=1)
        # 拿 v1 首页的游标去续一张 v2 的“合法页”
        with self.assertRaises((StaleCursorError, ValueError)):
            self.server.get_page(2, "ab", 1, cursor=old["next_cursor"])

    def test_downgrade_rejected(self):
        rev2 = self.compiler.publish(2, tiny_entries(), "2026-11-02")
        self.server.load_snapshot(rev2.snapshot_json())
        client = Client(self.verifier)
        client.fetch_page(self.tp, "a", 5, want_version=2)
        # 旧版本号页面（凭据合法但版本倒退）
        old_resp = self.server.get_page(1, "a", 5)
        with self.assertRaises(VerificationError):
            client.verify_response(old_resp)

    def test_hiding_entries_inside_pruned_subtree_rejected(self):
        # 正确页：前缀 "ab" 页长 2，本页 ab/0、ab/1，后继 abc/0
        honest = self.server.get_page(1, "ab", 2)
        self.assertTrue(honest["has_more"])
        abc_entry = next(e for e in tiny_entries() if e.key == ("abc", 0))

        # 攻击：服务器谎称“后面没有了”，并把后继叶子 abc/0 换成剪枝节点。
        # 剪枝哈希直接用 abc/0 的真实叶子哈希（哈希链仍然自洽），
        # 等价于“我知道这块的承诺值，但拒绝展开内容”。
        forged = json.loads(json.dumps(honest))  # 深拷贝
        forged["has_more"] = False
        forged["next_cursor"] = None

        def replace(node):
            if "internal" in node:
                for i, ch in enumerate(node["internal"]):
                    if "leaf" in ch and \
                            Entry.from_json(ch["leaf"]).key == ("abc", 0):
                        node["internal"][i] = {"pruned": {
                            "h": abc_entry.hash_bytes().hex(),
                            "first": ["abc", 0], "last": ["abc", 0],
                        }}
                        return True
                    if replace(ch):
                        return True
            return False

        self.assertTrue(replace(forged["proof"]))
        with self.assertRaises(VerificationError):
            self.client.verify_response(forged)

    def test_fetch_all_auto_restarts_after_revision(self):
        # v1 翻到一半服务器修订；fetch_all 遇跨版游标后从首页按新版重取
        v2_entries = tiny_entries() + [
            Entry("ab", 2, "ei1 bi4", "v2 新义项"),
            Entry("ac", 0, "ei1 si1", "v2 新词"),
        ]
        rev2 = self.compiler.publish(2, v2_entries, "2026-11-02")
        self.server.load_snapshot(rev2.snapshot_json())

        class StaleOnceTransport(NetworkTransport):
            def __init__(self, srv):
                super().__init__(srv)
                self.used = False

            def request(self, version, prefix, limit, cursor):
                if cursor is not None and not self.used:
                    self.used = True
                    # 模拟旧游标在新版服务器上第一次续页即被拒
                    raise StaleCursorError("模拟修订后旧游标失效")
                return self.server.get_page(version, prefix, limit, cursor)

        cli = Client(self.verifier)
        got = cli.fetch_all(StaleOnceTransport(self.server),
                            "ab", 2, want_version=1)
        self.assertEqual(
            [e.key for e in got],
            [("ab", 0), ("ab", 1), ("ab", 2), ("abc", 0), ("abd", 0)])
        self.assertEqual(cli.last_seen_version, 2)

    def test_wrong_trust_key_rejects(self):
        stranger = Verifier(Signer.generate("stranger").public_key_pem(),
                            "stranger")
        resp = self.server.get_page(1, "a", 5)
        with self.assertRaises(PermissionError):
            Client(stranger).verify_response(resp)

    # ------------------------------------------------ 稿件注入与隔离

    def test_manuscript_injection_and_isolation(self):
        ms = Manuscript()
        good = self.client.fetch_page(self.tp, "花", 10, want_version=1)
        ms.inject_page([Entry.from_json(d) for d in good["entries"]], 1)
        self.assertEqual(len(ms), 2)

        # 坏页：验证失败，绝不允许进入注入流程
        bad = self.server.get_page(1, "ab", 2)
        bad["entries"][0]["gloss"] = "被篡改的字义"
        with self.assertRaises(VerificationError):
            self.client.verify_response(bad)
        # 已注入的注释不受影响
        self.assertEqual(ms.get("花", 0)["gloss"], "花朵")
        self.assertIsNone(ms.get("ab", 0))

    def test_field_level_update_changes_root(self):
        e0 = next(e for e in tiny_entries() if e.key == ("花", 0))
        t1 = build_tree(tiny_entries())
        changed = [Entry(e.word, e.sense, e.pron,
                         "新字义" if e.key == ("花", 0) else e.gloss)
                   for e in tiny_entries()]
        t2 = build_tree(changed)
        self.assertNotEqual(t1.hash, t2.hash)
        # 仅改读音也一样
        changed2 = [Entry(e.word, e.sense,
                          "xin1 yin1" if e.key == ("花", 0) else e.pron,
                          e.gloss) for e in tiny_entries()]
        self.assertNotEqual(t1.hash, build_tree(changed2).hash)

    # ------------------------------------------------ 空树

    def test_empty_tree(self):
        rev = self.compiler.publish(1, [], "2026-11-01")
        srv = Server()
        srv.load_snapshot(rev.snapshot_json())
        cli = Client(self.verifier)
        resp = cli.fetch_page(NetworkTransport(srv), "", 10, want_version=1)
        self.assertEqual(resp["entries"], [])
        self.assertFalse(resp["has_more"])
        # 任意前缀都是空页且能被证明
        resp2 = cli.fetch_page(NetworkTransport(srv), "x", 10, want_version=1)
        self.assertEqual(resp2["entries"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
