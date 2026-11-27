"""Stress test: random learn/exit/reteach churn must keep counts non-negative
and equal to a from-scratch rebuild of the surviving members' sentences.
"""

from __future__ import annotations

import random
import unittest

from synth_ime import corpus
from synth_ime.engine import Device, MemoryStore

TOKENS = {name: (name * 64)[:64] for name in "ABC"}


def expected_members(member_sents):
    """Model containing only members' sentences (base lives separately)."""
    model = {}
    for sents in member_sents.values():
        for sid in sents:
            for (p, n) in _pairs(sid):
                model.setdefault(p, {})
                model[p][n] = model[p].get(n, 0) + 1
    return {p: {n: c for n, c in row.items() if c > 0} for p, row in model.items() if row}


def expected_full(member_sents, base):
    model = {}
    for source in (base, *member_sents.values()):
        for sid in source:
            for (p, n) in _pairs(sid):
                model.setdefault(p, {})
                model[p][n] = model[p].get(n, 0) + 1
    return {p: {n: c for n, c in row.items() if c > 0} for p, row in model.items() if row}


def _pairs(sid):
    words = sid.split(" ")
    out = [("", words[0])]
    out.extend(zip(words, words[1:]))
    return out


class ChurnStress(unittest.TestCase):
    def test_random_churn_matches_rebuild(self):
        words = corpus.lexicon(16)
        base = corpus.make_sentences(words, 3, seed=99)
        for seed in range(20):
            rng = random.Random(seed)
            d = Device(MemoryStore())
            d.set_base(base)
            live = {}  # name -> set of sentences currently taught
            for name in TOKENS:
                live[name] = set()
                d.join(TOKENS[name])
            learners = list(TOKENS)
            for step in range(60):
                name = rng.choice(learners)
                r = rng.random()
                if r < 0.6:
                    sid = rng.choice(corpus.make_sentences(words, 40, seed=seed * 100 + step))
                    if d.learn(TOKENS[name], [sid]):
                        live[name].add(sid)
                elif r < 0.8 and live[name]:
                    sid = rng.choice(sorted(live[name]))
                    d.unlearn(TOKENS[name], sid)
                    live[name].discard(sid)
                elif r < 0.9:
                    d.exit(TOKENS[name])
                    live[name] = set()
                    # rejoin clean
                    d.join(TOKENS[name])
                else:
                    # every count must stay non-negative and no empty rows
                    for p, row in d._model.items():
                        self.assertTrue(row, "empty row present")
                        for n, c in row.items():
                            self.assertGreaterEqual(c, 1, (seed, step, p, n))
            # Members-only model equals a rebuild; full counts equal base+members.
            self.assertEqual(d._model, expected_members(live), seed)
            for p in ("", "bra "):
                self.assertEqual(d.counts_for(p), _counts(expected_full(live, base), p))


def _counts(model, prefix):
    tokens = prefix.split()
    prev = tokens[-1] if tokens else ""
    return dict(sorted(model.get(prev, {}).items()))


if __name__ == "__main__":
    unittest.main()
