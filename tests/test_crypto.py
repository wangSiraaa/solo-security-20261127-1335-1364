"""Core cryptographic and custody tests."""

import unittest

from lottery.cabinets import (
    CabinetMissing,
    CabinetOffline,
    CabinetPair,
    CabinetReceipt,
    ReceiptMismatch,
)
from lottery.crypto import (
    commitment,
    compute_draw,
    digest,
    join_shares,
    order_key,
    original_payload,
    parse_original,
    promote_replacements,
    seal_digest,
    seal_payload,
    share_digest,
    split_secret,
)


class TestCrypto(unittest.TestCase):
    def test_canonical_digest_is_stable(self):
        a = {"b": 1, "a": [2, 3]}
        b = {"a": [2, 3], "b": 1}
        self.assertEqual(digest(a), digest(b))

    def test_commitment_binds_round_and_ticket(self):
        c = commitment("R1", "T1", "salt", "value")
        self.assertEqual(c, commitment("R1", "T1", "salt", "value"))
        self.assertNotEqual(c, commitment("R2", "T1", "salt", "value"))
        self.assertNotEqual(c, commitment("R1", "T2", "salt", "value"))
        self.assertNotEqual(c, commitment("R1", "T1", "salt", "other"))

    def test_compute_draw_is_deterministic_and_household_unique(self):
        roster = [
            {"ticket_id": "T1", "household_id": "H1"},
            {"ticket_id": "T2", "household_id": "H1"},
            {"ticket_id": "T3", "household_id": "H2"},
            {"ticket_id": "T4", "household_id": "H3"},
        ]
        reveals = {
            t["ticket_id"]: {"salt": f"s{t['ticket_id']}", "value": f"v{t['ticket_id']}"}
            for t in roster
        }
        seed1, order1, winners1 = compute_draw(roster, 2, "m", reveals)
        seed2, order2, winners2 = compute_draw(roster, 2, "m", reveals)
        self.assertEqual((seed1, order1, winners1), (seed2, order2, winners2))
        # two seats, but H1 may only occupy one of them
        hh = {t["ticket_id"]: t["household_id"] for t in roster}
        winner_hh = [hh[w] for w in winners1]
        self.assertEqual(len(winner_hh), len(set(winner_hh)))
        self.assertEqual(len(winners1), 2)
        # order covers every ticket exactly once
        self.assertEqual(sorted(order1), sorted(t["ticket_id"] for t in roster))

    def test_every_reveal_affects_the_seed(self):
        roster = [{"ticket_id": f"T{i}", "household_id": f"H{i}"} for i in range(3)]
        base = {t["ticket_id"]: {"salt": "s", "value": "v"} for t in roster}
        seeds = set()
        for flip in ["T0", "T1", "T2"]:
            r = {k: dict(v) for k, v in base.items()}
            r[flip]["value"] = "changed"
            seeds.add(compute_draw(roster, 2, "m", r)[0])
        self.assertEqual(len(seeds), 3, "each reveal must change the mixed seed")

    def test_order_key_depends_on_seed_and_ticket(self):
        self.assertNotEqual(order_key("s1", "T1"), order_key("s2", "T1"))
        self.assertNotEqual(order_key("s1", "T1"), order_key("s1", "T2"))

    def test_seal_payload_includes_cutoff(self):
        roster = [{"ticket_id": "T1", "household_id": "H1", "source": "escrow"}]
        p = seal_payload("R1", roster, 1, "m", "escrow")
        self.assertEqual(p["cutoff"], "two_cabinet_receipts")
        p2 = seal_payload("R1", roster, 1, "m", "opening")
        self.assertEqual(p2["cutoff"], "all_originals_present_or_postpone")

    def test_promote_replacements_is_ordered_and_household_safe(self):
        order = ["T1", "T2", "T3", "T4"]
        hh = {"T1": "H1", "T2": "H2", "T3": "H3", "T4": "H4"}
        # T1 absent; next eligible is T2
        promoted = promote_replacements(order, ["T1", "T3"], ["T1"], ["T2", "T3", "T4"], set(), hh)
        self.assertEqual(promoted, ["T2"])
        # if T2's household already seated, skip to T3? But T3 is seated -> T4
        promoted2 = promote_replacements(order, ["T1", "T3"], ["T1"], ["T2", "T3", "T4"], {"H2"}, hh)
        self.assertEqual(promoted2, ["T4"])
        # never picks a ticket that did not attend
        promoted3 = promote_replacements(order, ["T1", "T3"], ["T1"], ["T4"], set(), hh)
        self.assertEqual(promoted3, ["T4"])

    def test_split_and_join_secret(self):
        secret = original_payload("salt", "value")
        a, b = split_secret(secret)
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, secret)
        self.assertEqual(join_shares(a, b), secret)
        salt, value = parse_original(join_shares(a, b))
        self.assertEqual((salt, value), ("salt", "value"))

    def test_single_share_does_not_reveal_secret(self):
        secret = b"the-original-payload"
        a, b = split_secret(secret)
        self.assertNotEqual(a, secret)
        self.assertNotEqual(b, secret)


class TestCabinets(unittest.TestCase):
    def test_two_matching_receipts_release_same_original(self):
        pair = CabinetPair()
        payload = original_payload("s", "v")
        receipts = pair.deposit("R1", "T1", payload)
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[0].receipt_id, receipts[1].receipt_id)
        res = pair.retrieve("R1", "T1")
        self.assertEqual(res.original, payload)
        self.assertEqual(len(res.receipts), 2)

    def test_one_offline_cabinet_blocks_retrieval(self):
        pair = CabinetPair()
        pair.deposit("R1", "T1", b"secret")
        pair.set_online("cabinet-A", False)
        with self.assertRaises(CabinetOffline):
            pair.retrieve("R1", "T1")

    def test_single_cabinet_cannot_reconstruct(self):
        pair = CabinetPair()
        payload = original_payload("s", "v")
        pair.deposit("R1", "T1", payload)
        share, _rec = pair.a.handover("R1", "T1")
        self.assertNotEqual(share, payload)
        # the share alone does not parse as the original
        with self.assertRaises(Exception):
            parse_original(share)

    def test_missing_deposit_raises(self):
        pair = CabinetPair()
        with self.assertRaises(CabinetMissing):
            pair.retrieve("R1", "nope")

    def test_recombined_tamper_is_detected(self):
        pair = CabinetPair()
        pair.deposit("R1", "T1", b"secret")
        # corrupt one share's receipt so the pair mismatches
        pair.a._held[("R1", "T1")].receipt.receipt_id = "different"
        with self.assertRaises(ReceiptMismatch):
            pair.retrieve("R1", "T1")


if __name__ == "__main__":
    unittest.main()
