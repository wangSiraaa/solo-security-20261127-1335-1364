"""单元测试：门限数学、去重规则、交接状态机、代际与跨卷拒绝。

运行：python -m unittest archive_coop.tests.test_core -v
"""

import os
import shutil
import tempfile
import unittest

from .. import gf256, secretshare
from ..cabinet import IssuingCabinet
from ..policy import SITE_RECOVERY, SPLIT_SITE
from ..storage import Storage
from ..workbench import RecoveryError, Workbench


class TestGF256(unittest.TestCase):
    def test_tables_cover_full_cycle(self):
        # 生成元 3 在该不可约多项式下阶为 255
        self.assertEqual(len({gf256._LOG[i] for i in range(1, 256)}), 255)

    def test_mul_identity_and_inverse(self):
        for a in range(1, 256):
            self.assertEqual(gf256.mul(a, 1), a)
            self.assertEqual(gf256.mul(a, gf256.inv(a)), 1)

    def test_add_is_self_inverse(self):
        for a in range(256):
            self.assertEqual(gf256.add(a, a), 0)

    def test_known_product(self):
        # AES/Rijndael 域：0x57 * 0x83 = 0xc1
        self.assertEqual(gf256.mul(0x57, 0x83), 0xC1)

    def test_div(self):
        self.assertEqual(gf256.div(0xC1, 0x83), 0x57)


class TestShamir(unittest.TestCase):
    def test_roundtrip_at_threshold(self):
        secret = b"the quick brown fox" * 4
        shares = secretshare.split(secret, 3, [1, 2, 3, 4, 5])
        # 恰好门限份、任意挑 3 个份位都应还原
        for pick in ([1, 2, 3], [2, 4, 5], [1, 4, 5]):
            got = secretshare.combine([(i, shares[i]) for i in pick])
            self.assertEqual(got, secret)

    def test_below_threshold_fails_to_recover(self):
        secret = b"threshold-two-secret"
        shares = secretshare.split(secret, 2, [10, 20, 30])
        got = secretshare.combine([(10, shares[10])])
        # 单份插值出来的是常数项形状但几乎不可能等于真秘密
        self.assertNotEqual(got, secret)

    def test_duplicate_index_not_counted(self):
        secret = b"abcdefgh"
        shares = secretshare.split(secret, 2, [7, 8])
        # 同一个份位交两次，去重后只剩 1 份
        got = secretshare.combine([(7, shares[7]), (7, shares[7])])
        self.assertNotEqual(got, secret)

    def test_threshold_must_be_at_least_two(self):
        with self.assertRaises(ValueError):
            secretshare.split(b"x", 1, [1, 2])


class TestCabinetAndWorkbench(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="archtest_")
        self.storage = Storage(self.root)
        self.cab = IssuingCabinet(self.storage)
        self.bench = Workbench(self.storage, self.cab)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _env(self, vid, holder, gen=None):
        for env_id in self.storage.load_ledger()["custodians"][holder]["envelopes"]:
            env = self.storage.read_envelope(env_id)
            if env["volume_id"] == vid and (gen is None or env["generation"] == gen):
                return env
        raise KeyError(holder)

    def test_handover_state_machine(self):
        self.cab.seal_volume("T1", [(1, "页一"), (2, "页二")], SITE_RECOVERY)
        env = self._env("T1", "东甲")
        receipt = self.cab.start_handover(env["envelope_id"], "东甲", "东乙")
        # 在途：双方有效柜存都不含它
        held = {e["envelope_id"] for e in self.bench.present_envelopes("T1", ["东甲", "东乙"])}
        self.assertNotIn(env["envelope_id"], held)
        # 非接收方不能签收
        with self.assertRaises(PermissionError):
            self.cab.sign_handover(receipt["receipt_id"], "东甲")
        self.cab.sign_handover(receipt["receipt_id"], "东乙")
        held = {e["envelope_id"] for e in self.bench.present_envelopes("T1", ["东乙"])}
        self.assertIn(env["envelope_id"], held)
        # 回单已完成，不能再签
        with self.assertRaises(ValueError):
            self.cab.sign_handover(receipt["receipt_id"], "东乙")

    def test_cancelled_handover_returns_material(self):
        self.cab.seal_volume("T2", [(1, "页")], SITE_RECOVERY)
        env = self._env("T2", "西甲")
        r = self.cab.start_handover(env["envelope_id"], "西甲", "东甲")
        self.cab.cancel_handover(r["receipt_id"])
        held = {e["envelope_id"] for e in self.bench.present_envelopes("T2", ["西甲"])}
        self.assertIn(env["envelope_id"], held)

    def test_split_site_needs_three(self):
        self.cab.seal_volume("T3", [(1, "a"), (2, "b")], SPLIT_SITE)
        with self.assertRaises(RecoveryError):
            self.bench.recover("T3", ["东甲", "东乙"])           # 仅 2 份 < 3
        order = self.bench.recover("T3", ["东甲", "东乙", "西甲"])
        self.assertTrue(order["all_pages_recovered"])

    def test_old_generation_share_rejected(self):
        self.cab.seal_volume("T4", [(1, "a")], SITE_RECOVERY)
        ids = [self._env("T4", "东甲", 1)["envelope_id"],
               self._env("T4", "西甲", 1)["envelope_id"]]
        self.cab.reissue("T4", ids)
        old = self._env  # 旧份已移出柜存，直接扫目录取
        old_env = next(
            e for e in self._all_envs("T4") if e["generation"] == 1
        )
        with self.assertRaises(ValueError):
            self.cab.combine_for_recovery("T4", [old_env["envelope_id"],
                                                 self._env("T4", "东甲", 2)["envelope_id"]])

    def _all_envs(self, vid):
        d = os.path.join(self.root, "envelopes")
        out = []
        for name in os.listdir(d):
            if name.endswith(".json"):
                out.append(self.storage.read_envelope(name[:-5]))
        return [e for e in out if e["volume_id"] == vid]

    def test_recovery_order_persists_across_instances(self):
        self.cab.seal_volume("T5", [(1, "持久化页")], SITE_RECOVERY)
        order = self.bench.recover("T5", ["东乙", "西乙"])
        # 模拟重开程序：全新对象
        storage2 = Storage(self.root)
        cab2 = IssuingCabinet(storage2)
        bench2 = Workbench(storage2, cab2)
        again = bench2.get_recovery_order(order["order_id"])
        self.assertEqual(again["delivered_pages"][0]["text"], "持久化页")


if __name__ == "__main__":
    unittest.main()
