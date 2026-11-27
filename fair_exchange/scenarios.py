"""Deterministic replays of the cabinet facts described in the exercise.

The *same* cabinet facts are used throughout:
  tick 0  order opened, retention = 30
  tick 1  both parties deposit their original
  tick 2  both give a live delegation ("even if I go offline, locking both is
          allowed"); 乙 now goes offline
          -> 甲 stays online.

We then replay two returns of the absent party 乙 under the two settlement
policies the settler may choose:

  A. 乙 comes back and wants to change one character.
  B. 乙 comes back only at the end of the retention period.

Plus the one case that is mandatory regardless of policy: when both parties
are online and give a final confirmation, settlement *must* happen.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, Optional

from .session import Session
from .vault import (
    ABORTED, EFFECTIVE, EXPIRE_ABORT, EXPIRE_FALLBACK_LOCK, EARLY, HOLD,
    PENDING, VaultError,
)

RETENTION = 30
HOLD_WINDOW = 10


def _setup(root: str, policy: str, expiry_action: str = EXPIRE_ABORT) -> Session:
    s = Session(root)
    s.add_party("jia")   # 甲 -- online
    s.add_party("yi")    # 乙 -- offline after tick 2
    s.open_order("o", "jia", "yi", retention_ticks=RETENTION, tick=0)
    s.deposit("o", "jia", b"JIA original, v1", tick=1)
    s.deposit("o", "yi", b"YI original, v1", tick=1)
    s.delegate("o", "jia", tick=2)
    s.delegate("o", "yi", tick=2)
    s.set_policy("o", policy, tick=2, hold_ticks=HOLD_WINDOW,
                 expiry_action=expiry_action)
    return s


# ---------------------------------------------------------------------------
# A. 乙 returns and wants to change one character
# ---------------------------------------------------------------------------

def replay_yi_edit_early(root: str) -> Dict[str, Any]:
    """EARLY_LOCK: the offline delegation locks both at once; 乙's later edit is
    refused because the pair already took effect."""
    s = _setup(root, EARLY)
    st = s.status("o")
    assert st["state"] == EFFECTIVE
    jia_read = s.claim_and_open("o", "jia", tick=3)
    try:
        s.withdraw("o", "yi", tick=4)
        yi_edit = "ALLOWED"
    except VaultError as e:
        yi_edit = f"REJECTED:{e.code}"
    out = {
        "state_at_return": st["state"],
        "jia_read_immediately": True,
        "jia_saw": jia_read["plaintext"].decode(),
        "yi_edit": yi_edit,
        "effective_tick": st["effective_tick"],
    }
    s.close()
    return out


def replay_yi_edit_hold(root: str) -> Dict[str, Any]:
    """HOLD_FOR_CONFIRMATION: the settler reserves a window; 甲 must wait, and
    乙 may recall its delegation, re-upload, and re-confirm on the new pair."""
    s = _setup(root, HOLD)
    st0 = s.status("o")
    assert st0["state"] == PENDING
    try:
        s.claim_token("o", "jia")
        jia_wait = "READ_ALLOWED"
    except VaultError as e:
        jia_wait = f"WAITED:{e.code}"
    s.revoke_delegation("o", "yi", tick=4)
    old = s.vault.status("o")["b_version"]
    new_res = s.deposit("o", "yi", b"YI original, v2", tick=5)
    s.confirm("o", "jia", tick=6)
    s.confirm("o", "yi", tick=6)
    st1 = s.status("o")
    out = {
        "state_at_return": st0["state"],
        "jia_read_immediately": False,
        "jia_during_hold": jia_wait,
        "yi_edit": "ALLOWED",
        "old_yi_digest": old["digest"] if old else None,
        "new_yi_digest": new_res["digest"],
        "state_after_revise": st1["state"],
        "effective_tick": st1["effective_tick"],
    }
    s.close()
    return out


# ---------------------------------------------------------------------------
# B. 乙 comes back only at the end of the retention period
# ---------------------------------------------------------------------------

def replay_yi_end_early(root: str) -> Dict[str, Any]:
    """EARLY_LOCK: the pair is effective; 乙 may still claim through the last
    valid tick of retention, and one tick later the credential is dead."""
    s = _setup(root, EARLY)
    st = s.status("o")
    assert st["state"] == EFFECTIVE
    ru = st["retained_until"]
    inside = s.claim_and_open("o", "yi", tick=ru)  # last valid tick
    tok = [t for t in st["tokens"] if t["party"] == "jia"][0]
    s.advance("o", ru + 1)
    try:
        s.vault.claim("o", "jia", tok["id"], tick=ru + 1)
        late = "ALLOWED"
    except VaultError as e:
        late = f"REJECTED:{e.code}"
    out = {
        "state": st["state"],
        "effective_tick": st["effective_tick"],
        "retained_until": ru,
        "yi_read_at_last_valid_tick": {"tick": ru, "opened": True,
                                       "read_ver": inside["read_ver"]},
        "claim_one_tick_after": late,
    }
    s.close()
    return out


def replay_yi_end_hold_abort(root: str) -> Dict[str, Any]:
    """HOLD + ABORT: the hold window closes with no confirmation, the settler
    aborts; 乙 returns at retention end to find nothing was exchanged."""
    s = _setup(root, HOLD, EXPIRE_ABORT)
    s.advance("o", HOLD_WINDOW + 2)   # hold elapses (tick 12) -> abort
    st = s.status("o")
    assert st["state"] == ABORTED
    s.advance("o", st["retention_ticks"] + 1000)  # years later
    out = {
        "state_at_hold_expiry": st["state"],
        "hold_until": st["hold_until"],
        "state_at_retention_end": s.status("o")["state"],
        "tokens": s.status("o")["tokens"],
        "yi_return_at_retention_end": "NOTHING_TO_READ",
    }
    s.close()
    return out


def replay_yi_end_hold_fallback(root: str) -> Dict[str, Any]:
    """HOLD + FALLBACK_LOCK: on expiry, because both delegations are still live,
    the settler locks then; 乙 returning at retention end can still claim until
    the last valid tick."""
    s = _setup(root, HOLD, EXPIRE_FALLBACK_LOCK)
    s.advance("o", HOLD_WINDOW + 2)   # hold elapses -> fallback lock
    st = s.status("o")
    assert st["state"] == EFFECTIVE
    ru = st["retained_until"]
    inside = s.claim_and_open("o", "yi", tick=ru)
    out = {
        "state_at_hold_expiry": st["state"],
        "effective_tick": st["effective_tick"],
        "retained_until": ru,
        "yi_read_at_last_valid_tick": {"tick": ru, "opened": True,
                                       "read_ver": inside["read_ver"]},
    }
    s.close()
    return out


# ---------------------------------------------------------------------------
# Mandatory: both online, both confirm
# ---------------------------------------------------------------------------

def replay_both_confirm_mandatory(root: str) -> Dict[str, Any]:
    """Under *any* policy, once both parties are online and both give a final
    confirmation, settlement is mandatory."""
    results = {}
    # choose a policy that would otherwise never settle (NONE) and one in HOLD
    for policy in ("NONE", HOLD):
        s = _setup(os.path.join(root, policy.replace(" ", "_")), policy)
        # under NONE the delegations do not settle; under HOLD they are deferred
        assert s.status("o")["state"] == PENDING
        s.confirm("o", "jia", tick=3)
        assert s.status("o")["state"] == PENDING
        s.confirm("o", "yi", tick=3)
        st = s.status("o")
        assert st["state"] == EFFECTIVE, (policy, st["state"])
        results[policy] = {
            "state": st["state"],
            "effective_tick": st["effective_tick"],
            "tokens": len(st["tokens"]),
        }
        s.close()
    return results


# ---------------------------------------------------------------------------

def replay_output(root: Optional[str] = None) -> Dict[str, Any]:
    base = root or tempfile.mkdtemp(prefix="fair-")
    out: Dict[str, Any] = {
        "cabinet_facts": {
            "retention": RETENTION,
            "hold_window": HOLD_WINDOW,
            "tick_0": "order opened",
            "tick_1": "both deposited",
            "tick_2": "both delegated; yi goes offline; jia online",
            "jia": "online",
            "yi": "offline after tick 2",
        },
        "A_yi_returns_to_edit_one_char": {
            "EARLY_LOCK": replay_yi_edit_early(os.path.join(base, "edit_early")),
            "HOLD_FOR_CONFIRMATION": replay_yi_edit_hold(os.path.join(base, "edit_hold")),
        },
        "B_yi_returns_only_at_retention_end": {
            "EARLY_LOCK": replay_yi_end_early(os.path.join(base, "end_early")),
            "HOLD_FOR_CONFIRMATION_ABORT": replay_yi_end_hold_abort(
                os.path.join(base, "end_hold_abort")),
            "HOLD_FOR_CONFIRMATION_FALLBACK_LOCK": replay_yi_end_hold_fallback(
                os.path.join(base, "end_hold_fallback")),
        },
        "mandatory_both_online_confirm": replay_both_confirm_mandatory(
            os.path.join(base, "both_confirm")),
    }
    return out


if __name__ == "__main__":
    print(json.dumps(replay_output(), indent=2, ensure_ascii=False))
