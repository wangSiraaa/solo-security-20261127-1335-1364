"""Role-isolated storage of sealed boxes.

A sealed box is opaque bytes that anyone (notably the carrier) may inspect
for *size*: a header, a nonce, ciphertext, and a MAC.  Only the holder of
the recipient key can open it.  ``peek`` deliberately returns the
carrier-visible external record — name and byte length — and nothing more.
"""

import hashlib
import hmac
import os

from .util import ACError

_TAG = 16
_NONCE = 16
_MAC = 32

# Every sealed box carries TAG + NONCE + MAC over its plaintext.  The
# carrier sees this overhead as part of the box's byte count; it is a
# constant and therefore leaks nothing about the contents.
SEAL_OVERHEAD = _TAG + _NONCE + _MAC


def _stream(key, label, length):
    out = bytearray()
    ctr = 0
    while len(out) < length:
        out += hmac.new(key, label + ctr.to_bytes(8, "big"), hashlib.sha256).digest()
        ctr += 1
    return bytes(out[:length])


class SealedStore:
    def __init__(self, key, *, box_tag=None):
        if not isinstance(key, (bytes, bytearray)) or not key:
            raise ACError("SealedStore needs a non-empty bytes key")
        self._key = bytes(key)
        tag = os.urandom(_TAG) if box_tag is None else box_tag
        if not isinstance(tag, (bytes, bytearray)) or len(tag) != _TAG:
            raise ACError("box_tag must be 16 bytes")
        self._tag = bytes(tag)
        self._boxes = {}
        self._order = []

    def seal(self, name, plaintext):
        if not isinstance(name, str) or not name:
            raise ACError("box name must be a non-empty str")
        if not isinstance(plaintext, (bytes, bytearray)):
            raise ACError("box plaintext must be bytes")
        if name in self._boxes:
            raise ACError(f"box {name!r} already sealed")
        nonce = os.urandom(_NONCE)
        ks = _stream(self._key, self._tag + nonce, len(plaintext))
        ct = bytes(a ^ b for a, b in zip(plaintext, ks))
        mac = hmac.new(self._key, self._tag + nonce + ct, hashlib.sha256).digest()
        blob = self._tag + nonce + ct + mac
        self._boxes[name] = blob
        self._order.append(name)
        return len(blob)

    def peek(self, name):
        blob = self._boxes.get(name)
        if blob is None:
            raise ACError(f"no such box {name!r}")
        return {"box": name, "bytes": len(blob)}

    def peek_all(self):
        return [self.peek(n) for n in self._order]

    def blob(self, name):
        blob = self._boxes.get(name)
        if blob is None:
            raise ACError(f"no such box {name!r}")
        return blob

    def open(self, name):
        blob = self._boxes.get(name)
        if blob is None:
            raise ACError(f"no such box {name!r}")
        if len(blob) < _TAG + _NONCE + _MAC:
            raise ACError(f"box {name!r} truncated")
        tag = blob[:_TAG]
        nonce = blob[_TAG:_TAG + _NONCE]
        ct = blob[_TAG + _NONCE:-_MAC]
        mac = blob[-_MAC:]
        if not hmac.compare_digest(tag, self._tag):
            raise ACError(f"box {name!r} is not from this key")
        want = hmac.new(self._key, self._tag + nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, want):
            raise ACError(f"box {name!r} failed authentication")
        ks = _stream(self._key, self._tag + nonce, len(ct))
        return bytes(a ^ b for a, b in zip(ct, ks))

    def names(self):
        return list(self._order)

    # -- persistence (the warehouse view is these opaque blobs) --
    def export_blobs(self):
        return [{"name": n, "blob": self._boxes[n].hex()} for n in self._order]

    @classmethod
    def from_blobs(cls, key, blobs, *, box_tag):
        store = cls(key, box_tag=box_tag)
        for entry in blobs:
            name = entry["name"]
            blob = bytes.fromhex(entry["blob"])
            if name in store._boxes:
                raise ACError(f"duplicate box {name!r}")
            store._boxes[name] = blob
            store._order.append(name)
        return store
