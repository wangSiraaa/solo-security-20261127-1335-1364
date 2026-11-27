"""Public dictionaries: stable word slots, plus a *representation* per slot.

A word has two public faces:

  slot   stable identity, e.g. ``"longword"``.  Pages name words by slot, so
         the work order never changes when the codebook does.
  code   the integer symbol actually written into a packed page.  Its
         variable-length width (1 byte for <128, 2 bytes for <16384) is what
         makes a page long or short.

Changing a long word's *representation* (its code) can change packed sizes
and therefore the packing commitment; changing only its *spelling* (text)
cannot, because the text never travels.  The fingerprint used by the
commitment covers codes, not text.

The secret options use one public symbol table (``OPTIONS``) shared by all
orders; encoding a symbol does not reveal which option it came from.
"""

import hashlib

from .util import ACError

# slot -> text, and slot -> packed code.  Codes small enough to encode in one
# byte stay in 0..127; the "long word" sits in two-byte territory.
_WORDS = {
    "the": "the", "contract": "contract", "archive": "archive",
    "carrier": "carrier", "docket": "docket", "page": "page",
    "seal": "seal", "box": "box", "records": "records",
    "transmits": "transmits", "receives": "receives", "reviews": "reviews",
    "withholds": "withholds", "releases": "releases",
    "longword": "consolidates",
}

_CODES = {
    "the": 0, "contract": 1, "archive": 2, "carrier": 3,
    "docket": 4, "page": 5, "seal": 6, "box": 7,
    "records": 8, "transmits": 9, "receives": 10, "reviews": 11,
    "withholds": 12, "releases": 13,
    "longword": 200,        # two-byte code; the experiment's long word
}

# slot -> packed code for a second, wider public dictionary (optional use).
_WIDE_CODES = {
    "the": 0, "contract": 1, "archive": 2, "carrier": 3,
    "docket": 4, "page": 5, "seal": 6, "box": 7,
    "records": 8, "transmits": 9, "receives": 10, "reviews": 11,
    "withholds": 12, "releases": 13,
    "longword": 300,
}

# Public symbol table for secret options: symbol -> text.
OPTIONS = {
    1: "release",
    2: "withhold",
    3: "transfer",
    17: "review",
    300: "seal-at-night",
    4000: "defer",
}

DICTIONARY_SETS = {
    "tiny": (_WORDS, _CODES),
    "wide": (_WORDS, _WIDE_CODES),
}


def option_word(symbol):
    try:
        return OPTIONS[symbol]
    except KeyError:
        raise ACError(f"option symbol {symbol!r} not present") from None


class Dictionary:
    """Public codebook: stable slots, integer code representations."""

    def __init__(self, words=None, codes=None, name="custom"):
        words = dict(_WORDS if words is None else words)
        codes = dict(_CODES if codes is None else codes)
        if not words:
            raise ACError("dictionary must be non-empty")
        missing = set(words) - set(codes)
        extra = set(codes) - set(words)
        if missing or extra:
            raise ACError(f"slot/code mismatch: missing={missing} extra={extra}")
        seen = {}
        for slot, code in codes.items():
            if not isinstance(code, int) or code < 0:
                raise ACError("dictionary codes must be non-negative ints")
            if code in seen:
                raise ACError(f"duplicate code {code} for {seen[code]!r}/{slot!r}")
            seen[code] = slot
        self.name = name
        self._words = words
        self._codes = codes

    @classmethod
    def from_name(cls, name):
        try:
            words, codes = DICTIONARY_SETS[name]
        except KeyError:
            raise ACError(f"unknown dictionary {name!r}") from None
        return cls(words, codes, name=name)

    def copy(self, name=None):
        return Dictionary(self._words, self._codes, name=name or self.name)

    def __contains__(self, slot):
        return slot in self._words

    def slots(self):
        return sorted(self._words)

    def code_of(self, slot):
        try:
            return self._codes[slot]
        except KeyError:
            raise ACError(f"unknown word slot {slot!r}") from None

    def text_of_slot(self, slot):
        try:
            return self._words[slot]
        except KeyError:
            raise ACError(f"unknown word slot {slot!r}") from None

    def text_of_code(self, code):
        for slot, c in self._codes.items():
            if c == code:
                return self._words[slot]
        raise ACError(f"no word for code {code!r}")

    def items(self):
        return sorted(self._codes.items())

    def fingerprint(self):
        """Hash of the representation: slots and codes. Text is excluded."""
        h = hashlib.sha256()
        h.update(b"dict\x00")
        for slot, code in sorted(self._codes.items()):
            h.update(slot.encode())
            h.update(b"=")
            h.update(str(code).encode())
            h.update(b";")
        return h.hexdigest()

    # -- public representation edits (used by experiments) --
    def remap(self, slot, new_code):
        """Change a word's packed code (its representation)."""
        if slot not in self._codes:
            raise ACError(f"unknown word slot {slot!r}")
        if not isinstance(new_code, int) or new_code < 0:
            raise ACError("new code must be a non-negative int")
        for s, c in self._codes.items():
            if c == new_code and s != slot:
                raise ACError(f"code {new_code} already used by {s!r}")
        self._codes[slot] = new_code
        return self

    def respell(self, slot, text):
        """Change a word's spelling only. Representation (and packing) unchanged."""
        if slot not in self._words:
            raise ACError(f"unknown word slot {slot!r}")
        if not isinstance(text, str) or not text:
            raise ACError("spelling must be a non-empty str")
        self._words[slot] = text
        return self
