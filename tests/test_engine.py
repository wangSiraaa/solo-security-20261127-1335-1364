"""Behavioural tests for synth_ime, driven entirely by synthetic words.

Run with:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from synth_ime import corpus
from synth_ime.engine import (
    AuthError,
    Device,
    JsonStore,
    MemoryStore,
    ReplayError,
)

A = "a" * 64
B = "b" * 64
C = "c" * 64


def device(**kw):
    return Device(MemoryStore(), **kw)


class SentenceStartCompleteness(unittest.TestCase):
    def test_sentence_start_candidates_come_from_learned_sentences(self):
        d = device()
        d.learn(A, ["bra ka lo", "bra zu mi"])
        self.assertEqual(d.suggest(""), ["bra"])
        self.assertIn("ka", d.suggest("bra "))
        self.assertIn("zu", d.suggest("bra "))

    def test_new_sentence_start_appears_after_learning(self):
        d = device()
        d.learn(A, ["bra ka lo"])
        self.assertEqual(d.suggest(""), ["bra"])
        d.learn(B, ["nu po ta"])
        self.assertEqual(d.suggest(""), ["bra", "nu"])

    def test_partial_word_completion(self):
        d = device()
        d.learn(A, ["bra kla lo", "bra klo mi", "bra zu po"])
        self.assertEqual(d.suggest("bra kl"), ["kla", "klo"])
        self.assertEqual(d.suggest("bra k"), ["kla", "klo"])
        self.assertEqual(d.suggest("bra z"), ["zu"])


class SharedWordSurvivesExit(unittest.TestCase):
    def test_word_taught_by_both_survives_a_exit(self):
        d = device()
        d.learn(A, ["bra ka lo", "bra ka mi"])
        d.learn(B, ["bra ka zu"])
        self.assertIn("ka", d.suggest("bra "))
        d.exit(A)
        self.assertIn("ka", d.suggest("bra "))   # B still teaches it
        self.assertNotIn("mi", d.suggest("bra "))  # A-only continuation gone

    def test_matches_never_had_A_baseline_exactly(self):
        both = device()
        both.learn(A, ["bra ka lo", "bra zu mi", "kla po ne"])
        both.learn(B, ["bra ka zu", "nu po ta"])
        after = device()
        after.learn(B, ["bra ka zu", "nu po ta"])
        both.exit(A)
        for prefix in ("", "bra ", "bra k", "nu ", "po ", "kla ", "bra kla"):
            self.assertEqual(both.suggest(prefix), after.suggest(prefix), prefix)
            self.assertEqual(both.counts_for(prefix), after.counts_for(prefix), prefix)
            self.assertEqual(both.chain(prefix, 4), after.chain(prefix, 4), prefix)
        self.assertEqual(both.vocabulary(), after.vocabulary())

    def test_exit_by_member_id_without_token(self):
        # Family member already walked off with their library: still revocable.
        d = device()
        mid = d.join(A)
        d.learn(A, ["bra ka lo"])
        d.learn(B, ["bra ka zu"])
        d.revoke(mid)  # no token needed
        self.assertEqual(d.suggest("bra "), ["ka"])
        self.assertNotIn("mi", d.suggest("bra "))


class ReteachTriggersDisappearance(unittest.TestCase):
    def test_change_Bs_learning_removes_word_with_no_other_source(self):
        d = device()
        d.learn(A, ["bra ka lo"])
        d.learn(B, ["bra ka zu"])
        d.exit(A)
        self.assertEqual(d.suggest("bra "), ["ka"])
        d.replace_sentence(B, "bra ka zu", "bra nu zu")
        self.assertEqual(d.suggest("bra "), ["nu"])
        self.assertNotIn("ka", d.suggest("bra "))
        self.assertNotIn("ka", d.chain("bra ", 3))

    def test_replacing_only_own_copy_keeps_other_members_copy(self):
        d = device()
        d.learn(A, ["bra ka lo"])
        d.learn(B, ["bra ka zu"])
        d.replace_sentence(B, "bra ka zu", "bra nu zu")
        self.assertIn("ka", d.suggest("bra "))
        self.assertIn("nu", d.suggest("bra "))


class OrderingLeakTests(unittest.TestCase):
    def test_suggestion_order_independent_of_member_and_teach_order(self):
        sents = ["bra ka lo", "bra ka mi", "bra zu po", "bra zu ta"]
        d1 = device()
        for s in sents:
            d1.learn(A, [s])
        d2 = device()
        for s in reversed(sents):
            d2.learn(B, [s])
        for prefix in ("", "bra ", "bra z"):
            self.assertEqual(d1.suggest(prefix), d2.suggest(prefix))

    def test_tie_break_is_content_derived_not_insertion(self):
        d = device()
        d.learn(A, ["bra zzz lo"])
        d.learn(A, ["bra aaa lo"])
        d2 = device()
        d2.learn(B, ["bra aaa lo"])
        d2.learn(B, ["bra zzz lo"])
        self.assertEqual(d.suggest("bra "), d2.suggest("bra "))

    def test_order_survives_restart(self):
        path = tempfile.mktemp(suffix=".json")
        try:
            d = Device(JsonStore(path))
            d.learn(A, ["bra ka lo", "bra zu mi"])
            before = d.suggest("bra ")
            d2 = Device(JsonStore(path))
            self.assertEqual(d2.suggest("bra "), before)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_suggestions_identical_regardless_of_who_else_taught(self):
        # Same final counts, different authorship split: identical order.
        d1 = device()
        d1.learn(A, ["bra ka lo", "bra zu po"])
        d2 = device()
        d2.learn(A, ["bra ka lo"])
        d2.learn(B, ["bra zu po"])
        self.assertEqual(d1.suggest("bra "), d2.suggest("bra "))


class RestartAndIndependence(unittest.TestCase):
    def test_reboot_does_not_resurrect_withdrawn_text(self):
        path = tempfile.mktemp(suffix=".json")
        try:
            d = Device(JsonStore(path))
            d.learn(A, ["bra secretlo x"])
            d.exit(A)
            d2 = Device(JsonStore(path))
            self.assertEqual(d2.suggest("bra "), [])
            with open(path, "r", encoding="utf-8") as fh:
                blob = fh.read()
            self.assertNotIn("secretlo", blob)
            # Even with the seal opened, no withdrawn sentence remains anywhere.
            self.assertNotIn("secretlo", json.dumps(d2.state))
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_rejoin_after_exit_starts_clean(self):
        d = device()
        d.learn(A, ["bra ka lo"])
        d.exit(A)
        d.learn(A, ["bra nu po"])
        self.assertEqual(d.suggest("bra "), ["nu"])
        self.assertNotIn("ka", d.suggest("bra "))

    def test_repeated_exit_is_idempotent(self):
        d = device()
        d.learn(A, ["bra ka lo"])
        self.assertEqual(d.exit(A), "revoked")
        self.assertEqual(d.exit(A), "already-revoked")
        self.assertEqual(d.revoke(d.member_id(B)), "already-revoked")
        self.assertEqual(d.suggest("bra "), [])

    def test_counts_and_receipts_are_sealed_on_disk(self):
        d = device()
        d.learn(A, ["bra ka lo"])
        blob = json.dumps(d.state)
        # The merged model and the receipt are both sealed, not plaintext.
        self.assertNotIn("bra", blob)
        self.assertNotIn("ka", blob)

    def test_member_sentences_only_via_device_key(self):
        path = tempfile.mktemp(suffix=".json")
        try:
            d = Device(JsonStore(path))
            mid = d.join(A)
            d.learn(A, ["bra ka lo"])
            # A fresh device loading the same file can decrypt receipts with the
            # stored device key (internal introspection), but the *file alone*
            # reveals no plaintext to a casual reader.
            d2 = Device(JsonStore(path))
            self.assertEqual(d2.member_sentences(mid), ["bra ka lo"])
        finally:
            if os.path.exists(path):
                os.unlink(path)


class BackupRestoreAndReplay(unittest.TestCase):
    def test_backup_restore_roundtrip_preserves_counts(self):
        d = device()
        d.learn(A, ["bra ka lo", "bra ka mi"])
        bundle = d.member_backup(A)
        d2 = device()
        d2.member_restore(A, bundle)
        self.assertEqual(d2.counts_for("bra "), d.counts_for("bra "))
        self.assertEqual(d2.suggest("bra "), ["ka"])

    def test_backup_is_not_readable_without_token(self):
        d = device()
        d.learn(A, ["bra ka lo"])
        bundle = d.member_backup(A)
        self.assertNotIn("bra ka lo", json.dumps(bundle))
        with self.assertRaises(AuthError):
            device().member_restore(B, bundle)

    def test_revoked_epoch_cannot_be_replayed(self):
        d = device()
        d.learn(A, ["bra ka lo"])
        stale = d.member_backup(A)
        d.exit(A)
        with self.assertRaises(ReplayError):
            d.member_restore(A, stale)
        self.assertEqual(d.suggest("bra "), [])

    def test_restore_replace_swaps_material(self):
        d = device()
        d.learn(A, ["bra ka lo"])
        bundle = d.member_backup(A)
        d.replace_sentence(A, "bra ka lo", "bra nu po")
        self.assertEqual(d.suggest("bra "), ["nu"])
        d.member_restore(A, bundle, replace=True)
        self.assertEqual(d.suggest("bra "), ["ka"])
        self.assertNotIn("nu", d.suggest("bra "))

    def test_cross_device_restore_respects_existing_revocation(self):
        # Device 1 learns A at epoch 1 and exports it.
        d1 = device()
        d1.learn(A, ["bra ka lo"])
        bundle = d1.member_backup(A)
        # Device 2 has A at epoch 2 already revoked (A joined/left once).
        d2 = device()
        d2.join(A)
        d2.exit(A)
        # Restoring the epoch-1 bundle must be refused; withdrawn stays withdrawn.
        with self.assertRaises(ReplayError):
            d2.member_restore(A, bundle)
        self.assertEqual(d2.suggest("bra "), [])

    def test_restore_into_fresh_device_adopts_bundle_epoch(self):
        d1 = device()
        d1.learn(A, ["bra ka lo"])
        bundle = d1.member_backup(A)
        d2 = device()
        d2.member_restore(A, bundle)
        self.assertEqual(d2.state["members"][d2.member_id(A)]["epoch"], bundle["epoch"])
        self.assertEqual(d2.suggest("bra "), ["ka"])


class LocalDisabledWords(unittest.TestCase):
    def test_disabling_reroutes_to_next_candidate(self):
        d = device()
        d.learn(A, ["bra ka lo", "bra ka mi", "bra nu po"])
        self.assertEqual(d.suggest("bra ")[0], "ka")
        d.set_current_user("alice")
        d.local_disable("ka")
        self.assertEqual(d.suggest("bra "), ["nu"])
        d.learn(A, ["bra nu ka"])
        self.assertNotIn("ka", d.chain("bra ", 3))
        d.local_enable("ka")
        self.assertEqual(d.suggest("bra ")[0], "ka")

    def test_disabled_words_are_per_current_user(self):
        d = device()
        d.learn(A, ["bra ka lo", "bra nu po"])
        d.set_current_user("alice")
        d.local_disable("ka")
        self.assertNotIn("ka", d.suggest("bra "))
        d.set_current_user("bob")
        self.assertIn("ka", d.suggest("bra "))

    def test_local_disable_does_not_change_model(self):
        d = device()
        d.learn(A, ["bra ka lo", "bra nu po", "bra nu ta"])
        before = d.counts_for("bra ")
        d.set_current_user("alice")
        d.local_disable("nu")
        self.assertEqual(before, d.counts_for("bra "))
        self.assertEqual(d.suggest("bra ")[0], "ka")


class EngineInvariants(unittest.TestCase):
    def test_counts_are_owner_agnostic(self):
        d1 = device()
        d1.learn(A, ["bra ka lo", "bra ka mi"])
        d2 = device()
        d2.learn(B, ["bra ka lo"])
        d2.learn(C, ["bra ka mi"])
        self.assertEqual(d1.counts_for("bra "), d2.counts_for("bra "))

    def test_dedup_within_member_but_not_across_members(self):
        d = device()
        d.learn(A, ["bra ka lo", "bra ka lo", "bra ka lo"])
        self.assertEqual(d.counts_for("bra "), {"ka": 1})
        d.learn(B, ["bra ka lo"])
        self.assertEqual(d.counts_for("bra "), {"ka": 2})

    def test_base_corpus_counts_merge_with_members(self):
        d = device()
        d.set_base(["bra ka lo"])
        d.learn(A, ["bra zu po"])
        self.assertEqual(d.counts_for("bra "), {"ka": 1, "zu": 1})
        d.exit(A)
        self.assertEqual(d.counts_for("bra "), {"ka": 1})

    def test_sealed_state_survives_tamper_detection(self):
        import base64

        d = device()
        d.learn(A, ["bra ka lo"])
        sealed = d.state["sealed_model"]
        raw = bytearray(base64.b64decode(sealed))
        raw[-1] ^= 0x01
        d.state["sealed_model"] = base64.b64encode(bytes(raw)).decode()
        d.store.save(d.state)  # persist the tampered state
        with self.assertRaises(Exception):
            Device(d.store)  # reopening must fail authentication


if __name__ == "__main__":
    unittest.main()
