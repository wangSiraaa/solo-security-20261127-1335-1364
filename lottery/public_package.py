"""Writing public packages to disk, and the independent verifier.

The verifier deliberately shares *no* mutable state with ``DrawService``.  It
takes only the published packet (plus the event log for chain checks) and
recomputes everything with the same public primitives.  This models the outside
reader: "外部读者应能从真正发布的材料复算名单和结果".
"""

from __future__ import annotations

import json
import os

from .crypto import (
    CUTOFF_BY_SHIFT,
    SHIFT_COVERS,
    commitment,
    compute_draw,
    digest,
    promote_replacements,
    seal_digest,
)
from .models import VerificationResult, verify_event_chain

DEFAULT_PACKAGE_DIR = os.path.join(os.getcwd(), "public_packages")


def write_package(package_dir: str, round_id: str, pkg: dict) -> str:
    os.makedirs(package_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in round_id)
    path = os.path.join(package_dir, f"{safe}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(pkg, fh, sort_keys=True, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def load_package(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def verify_package(pkg: dict) -> VerificationResult:
    """Recompute a round's winners purely from the published packet.

    Checks, in order:
      1. packet hash integrity;
      2. the sealed rules digest recomputes from the published roster/rules;
      3. every published commitment recomputes from its reveal;
      4. no revealed ticket is outside the sealed roster;
      5. the seed, draw order and winners all recompute;
      6. household uniqueness holds among the winners.
    """
    res = VerificationResult(ok=False)

    # 1. packet hash
    body = {k: v for k, v in pkg.items() if k != "packet_hash"}
    expected_hash = digest(body)
    res.add(
        "packet_hash",
        pkg.get("packet_hash") == expected_hash,
        "packet_hash does not match packet contents",
    )

    # 2. seal digest
    seal = pkg.get("seal", {})
    expected_seal = seal_digest(
        seal.get("round_id", ""),
        seal.get("roster", []),
        seal.get("quota", 0),
        seal.get("mixing_rule", ""),
        seal.get("witness_shift", ""),
    )
    res.add(
        "seal_hash",
        pkg.get("seal_hash") == expected_seal,
        "sealed rules digest does not recompute from published material",
    )

    roster = seal.get("roster", [])
    roster_ids = [t["ticket_id"] for t in roster]
    quota = seal.get("quota", 0)
    mixing_rule = seal.get("mixing_rule", "")

    # 2b. the top-level copies must agree with the sealed copy
    res.add(
        "roster_consistent",
        pkg.get("roster") == roster,
        "top-level roster does not match the sealed roster",
    )
    res.add(
        "quota_consistent",
        pkg.get("quota") == quota,
        "top-level quota does not match the sealed quota",
    )
    res.add(
        "mixing_rule_consistent",
        pkg.get("mixing_rule") == mixing_rule,
        "top-level mixing rule does not match the sealed rule",
    )

    # 3. commitment binding
    commitments = pkg.get("commitments", {})
    reveals = pkg.get("reveals", {})
    bind_ok = True
    detail = ""
    for tid in roster_ids:
        c = commitments.get(tid)
        r = reveals.get(tid)
        if c is None:
            bind_ok = False
            detail = f"missing commitment for {tid!r}"
            break
        if r is None:
            bind_ok = False
            detail = f"missing reveal for {tid!r}"
            break
        if commitment(pkg["round_id"], tid, r["salt"], r["value"]) != c:
            bind_ok = False
            detail = f"reveal for {tid!r} does not match commitment"
            break
    res.add("commitments_bind_reveals", bind_ok, detail)

    # 4. no stray reveal
    stray = set(reveals) - set(roster_ids)
    res.add(
        "no_stray_reveals",
        not stray,
        f"revealed tickets outside sealed roster: {sorted(stray)}",
    )

    # 4b. sources consistent with the sealed witness shift (one shift only)
    shift = seal.get("witness_shift", "")
    expected_source = SHIFT_COVERS.get(shift)
    bad_sources = [
        t["ticket_id"]
        for t in roster
        if expected_source is not None and t.get("source") != expected_source
    ]
    res.add(
        "sources_match_shift",
        expected_source is not None and not bad_sources,
        f"tickets {bad_sources} do not match the sealed {shift!r} shift",
    )
    res.add(
        "cutoff_matches_shift",
        seal.get("cutoff") == CUTOFF_BY_SHIFT.get(shift),
        "published cutoff rule does not match the sealed witness shift",
    )

    if not all(res.checks.get(k, False) for k in (
        "packet_hash", "seal_hash", "roster_consistent", "quota_consistent",
        "mixing_rule_consistent", "commitments_bind_reveals", "no_stray_reveals",
        "sources_match_shift", "cutoff_matches_shift",
    )):
        res.ok = False
        return res

    # 5. recompute the draw
    try:
        seed, draw_order, winners = compute_draw(roster, quota, mixing_rule, reveals)
    except Exception as exc:  # noqa: BLE001 - surface any recompute failure
        res.add("recompute", False, f"recompute failed: {exc}")
        res.ok = False
        return res

    res.recomputed_winners = winners
    res.recomputed_draw_order = draw_order
    res.add("seed_matches", pkg.get("seed") == seed, "published seed does not recompute")
    res.add(
        "draw_order_matches",
        pkg.get("draw_order") == draw_order,
        "published draw order does not recompute",
    )
    # The immutable draw result must recompute.
    published_draw = pkg.get("draw_winners", pkg.get("winners", []))
    res.add(
        "draw_winners_match",
        published_draw == winners,
        "published draw winners do not recompute from public material",
    )

    # 6. one seat per household, among the immutable draw winners
    hh_by_ticket = {t["ticket_id"]: t["household_id"] for t in roster}
    winner_households = [hh_by_ticket[w] for w in winners if w in hh_by_ticket]
    res.add(
        "household_unique",
        len(winner_households) == len(set(winner_households)),
        "a household occupies more than one winning seat",
    )

    # 7. replay replacements from the sealed order and check effective seats.
    effective, repl_ok, repl_detail = replay_replacements(pkg, draw_order, hh_by_ticket)
    res.add("replacements_replay", repl_ok, repl_detail)
    res.add(
        "effective_winners_match",
        pkg.get("winners") == effective,
        "effective seat holders do not match the replayed replacement history",
    )
    res.recomputed_winners = effective

    res.ok = all(res.checks.values())
    return res


def replay_replacements(
    pkg: dict, draw_order: list[str], hh_by_ticket: dict[str, str]
) -> tuple[list[str], bool, str]:
    """Replay the published replacement steps against the sealed order.

    Each step names who was absent, who attended on site, and which tickets were
    promoted.  We independently recompute the promoted tickets with the shared
    replacement rule and require equality.
    """
    draw_winners = pkg.get("draw_winners", pkg.get("winners", []))
    current = list(draw_winners)
    claimed_households: set[str] = set()
    for step in pkg.get("replacements", []):
        absent = step.get("absent", [])
        attended = step.get("attended", [])
        expected_promoted = step.get("replacements", [])
        promoted = promote_replacements(
            draw_order,
            current,
            absent,
            attended,
            claimed_households,
            hh_by_ticket,
        )
        if promoted != expected_promoted:
            return current, False, (
                f"replacement step promotes {expected_promoted} but sealed order yields {promoted}"
            )
        for w in absent:
            if w in hh_by_ticket:
                claimed_households.add(hh_by_ticket[w])
        current = [w for w in current if w not in set(absent)] + promoted
    return current, True, ""


def verify_round(material: dict) -> VerificationResult:
    """Verify a round from a complete public material bundle.

    ``material`` may contain ``package`` (or ``packages`` list) and ``events``.
    Event-chain integrity is checked when events are supplied.
    """
    res = verify_package(material["package"])
    events = material.get("events")
    if events is not None:
        chain_ok, chain_detail = verify_event_chain(events)
        res.add("event_chain", chain_ok, chain_detail or "event chain broken")
        res.ok = res.ok and chain_ok
    return res
