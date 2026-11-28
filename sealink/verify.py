"""Standalone verifier for an island's merged whole-observation file (.obsf).

This is the "现场只认最终可独立打开的整条观测 / 复核内层摘要" check, and it must
work from the file alone -- no shared state, as if an operator picked it up days
later.

An .obsf is:

  line 1 : JSON header {obs_id, root, window, segments:[[sid, inner_digest, len], ...]}
  then   : one raw segment payload per line, in header order

Verification:
  1. each payload's SHA-256 equals its header inner_digest,
  2. each payload's length equals its declared length,
  3. the recomputed window root equals the header root,
  4. the segment ids are continuously sequenced for the declared window.
"""

from __future__ import annotations

import hashlib
import json
import sys


def _window_root(ordered) -> str:
    h = hashlib.sha256()
    h.update(b"window|")
    for sid, data in ordered:
        h.update(sid.encode("utf-8"))
        h.update(b"\x00")
        h.update(data)
        h.update(b"\x00")
    return h.hexdigest()


def verify_obsf(path: str) -> dict:
    with open(path, "rb") as fh:
        raw = fh.read()
    nl = raw.index(b"\n")
    header = json.loads(raw[:nl])
    body = raw[nl + 1:]
    payloads = body.split(b"\n")
    if payloads and payloads[-1] == b"":
        payloads.pop()

    segs = header["segments"]
    ok = True
    reasons = []

    if len(payloads) != len(segs):
        return {"ok": False, "reason": f"payload count {len(payloads)} != "
                f"declared {len(segs)}"}

    ordered = []
    for (sid, inner, length), data in zip(segs, payloads):
        if len(data) != length:
            ok = False
            reasons.append(f"{sid}: length {len(data)} != {length}")
        if hashlib.sha256(data).hexdigest() != inner:
            ok = False
            reasons.append(f"{sid}: inner digest mismatch")
        ordered.append((sid, data))

    computed_root = _window_root(ordered)
    if computed_root != header["root"]:
        ok = False
        reasons.append(f"root mismatch: {computed_root} != {header['root']}")

    # sequence continuity: window [seg_lo..seg_hi] x [t_lo..t_hi]
    lo_s, hi_s, lo_t, hi_t = header["window"]
    want = [f"{header['obs_id']}:s{s}:t{t}"
            for s in range(lo_s, hi_s + 1) for t in range(lo_t, hi_t + 1)]
    if [sid for sid, _ in ordered] != want:
        ok = False
        reasons.append("segment ids not continuously sequenced for the window")

    return {"ok": ok, "reasons": reasons, "root": computed_root,
            "segments": len(ordered)}


if __name__ == "__main__":
    for p in sys.argv[1:]:
        r = verify_obsf(p)
        print(json.dumps(r, indent=2))
        sys.exit(0 if r["ok"] else 1)
