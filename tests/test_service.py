"""Service-level tests for the draw lifecycle and the brief's acceptance cases."""

import tempfile
import unittest

from lottery import (
    CommitmentMismatch,
    DrawError,
    DrawService,
    DuplicateSettlement,
    RulesLocked,
    RevealGate,
    UploadError,
    WrongState,
    verify_package,
)
from lottery.audit import classify_round, is_possible_redraw
from lottery.models import verify_event_chain
from lottery.scenarios import (
    commit_all,
    escrow_all,
    incident_cabinet_offline_submitters_present,
    incident_escrow_absent_depositor,
    make_participants,
    reveal_all,
)


def fresh_service():
    return DrawService(package_dir=tempfile.mkdtemp())


def three_escrow(svc, rid="R1", hhs=("H1", "H2", "H3"), quota=2):
    parts = make_participants(
        [(f"T{i+1}", hhs[i]) for i in range(3)], source="escrow"
    )
    svc.create_round(
        rid,
        [p.as_ticket() for p in parts],
        quota=quota,
        mixing_rule="sha256-order-v1",
        witness_shift="escrow",
    )
    commit_all(svc, rid, parts)
    escrow_all(svc, rid, parts)
    return parts


class TestBaseline(unittest.TestCase):
    """Baseline uses three tickets; a reviewer recomputes and issues a receipt."""

    def test_three_ticket_baseline(self):
        svc = fresh_service()
        parts = three_escrow(svc)
        out = svc.open_round("R1", witness="reviewer")
        self.assertTrue(out.opened)
        self.assertEqual(len(out.winners), 2)

        res = verify_package(svc.package("R1"))
        self.assertTrue(res.ok, res.errors)
        self.assertEqual(res.recomputed_winners, out.winners)

        # reviewer issues a real claim receipt for one winner
        winner = out.winners[0]
        receipt = svc.claim("R1", winner)
        self.assertEqual(receipt.ticket_id, winner)
        self.assertTrue(receipt.receipt_hash)

    def test_sealed_before_any_reveal(self):
        svc = fresh_service()
        parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="escrow")
        svc.create_round(
            "R1", [p.as_ticket() for p in parts], 2, "sha256-order-v1", "escrow"
        )
        rnd = svc.rounds["R1"]
        self.assertEqual(rnd.state, "SEALED")
        self.assertTrue(rnd.seal_hash)
        # commitment comes after seal
        svc.commit("R1", "T1", salt="s", value="v")
        self.assertEqual(len(svc.rounds["R1"].commitments), 1)

    def test_cannot_commit_before_seal(self):
        svc = fresh_service()
        parts = make_participants([("T1", "H1")], source="escrow")
        svc.create_round("R1", [p.as_ticket() for p in parts], 1, "m", "escrow")
        # already sealed, so committing works; creating a second then unsealing is not exposed
        with self.assertRaises(WrongState):
            svc.rounds["R1"].state = "COMMITTING"
            svc.commit("R1", "T1", salt="s", value="v")


class TestTamperAndRestore(unittest.TestCase):
    """Swap one reveal for a value that contradicts its commitment -> fails."""

    def test_tampered_reveal_fails_then_restore_passes(self):
        import copy

        from lottery.crypto import digest

        svc = fresh_service()
        parts = three_escrow(svc)
        out = svc.open_round("R1", witness="reviewer")
        pkg = svc.package("R1")

        bad = copy.deepcopy(pkg)
        victim = pkg["draw_winners"][0]
        bad["reveals"][victim]["value"] = "forged"
        bad["packet_hash"] = digest({k: v for k, v in bad.items() if k != "packet_hash"})
        res_bad = verify_package(bad)
        self.assertFalse(res_bad.ok)
        self.assertFalse(res_bad.checks["commitments_bind_reveals"])

        res_ok = verify_package(copy.deepcopy(pkg))
        self.assertTrue(res_ok.ok, res_ok.errors)

    def test_service_rejects_mismatched_reveal(self):
        svc = fresh_service()
        parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="onsite")
        svc.create_round("R1", [p.as_ticket() for p in parts], 2, "m", "opening")
        commit_all(svc, "R1", parts)
        with self.assertRaises(CommitmentMismatch):
            svc.submit_reveal("R1", "T1", "wrong-salt", "wrong-value")
        self.assertEqual(svc.rounds["R1"].state, "INVALID")


class TestDuplicateClaim(unittest.TestCase):
    """Once the claims ledger shows a seat handed out, a re-claim is refused."""

    def test_duplicate_claim_refused(self):
        svc = fresh_service()
        parts = three_escrow(svc)
        out = svc.open_round("R1", witness="reviewer")
        winner = out.winners[0]
        svc.mark_settled("R1", winner)  # ledger shows it handed out
        with self.assertRaises(DuplicateSettlement):
            svc.claim("R1", winner)

    def test_claim_twice_refused(self):
        svc = fresh_service()
        out = svc.open_round("R1", witness="r") if False else None  # noqa
        parts = three_escrow(svc)
        out = svc.open_round("R1", witness="reviewer")
        w = out.winners[0]
        svc.claim("R1", w)
        with self.assertRaises(DuplicateSettlement):
            svc.claim("R1", w)

    def test_household_claims_once_across_sibling_tickets(self):
        svc = fresh_service()
        parts = make_participants(
            [("T1", "H1"), ("T2", "H1"), ("T3", "H2")], source="escrow"
        )
        svc.create_round("R1", [p.as_ticket() for p in parts], 2, "m", "escrow")
        commit_all(svc, "R1", parts)
        escrow_all(svc, "R1", parts)
        out = svc.open_round("R1", witness="r")
        # only one H1 ticket can win, so both winners are distinct households
        hh = {t.ticket_id: t.household_id for t in parts}
        self.assertEqual(len({hh[w] for w in out.winners}), 2)
        # claim one winner; a sibling (non-winner) cannot be claimed
        svc.claim("R1", out.winners[0])
        for t in parts:
            if t.ticket_id not in out.winners:
                with self.assertRaises(DrawError):
                    svc.claim("R1", t.ticket_id)


class TestLockAndMustOpen(unittest.TestCase):
    """Complete material must open; a locked round cannot be postponed."""

    def test_complete_material_opens_immediately(self):
        svc = fresh_service()
        parts = three_escrow(svc)
        out = svc.open_round("R1", witness="r")
        self.assertTrue(out.opened)
        self.assertFalse(out.postponed)

    def test_locked_round_cannot_be_postponed(self):
        svc = fresh_service()
        parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="onsite")
        svc.create_round("R1", [p.as_ticket() for p in parts], 2, "m", "opening")
        commit_all(svc, "R1", parts)
        reveal_all(svc, "R1", parts)  # all present -> locked
        self.assertEqual(svc.rounds["R1"].phase, "LOCKED")
        with self.assertRaises(RulesLocked):
            svc.postpone("R1", "try to dodge", "witness")

    def test_first_reveal_gated_until_locked(self):
        svc = fresh_service()
        parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="onsite")
        svc.create_round("R1", [p.as_ticket() for p in parts], 2, "m", "opening")
        commit_all(svc, "R1", parts)
        svc.submit_reveal("R1", "T1", parts[0].salt, parts[0].value)
        with self.assertRaises(RevealGate):
            svc.first_reveal("R1")  # partial reveal must stay hidden
        svc.submit_reveal("R1", "T2", parts[1].salt, parts[1].value)
        svc.submit_reveal("R1", "T3", parts[2].salt, parts[2].value)
        self.assertEqual(len(svc.first_reveal("R1")), 3)

    def test_organiser_cannot_choose_second_reveal(self):
        # A partial reveal cannot change who wins: the seed uses the full set.
        from lottery.crypto import compute_draw

        roster = [{"ticket_id": f"T{i}", "household_id": f"H{i}"} for i in range(3)]
        all_reveals = {
            t["ticket_id"]: {"salt": f"s{t['ticket_id']}", "value": f"v{t['ticket_id']}"}
            for t in roster
        }
        _seed, _order, winners_full = compute_draw(roster, 2, "m", all_reveals)
        # try every "one reveal swapped for a backup" variant; none reuses full set
        for t in roster:
            alt = {k: dict(v) for k, v in all_reveals.items()}
            alt[t["ticket_id"]]["value"] = "backup"
            _s, _o, w = compute_draw(roster, 2, "m", alt)
            self.assertNotEqual(w, winners_full)


class TestPostponeReopenReissue(unittest.TestCase):
    def test_incomplete_round_postpones_without_winners(self):
        svc = fresh_service()
        parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="onsite")
        svc.create_round("R1", [p.as_ticket() for p in parts], 2, "m", "opening")
        commit_all(svc, "R1", parts)
        svc.submit_reveal("R1", "T1", parts[0].salt, parts[0].value)
        svc.submit_reveal("R1", "T2", parts[1].salt, parts[1].value)
        out = svc.open_round("R1", witness="w")
        self.assertTrue(out.postponed)
        self.assertFalse(out.opened)
        self.assertEqual(svc.winners("R1"), [])  # no provisional winners leaked

    def test_reopen_requires_fresh_witness(self):
        svc = fresh_service()
        parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="onsite")
        svc.create_round("R1", [p.as_ticket() for p in parts], 2, "m", "opening")
        commit_all(svc, "R1", parts)
        svc.submit_reveal("R1", "T1", parts[0].salt, parts[0].value)
        svc.open_round("R1", witness="w")
        new = svc.reopen("R1", "R1b", witness="fresh-witness")
        self.assertEqual(new.reopened_from, "R1")
        with self.assertRaises(DrawError):
            svc.reopen("R1", "R1c", witness="")  # no witness -> refused

    def test_reopen_only_postponed(self):
        svc = fresh_service()
        parts = three_escrow(svc)
        svc.open_round("R1", witness="r")
        with self.assertRaises(WrongState):
            svc.reopen("R1", "R1b", witness="x")

    def test_reissue_identical_packet_and_winners(self):
        svc = fresh_service()
        parts = three_escrow(svc)
        out = svc.open_round("R1", witness="r")
        first = svc.package("R1")
        again = svc.reissue("R1")
        self.assertEqual(first["packet_hash"], again["packet_hash"])
        self.assertEqual(first["winners"], again["winners"])

    def test_classification_labels(self):
        svc = fresh_service()
        parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="onsite")
        svc.create_round("R1", [p.as_ticket() for p in parts], 2, "m", "opening")
        commit_all(svc, "R1", parts)
        svc.submit_reveal("R1", "T1", parts[0].salt, parts[0].value)
        svc.open_round("R1", witness="w")
        info = classify_round(svc.log(), "R1")
        self.assertIn("POSTPONED", info["labels"])
        svc.reopen("R1", "R1b", witness="fresh")
        info2 = classify_round(svc.log(), "R1b")
        self.assertIn("SUPPLEMENTARY_OPENING", info2["labels"])
        ok, _ = is_possible_redraw(svc.log(), "R1")
        self.assertTrue(ok)


class TestEventChain(unittest.TestCase):
    def test_chain_verifies_and_detects_tamper(self):
        svc = fresh_service()
        three_escrow(svc)
        svc.open_round("R1", witness="r")
        log = svc.log()
        ok, _head = verify_event_chain(log)
        self.assertTrue(ok)
        tampered = [dict(e) for e in log]
        tampered[0]["data"] = dict(tampered[0]["data"])
        tampered[0]["data"]["quota"] = 999
        ok2, detail = verify_event_chain(tampered)
        self.assertFalse(ok2)


class TestAcceptanceSequence(unittest.TestCase):
    """The exact reviewer sequence from the brief, on three tickets."""

    def test_full_acceptance_sequence(self):
        import copy

        from lottery.crypto import digest

        svc = fresh_service()
        parts = three_escrow(svc)
        out = svc.open_round("R1", witness="reviewer")

        # reviewer recomputes winners from the public package
        pkg = svc.package("R1")
        res = verify_package(pkg)
        self.assertTrue(res.ok, res.errors)
        self.assertEqual(res.recomputed_winners, out.winners)

        # and actually issues one claim receipt
        winner = out.winners[0]
        receipt = svc.claim("R1", winner)
        self.assertEqual(receipt.ticket_id, winner)

        # swap one reveal for a value inconsistent with its commitment
        bad = copy.deepcopy(pkg)
        bad["reveals"][winner]["value"] = "not-the-committed-value"
        bad["packet_hash"] = digest({k: v for k, v in bad.items() if k != "packet_hash"})
        res_bad = verify_package(bad)
        self.assertFalse(res_bad.ok)

        # restore the original reveal: verifies again
        res_ok = verify_package(copy.deepcopy(pkg))
        self.assertTrue(res_ok.ok, res_ok.errors)

        # mark the seat as already handed out on the claims ledger, then
        # re-claim the same reservation -> refused
        svc.mark_settled("R1", winner)
        with self.assertRaises(DuplicateSettlement):
            svc.claim("R1", winner)


class TestRoundIsolation(unittest.TestCase):
    def test_first_round_packet_retrievable_after_second_round(self):
        svc = fresh_service()
        p1 = three_escrow(svc, rid="R1")
        out1 = svc.open_round("R1", witness="r")
        pkg1_before = svc.package("R1")

        # second, independent round
        p2 = make_participants([("S1", "G1"), ("S2", "G2"), ("S3", "G3")], source="escrow")
        svc.create_round("R2", [p.as_ticket() for p in p2], 2, "m", "escrow")
        commit_all(svc, "R2", p2)
        escrow_all(svc, "R2", p2)
        svc.open_round("R2", witness="r2")

        # round one's original packet is still retrievable by its round number
        pkg1_after = svc.package("R1")
        self.assertEqual(pkg1_before["packet_hash"], pkg1_after["packet_hash"])
        self.assertTrue(verify_package(pkg1_after).ok)

    def test_unknown_round_errors(self):
        svc = fresh_service()
        with self.assertRaises(DrawError):
            svc.package("nope")


if __name__ == "__main__":
    unittest.main()
