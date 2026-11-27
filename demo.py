"""End-to-end walkthrough of the exercise.  Run:  python3 demo.py

It plays the four required beats and prints, at each step, what the station can
verify *under the chosen disclosure approach*:

  1. two disjoint fault domains, actually connected; announce reaches the far end
  2. one interchange edit merges both paths onto one domain -> cannot promise
     reachability during that domain's outage
  3. restore both paths, detach the receiving backup port -> even a valid
     isolation proof cannot make the real delivery succeed
  4. another operator isolates a FICTIONAL domain, switches lines, and retrieves
     only the announcements that were genuinely received
"""

from __future__ import annotations

from faultdomain import scenario
from faultdomain.adapters import evidence_from_export, evidence_from_proof, make_probe
from faultdomain.delivery import DeliveryEngine
from faultdomain.desk import ActuallyConnected, Desk, Evidence
from faultdomain.export_service import TopologyExportService, ExportStation
from faultdomain.operator_console import LineSpec, SecondOperatorConsole
from faultdomain.service import LineEvidenceService


def hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def show(trace) -> None:
    for line in trace:
        print("   " + line)


def main() -> None:
    scn = scenario.build()
    w = scn.world
    d = w.dispatcher
    engine = DeliveryEngine(w)
    desk = Desk()
    svc = LineEvidenceService(w, engine)

    def paths():
        return [
            ("pa-src", "pb-dst", "SRC", scn.lease("L-PRIMARY")),
            ("pa-src-2", "pb-dst-2", "SRC", scn.lease("L-BACKUP")),
        ]

    def connected(detach_backup=False):
        return [
            ActuallyConnected("pa-src", "pb-dst"),
            ActuallyConnected("pa-src-2", None if detach_backup else "pb-dst-2"),
        ]

    show_bundle()

    # ------------------------------------------------------------------
    hr("BEAT 1  Two disjoint fault domains, actually wired; announce delivers")
    for tid, lid, sp, dp in [
        ("ann-1", "L-PRIMARY", "pa-src", "pb-dst"),
        ("ann-2", "L-BACKUP", "pa-src-2", "pb-dst-2"),
    ]:
        r = engine.deliver(tid, "SRC", "DST", scn.lease(lid), sp, dp,
                           announce="broadcast")
        print(f"deliver {lid}: ok={r.ok} {r.reason}")
        print(f"   received at: {sorted({rec.domain for rec in r.records})}")

    # Approach A: station self-assembles
    exp = TopologyExportService(w)
    st = ExportStation(exp.export(["L-PRIMARY", "L-BACKUP"]))
    print("\n[approach A] station assembles paths itself and checks disjointness:")
    print("   disjoint =", st.disjoint("L-PRIMARY", "L-BACKUP"))

    # Approach B: signed proof
    svc.verifier_setup(["OP-A", "OP-B", "OP-C", "OP-D", "OP-X"],
                       ["L-PRIMARY", "L-BACKUP"])
    proof = svc.issue_proof("L-PRIMARY", "L-BACKUP")
    ps = svc.proof_station()
    print("[approach B] station verifies a signed proof:")
    print("   signature/claim =", ps.verify_structural(proof))
    print("   binds ports     =",
          ps.binds_actual_paths(proof, "pa-src", "pb-dst", "pa-src-2", "pb-dst-2"))

    ev = Evidence("export", True,
                  [("pa-src", "pb-dst"), ("pa-src-2", "pb-dst-2")],
                  path_survives=[True, True])
    ok, trace = desk.decide(ev, connected(), cut_domain=None,
                            probe=make_probe(w, engine, "DST", paths()))
    print(f"\n[desk] may continue? {ok}")
    show(trace)

    # ------------------------------------------------------------------
    hr("BEAT 2  Change ONLY one interchange -> both paths share one domain")
    scenario.route_backup_through_primary(d, "IC-EAST")
    print("after edit, PRIMARY domains:", sorted(w.resolve(scn.lease("L-PRIMARY")).domains))
    print("after edit, BACKUP  domains:", sorted(w.resolve(scn.lease("L-BACKUP")).domains))
    w.cut_domain("IC-EAST", "drill")
    r1 = engine.deliver("x1", "SRC", "DST", scn.lease("L-PRIMARY"), "pa-src", "pb-dst")
    r2 = engine.deliver("x2", "SRC", "DST", scn.lease("L-BACKUP"), "pa-src-2", "pb-dst-2")
    print("cut IC-EAST -> primary delivers:", r1.ok, "| backup delivers:", r2.ok)
    # Even stale 'disjoint' paperwork cannot save it.
    ev = Evidence("verifier", True,
                  [("pa-src", "pb-dst"), ("pa-src-2", "pb-dst-2")])
    ok, trace = desk.decide(ev, connected(), cut_domain="IC-EAST",
                            probe=make_probe(w, engine, "DST", paths()))
    print(f"\n[desk] may continue during IC-EAST outage? {ok}")
    show(trace)

    st_after = ExportStation(exp.export(["L-PRIMARY", "L-BACKUP"]))
    print("\n[approach A] repeated export reveals the shared domain label:",
          st_after.shared_domains("L-PRIMARY", "L-BACKUP"))

    # ------------------------------------------------------------------
    hr("BEAT 3  Restore both paths, detach receiving backup port")
    scenario.unroute_backup(d)
    w.restore_domain("IC-EAST")
    w.terminals["DST"].detach("pb-dst-2")
    print("backup destination port pb-dst-2 is now DETACHED")
    proof = svc.issue_proof("L-PRIMARY", "L-BACKUP")  # valid paperwork
    print("fresh signed proof valid?",
          ps.verify_structural(proof))
    r = engine.deliver("y1", "SRC", "DST", scn.lease("L-BACKUP"), "pa-src-2", "pb-dst-2")
    print("real delivery over backup ok?", r.ok, "|", r.reason)
    ev = Evidence("verifier", True,
                  [("pa-src", "pb-dst"), ("pa-src-2", "pb-dst-2")],
                  signature_ok=True, fresh=True, contract_active=True)
    ok, trace = desk.decide(ev, connected(detach_backup=True), cut_domain=None,
                            probe=make_probe(w, engine, "DST", paths()))
    print(f"\n[desk] may continue with a valid proof but detached port? {ok}")
    show(trace)

    # ------------------------------------------------------------------
    hr("BEAT 4  A second operator isolates a FICTIONAL domain, switches, reads")
    w.terminals["DST"].attach("pb-dst-2")  # restore the physical port
    console = SecondOperatorConsole(w, engine)
    console.register_line(LineSpec("primary", "L-PRIMARY", "pa-src", "pb-dst"))
    console.register_line(LineSpec("backup", "L-BACKUP", "pa-src-2", "pb-dst-2"))

    console.isolate("GHOST-DOMAIN-7", reason="operator-2 drill")
    print("fictional domain GHOST-DOMAIN-7 isolated; handling log =",
          console.query_domain_log("GHOST-DOMAIN-7"))
    console.switch_line("primary")
    print("announce over primary:", console.announce("ann-10", "broadcast"))
    console.switch_line("backup")
    print("announce over backup:", console.announce("ann-11", "broadcast"))
    print("genuinely received:", console.received_announcements())
    for tid in console.received_announcements():
        print(f"   {tid} -> destination log:",
              [r.role for r in w.records_for("DST", tid)])

    # ------------------------------------------------------------------
    hr("CAPABILITY SUMMARY")
    print("Approach A (per-domain export):")
    print("   + station assembles paths, recomputes disjointness & cut survival")
    print("   + station can offline-recombine fragments")
    print("   - stable labels leak WHICH domain two leases share on repeat export")
    print("Approach B (trusted verifier):")
    print("   + station verifies an authentic, port-bound, fresh, in-contract proof")
    print("   - station never sees the internal shared relation")
    print("   - station cannot self-renew after a re-lease; only the verifier can")


def show_bundle() -> None:
    hr("TOPOLOGY")
    scn = scenario.build()
    print("PRIMARY: SRC --OP-A:A1--> [IC-EAST] --OP-B:B2--> DST")
    print("BACKUP : SRC --OP-C:C3--> [IC-WEST] --OP-D:D4--> DST")
    print("Both interchanges run by OP-X but are distinct fault domains at start.")


if __name__ == "__main__":
    main()
