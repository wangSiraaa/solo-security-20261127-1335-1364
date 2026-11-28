"""Choosing an eligibility path, and the guarantee each path makes.

Only one path is wired up for the run, per the brief ("pick one").  The choice
determines three things the story asks about:

1. which conclusions the door ends up with,
2. what limited-purpose material each party retains,
3. what the system can still promise when the issuing desk / proof service is
   away.

=================  ==========================  ==============================
                   Path A: pre-issued tokens   Path B: local proof service
=================  ==========================  ==============================
Door learns         (day, tier) cleared         (day, tier) cleared
Retained material   signed token + stub         signed assertion + stub
Unknown date        FAIL (grid is finite)       OK (derived from DOB live)
Desk/authority away FAIL for new dates           OK while service is up
=================  ==========================  ==============================
"""

from __future__ import annotations

from dataclasses import dataclass

from .calendar import Program


@dataclass(frozen=True)
class PathChoice:
    name: str
    door_conclusion: str
    desk_material: str
    door_material: str
    late_date_ok: bool
    promise_when_offscreen_issuer_away: str


PATH_A = PathChoice(
    name="preissue",
    door_conclusion="holder cleared tier T on exactly date D (per signed token)",
    desk_material="finite (date x tier) token grid + desk ledger of stubs sold",
    door_material="the stub, the desk's signature, a wallet-bound token, the admission log",
    late_date_ok=False,
    promise_when_offscreen_issuer_away=(
        "new dates with no pre-issued token cannot be sold or admitted"
    ),
)

PATH_B = PathChoice(
    name="assertion",
    door_conclusion="local proof service signed (day, tier, result=met-on-day) for this wallet",
    desk_material="per-screening assertions + desk ledger of stubs sold",
    door_material="the stub, the desk's signature, the proof-service assertion, the admission log",
    late_date_ok=True,
    promise_when_offscreen_issuer_away=(
        "a proof already issued for that screening still admits; brand-new dates "
        "need the service back online"
    ),
)


def for_mode(mode: str) -> PathChoice:
    return PATH_A if mode == "preissue" else PATH_B


class Unsatisfiable(Exception):
    """The chosen path literally cannot produce the required ticket."""


def plan_program(program: Program, wallet, mode: str,
                 authority=None, service=None) -> list[dict]:
    """Decide, before selling, whether each screening can be proved.

    Returns one entry per screening describing feasibility, so a desk can tell a
    buyer *which* shows it can promise under the current path and whether the
    off-screen issuer / proof service being away changes that.
    """
    out = []
    for i, s in enumerate(program.screenings):
        entry = {
            "index": i,
            "title": s.title,
            "day": s.date.isoformat(),
            "tier": s.age_tier,
            "feasible": False,
            "note": "",
        }
        if mode == "preissue":
            if authority is None:
                entry["note"] = "pre-issue authority away: cannot promise any show"
            elif not authority.can_cover(s.date, s.age_tier):
                entry["note"] = "no pre-issued token for this date/tier"
            elif not wallet.cred.meets(s.age_tier, s.date):
                entry["note"] = "holder below tier"
            else:
                entry["feasible"] = True
                entry["note"] = "covered by pre-issued grid"
        else:
            if service is None or not service.online:
                held = wallet.assertions.get((program.program_id, s.date, s.age_tier))
                if held:
                    entry["feasible"] = True
                    entry["note"] = "service away, but a matching assertion is already held"
                else:
                    entry["note"] = "proof service away and no assertion pre-held"
            elif not wallet.cred.meets(s.age_tier, s.date):
                entry["note"] = "holder below tier on this day"
            else:
                entry["feasible"] = True
                entry["note"] = "proof service can answer for this exact day"
        out.append(entry)
    return out
