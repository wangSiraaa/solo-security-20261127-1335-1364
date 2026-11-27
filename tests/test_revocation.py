"""Randomised, brute-force check of the central revocation guarantee.

For random corpora split between members, after removing one member the device's
observable output (suggestions at every reachable prefix, multi-step chains, and
vocabulary) must equal a device that never contained that member's sentences.

Because base corpus and other members are identical in both devices, this is a
direct instantiation of the brief's comparison baseline.
"""

from __future__ import annotations

import itertools
import random
import unittest

from synth_ime import corpus
from synth_ime.engine import Device, MemoryStore

A = "a" * 64
B = "b" * 64
C = "c" * 64


def all_prefixes(sentences):
    """Every whole-word prefix and every one-character partial prefix."""
    prefs = {""}
    for sid in sentences:
        words = sid.split(" ")
        for i in range(1, len(words) + 1):
            whole = " ".join(words[:i])
            if i < len(words):
                prefs.add(whole + " ")
                prefs.add(whole + " " + words[i][:1])
            else:
                prefs.add(whole + " ")
                prefs.add(whole)
    return prefs


class BruteForceRevocation(unittest.TestCase):
    def test_random_corpora_match_never_had_baseline(self):
        words = corpus.lexicon(18)
        for seed in range(12):
            rng = random.Random(seed)
            n = rng.randint(6, 14)
            sents = corpus.make_sentences(words, n, seed=seed, min_len=2, max_len=5)
            rng.shuffle(sents)
            split = [sents[i::3] for i in range(3)]
            a_sents, b_sents, c_sents = split
            base = corpus.make_sentences(words, 4, seed=1000 + seed)

            both = Device(MemoryStore())
            both.set_base(base)
            for s in a_sents:
                both.learn(A, [s])
            for s in b_sents:
                both.learn(B, [s])
            for s in c_sents:
                both.learn(C, [s])

            never = Device(MemoryStore())
            never.set_base(base)
            for s in b_sents:
                never.learn(B, [s])
            for s in c_sents:
                never.learn(C, [s])

            both.exit(A)

            prefs = all_prefixes(base + b_sents + c_sents + a_sents)
            for p in sorted(prefs):
                self.assertEqual(both.suggest(p), never.suggest(p), (seed, p))
                self.assertEqual(both.counts_for(p), never.counts_for(p), (seed, p))
                self.assertEqual(both.chain(p, 5), never.chain(p, 5), (seed, p))
            self.assertEqual(both.vocabulary(), never.vocabulary())
            # The merged model must be byte-identical in content, too.
            self.assertEqual(both._model, never._model)

    def test_two_members_same_sentence_survives_one_exit(self):
        words = corpus.lexicon(12)
        sents = corpus.make_sentences(words, 6, seed=7)
        shared = sents[0]
        d = Device(MemoryStore())
        d.learn(A, sents)
        d.learn(B, [shared])
        before = {p: d.suggest(p) for p in all_prefixes(sents)}
        d.exit(A)
        # B still teaches the shared sentence, so its own first-pair remains.
        first, second = shared.split(" ")[:2]
        self.assertIn(second, d.suggest(first + " "))

    def test_base_corpus_survives_member_exit(self):
        # A sentence start contributed only by the base must remain after a
        # member who also taught unrelated sentences leaves.
        words = corpus.lexicon(12)
        base = ["bra prr klo"]
        d = Device(MemoryStore())
        d.set_base(base)
        d.learn(A, ["nu po ta"])
        d.exit(A)
        self.assertEqual(d.suggest(""), ["bra"])
        self.assertIn("prr", d.suggest("bra "))

    def test_retention_of_other_members_after_multiple_exits(self):
        words = corpus.lexicon(12)
        d = Device(MemoryStore())
        d.learn(A, ["bra ka lo"])
        d.learn(B, ["bra ka zu"])
        d.learn(C, ["bra ka mi"])
        d.exit(A)
        d.exit(B)
        self.assertEqual(d.suggest("bra "), ["ka"])  # C still holds it
        d.exit(C)
        self.assertEqual(d.suggest("bra "), [])


if __name__ == "__main__":
    unittest.main()
