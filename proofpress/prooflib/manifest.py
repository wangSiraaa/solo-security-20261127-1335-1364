"""The signed approval manifest (option B: editor-signed parts manifest).

The editor signs a canonical JSON document that binds:

* the edition (job id + edition number + issue time),
* the binding direction,
* the exact normalized content of every page (sha256 of decoded RGB pixels)
  in the *actual reading order*,
* the signature (fold-sheet) grouping and insert placement,
* the official approval texts, scoped to the whole book, to one opening
  (left/right pages as the reader opens the book) or to one signature,
* the cross-page spread image that must join across two adjacent pages.

The same pixel content may appear at several positions; positions, not
files, carry meaning.  Attachment filenames are deliberately irrelevant.
"""

import hashlib
import json

PROFILE = "proof-approval/1"


def canonical_bytes(manifest):
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def manifest_sha256(manifest_bytes):
    return hashlib.sha256(manifest_bytes).hexdigest()


def reading_sequence(manifest):
    """Derive the global reading sequence of positions (1-based) implied by
    the signature grouping + insert placement.  Must equal 1..N in order."""
    seq = []
    for sig in sorted(manifest["signatures"], key=lambda s: s["id"]):
        main = list(sig["positions"])
        inserts = sorted(sig.get("inserts", []), key=lambda i: i["after_leaf"])
        # walk leaf by leaf, inserting insert-leaf positions where placed
        out = []
        leaves = len(main) // 2
        for k in range(leaves):
            out.extend(main[2 * k : 2 * k + 2])
            for ins in inserts:
                if ins["after_leaf"] == k + 1:
                    out.extend(ins["positions"])
        seq.extend(out)
    return seq


def check_manifest_structure(manifest):
    if manifest.get("profile") != PROFILE:
        raise ValueError("unknown manifest profile")
    n = len(manifest["page_order"])
    seq = reading_sequence(manifest)
    if seq != list(range(1, n + 1)):
        raise ValueError(
            "signature/insert structure does not reproduce the reading order"
        )
    # positions referenced by approvals must exist
    for ap in manifest.get("approvals", []):
        if ap["scope"] == "opening":
            for p in (ap["left"], ap["right"]):
                if not (1 <= p <= n):
                    raise ValueError("approval references bad position")
    return True


def signature_sequence(manifest, sig_id):
    """Reading sequence of positions inside one signature (fold sheet)."""
    for sig in manifest["signatures"]:
        if sig["id"] == sig_id:
            main = list(sig["positions"])
            out = []
            leaves = len(main) // 2
            for k in range(leaves):
                out.extend(main[2 * k : 2 * k + 2])
                for ins in sorted(sig.get("inserts", []),
                                  key=lambda i: i["after_leaf"]):
                    if ins["after_leaf"] == k + 1:
                        out.extend(ins["positions"])
            return out
    raise KeyError(sig_id)


def opening_pairs(manifest):
    """All openings (pairs of positions seen together when the reader turns
    a leaf), as (left, right) per the manifest's binding direction."""
    seq = reading_sequence(manifest)
    pairs = []
    for k in range(0, len(seq) - 1, 2):
        a, b = seq[k + 1], seq[k + 2] if k + 2 < len(seq) else None
        if b is None:
            break
        if manifest["binding_direction"] == "ltr":
            pairs.append((a, b))
        else:
            pairs.append((b, a))
    return pairs
