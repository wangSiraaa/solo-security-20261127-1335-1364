"""Page codecs: 'layout' (reserved shapes) and 'shard' (constant length).

A *resolved* page is a list of ``(public_code, secret_code_or_None)`` pairs.
``public_code`` is the template word's public dictionary index.  When a
token carries a secret option, ``secret_code`` is the public dictionary
index of the chosen alternate word; otherwise it is ``None`` and the
template word stands.

  layout  bitmap of "token has a secret option" + variable-length (LEB128)
          public indices + variable-length secret indices.  Common values
          get short codes, so page lengths vary with the option -> box
          shapes are reserved for the worst legal option and padded.
  shard   fixed 2-byte public + 2-byte secret per token (0xFFFF = no
          secret).  Every page of a given token count has the same length,
          independent of the option, so pages ship independently.  Cost:
          no short codes, common pages lose ordinary compression.

Lengths are public in shard mode, and only *box* lengths are public in
layout mode (padding hides per-option variation).
"""

import struct

from .util import ACError

_SHARD_NONE = 0xFFFF
_SHARD_MAX = 0xFFFE


def _uvar(n):
    if not isinstance(n, int) or n < 0:
        raise ACError("uvar needs a non-negative int")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _rvar(data, pos):
    shift = 0
    val = 0
    while True:
        if pos >= len(data):
            raise ACError("truncated varint")
        b = data[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7
        if shift > 63:
            raise ACError("varint too long")


def _uvar_len(n):
    if not isinstance(n, int) or n < 0:
        raise ACError("uvar length needs a non-negative int")
    length = 1
    while n >= 0x80:
        n >>= 7
        length += 1
    return length


def _check_triples(triples):
    for i, t in enumerate(triples):
        if not isinstance(t, (list, tuple)) or len(t) != 2:
            raise ACError(f"token {i}: expected (public, secret)")
        p, s = t
        if not isinstance(p, int) or p < 0:
            raise ACError(f"token {i}: public code must be a non-negative int")
        if s is not None and (not isinstance(s, int) or s < 0):
            raise ACError(f"token {i}: secret code must be None or a non-negative int")


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------
def encode_layout(triples):
    triples = list(triples)
    _check_triples(triples)
    n = len(triples)
    bitmap = bytearray((n + 7) // 8)
    pub = bytearray()
    sec = bytearray()
    for i, (p, s) in enumerate(triples):
        if s is not None:
            bitmap[i >> 3] |= 1 << (i & 7)
            sec += _uvar(s)
        pub += _uvar(p)
    return struct.pack(">H", n) + bytes(bitmap) + bytes(pub) + bytes(sec)


def decode_layout(blob):
    if len(blob) < 2:
        raise ACError("layout page truncated")
    n = struct.unpack(">H", blob[:2])[0]
    pos = 2
    bm_len = (n + 7) // 8
    if pos + bm_len > len(blob):
        raise ACError("layout bitmap truncated")
    bitmap = blob[pos:pos + bm_len]
    pos += bm_len
    publics = []
    for _ in range(n):
        v, pos = _rvar(blob, pos)
        publics.append(v)
    triples = []
    for i in range(n):
        if bitmap[i >> 3] & (1 << (i & 7)):
            v, pos = _rvar(blob, pos)
            triples.append((publics[i], v))
        else:
            triples.append((publics[i], None))
    if pos != len(blob):
        raise ACError("layout page has trailing bytes")
    return n, triples


def layout_len(triples):
    n = len(triples)
    total = 2 + (n + 7) // 8
    for p, s in triples:
        total += _uvar_len(p)
        if s is not None:
            total += _uvar_len(s)
    return total


# --------------------------------------------------------------------------
# shard
# --------------------------------------------------------------------------
def encode_shard(triples):
    triples = list(triples)
    _check_triples(triples)
    n = len(triples)
    out = bytearray(struct.pack(">H", n))
    for p, s in triples:
        if p > _SHARD_MAX:
            raise ACError(f"shard public code {p} too large")
        if s is not None and s > _SHARD_MAX:
            raise ACError(f"shard secret code {s} too large")
        out += struct.pack(">HH", p, _SHARD_NONE if s is None else s)
    return bytes(out)


def decode_shard(blob):
    if len(blob) < 2:
        raise ACError("shard page truncated")
    n = struct.unpack(">H", blob[:2])[0]
    if len(blob) != 2 + 4 * n:
        raise ACError("shard page length mismatch")
    triples = []
    pos = 2
    for _ in range(n):
        p, s = struct.unpack(">HH", blob[pos:pos + 4])
        pos += 4
        triples.append((p, None if s == _SHARD_NONE else s))
    return n, triples


def shard_len(n_tokens):
    return 2 + 4 * n_tokens


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------
_CODECS = {
    "layout": (encode_layout, decode_layout),
    "shard": (encode_shard, decode_shard),
}


def _impl(method):
    try:
        return _CODECS[method]
    except KeyError:
        raise ACError(f"unknown packing method {method!r}") from None


def encode_page(triples, method):
    enc, _ = _impl(method)
    return enc(triples)


def decode_page(blob, method):
    _, dec = _impl(method)
    return dec(blob)


def page_len(triples, method):
    if method == "layout":
        return layout_len(triples)
    if method == "shard":
        return shard_len(len(triples))
    raise ACError(f"unknown packing method {method!r}")
