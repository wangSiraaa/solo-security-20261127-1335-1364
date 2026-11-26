"""Cryptographic helpers built only on the Python standard library.

All digests are SHA-256 over canonical, length-safe encodings.  Everything the
service publishes can be re-computed by an independent reader using only these
functions, which is the whole point of a public draw.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

_HASH_DOMAIN_COMMIT = "commit-v1"
_HASH_DOMAIN_MIX = "mix-v1"
_HASH_DOMAIN_ORDER = "order-v1"
_HASH_DOMAIN_SHARE = "share-v1"
_HASH_DOMAIN_RECEIPT = "receipt-v1"

NULL = b"\x00"

# Delivery sources for a ticket's original.
#   escrow -> 甲: sealed original handed to both cabinets up front.
#   onsite -> 乙: commitment up front, original delivered at the opening.
SOURCES = ("escrow", "onsite")

# The single shift a witness can work in a round.
#   escrow  -> oversaw early cabinet custody; absent on opening day.
#   opening -> hosts opening-day synchronous sealing; no prior custody.
WITNESS_SHIFTS = ("escrow", "opening")

# Which delivery source each shift can actually complete.
SHIFT_COVERS = {"escrow": "escrow", "opening": "onsite"}

# How a round gates the "first reveal":
#   escrow shift  -> release against two matching cabinet receipts;
#   opening shift -> hidden until every original is present, else postpone.
CUTOFF_BY_SHIFT = {
    "escrow": "two_cabinet_receipts",
    "opening": "all_originals_present_or_postpone",
}


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON serialisation used for every digest in the system."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def random_salt(nbytes: int = 16) -> str:
    """A fresh random salt for a participant commitment."""
    import secrets as _secrets

    return _secrets.token_hex(nbytes)


def participant_commit(
    round_id: str, ticket_id: str, value: str, salt: str | None = None
) -> tuple[str, str]:
    """Build a salted commitment, returning ``(salt, commit_hash)``.

    The salt is random unless supplied.  A participant keeps ``salt`` and
    ``value`` secret and publishes only ``commit_hash`` until reveal time.
    """
    if salt is None:
        salt = random_salt()
    return salt, commitment(round_id, ticket_id, salt, value)


def digest(obj: Any) -> str:
    """SHA-256 of the canonical JSON encoding of ``obj``."""
    return sha256_hex(canonical_json(obj))


def _join(parts: Iterable[str]) -> bytes:
    # NUL separators keep fixed-width fields unambiguous.  All inputs are hex
    # strings or identifier tokens and may never contain NUL.
    return NULL.join(p.encode("utf-8") for p in parts)


def commitment(round_id: str, ticket_id: str, salt: str, value: str) -> str:
    """Bind a participant's (salt, value) pair to one round and one ticket.

    A commitment cannot be transplanted to another round or another ticket,
    which blocks re-use of a favourable reveal ("this one or the backup").
    """
    return sha256_hex(
        _join([_HASH_DOMAIN_COMMIT, str(round_id), str(ticket_id), salt, value])
    )


def mix_seed(mixing_rule: str, roster: list[dict], reveals: dict[str, dict]) -> str:
    """Combine the sealed mixing rule with every revealed value, in roster order.

    The roster order is sealed before any reveal is accepted, and *all* values
    are included, so the organiser cannot drop or substitute one contribution.
    """
    parts = [_HASH_DOMAIN_MIX, mixing_rule]
    for t in roster:
        tid = t["ticket_id"]
        r = reveals[tid]
        parts.extend([tid, r["salt"], r["value"]])
    return sha256_hex(_join(parts))


def order_key(seed: str, ticket_id: str) -> str:
    """Deterministic sort key for one ticket under a mixed seed."""
    return sha256_hex(_join([_HASH_DOMAIN_ORDER, seed, str(ticket_id)]))


def compute_draw(
    roster: list[dict],
    quota: int,
    mixing_rule: str,
    reveals: dict[str, dict],
) -> tuple[str, list[str], list[str]]:
    """Recompute (seed, draw_order, winners) from public material.

    ``draw_order`` is the full independently-recomputable ordering.  ``winners``
    is the prefix of that order that fills ``quota`` seats while honouring
    "same household at most once".  Replacements later replay this exact order.
    """
    seed = mix_seed(mixing_rule, roster, reveals)
    keyed = sorted(
        (order_key(seed, t["ticket_id"]), t["ticket_id"], t["household_id"])
        for t in roster
    )
    draw_order = [tid for _k, tid, _hh in keyed]

    winners: list[str] = []
    seen_households: set[str] = set()
    for _k, tid, hh in keyed:
        if hh in seen_households:
            continue
        winners.append(tid)
        seen_households.add(hh)
        if len(winners) == quota:
            break
    return seed, draw_order, winners


def seal_payload(
    round_id: str,
    roster: list[dict],
    quota: int,
    mixing_rule: str,
    witness_shift: str,
) -> dict:
    """The material that is committed to *before* any participant reveals.

    Includes each ticket's delivery ``source`` and the round's ``witness_shift``
    so the reader can confirm the round's custody design was fixed up front.
    ``cutoff`` records the sealed rule for when the first reveal may be shown.
    """
    return {
        "round_id": str(round_id),
        "roster": [
            {
                "ticket_id": t["ticket_id"],
                "household_id": t["household_id"],
                "source": t.get("source", ""),
            }
            for t in roster
        ],
        "quota": quota,
        "mixing_rule": mixing_rule,
        "witness_shift": witness_shift,
        "cutoff": CUTOFF_BY_SHIFT.get(witness_shift, "unknown"),
    }


def seal_digest(
    round_id: str,
    roster: list[dict],
    quota: int,
    mixing_rule: str,
    witness_shift: str,
) -> str:
    return digest(seal_payload(round_id, roster, quota, mixing_rule, witness_shift))


def promote_replacements(
    draw_order: list[str],
    current_winners: list[str],
    absent: list[str],
    attended: list[str],
    claimed_households: set[str],
    hh_by_ticket: dict[str, str],
) -> list[str]:
    """Pure replacement rule shared by the service and the verifier.

    Walk the *originally sealed* draw order and promote the next eligible
    tickets, where eligibility means: not absent, not already seated, physically
    present (``attended``), the household is not already seated among the
    remaining winners, and the household has no prior claim in this round.  No
    candidate outside the sealed order is ever considered.
    """
    absent_set = set(absent)
    present = set(attended)
    remaining = [w for w in current_winners if w not in absent_set]
    seated = {hh_by_ticket[w] for w in remaining}
    taken = set(remaining)
    promoted: list[str] = []
    needed = len(absent_set)
    for tid in draw_order:
        if len(promoted) >= needed:
            break
        if tid in absent_set or tid in taken:
            continue
        if tid not in present:
            continue
        hh = hh_by_ticket[tid]
        if hh in seated or hh in claimed_households:
            continue
        promoted.append(tid)
        seated.add(hh)
        taken.add(tid)
    return promoted


# ---------------------------------------------------------------------------
# Two-of-two secret sharing used by the mock witness cabinets.
# ---------------------------------------------------------------------------


def split_secret(secret: bytes, rand: bytes | None = None) -> tuple[bytes, bytes]:
    """XOR-split ``secret`` into two shares; either one alone is useless."""
    import secrets as _secrets

    if rand is None:
        rand = _secrets.token_bytes(len(secret))
    if len(rand) != len(secret):
        raise ValueError("random pad must match secret length")
    share_a = rand
    share_b = bytes(x ^ y for x, y in zip(secret, rand))
    return share_a, share_b


def join_shares(share_a: bytes, share_b: bytes) -> bytes:
    if len(share_a) != len(share_b):
        raise ValueError("shares must have equal length")
    return bytes(x ^ y for x, y in zip(share_a, share_b))


def share_digest(cabinet_id: str, round_id: str, ticket_id: str, share: bytes) -> str:
    return sha256_hex(
        _join(
            [
                _HASH_DOMAIN_SHARE,
                cabinet_id,
                str(round_id),
                str(ticket_id),
                share.hex(),
            ]
        )
    )


def receipt_digest(cabinet_id: str, round_id: str, ticket_id: str, receipt_id: str) -> str:
    return sha256_hex(
        _join(
            [
                _HASH_DOMAIN_RECEIPT,
                cabinet_id,
                str(round_id),
                str(ticket_id),
                receipt_id,
            ]
        )
    )


# ---------------------------------------------------------------------------
# Original reveal payloads (what the cabinets hold pieces of, in escrow mode).
# ---------------------------------------------------------------------------


def original_payload(salt: str, value: str) -> bytes:
    """Canonical bytes for a participant's sealed original (salt, value)."""
    return canonical_json({"salt": salt, "value": value})


def parse_original(payload: bytes) -> tuple[str, str]:
    obj = json.loads(payload.decode("utf-8"))
    return obj["salt"], obj["value"]
