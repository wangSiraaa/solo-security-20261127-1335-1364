"""Runs the whole story end-to-end for a chosen eligibility path.

The narrative (matching the brief):

 1. Desk registers a weekend programme with two age tiers, with a *gap day*
    between the two screenings.
 2. Two buyers get drill credentials in their own wallets.
 3. A ticket that buys both screenings in one bundle, one stub each.
 4. One stub is taken to the door; the desk is then shut.  A *fresh* usher
    terminal -- holding only this screening, the earlier admission log, and the
    buyer's presentation -- posts the entry and an exit receipt; showing the same
    stub again takes no extra seat.
 5. A different buyer whose birthday falls between the two shows is eligible for
    the late (12+) show but not the early (18+) show.  A bundle built from her
    late proof plus *someone else's* early proof is refused (can't stitch two
    viewers), and her own full bundle is refused for the early show.
 6. A viewer who turns the late-show tier age exactly on the late-show day has
    valid late eligibility; when the screening is moved one day earlier, the old
    stub must NOT admit, while already-posted receipts stay queryable.
 7. A second programme / birthday: verification from the normal entrance, and an
    audit of what each counter actually retained -- can the same viewer be joined
    across shops?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from . import audit, crypto
from .assertion import LocalProofService
from .calendar import Program, Screening, days, default_program
from .credential import LocalCredentialIssuer
from .door import DoorTerminal, TicketView
from .paths import for_mode
from .preissue import PreIssueAuthority
from .tickets import IssuedTicket, MixedHolderError, TicketDesk
from .wallet import NotEligible, Wallet


@dataclass
class StoryResult:
    path: str
    steps: dict = field(default_factory=dict)
    assertions: dict = field(default_factory=dict)


def _new_viewer(issuer: LocalCredentialIssuer, mode: str, dob: date) -> Wallet:
    return Wallet(issuer.mint(dob), mode)


def run(mode: str = "assertion", *, program: Program | None = None) -> StoryResult:
    """Execute the narrative.  Returns evidence the tests assert on."""
    choice = for_mode(mode)
    res = StoryResult(path=choice.name)
    steps = res.steps

    # ---- 1. desk registers the weekend programme ----------------------
    program = program or default_program()
    early_i, late_i = 0, 1
    early, late = program.screening(early_i), program.screening(late_i)
    steps["program"] = {
        "id": program.program_id,
        "screenings": [
            {"title": s.title, "day": s.date.isoformat(), "tier": s.age_tier}
            for s in program.screenings
        ],
        "gap_days_between": (late.date - early.date).days,
    }

    # ---- shared authorities -------------------------------------------
    issuer = LocalCredentialIssuer()
    service = LocalProofService()
    authority = PreIssueAuthority(
        "preissue-desk", program.program_id, program.dates, sorted(set(program.tiers))
    )
    trusted = {"preissue": authority.public, "assertion": service.public}
    desk = TicketDesk("desk-1", program, trusted)
    steps["path_choice"] = {
        "name": choice.name,
        "door_conclusion": choice.door_conclusion,
        "late_date_ok": choice.late_date_ok,
        "promise_when_issuer_away": choice.promise_when_offscreen_issuer_away,
    }

    def equip(wallet: Wallet, prog: Program, indices: list[int],
              auth=authority, svc=service) -> None:
        for i in indices:
            s = prog.screening(i)
            if mode == "preissue":
                wallet.obtain_preissued(auth, prog.program_id, s.date, s.age_tier)
            else:
                wallet.prefetch_assertion(svc, prog.program_id, s.date, s.age_tier)

    # ---- 2. two buyers get drill credentials --------------------------
    alice = _new_viewer(issuer, mode, date(1990, 5, 1))   # adult throughout
    bob = _new_viewer(issuer, mode, date(1985, 2, 2))     # adult throughout
    steps["buyers"] = {
        "alice_age_early": alice.cred.age_on(early.date),
        "alice_age_late": alice.cred.age_on(late.date),
        "bob_age_early": bob.cred.age_on(early.date),
        "bob_age_late": bob.cred.age_on(late.date),
    }

    # ---- 3. a full two-show bundle for one holder ---------------------
    equip(alice, program, [early_i, late_i])
    alice_proofs = {
        i: alice.eligibility_for(program, i, authority=authority, service=service)
        for i in (early_i, late_i)
    }
    alice_sale = desk.sell(alice_proofs)
    alice_ticket = IssuedTicket(alice_sale, alice.binding_key(program.program_id), alice_proofs)
    steps["alice_bundle"] = {
        "ticket_id": alice_sale.ticket_id,
        "stub_indices": sorted(alice_sale.stubs),
        "single_holder": len({(s.holder_handle, s.binding_pub.value) for s in alice_sale.stubs.values()}) == 1,
        "holder_handle": alice_sale.holder_handle,
    }
    res.assertions["alice_binding_pub"] = alice_sale.binding_pub.hex

    # ---- 4. one stub to the door, desk shuts, fresh usher terminal ----
    nonce1 = crypto.sha256(b"door-nonce-1").hex()
    presentation = alice_ticket.present(early_i, nonce1)
    steps["presentation_has_no_dob"] = alice.cred.dob.isoformat() not in repr(presentation)
    steps["presentation_has_no_global_id"] = alice.cred.exercise_id not in repr(presentation)

    # Build the door only from public info + history; the desk is shut.  The door
    # is given only the *public* key(s) of the active path, never the desk's.
    min_door_trust = {mode: trusted[mode]}
    door = DoorTerminal(
        "usher-1",
        TicketView(program.program_id, program.fingerprint(), early_i, early.date, early.age_tier),
        desk.public,
        trusted=min_door_trust,
    )
    entry = door.admit(presentation, nonce1)
    steps["entry"] = {"accepted": entry["accepted"], "seat": entry.get("seat")}

    # Same stub again -> no extra seat.
    nonce2 = crypto.sha256(b"door-nonce-2").hex()
    replay = door.admit(alice_ticket.present(early_i, nonce2), nonce2)
    steps["replay"] = {
        "accepted": replay["accepted"],
        "reason": replay.get("reason"),
        "seats_used": door.admitted_count(),
        "already_seat": replay.get("already_seat"),
    }

    # Exit receipt from the same terminal.
    exit_nonce = crypto.sha256(b"door-exit").hex()
    leave = door.depart(alice_ticket.present(early_i, exit_nonce), exit_nonce)
    steps["exit"] = {
        "accepted": leave["accepted"],
        "seat": leave.get("seat"),
        "receipt_valid": door.receipt_valid(leave["receipt"], "exit_receipt"),
    }

    # Re-showing the same stub after leaving must still take no new seat.
    reentry_nonce = crypto.sha256(b"door-reentry").hex()
    reentry = door.admit(alice_ticket.present(early_i, reentry_nonce), reentry_nonce)
    steps["reentry_same_stub"] = {
        "accepted": reentry["accepted"],
        "reason": reentry.get("reason"),
        "seats_used": door.admitted_count(),
        "inside": door.inside_count(),
    }
    steps["entry_receipt_valid"] = door.receipt_valid(entry["receipt"], "entry_receipt")

    # The door sees only its own screening's records, never the other show.
    door_material = audit.collect_door_material(door)
    steps["door_sees_only_early"] = audit.door_sees_only_its_screening(
        door_material, other_indices=[late_i]
    )

    # ---- 5. birthday between the two shows ----------------------------
    # Carol turns 18 on the gap day: 17 at the early 18+ gala, 18 by the late
    # 12+ show.  She is eligible for late only.
    gap_day = early.date + timedelta(days=1)
    carol_dob = date(gap_day.year - 18, gap_day.month, gap_day.day)
    carol = _new_viewer(issuer, mode, carol_dob)
    steps["carol"] = {
        "dob": carol_dob.isoformat(),
        "birthday_is_gap_day": carol_dob.replace(year=carol_dob.year + 18) == gap_day,
        "age_early": carol.cred.age_on(early.date),
        "age_late": carol.cred.age_on(late.date),
        "early_eligible": carol.cred.meets(early.age_tier, early.date),
        "late_eligible": carol.cred.meets(late.age_tier, late.date),
    }

    # Carol can prove her late (12+) eligibility.
    equip(carol, program, [late_i])
    carol_late_proof = carol.eligibility_for(program, late_i, authority=authority, service=service)

    # Alice's early (18+) proof -- someone else's eligibility.
    alice_early_proof = alice.eligibility_for(program, early_i, authority=authority, service=service)

    # Attempt 1: stitch Carol's late proof with Alice's early proof -> refused.
    try:
        desk.sell({early_i: alice_early_proof, late_i: carol_late_proof})
        steps["stitch_rejected"] = False
        steps["stitch_reason"] = None
    except MixedHolderError as exc:
        steps["stitch_rejected"] = True
        steps["stitch_reason"] = str(exc)

    # Attempt 2: Carol tries her own full bundle -> refused for the early show.
    try:
        carol_early_proof = carol.eligibility_for(program, early_i, authority=authority, service=service)
        desk.sell({early_i: carol_early_proof, late_i: carol_late_proof})
        steps["carol_own_bundle_sold"] = False
        steps["carol_early_refusal"] = None
    except NotEligible as exc:
        steps["carol_own_bundle_sold"] = False
        steps["carol_early_refusal"] = str(exc)

    # Carol's own *late-only* ticket is fine (she's 12+ eligible there).
    carol_late_sale = desk.sell({late_i: carol_late_proof})
    steps["carol_late_only_ticket"] = carol_late_sale.ticket_id

    # Desk books: no seat was ever created by the refused attempts.  Alice (2)
    # + Carol late-only (1) = 3 seats; early has just Alice's one.
    steps["desk_books_after_carol"] = desk.reconcile()

    # ---- 6. viewer who turns the late tier age on the late-show day ---
    dave_dob = date(late.date.year - late.age_tier, late.date.month, late.date.day)
    dave = _new_viewer(issuer, mode, dave_dob)
    steps["dave"] = {
        "dob": dave_dob.isoformat(),
        "age_on_late_day": dave.cred.age_on(late.date),
        "age_one_day_earlier": dave.cred.age_on(late.date - timedelta(days=1)),
        "late_eligible_on_day": dave.cred.meets(late.age_tier, late.date),
    }
    equip(dave, program, [late_i])
    dave_proof = dave.eligibility_for(program, late_i, authority=authority, service=service)
    dave_sale = desk.sell({late_i: dave_proof})
    dave_ticket = IssuedTicket(dave_sale, dave.binding_key(program.program_id), {late_i: dave_proof})

    # Move the late screening one day earlier and open a fresh door for it.
    moved_date = late.date - timedelta(days=1)
    door2 = DoorTerminal(
        "usher-2",
        TicketView(program.program_id, program.fingerprint(), late_i, moved_date, late.age_tier),
        desk.public,
        trusted=min_door_trust,
    )
    n = crypto.sha256(b"dave-moved").hex()
    moved_result = door2.admit(dave_ticket.present(late_i, n), n)
    steps["dave_moved_date"] = {
        "moved_to": moved_date.isoformat(),
        "accepted": moved_result["accepted"],
        "reason": moved_result.get("reason"),
        "seats_used": door2.admitted_count(),
    }
    # The original terminal's already-posted receipts remain queryable and valid.
    steps["prior_admissions_still_queryable"] = {
        "count": len([r for r in door.history() if r["accepted"] and r["kind"] == "entry"]),
        "entry_receipt_still_valid": door.receipt_valid(entry["receipt"], "entry_receipt"),
    }

    # ---- 7. second programme + audit at the normal entrance -----------
    program2 = Program(
        "weekend-2026-w50",
        [
            Screening("Friday opening", days(3), 6),
            Screening("Saturday gala", days(5), 16),
        ],
    )
    authority2 = PreIssueAuthority(
        "preissue-desk-2", program2.program_id, program2.dates, sorted(set(program2.tiers))
    )
    desk2 = TicketDesk("desk-2", program2,
                       {"preissue": authority2.public, "assertion": service.public})

    # The SAME viewer, Erin, attends both shops with one credential.
    erin = _new_viewer(issuer, mode, date(1995, 6, 6))

    equip(erin, program, [early_i, late_i])
    erin_p1 = {i: erin.eligibility_for(program, i, authority=authority, service=service)
               for i in (early_i, late_i)}
    desk.sell(erin_p1)

    equip(erin, program2, [0, 1], auth=authority2, svc=service)
    erin_p2 = {i: erin.eligibility_for(program2, i, authority=authority2, service=service)
               for i in (0, 1)}
    erin_sale2 = desk2.sell(erin_p2)

    # Erin verifies at shop 2's own normal entrance.
    shop2_door = DoorTerminal(
        "usher-shop2",
        TicketView(program2.program_id, program2.fingerprint(), 1,
                   program2.screening(1).date, program2.screening(1).age_tier),
        desk2.public,
        trusted={"preissue": authority2.public, "assertion": service.public},
    )
    shop2_ticket = IssuedTicket(erin_sale2, erin.binding_key(program2.program_id), erin_p2)
    n2 = crypto.sha256(b"erin-shop2").hex()
    shop2_entry = shop2_door.admit(shop2_ticket.present(1, n2), n2)
    steps["shop2_normal_entrance"] = {
        "accepted": shop2_entry["accepted"],
        "seat": shop2_entry.get("seat"),
    }

    steps["erin_two_shops"] = {"shop1": program.program_id, "shop2": program2.program_id}

    # Audit what each counter retained.
    desk1_mat = audit.collect_desk_material(desk, choice.name)
    desk2_mat = audit.collect_desk_material(desk2, choice.name)
    steps["desk1_privacy"] = audit.audit_privacy(desk1_mat, erin.cred.dob.isoformat())
    steps["desk2_privacy"] = audit.audit_privacy(desk2_mat, erin.cred.dob.isoformat())
    steps["door_privacy"] = audit.audit_privacy(door_material, alice.cred.dob.isoformat())
    steps["cross_shop"] = audit.try_cross_desk_correlation(desk1_mat, desk2_mat)

    # Erin's handle is stable inside a programme, unlinkable across programmes.
    res.assertions["erin_handle_p1"] = erin.nullifier(program.program_id)
    res.assertions["erin_handle_p2"] = erin.nullifier(program2.program_id)
    res.assertions["handle_stable_within_program"] = (
        erin.nullifier(program.program_id) == erin.nullifier(program.program_id)
    )
    res.assertions["handle_differs_across_programs"] = (
        erin.nullifier(program.program_id) != erin.nullifier(program2.program_id)
    )
    res.assertions["cross_shop_correlatable"] = steps["cross_shop"]["correlatable"]

    return res


if __name__ == "__main__":  # pragma: no cover
    import json
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "assertion"
    result = run(mode)
    print(json.dumps({"path": result.path, "steps": result.steps,
                      "assertions": result.assertions}, indent=2, default=str))
