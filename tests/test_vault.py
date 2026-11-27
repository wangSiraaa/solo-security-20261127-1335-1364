"""Tests for the paired fair-exchange vault.

Run with:  python3 -m unittest discover -s tests -v
"""

import os
import shutil
import tempfile
import unittest

from fair_exchange.crypto import canonical_json, sha256_hex
from fair_exchange.scenarios import replay_output
from fair_exchange.session import Session
from fair_exchange.vault import (
    ABORTED, COLLECTING, CONFIRMATION, DELEGATION, EARLY, EFFECTIVE,
    EXPIRE_ABORT, EXPIRE_FALLBACK_LOCK, HOLD, PENDING, VaultError,
)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fx-test-")
        self.s = Session(self.root)
        self.jia = self.s.add_party("jia")
        self.yi = self.s.add_party("yi")
        self.s.open_order("o", "jia", "yi", retention_ticks=30, tick=0)

    def tearDown(self):
        self.s.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def both_deposit(self, jia_text=b"JIA v1", yi_text=b"YI v1", tick=1):
        ra = self.s.deposit("o", "jia", jia_text, tick)
        rb = self.s.deposit("o", "yi", yi_text, tick)
        return ra, rb

    def pair(self):
        st = self.s.status("o")
        return st["a_version"], st["b_version"]


class TestPairedEffectiveness(Base):
    def test_nobody_reads_before_both_committed(self):
        self.s.deposit("o", "jia", b"JIA v1", tick=1)  # only jia deposited
        st = self.s.status("o")
        self.assertEqual(st["state"], COLLECTING)
        self.assertEqual(st["tokens"], [])
        # also no claim path exists at all while collecting
        with self.assertRaises(VaultError) as cm:
            self.s.vault.claim("o", "jia", "bogus", tick=2)
        self.assertEqual(cm.exception.code, "NOT_EFFECTIVE")

    def test_both_confirm_settles_same_tick_with_tokens_for_each(self):
        ra, rb = self.both_deposit()
        self.s.confirm("o", "jia", tick=2)
        self.assertEqual(self.s.status("o")["state"], PENDING)
        self.s.confirm("o", "yi", tick=2)
        st = self.s.status("o")
        self.assertEqual(st["state"], EFFECTIVE)
        self.assertEqual(st["effective_tick"], 2)
        parties = {t["party"] for t in st["tokens"]}
        self.assertEqual(parties, {"jia", "yi"})
        # rights acquired at the same tick; each token reads the *other* version
        for t in st["tokens"]:
            if t["party"] == "jia":
                self.assertEqual(t["read_ver"], rb["ver"])
            else:
                self.assertEqual(t["read_ver"], ra["ver"])

    def test_rights_acquired_interleaved_still_one_tick(self):
        # A submits delegation, then B confirmation; only the *pair* settles
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=3)
        self.assertEqual(self.s.status("o")["state"], PENDING)
        self.s.delegate("o", "yi", tick=3)
        st = self.s.status("o")
        self.assertEqual(st["state"], EFFECTIVE)
        # two RIGHT_EFFECTIVE ledger rows share the settle tick
        rows = [e for e in self.s.ledger("o") if e["event"] == "RIGHT_EFFECTIVE"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["tick"] for r in rows}, {3})
        self.assertEqual({r["party"] for r in rows}, {"jia", "yi"})

    def test_only_one_side_cannot_read(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)  # only jia
        st = self.s.status("o")
        self.assertEqual(st["state"], PENDING)
        with self.assertRaises(VaultError) as cm:
            self.s.claim_token("o", "jia")
        self.assertEqual(cm.exception.code, "NO_TOKEN")
        # and the vault refuses a raw claim while not effective
        with self.assertRaises(VaultError) as cm:
            self.s.vault.claim("o", "jia", "bogus", tick=2)
        self.assertEqual(cm.exception.code, "NOT_EFFECTIVE")


class TestReuploadCannotRideOldAuth(Base):
    def test_new_digest_makes_old_delegation_inert(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        # jia gives a delegation that names the *current* (a_ver, b_ver) pair
        self.s.delegate("o", "jia", tick=2)
        # jia re-uploads a different original (a new digest) -> supersedes a_ver
        new = self.s.deposit("o", "jia", b"JIA CHANGED", tick=3)
        self.assertNotEqual(new["digest"], ra["digest"])
        # yi gives a delegation, but jia's old one must *not* apply to the new pair
        self.s.delegate("o", "yi", tick=4)
        st = self.s.status("o")
        self.assertEqual(st["state"], PENDING,
                         "old delegation must not settle a re-uploaded pair")
        # only after jia re-delegates on the new pair does it settle
        self.s.delegate("o", "jia", tick=5)
        self.assertEqual(self.s.status("o")["state"], EFFECTIVE)

    def test_digest_override_does_not_release_old_auth(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)
        self.s.delegate("o", "yi", tick=2)
        # old pair is still in place here; now simulate tamper *before* settle
        # by using a fresh order to keep both sides' auths pending.
        self.s.close()
        self.s = Session(self.root)
        self.s.open_order("t", "jia", "yi", retention_ticks=30, tick=10)
        ra2 = self.s.deposit("t", "jia", b"JIA v1", tick=11)
        rb2 = self.s.deposit("t", "yi", b"YI v1", tick=11)
        self.s.vault.set_policy("t", EARLY, tick=12)
        self.s.delegate("t", "jia", tick=12)
        self.s.delegate("t", "yi", tick=12)   # settles immediately under EARLY
        st = self.s.status("t")
        self.assertEqual(st["state"], EFFECTIVE)
        # overwrite jia's effective version digest -> token/ciphertext/digest diverge
        with self.assertRaises(VaultError) as cm:
            self.s.tamper_digest("t", "jia", sha256_hex(b"UNSEEN"), tick=13)
        self.assertEqual(cm.exception.code, "ALREADY_SETTLED")

    def test_tamper_before_settle_blocks_matching(self):
        # build a PENDING order with a delegation, tamper the digest in place,
        # then the second delegation must not settle against a mismatched pair.
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)
        self.s.tamper_digest("o", "jia", sha256_hex(b"FORGED"), tick=3)
        # yi's delegation built on the tampered digest would *match* stored bytes
        # but jia's original delegation (old digest) can no longer match; the
        # pair must not settle.
        self.s.delegate("o", "yi", tick=4)
        st = self.s.status("o")
        self.assertEqual(st["state"], PENDING)
        self.assertEqual(st["a_version"]["digest"], sha256_hex(b"FORGED"))


class TestStaleCredentialCannotBackRead(Base):
    def test_expired_token_cannot_record_read(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)
        self.s.delegate("o", "yi", tick=2)
        st = self.s.status("o")
        self.assertEqual(st["state"], EFFECTIVE)
        retained_until = st["retained_until"]
        tok = self.s.claim_token("o", "yi")
        # yi's terminal signs a receipt, but it's one tick past expiry
        with self.assertRaises(VaultError) as cm:
            self.s.record_read("o", "yi", tok["id"], tok["read_ver"], tok["read_digest"],
                               b"YI v1", tick=retained_until + 1)
        self.assertEqual(cm.exception.code, "TOKEN_EXPIRED")
        self.assertEqual(self.s.vault.opens("o"), [],
                         "no read may be recorded from an expired credential")
        # the ledger must show the rejection explicitly
        rej = [e for e in self.s.ledger("o") if e["event"] == "OPEN_REJECTED"]
        self.assertTrue(rej)

    def test_read_within_window_records_and_is_attributed(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)
        self.s.delegate("o", "yi", tick=2)
        st = self.s.status("o")
        tok = self.s.claim_token("o", "jia")
        rec = self.s.claim_and_open("o", "jia", tick=st["retained_until"])
        self.assertEqual(rec["plaintext"], b"YI v1")
        opens = self.s.vault.opens("o")
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0]["party"], "jia")
        self.assertEqual(opens[0]["read_ver"], rb["ver"])
        # ledger attributes the open to jia reading yi's version
        opened = [e for e in self.s.ledger("o") if e["event"] == "OPENED"]
        self.assertEqual(opened[0]["party"], "jia")
        self.assertEqual(opened[0]["ver"], rb["ver"])

    def test_restore_same_pair_then_stale_report_rejected(self):
        # "restore the same-version double packet, and let only yi's terminal
        # report a receipt from an expired claim credential" -> must not record.
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)
        self.s.delegate("o", "yi", tick=2)
        st = self.s.status("o")
        self.assertEqual(st["state"], EFFECTIVE)
        tok = self.s.claim_token("o", "yi")
        # even with the pair fully intact (same version, same digest), an
        # expired credential cannot make a read of yi's *own* counterparty count.
        with self.assertRaises(VaultError) as cm:
            self.s.record_read("o", "yi", tok["id"], tok["read_ver"], tok["read_digest"],
                               b"JIA v1", tick=st["retained_until"] + 1)
        self.assertEqual(cm.exception.code, "TOKEN_EXPIRED")
        self.assertEqual(self.s.vault.opens("o"), [])


class TestReplayScenarios(Base):
    def test_yi_edit_matrix(self):
        out = replay_output(os.path.join(self.root, "replay"))
        a = out["A_yi_returns_to_edit_one_char"]
        early = a["EARLY_LOCK"]
        self.assertEqual(early["state_at_return"], EFFECTIVE)
        self.assertTrue(early["jia_read_immediately"])
        self.assertEqual(early["jia_saw"], "YI original, v1")
        self.assertEqual(early["yi_edit"], "REJECTED:ALREADY_SETTLED")
        hold = a["HOLD_FOR_CONFIRMATION"]
        self.assertEqual(hold["state_at_return"], PENDING)
        self.assertFalse(hold["jia_read_immediately"])
        self.assertTrue(hold["jia_during_hold"].startswith("WAITED"))
        self.assertEqual(hold["yi_edit"], "ALLOWED")
        self.assertEqual(hold["state_after_revise"], EFFECTIVE)
        self.assertNotEqual(hold["old_yi_digest"], hold["new_yi_digest"])

    def test_yi_end_matrix(self):
        out = replay_output(os.path.join(self.root, "replay2"))
        b = out["B_yi_returns_only_at_retention_end"]
        e = b["EARLY_LOCK"]
        self.assertTrue(e["yi_read_at_last_valid_tick"]["opened"])
        self.assertTrue(e["claim_one_tick_after"].startswith("REJECTED"))
        ab = b["HOLD_FOR_CONFIRMATION_ABORT"]
        self.assertEqual(ab["state_at_hold_expiry"], ABORTED)
        self.assertEqual(ab["tokens"], [])
        fb = b["HOLD_FOR_CONFIRMATION_FALLBACK_LOCK"]
        self.assertEqual(fb["state_at_hold_expiry"], EFFECTIVE)
        self.assertTrue(fb["yi_read_at_last_valid_tick"]["opened"])

    def test_both_online_confirm_is_mandatory(self):
        out = replay_output(os.path.join(self.root, "replay3"))
        mand = out["mandatory_both_online_confirm"]
        for policy in ("NONE", "HOLD_FOR_CONFIRMATION"):
            self.assertEqual(mand[policy]["state"], EFFECTIVE)
            self.assertEqual(mand[policy]["tokens"], 2)


class TestRestartAndWithdraw(Base):
    def test_withdraw_before_effectiveness(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)
        self.s.withdraw("o", "yi", tick=3)
        st = self.s.status("o")
        self.assertEqual(st["state"], COLLECTING)
        self.assertIsNone(st["b_version"])
        # jia's delegation named the withdrawn pair and must have been revoked
        revoked = [e for e in self.s.ledger("o") if e["event"] == "AUTH_REVOKED"]
        self.assertTrue(revoked)

    def test_cannot_withdraw_after_effectiveness(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)
        self.s.delegate("o", "yi", tick=2)
        with self.assertRaises(VaultError) as cm:
            self.s.withdraw("o", "yi", tick=3)
        self.assertEqual(cm.exception.code, "ALREADY_SETTLED")
        self.assertEqual(self.s.status("o")["state"], EFFECTIVE)

    def test_restart_preserves_state_and_can_resume(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)
        self.s.close()
        # reopen from disk
        self.s = Session(self.root)
        st = self.s.status("o")
        self.assertEqual(st["state"], PENDING)
        self.assertEqual(st["a_version"]["digest"], ra["digest"])
        # yi's delegation completes the pair after restart
        self.s.delegate("o", "yi", tick=3)
        self.assertEqual(self.s.status("o")["state"], EFFECTIVE)
        # and the token materially lets jia read yi's original
        rec = self.s.claim_and_open("o", "jia", tick=3)
        self.assertEqual(rec["plaintext"], b"YI v1")

    def test_ledger_separates_rights_from_reads(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)
        self.s.delegate("o", "yi", tick=2)
        self.s.claim_and_open("o", "yi", tick=3)
        ev = [e["event"] for e in self.s.ledger("o")]
        self.assertIn("PAIR_EFFECTIVE", ev)
        self.assertIn("RIGHT_EFFECTIVE", ev)
        self.assertIn("TOKENS_ISSUED", ev)
        self.assertIn("OPENED", ev)
        # right acquired at 2, open at 3 -- distinct ticks/events
        rights = [e for e in self.s.ledger("o") if e["event"] == "RIGHT_EFFECTIVE"]
        opens = [e for e in self.s.ledger("o") if e["event"] == "OPENED"]
        self.assertEqual({e["tick"] for e in rights}, {2})
        self.assertEqual({e["tick"] for e in opens}, {3})
        self.assertEqual(opens[0]["party"], "yi")


class TestHoldPolicy(Base):
    def test_fallback_lock_on_expiry_when_both_delegations_live(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", HOLD, tick=2, hold_ticks=5,
                                expiry_action=EXPIRE_FALLBACK_LOCK)
        self.s.delegate("o", "jia", tick=2)
        self.s.delegate("o", "yi", tick=2)
        self.assertEqual(self.s.status("o")["state"], PENDING)
        self.s.advance("o", tick=7)
        st = self.s.status("o")
        self.assertEqual(st["state"], EFFECTIVE)
        self.assertEqual(st["effective_tick"], 7)

    def test_abort_on_expiry_without_confirmation(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", HOLD, tick=2, hold_ticks=5,
                                expiry_action=EXPIRE_ABORT)
        self.s.delegate("o", "jia", tick=2)
        self.s.delegate("o", "yi", tick=2)
        self.assertEqual(self.s.status("o")["state"], PENDING)
        self.s.advance("o", tick=7)
        self.assertEqual(self.s.status("o")["state"], ABORTED)
        self.assertEqual(self.s.status("o")["tokens"], [])

    def test_online_both_confirming_mandatory_settle_under_hold(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", HOLD, tick=2, hold_ticks=50,
                                expiry_action=EXPIRE_ABORT)
        self.s.confirm("o", "jia", tick=3)
        self.s.confirm("o", "yi", tick=3)
        self.assertEqual(self.s.status("o")["state"], EFFECTIVE)


class TestVaultCannotForgeReceipt(Base):
    def test_vault_lacks_private_keys(self):
        # the cabinet stores only public parameters; it cannot sign a receipt
        pub = self.s.public_key("jia")
        self.assertNotIn("d", pub)
        self.assertIn("n", pub)

    def test_forged_receipt_rejected(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)
        self.s.delegate("o", "yi", tick=2)
        tok = self.s.claim_token("o", "jia")
        fake = {"payload": {"order_id": "o", "party": "jia", "token_id": tok["id"],
                            "read_ver": tok["read_ver"],
                            "read_digest": tok["read_digest"],
                            "plaintext_digest": tok["read_digest"]},
                "sig": "00" * 128}
        with self.assertRaises(VaultError) as cm:
            self.s.vault.report_open("o", "jia", tok["id"], tok["read_ver"],
                                     tok["read_digest"], fake, tick=3)
        self.assertEqual(cm.exception.code, "BAD_RECEIPT_SIGNATURE")


class TestExactAdversarialWording(Base):
    def test_swap_jia_original_to_unagreed_digest_old_auth_never_effective(self):
        """「把甲已入柜原件换为乙尚未同意的新摘要，不能让旧授权生效」:
        swap 甲's deposited original for a *different* packet (new digest) that
        乙 never agreed to. 乙's old delegation (naming the old digests) must not
        settle the new pair; only after 乙 re-agrees does it settle, and then the
        content opened is the new packet."""
        # Use EARLY so a live *pair* of delegations would settle; this makes the
        # "old auth must not carry over" property observable.
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "yi", tick=2)     # 乙 agrees to the ORIGINAL pair
        # 甲 swaps its original for a new packet (new digest) 乙 never agreed to
        swapped = self.s.deposit("o", "jia", b"JIA SWAPPED", tick=3)
        self.assertNotEqual(swapped["digest"], ra["digest"])
        # 甲 declares a delegation on the new stored pair. 乙's old delegation
        # named the old jia digest, so it cannot match -> must stay PENDING.
        self.s.delegate("o", "jia", tick=4)
        st = self.s.status("o")
        self.assertEqual(st["state"], PENDING,
                         "乙's old consent was for the old digest; it cannot release the swap")
        self.assertEqual(st["a_version"]["digest"], swapped["digest"])
        # 乙 re-agrees on the new pair -> settles; opened content is the swap
        self.s.confirm("o", "yi", tick=5)
        self.s.confirm("o", "jia", tick=5)
        self.assertEqual(self.s.status("o")["state"], EFFECTIVE)
        rec = self.s.claim_and_open("o", "yi", tick=6)
        self.assertEqual(rec["read_ver"], st["a_version"]["id"])
        self.assertEqual(rec["plaintext"], b"JIA SWAPPED")

    def test_inplace_digest_tamper_keeps_old_auth_inert(self):
        """Same idea via an in-place digest tamper: no authorization that named
        the old digest may release the tampered entry."""
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)         # names the original digest
        new_digest = sha256_hex(b"JIA FORGED")
        self.s.tamper_digest("o", "jia", new_digest, tick=3)
        self.s.delegate("o", "yi", tick=4)          # names the tampered digest
        st = self.s.status("o")
        self.assertEqual(st["state"], PENDING,
                         "jia's old consent cannot pair with a tampered digest")
        self.assertEqual(st["a_version"]["digest"], new_digest)

    def test_restore_identical_pair_stale_credential_not_recorded(self):
        """「还原同版双包，仅让乙的终端回报来自过期领取凭据，就不能记它已读」:
        the pair is the *identical* version pair (untouched), and only 乙's
        terminal reports a read using an expired claim credential -> it must
        not be recorded as read."""
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)
        self.s.delegate("o", "yi", tick=2)
        st = self.s.status("o")
        self.assertEqual(st["state"], EFFECTIVE)
        # confirm the pair is intact and identical to what was deposited
        self.assertEqual(st["a_version"]["digest"], ra["digest"])
        self.assertEqual(st["b_version"]["digest"], rb["digest"])
        tok = self.s.claim_token("o", "yi")
        # 乙 signs a genuine receipt, but the claim credential is expired
        with self.assertRaises(VaultError) as cm:
            self.s.record_read("o", "yi", tok["id"], tok["read_ver"],
                               tok["read_digest"], b"JIA v1",
                               tick=st["retained_until"] + 1)
        self.assertEqual(cm.exception.code, "TOKEN_EXPIRED")
        # nothing recorded as read
        self.assertEqual(self.s.vault.opens("o"), [])
        # and the ledger shows the explicit rejection
        rej = [e for e in self.s.ledger("o") if e["event"] == "OPEN_REJECTED"]
        self.assertEqual(len(rej), 1)
        self.assertEqual(rej[0]["party"], "yi")


class TestRetentionExpiry(Base):
    def test_mark_expired_after_retention(self):
        ra, rb = self.both_deposit()
        self.s.vault.set_policy("o", EARLY, tick=2)
        self.s.delegate("o", "jia", tick=2)
        self.s.delegate("o", "yi", tick=2)
        st = self.s.status("o")
        ru = st["retained_until"]
        self.s.advance("o", tick=ru + 1)
        self.assertEqual(self.s.status("o")["state"], "EXPIRED")
        # a late claim is refused because the order expired
        tok = self.s.claim_token("o", "jia")
        with self.assertRaises(VaultError) as cm:
            self.s.vault.claim("o", "jia", tok["id"], tick=ru + 1)
        self.assertEqual(cm.exception.code, "NOT_EFFECTIVE")


if __name__ == "__main__":
    unittest.main()
