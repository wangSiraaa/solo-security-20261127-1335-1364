"""Test suite for the split-fault-domain evidence service and rehearsal desk.

Run with:  python3 -m unittest -v   (from the repo root)
"""

from __future__ import annotations

import unittest

from faultdomain import scenario
from faultdomain.adapters import evidence_from_export, evidence_from_proof, make_probe
from faultdomain.delivery import DeliveryEngine
from faultdomain.desk import ActuallyConnected, Desk, Evidence
from faultdomain.export_service import ExportStation, TopologyExportService
from faultdomain.model import Hop, Lease
from faultdomain.operator_console import LineSpec, SecondOperatorConsole
from faultdomain.service import LineEvidenceService


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.scn = scenario.build()
        self.w = self.scn.world
        self.d = self.w.dispatcher
        self.engine = DeliveryEngine(self.w)
        self.desk = Desk()

    # convenience -------------------------------------------------------

    def paths(self):
        return [
            ("pa-src", "pb-dst", "SRC", self.scn.lease("L-PRIMARY")),
            ("pa-src-2", "pb-dst-2", "SRC", self.scn.lease("L-BACKUP")),
        ]

    def connected(self, detach_backup_dst=False):
        return [
            ActuallyConnected("pa-src", "pb-dst"),
            ActuallyConnected("pa-src-2", None if detach_backup_dst else "pb-dst-2"),
        ]


# ======================================================================
# 1. Two disjoint domains, actually connected, announce reaches the far end
# ======================================================================

class TestDisjointDelivery(Base):
    def test_pair_is_disjoint_and_delivers(self):
        va = self.w.resolve(self.scn.lease("L-PRIMARY"))
        vb = self.w.resolve(self.scn.lease("L-BACKUP"))
        self.assertTrue(va.domains.isdisjoint(vb.domains))
        self.assertEqual(va.ingress, "pa-src")
        self.assertEqual(va.egress, "pb-dst")
        self.assertEqual(vb.ingress, "pa-src-2")
        self.assertEqual(vb.egress, "pb-dst-2")

        r1 = self.engine.deliver("probe-A", "SRC", "DST",
                                 self.scn.lease("L-PRIMARY"), "pa-src", "pb-dst")
        r2 = self.engine.deliver("probe-B", "SRC", "DST",
                                 self.scn.lease("L-BACKUP"), "pa-src-2", "pb-dst-2")
        self.assertTrue(r1.ok)
        self.assertTrue(r2.ok)
        # real per-domain handling records exist at the destination
        self.assertTrue(self.w.records_for("DST", "probe-A"))
        self.assertTrue(self.w.records_for("DST", "probe-B"))

    def test_desk_accepts_before_any_edit(self):
        probe = make_probe(self.w, self.engine, "DST", self.paths())
        ev = Evidence("export", True,
                      [("pa-src", "pb-dst"), ("pa-src-2", "pb-dst-2")])
        ok, trace = self.desk.decide(ev, self.connected(), cut_domain=None, probe=probe)
        self.assertTrue(ok, "\n".join(trace))


# ======================================================================
# 2. Single interchange edit -> both cross one domain -> cannot promise
#    reachability during that domain's outage
# ======================================================================

class TestSingleInterchangeEdit(Base):
    def test_edit_merges_fault_domains(self):
        scenario.route_backup_through_primary(self.d, "IC-EAST")
        va = self.w.resolve(self.scn.lease("L-PRIMARY"))
        vb = self.w.resolve(self.scn.lease("L-BACKUP"))
        self.assertIn("IC-EAST", va.domains)
        self.assertIn("IC-EAST", vb.domains)
        self.assertFalse(va.domains.isdisjoint(vb.domains))

    def test_cut_kills_both_paths(self):
        scenario.route_backup_through_primary(self.d, "IC-EAST")
        self.w.cut_domain("IC-EAST", "drill")
        r1 = self.engine.deliver("p1", "SRC", "DST",
                                 self.scn.lease("L-PRIMARY"), "pa-src", "pb-dst")
        r2 = self.engine.deliver("p2", "SRC", "DST",
                                 self.scn.lease("L-BACKUP"), "pa-src-2", "pb-dst-2")
        self.assertFalse(r1.ok)
        self.assertFalse(r2.ok)

    def test_desk_refuses_even_with_disjoint_paperwork(self):
        scenario.route_backup_through_primary(self.d, "IC-EAST")
        self.w.cut_domain("IC-EAST", "drill")
        probe = make_probe(self.w, self.engine, "DST", self.paths())
        # Paperwork still (wrongly or stale) claims disjoint.
        ev = Evidence("verifier", True,
                      [("pa-src", "pb-dst"), ("pa-src-2", "pb-dst-2")])
        ok, trace = self.desk.decide(ev, self.connected(), cut_domain="IC-EAST", probe=probe)
        self.assertFalse(ok)
        self.assertTrue(any("no path survives" in t for t in trace), "\n".join(trace))

    def test_export_shows_shared_domain_after_repeat(self):
        exp = TopologyExportService(self.w)
        before = ExportStation(exp.export(["L-PRIMARY", "L-BACKUP"]))
        self.assertTrue(before.disjoint("L-PRIMARY", "L-BACKUP")[0])
        scenario.route_backup_through_primary(self.d, "IC-EAST")
        after = ExportStation(exp.export(["L-PRIMARY", "L-BACKUP"]))
        disjoint, why = after.disjoint("L-PRIMARY", "L-BACKUP")
        self.assertFalse(disjoint)
        self.assertTrue(after.shared_domains("L-PRIMARY", "L-BACKUP"))


# ======================================================================
# 3. Restore both paths, detach the receiving backup port -> even a good
#    isolation proof cannot make the real delivery succeed
# ======================================================================

class TestDetachedBackupPort(Base):
    def test_detached_port_blocks_delivery_despite_valid_proof(self):
        svc = LineEvidenceService(self.w, self.engine)
        svc.verifier_setup(
            ["OP-A", "OP-B", "OP-C", "OP-D", "OP-X"],
            ["L-PRIMARY", "L-BACKUP"],
        )
        proof = svc.issue_proof("L-PRIMARY", "L-BACKUP")
        ps = svc.proof_station()
        # proof is genuinely valid and each lease is disjoint
        self.assertTrue(ps.verify_structural(proof)[0])

        # detach the receiving backup port
        self.w.terminals["DST"].detach("pb-dst-2")

        # the real delivery over backup now fails
        r = self.engine.deliver("p-backup", "SRC", "DST",
                                self.scn.lease("L-BACKUP"), "pa-src-2", "pb-dst-2")
        self.assertFalse(r.ok)
        self.assertIn("detached", r.reason)

        # the desk refuses despite the valid proof binding the *old* port
        ev = Evidence("verifier", True,
                      [("pa-src", "pb-dst"), ("pa-src-2", "pb-dst-2")],
                      signature_ok=True, fresh=True, contract_active=True)
        probe = make_probe(self.w, self.engine, "DST", self.paths())
        ok, trace = self.desk.decide(ev, self.connected(detach_backup_dst=True),
                                     cut_domain=None, probe=probe)
        self.assertFalse(ok)
        self.assertTrue(any("detached" in t for t in trace), "\n".join(trace))

    def test_reissue_after_detach_still_cannot_deliver(self):
        # detaching is physical; a *fresh, valid* proof changes nothing.
        svc = LineEvidenceService(self.w, self.engine)
        svc.verifier_setup(["OP-A", "OP-B", "OP-C", "OP-D", "OP-X"],
                           ["L-PRIMARY", "L-BACKUP"])
        self.w.terminals["DST"].detach("pb-dst-2")
        proof2 = svc.issue_proof("L-PRIMARY", "L-BACKUP")  # valid paperwork
        ps = svc.proof_station()
        self.assertTrue(ps.verify_structural(proof2)[0])
        r = self.engine.deliver("p2", "SRC", "DST",
                                self.scn.lease("L-BACKUP"), "pa-src-2", "pb-dst-2")
        self.assertFalse(r.ok)


# ======================================================================
# 4. Another operator isolates a FICTIONAL domain, switches lines, and
#    retrieves only genuinely received announcements
# ======================================================================

class TestSecondOperatorFictionalDomain(Base):
    def test_fictional_domain_cut_does_not_block_real_receipt(self):
        console = SecondOperatorConsole(self.w, self.engine)
        console.register_line(LineSpec("primary", "L-PRIMARY", "pa-src", "pb-dst"))
        console.register_line(LineSpec("backup", "L-BACKUP", "pa-src-2", "pb-dst-2"))

        console.isolate("GHOST-DOMAIN-7", reason="operator-2 drill")
        # The fictional domain exists in nobody's path -> no handling logs exist.
        self.assertEqual(self.w.records_for("GHOST-DOMAIN-7"), [])

        console.switch_line("primary")
        self.assertTrue(console.announce("ann-1", "real announcement"))
        console.switch_line("backup")
        self.assertTrue(console.announce("ann-2", "real announcement"))

        self.assertEqual(console.received_announcements(), ["ann-1", "ann-2"])
        # the announcements are provably received at the destination
        self.assertTrue(self.w.records_for("DST", "ann-1"))
        self.assertTrue(self.w.records_for("DST", "ann-2"))

    def test_genuinely_cut_domain_blocks_receipt(self):
        console = SecondOperatorConsole(self.w, self.engine)
        console.register_line(LineSpec("primary", "L-PRIMARY", "pa-src", "pb-dst"))
        console.switch_line("primary")
        console.isolate("OP-A", reason="real cut")
        self.assertFalse(console.announce("ann-x"))
        self.assertEqual(console.received_announcements(), [])

    def test_fake_receipt_cannot_be_manufactured(self):
        console = SecondOperatorConsole(self.w, self.engine)
        console.register_line(LineSpec("primary", "L-PRIMARY", "pa-src", "pb-dst"))
        console.switch_line("primary")
        console.announce("ann-real")
        # Querying a domain that never handled the ticket returns nothing.
        self.assertEqual(self.w.records_for("GHOST-DOMAIN-7", "ann-real"), [])
        self.assertEqual(console.query_domain_log("GHOST-DOMAIN-7"), [])


# ======================================================================
# 5. Operational edge cases: late reports, port re-lease, contract expiry
# ======================================================================

class TestOperationalEdgeCases(Base):
    def test_late_export_report_is_incomplete_then_completes(self):
        exp = TopologyExportService(self.w)
        exp.mark_late("OP-C")
        st = ExportStation(exp.export(["L-PRIMARY", "L-BACKUP"]))
        self.assertFalse(st.bundle.leases["L-BACKUP"].complete)
        self.assertFalse(st.assemble("L-BACKUP").ok)
        self.assertFalse(st.disjoint("L-PRIMARY", "L-BACKUP")[0])

        exp.report_now("OP-C")
        st2 = ExportStation(exp.export(["L-PRIMARY", "L-BACKUP"]))
        self.assertTrue(st2.bundle.leases["L-BACKUP"].complete)
        self.assertTrue(st2.disjoint("L-PRIMARY", "L-BACKUP")[0])

    def test_contract_expiry_blocks_export_evidence(self):
        exp = TopologyExportService(self.w)
        self.w.tick(2000)
        st = ExportStation(exp.export(["L-PRIMARY", "L-BACKUP"]))
        self.assertFalse(st.contract_active("L-PRIMARY", self.w.epoch))
        ev = evidence_from_export(
            st, "L-PRIMARY", "L-BACKUP", self.w.epoch, None,
            (("pa-src", "pb-dst"), ("pa-src-2", "pb-dst-2")),
        )
        self.assertFalse(ev.disjoint_ok)  # contract failure folds into refusal

    def test_port_release_makes_old_proof_stale_and_unrenewable(self):
        svc = LineEvidenceService(self.w, self.engine)
        svc.verifier_setup(["OP-A", "OP-B", "OP-C", "OP-D", "OP-X"],
                           ["L-PRIMARY", "L-BACKUP"])
        proof = svc.issue_proof("L-PRIMARY", "L-BACKUP")
        ps = svc.proof_station()
        self.w.tick(1)

        # old proof is now stale
        self.assertFalse(ps.fresh_for_epoch(proof, self.w.epoch)[0])
        # the station cannot self-renew
        with self.assertRaises(PermissionError):
            ps.current_path_commit("L-BACKUP")

        # re-lease backup onto a different destination port
        self.d.links[("OP-D", "D4")].b = "pb-dst-3"
        self.w.terminals["DST"].ports["pb-dst-3"] = True
        new_lease = Lease("L-BACKUP",
                          (Hop("OP-C", "C3"), Hop("OP-D", "D4")),
                          valid_from=0, valid_until=1000)
        svc.rele_connect("L-BACKUP", new_lease)

        # old proof no longer binds the actual wiring
        ok, _ = ps.binds_actual_paths(proof, "pa-src", "pb-dst",
                                      "pa-src-2", "pb-dst-3")
        self.assertFalse(ok)
        # a freshly-issued proof does bind the new wiring
        fresh = svc.issue_proof("L-PRIMARY", "L-BACKUP")
        ok2, _ = ps.binds_actual_paths(fresh, "pa-src", "pb-dst",
                                       "pa-src-2", "pb-dst-3")
        self.assertTrue(ok2)

    def test_offline_recombination_detects_broken_adjacency(self):
        exp = TopologyExportService(self.w)
        st = ExportStation(exp.export(["L-PRIMARY", "L-BACKUP"]))
        chain = st.recombine("L-PRIMARY", "L-BACKUP", 1)
        # P's first segment cannot meet Q's second segment at any witness
        self.assertFalse(st.recombination_is_contiguous(chain))


# ======================================================================
# 6. What each approach can / cannot verify (capability preservation)
# ======================================================================

class TestCapabilityPreservation(Base):
    def test_approach_a_recomputes_but_leaks_shared_domain(self):
        exp = TopologyExportService(self.w)
        st = ExportStation(exp.export(["L-PRIMARY", "L-BACKUP"]))
        # station can assemble and check disjointness itself
        self.assertTrue(st.disjoint("L-PRIMARY", "L-BACKUP")[0])
        # and can compute cut survival for a known domain label
        label = st.assemble("L-PRIMARY").interchanges[0]
        self.assertFalse(st.survives_cut("L-PRIMARY", label))
        # and a fictional label doesn't appear -> survives
        self.assertTrue(st.survives_cut("L-PRIMARY", "X-does-not-exist"))

    def test_approach_b_hides_internals_but_is_authentic(self):
        svc = LineEvidenceService(self.w, self.engine)
        svc.verifier_setup(["OP-A", "OP-B", "OP-C", "OP-D", "OP-X"],
                           ["L-PRIMARY", "L-BACKUP"])
        proof = svc.issue_proof("L-PRIMARY", "L-BACKUP")
        ps = svc.proof_station()
        # station can verify authenticity & the disjoint claim...
        self.assertTrue(ps.verify_structural(proof)[0])
        self.assertTrue(proof.disjoint)
        # ...but the proof carries no internal domain labels
        self.assertNotIn("domains", proof.statement())
        # ...and it cannot recompute a fresh path commitment
        with self.assertRaises(PermissionError):
            ps.current_path_commit("L-PRIMARY")

    def test_tampered_proof_is_rejected(self):
        svc = LineEvidenceService(self.w, self.engine)
        svc.verifier_setup(["OP-A", "OP-B", "OP-C", "OP-D", "OP-X"],
                           ["L-PRIMARY", "L-BACKUP"])
        proof = svc.issue_proof("L-PRIMARY", "L-BACKUP")
        ps = svc.proof_station()
        # flip a field without re-signing
        forged = proof.__class__(**{**proof.__dict__, "disjoint": True,
                                    "egress_b": "pb-dst-999"})
        self.assertFalse(ps.verify_structural(forged)[0])


if __name__ == "__main__":
    unittest.main()
