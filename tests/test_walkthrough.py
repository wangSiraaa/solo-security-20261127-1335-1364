"""Integrated narrative test: the exact walkthrough from the brief.

Every sentence uses invented words.  Each step is a named assertion so a
failure points at the requirement it violates.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from synth_ime import corpus
from synth_ime.engine import Device, JsonStore, MemoryStore, ReplayError

A = "a" * 64
B = "b" * 64


class BriefWalkthrough(unittest.TestCase):
    def test_full_scenario(self):
        w = corpus.lexicon(32)
        # Invented words used below are real members of the synthetic lexicon.
        bra, ka, zu, lo, mi, nu, po = w[0], w[1], w[2], w[3], w[4], w[5], w[6]
        s_a = f"{bra} {ka} {lo}"
        s_b = f"{bra} {ka} {zu}"
        s_b2 = f"{bra} {nu} {zu}"
        s_a_only = f"{bra} {mi} {po}"

        pid = os.getpid()
        path = tempfile.mktemp(prefix="brief-%d-" % pid, suffix=".json")
        try:
            # --- on the shared tablet: A teaches two sentences, B one ---
            t = Device(JsonStore(path))
            t.learn(A, [s_a, s_a_only])
            t.learn(B, [s_b])
            self.assertIn(ka, t.suggest(f"{bra} "), "both teach bra->ka")

            # --- A exits (leaves with their library) ---
            t.exit(A)
            self.assertIn(ka, t.suggest(f"{bra} "), "ka survives: B also taught it")
            self.assertNotIn(mi, t.suggest(f"{bra} "), "A-only word mi is gone")

            # --- includes a genuine sentence start, not only continuations ---
            # B teaches a brand-new sentence start; it must complete.
            t.learn(B, [f"{nu} {po} {zu}"])
            self.assertIn(nu, t.suggest(""), "new sentence starts present")

            # --- reboot: withdrawn material must not come back ---
            t2 = Device(JsonStore(path))
            self.assertNotIn(mi, t2.suggest(f"{bra} "), "no resurrection after reboot")
            for s in (s_a, s_a_only, s_b, s_b2):
                self.assertNotIn(s, json.dumps(t2.state), "no plaintext on disk")

            # --- B re-teaches the shared sentence to a different word ---
            t2.replace_sentence(B, s_b, s_b2)
            self.assertNotIn(ka, t2.suggest(f"{bra} "), "ka has no other source now")
            self.assertIn(nu, t2.suggest(f"{bra} "), "nu now follows bra")
            self.assertNotIn(ka, t2.chain(f"{bra} ", 4), "multi-step also drops ka")

            # --- restore learning material, then only change local disable ---
            d = Device(MemoryStore())
            d.learn(B, [f"{bra} {nu} {zu}", f"{bra} {po} {lo}"])
            counts_before = d.counts_for(f"{bra} ")
            d.set_current_user("alice")
            d.local_disable(nu)          # only local change, model untouched
            self.assertEqual(d.counts_for(f"{bra} "), counts_before,
                             "local disable must not alter the model")
            self.assertEqual(d.suggest(f"{bra} "), [po], "re-routes to next candidate")
            d.local_enable(nu)
            self.assertIn(nu, d.suggest(f"{bra} "), "re-enabling restores candidate")

            # --- repeated exit is safe and idempotent ---
            self.assertEqual(t2.exit(B), "revoked")
            self.assertEqual(t2.exit(B), "already-revoked")
        finally:
            if os.path.exists(path):
                os.unlink(path)


class SharedWordStillCompletes(unittest.TestCase):
    def test_shared_sentence_start_by_two_members_survives_one_exit(self):
        # 'bra' opens both A's and B's sentences; after A leaves, 'bra' must
        # still be completed from B's teaching.
        d = Device(MemoryStore())
        d.learn(A, ["bra ka lo"])
        d.learn(B, ["bra zu mi"])
        d.exit(A)
        self.assertEqual(d.suggest(""), ["bra"])
        self.assertEqual(d.suggest("bra "), ["zu"])


if __name__ == "__main__":
    unittest.main()
