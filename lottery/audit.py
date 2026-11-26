"""Audit helpers so an outside reader can classify a round's history.

Given only the published event log, these functions tell apart:

* **延期 (postponement)** – the round was postponed; no winners were published.
* **补开 (supplementary opening / reopening)** – a postponed round was reopened
  under a fresh public witness as a new, linked round.
* **同轮重发 (same-round re-issue)** – an already-open round was re-published;
  the packet hash and winners are identical to the original.

Nothing here trusts the operator's summary; everything is read from the chain.
"""

from __future__ import annotations

from typing import Any


def _events_for(log: list[dict[str, Any]], round_id: str) -> list[dict[str, Any]]:
    out = []
    for e in log:
        d = e.get("data", {})
        if d.get("round_id") == round_id or d.get("reopened_from") == round_id:
            out.append(e)
    return out


def classify_round(log: list[dict[str, Any]], round_id: str) -> dict[str, Any]:
    """Return a structured, event-sourced description of one round's history."""
    events = _events_for(log, round_id)
    created = next((e for e in events if e["type"] == "ROUND_CREATED"), None)
    if created is None:
        raise KeyError(f"no creation event for round {round_id!r}")

    postponed = [e for e in events if e["type"] == "ROUND_POSTPONED"]
    reopened_into = [
        e for e in log
        if e["type"] == "ROUND_REOPENED" and e["data"].get("reopened_from") == round_id
    ]
    reopened_from = created["data"].get("reopened_from", "")
    opened = next((e for e in events if e["type"] == "ROUND_OPENED"), None)
    reissued = [e for e in events if e["type"] == "ROUND_REISSUED"]
    locked = next((e for e in events if e["type"] == "ROUND_LOCKED"), None)

    reasons: list[str] = []
    if reopened_from:
        reasons.append("SUPPLEMENTARY_OPENING")  # 补开
    if postponed and not opened:
        reasons.append("POSTPONED")              # 延期 (winners never published)
    if opened:
        reasons.append("OPENED")
    if reissued:
        reasons.append("REISSUED")               # 同轮重发
    if reopened_into:
        reasons.append("HAS_REOPENINGS")

    return {
        "round_id": round_id,
        "labels": reasons,
        "reopened_from": reopened_from,
        "reopened_into": [e["data"]["round_id"] for e in reopened_into],
        "postponed": bool(postponed),
        "postponement_reasons": [e["data"].get("reason", "") for e in postponed],
        "postponement_witnesses": [e["data"].get("witness", "") for e in postponed],
        "locked": locked is not None,
        "opened": opened is not None,
        "winners": opened["data"].get("winners", []) if opened else [],
        "reissued_hashes": [e["data"].get("original_package_hash") for e in reissued],
        "event_count": len(events),
    }


def rounds_overview(log: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Classify every round that appears in the log."""
    ids = []
    for e in log:
        if e["type"] == "ROUND_CREATED":
            ids.append(e["data"]["round_id"])
    return {rid: classify_round(log, rid) for rid in ids}


def is_possible_redraw(log: list[dict[str, Any]], round_id: str) -> tuple[bool, str]:
    """Detect whether a round's published winners could have been redrawn.

    A round is safe if its winners were fixed by a locked, seeded order and any
    re-issue reproduces the identical packet hash.  A re-issue that changed the
    packet hash, or winners appearing without a ROUND_OPENED, is a red flag.
    """
    info = classify_round(log, round_id)
    if info["winners"] and not info["opened"]:
        return False, "winners published without an opening event"
    if not info["locked"] and info["opened"]:
        return False, "round opened without ever being locked"
    return True, "winners are bound to a locked, sealed order"
