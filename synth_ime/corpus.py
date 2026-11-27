"""Synthetic corpus generator.

Every sentence is built from invented words (no natural-language tokens), so
the tests exercise the mechanism without leaning on real vocabulary.  Words are
deterministically generated, so generated corpora are reproducible.
"""

from __future__ import annotations

import itertools
import random
from typing import Iterable, List

_ONSETS = ["br", "ch", "d", "fl", "gr", "h", "k", "l", "m", "n", "p", "qu", "r", "s", "t", "v", "z", "tr", "sn", "pl"]
_VOWELS = ["a", "e", "i", "o", "u", "ai", "ea", "ou", "y"]
_CODAS = ["", "n", "r", "l", "x", "th", "sk", "nd", "mp", "ft"]


def synth_word(i: int) -> str:
    """Deterministic, pronounceable-ish invented word for index ``i``."""
    if i < 0:
        raise ValueError("word index must be non-negative")
    o = _ONSETS[i % len(_ONSETS)]
    v = _VOWELS[(i // len(_ONSETS)) % len(_VOWELS)]
    c = _CODAS[(i // (len(_ONSETS) * len(_VOWELS))) % len(_CODAS)]
    suffix = "" if i < len(_ONSETS) * len(_VOWELS) * len(_CODAS) else str(i)
    return (o + v + c + suffix).lower()


def lexicon(n: int) -> List[str]:
    words = [synth_word(i) for i in range(n)]
    if len(set(words)) != n:
        raise AssertionError("synthetic lexicon collision")
    return words


def make_sentences(words: Iterable[str], count: int, seed: int = 0, min_len: int = 3, max_len: int = 5) -> List[str]:
    """Build ``count`` distinct random sentences over ``words``."""
    pool = list(words)
    rng = random.Random(seed)
    seen = set()
    out: List[str] = []
    guard = 0
    while len(out) < count:
        guard += 1
        if guard > count * 1000:
            raise RuntimeError("could not generate enough distinct sentences")
        n = rng.randint(min_len, max_len)
        words_pick = tuple(rng.choice(pool) for _ in range(n))
        if len(set(words_pick)) < n:  # keep sentences free of repeated words
            continue
        sentence = " ".join(words_pick)
        if sentence in seen:
            continue
        seen.add(sentence)
        out.append(sentence)
    return out


def sentences_starting_with(word: str, words: Iterable[str], count: int, seed: int = 0, length: int = 3) -> List[str]:
    """Sentences whose first word is exactly ``word`` (for sentence-start tests)."""
    pool = [w for w in words if w != word]
    rng = random.Random(seed)
    seen = set()
    out: List[str] = []
    guard = 0
    while len(out) < count:
        guard += 1
        if guard > count * 1000:
            raise RuntimeError("could not generate enough distinct sentences")
        rest = tuple(rng.sample(pool, length - 1))
        sentence = " ".join((word,) + rest)
        if sentence in seen:
            continue
        seen.add(sentence)
        out.append(sentence)
    return out
