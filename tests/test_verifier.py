"""Verifier and audit tests: the outside reader must recompute independently."""

import copy
import tempfile
import unittest

from lottery import (
    DrawService,
    load_package,
    verify_event_chain,
    verify_package,
    verify_round,
    write_package,
)
from lottery.audit import classify_round, is_possible_redraw, rounds_overview
from lottery.crypto import digest
from lottery.scenarios import commit_all, escrow_all, make_participants


def built_package(rid="R1"):
    svc = DrawService(package_dir=tempfile.mkdtemp())
    parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="escrow")
    svc.create_round(rid, [p.as_ticket() for p in parts], 2, "sha256-order-v1", "escrow")
    commit_all(svc, rid, parts)
    escrow_all(svc, rid, parts)
    svc.open_round(rid, witness="reviewer")
    return svc, svc.package(rid)


def rehash(pkg):
    body = {k: v for k, v in pkg.items() if k != "packet_hash"}
    return digest(body)


class TestVerifier(unittest.TestCase):
    def test_clean_package_verifies(self):
        _svc, pkg = built_package()
        res = verify_package(pkg)
        self.assertTrue(res.ok, res.errors)
        self.assertEqual(set(res.checks), {
            "packet_hash", "seal_hash", "roster_consistent", "quota_consistent",
            "mixing_rule_consistent", "commitments_bind_reveals", "no_stray_reveals",
            "sources_match_shift", "cutoff_matches_shift", "seed_matches",
            "draw_order_matches", "draw_winners_match", "household_unique",
            "replacements_replay", "effective_winners_match",
        })

    def test_packet_hash_tamper_detected(self):
        _svc, pkg = built_package()
        bad = copy.deepcopy(pkg)
        bad["quota"] = 3  # change without rehashing
        res = verify_package(bad)
        self.assertFalse(res.ok)
        self.assertFalse(res.checks["packet_hash"])

    def test_seal_tamper_detected(self):
        _svc, pkg = built_package()
        bad = copy.deepcopy(pkg)
        bad["seal"]["quota"] = 1
        bad["packet_hash"] = rehash(bad)
        res = verify_package(bad)
        self.assertFalse(res.ok)
        self.assertFalse(res.checks["seal_hash"])

    def test_stray_reveal_detected(self):
        _svc, pkg = built_package()
        bad = copy.deepcopy(pkg)
        bad["reveals"]["ghost"] = {"salt": "x", "value": "y"}
        bad["packet_hash"] = rehash(bad)
        res = verify_package(bad)
        self.assertFalse(res.ok)
        self.assertFalse(res.checks["no_stray_reveals"])

    def test_swapped_winners_detected(self):
        _svc, pkg = built_package()
        bad = copy.deepcopy(pkg)
        bad["draw_winners"] = list(reversed(bad["draw_winners"]))
        bad["packet_hash"] = rehash(bad)
        res = verify_package(bad)
        self.assertFalse(res.ok)
        self.assertFalse(res.checks["draw_winners_match"])

    def test_shift_or_cutoff_mismatch_detected(self):
        _svc, pkg = built_package()
        bad = copy.deepcopy(pkg)
        bad["seal"]["witness_shift"] = "opening"
        # re-seal so the seal hash itself matches, but cutoff/sources still clash
        from lottery.crypto import seal_digest

        bad["seal_hash"] = seal_digest(
            bad["seal"]["round_id"], bad["seal"]["roster"], bad["seal"]["quota"],
            bad["seal"]["mixing_rule"], bad["seal"]["witness_shift"],
        )
        bad["packet_hash"] = rehash(bad)
        res = verify_package(bad)
        self.assertFalse(res.ok)
        self.assertFalse(res.checks["sources_match_shift"] or res.checks["cutoff_matches_shift"])

    def test_corrupt_reveal_breaks_binding(self):
        _svc, pkg = built_package()
        bad = copy.deepcopy(pkg)
        for tid in bad["reveals"]:
            bad["reveals"][tid]["salt"] = "tampered"
            break
        bad["packet_hash"] = rehash(bad)
        res = verify_package(bad)
        self.assertFalse(res.ok)
        self.assertFalse(res.checks["commitments_bind_reveals"])

    def test_verify_round_checks_event_chain(self):
        svc, pkg = built_package()
        mat = {"package": pkg, "events": svc.log()}
        res = verify_round(mat)
        self.assertTrue(res.ok, res.errors)
        bad_events = svc.log()
        bad_events[0]["data"] = dict(bad_events[0]["data"])
        bad_events[0]["data"]["quota"] = 7
        res2 = verify_round({"package": pkg, "events": bad_events})
        self.assertFalse(res2.ok)

    def test_package_round_trips_through_disk(self):
        _svc, pkg = built_package("R-disk")
        d = tempfile.mkdtemp()
        path = write_package(d, "R-disk", pkg)
        loaded = load_package(path)
        self.assertEqual(loaded, pkg)
        self.assertTrue(verify_package(loaded).ok)


class TestAudit(unittest.TestCase):
    def test_postponed_then_supplementary(self):
        svc = DrawService(package_dir=tempfile.mkdtemp())
        parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="onsite")
        svc.create_round("R1", [p.as_ticket() for p in parts], 2, "m", "opening")
        commit_all(svc, "R1", parts)
        svc.submit_reveal("R1", "T1", parts[0].salt, parts[0].value)
        svc.open_round("R1", witness="w")
        info = classify_round(svc.log(), "R1")
        self.assertIn("POSTPONED", info["labels"])
        self.assertFalse(info["opened"])
        self.assertEqual(info["winners"], [])

        svc.reopen("R1", "R1b", witness="fresh")
        info_b = classify_round(svc.log(), "R1b")
        self.assertIn("SUPPLEMENTARY_OPENING", info_b["labels"])
        self.assertEqual(info_b["reopened_from"], "R1")

        overview = rounds_overview(svc.log())
        self.assertEqual(set(overview), {"R1", "R1b"})

    def test_reissue_classified(self):
        svc, _pkg = built_package("R1")
        svc.reissue("R1")
        info = classify_round(svc.log(), "R1")
        self.assertIn("REISSUED", info["labels"])
        self.assertEqual(len(info["reissued_hashes"]), 1)

    def test_redraw_detection_flags_winners_without_open(self):
        # forge a log where winners appear but no ROUND_OPENED exists
        svc, _pkg = built_package("R1")
        log = [e for e in svc.log() if e["type"] != "ROUND_OPENED"]
        # re-chain is not needed for classify (it reads by round id)
        info = classify_round(log, "R1")
        self.assertEqual(info["winners"], [])
        ok, _ = is_possible_redraw(log, "R1")
        self.assertTrue(ok)  # no winners published at all


if __name__ == "__main__":
    unittest.main()
