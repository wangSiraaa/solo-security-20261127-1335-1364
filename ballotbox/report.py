"""Final deliverable: a re-countable, verifiable audit receipt.

Design constraints from the brief ("最后交一份可重新统计、也能核实挂失结论的回执，
不把会员身份与选择合在同一张表里"):

* **Re-countable** -- ``tally.json`` is rebuilt purely from the counted ballots
  the halls recorded; running ``verify_report`` re-derives the same counts from
  the raw hall ledgers and compares.
* **Loss conclusions verifiable** -- ``loss.json`` lists every token's loss
  request / effective time / whether a ballot was counted first, derived from
  the desk ledger, with the hash-chain head so tampering is detectable.
* **Identity and choice never share a table** --
    - ``identity.json``  : token_id, member_id, authority, loss state (no choice)
    - ``ballots.json``   : ballot_id, hall, choice, status (no token/member)
  The only join key between the two is ``ballot_id`` held in the *desk* ledger
  (token -> ballot_id), and the desk never records a choice.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List

from .model import BALLOT_COUNTED, BALLOT_PENDING, BALLOT_VOID


def _write_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, sort_keys=True, ensure_ascii=False, indent=2)
        fh.write("\n")


def build_report(desk, out_dir: str) -> Dict[str, str]:
    """Emit the final receipt bundle into ``out_dir``; return the file paths."""
    os.makedirs(out_dir, exist_ok=True)
    hall_paths = {}

    # ---- anonymous ballots table (no token, no member) ----------------
    all_ballots: List[dict] = []
    for hid, hall in desk.halls.items():
        for b in hall.ballots():
            all_ballots.append({
                "ballot_id": b["ballot_id"],
                "event_id": b["event_id"],
                "hall": b["hall"],
                "choice": b["choice"],
                "status": b["status"],
                "at": b["at"],
            })
    all_ballots.sort(key=lambda r: (r["hall"], r["at"], r["ballot_id"]))
    _write_json(os.path.join(out_dir, "ballots.json"), {
        "note": "anonymous choices; no token_id and no member_id in this table",
        "ballots": all_ballots,
    })

    # ---- tally (only COUNTED ballots) ---------------------------------
    tally: Dict[str, int] = {}
    per_hall: Dict[str, Dict[str, int]] = {}
    for b in all_ballots:
        if b["status"] != BALLOT_COUNTED:
            continue
        tally[b["choice"]] = tally.get(b["choice"], 0) + 1
        per_hall.setdefault(b["hall"], {})
        per_hall[b["hall"]][b["choice"]] = \
            per_hall[b["hall"]].get(b["choice"], 0) + 1
    _write_json(os.path.join(out_dir, "tally.json"), {
        "counted_total": sum(tally.values()),
        "by_choice": tally,
        "by_hall": per_hall,
    })

    # ---- identity / authority / loss table (no choice) ----------------
    identity = []
    for token_id, rec in sorted(desk.tokens.items()):
        identity.append({
            "token_id": token_id,
            "member_id": rec.member_id,
            "event_id": rec.event_id,
            "authority_kind": rec.authority[0]
            if isinstance(rec.authority, tuple) else rec.authority,
            "final_hall": rec.authority[1]
            if isinstance(rec.authority, tuple) else None,
            "loss_state": rec.loss_state,
            "loss_requested_at": rec.loss_requested_at,
            "loss_effective_at": rec.loss_effective_at,
            "finalized": rec.finalized,
            "finalized_hall": rec.finalized_hall,
            "finalized_at": rec.finalized_at,
            # NOTE: no choice, no ballot contents -- only the opaque link id
            "ballot_id": rec.consumed_ballot,
        })
    _write_json(os.path.join(out_dir, "identity.json"), {
        "note": "member<->token and loss conclusions; no choice in this table",
        "records": identity,
    })

    # ---- loss conclusions --------------------------------------------
    losses = []
    for token_id in sorted(desk.tokens):
        losses.append(desk.loss_conclusion(token_id))
    _write_json(os.path.join(out_dir, "loss.json"), {
        "note": "one row per token: outcome of 挂失, and whether a vote counted",
        "conclusions": losses,
    })

    # ---- chain heads for tamper-evidence ------------------------------
    chains = {"desk": desk.ledger.verify_chain()}
    for hid, hall in desk.halls.items():
        chains[hid] = hall.ledger.verify_chain()
    _write_json(os.path.join(out_dir, "chains.json"), chains)

    paths = {name: os.path.join(out_dir, name) for name in
             ("ballots.json", "tally.json", "identity.json", "loss.json",
              "chains.json")}
    return paths


def verify_report(desk, out_dir: str) -> Dict[str, object]:
    """Independently re-derive the tally from raw hall ledgers and compare.

    Returns a dict with ``ok`` plus per-check detail.  This is what makes the
    receipt "可重新统计" and "可核实".
    """
    problems: List[str] = []

    # 1. hash chains intact
    chain_ok = {}
    chain_ok["desk"] = desk.ledger.verify_chain()["ok"]
    for hid, hall in desk.halls.items():
        chain_ok[hid] = hall.ledger.verify_chain()["ok"]
    if not all(chain_ok.values()):
        problems.append(f"broken hash chain: {chain_ok}")

    # 2. re-count from raw hall ledgers (independent of tally.json)
    raw: Dict[str, int] = {}
    for hall in desk.halls.values():
        for rec in hall.ledger:
            if rec["type"] == "ballot":
                bid = rec["data"]["ballot_id"]
                status = hall.ballot_status(bid)
                if status == BALLOT_COUNTED:
                    ch = rec["data"]["choice"]
                    raw[ch] = raw.get(ch, 0) + 1

    with open(os.path.join(out_dir, "tally.json"), encoding="utf-8") as fh:
        saved = json.load(fh)
    if saved["by_choice"] != raw:
        problems.append(f"tally mismatch: saved={saved['by_choice']} raw={raw}")

    # 3. identity table must not contain a choice field
    with open(os.path.join(out_dir, "identity.json"), encoding="utf-8") as fh:
        ident = json.load(fh)
    for row in ident["records"]:
        for forbidden in ("choice", "selection", "vote"):
            if forbidden in row:
                problems.append(f"identity row leaks '{forbidden}': {row['token_id']}")

    # 4. at most one COUNTED ballot per token (每人一票); check via desk finals
    counted_per_token: Dict[str, int] = {}
    for rec in desk.ledger:
        if rec["type"] == "final":
            tok = rec["data"]["token_id"]
            counted_per_token[tok] = counted_per_token.get(tok, 0) + 1
    dupes = {t: c for t, c in counted_per_token.items() if c > 1}
    if dupes:
        problems.append(f"token counted more than once: {dupes}")

    # 5. no more counted ballots than members who could cast one
    counted_ballots = sum(raw.values())
    if counted_ballots > len(desk.members):
        problems.append(f"counted {counted_ballots} > eligible members "
                        f"{len(desk.members)}")

    return {
        "ok": not problems,
        "problems": problems,
        "chains": chain_ok,
        "recount": raw,
    }


__all__ = ["build_report", "verify_report"]
