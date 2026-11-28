"""End-to-end demonstration of the museum referral / acceptance drill.

    python3 -m museum.demo

Prints, step by step, the two-step referral route and the translation-desk
route, including what is accepted, what is held for evidence, and how a
successor re-checks the archive offline.  Every value is reproducible.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .authority import ReferralGraph
from .crypto import canonical_json
from .issue import Librarian, Museum
from .model import (
    CLAIM_EXHIBIT,
    VERIFY_DESCRIPTION,
    CollectionScope,
    Item,
    Validity,
    iso,
    parse_prefix,
)
from .receiver import Dossier, Receiver, ROUTE_DESK

T0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


def _val(days=90, start=T0):
    return Validity(start=iso(start), end=iso(start + timedelta(days=days)))


def _at(day=1):
    return iso(T0 + timedelta(days=day))


def hr(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main():
    hr("0. 馆与人员 / Museums and people")
    origin = Museum("Origin")
    middle = Museum("Middle")
    recv = Receiver("Receiver")
    desk_mark = None
    lib_a = Librarian("陈静")   # travelling librarian
    lib_b = Librarian("陈静")   # different person, same short name
    print(f"  Origin   key {origin.fp[:16]}")
    print(f"  Middle   key {middle.fp[:16]}")
    print(f"  Receiver key {recv.fp[:16]}")
    print(f"  陈静 A   key {lib_a.fp[:16]}")
    print(f"  陈静 B   key {lib_b.fp[:16]}   (same name, different person)")

    hr("1. 登记 / Registration (two same-named librarians at Origin)")
    origin.register("陈静", lib_a.key, issued_at=_at(0))
    origin.register("陈静", lib_b.key, issued_at=_at(0))
    recv.trust_root(origin)
    print("  Origin registered 陈静 twice; names collide, keys disambiguate.")
    print(f"  Receiver trusts root {origin.fp[:16]}.")

    hr("2. 引荐 / Referral with overlapping prefixes + one exception")
    scope = CollectionScope.of(
        "Origin",
        [parse_prefix("COL-00"), parse_prefix("COL-001")],   # overlap
        [parse_prefix("COL-0015")],                          # exception
    )
    print("  effective scope:", scope.describe())
    ref = origin.issue_referral(
        audience=recv, subject_key=lib_a.key, from_name="陈静", to_name="陈静·甲",
        scope=scope, purposes={VERIFY_DESCRIPTION, CLAIM_EXHIBIT}, validity=_val(),
    )
    print("  referral signature valid:", ref.signature_ok(),
          "registration valid:", ref.registration_ok())

    hr("3. 收件馆处理 / Receiver files two documents")
    # 3a: catalog correction inside scope -> file into catalog
    item_ok = Item("Origin", "COL", 1234, "main", "青瓷碗")
    doc_ok = lib_a.sign_document(
        doc_type="catalog_correction", item=item_ok,
        statement="更正年代为北宋", signed_at=_at(1),
    )
    d1 = recv.file(doc_ok, referrals=[ref])
    print(f"  [catalog_correction {item_ok.call_number}] "
          f"accepted={d1.decision.accepted} as {d1.decision.accepted_name!r}")
    if d1.decision.accepted:
        recv.file_into_catalog(d1)
        print("    -> filed into local catalog (subledger 'accepted').")

    # 3b: exhibit claim inside the EXCLUDED range -> hold for evidence
    item_bad = Item("Origin", "COL", 1550, "main", "青铜鼎")
    doc_bad = lib_a.sign_document(
        doc_type="exhibit_claim", item=item_bad,
        statement="签领展品", signed_at=_at(1),
    )
    d2 = recv.file(doc_bad, referrals=[ref])
    print(f"  [exhibit_claim   {item_bad.call_number}] "
          f"accepted={d2.decision.accepted}")
    if not d2.decision.accepted:
        recv.hold_for_evidence(d2)
        print(f"    -> held for evidence: {d2.decision.reason}")

    hr("4. 同名冒认 / Same-name impostor is rejected")
    doc_imp = lib_b.sign_document(
        doc_type="exhibit_claim", item=Item("Origin", "COL", 500, "main", "玉璧"),
        statement="签领展品", signed_at=_at(1),
    )
    d3 = recv.file(doc_imp, referrals=[ref])
    print(f"  accepted={d3.decision.accepted}  ({d3.decision.reason})")

    hr("5. 逐级引荐 / Two-hop step-by-step via Middle")
    r1 = origin.issue_referral(
        audience=middle, subject_key=lib_a.key, from_name="陈静", to_name="陈静·中",
        scope=scope, purposes={VERIFY_DESCRIPTION, CLAIM_EXHIBIT}, validity=_val(),
    )
    middle.register("陈静·中", lib_a.key)
    # Middle narrows the prefix: excludes COL-0005xx region via prefix COL-002
    narrowed = CollectionScope.of("Origin", [parse_prefix("COL-002")])
    r2 = middle.issue_referral(
        audience=recv, subject_key=lib_a.key, from_name="陈静·中", to_name="陈静·终",
        scope=narrowed, purposes={VERIFY_DESCRIPTION, CLAIM_EXHIBIT}, validity=_val(),
    )
    print("  Middle narrows scope to:", narrowed.describe())
    doc_2hop_ok = lib_a.sign_document(
        doc_type="catalog_correction", item=Item("Origin", "COL", 2500, "main", "册页"),
        statement="说明核对", signed_at=_at(1),
    )
    d4 = recv.file(doc_2hop_ok, referrals=[r1, r2])
    print(f"  in narrowed prefix ({doc_2hop_ok.item.call_number}) accepted="
          f"{d4.decision.accepted} as {d4.decision.accepted_name!r}")
    doc_2hop_bad = lib_a.sign_document(
        doc_type="catalog_correction", item=Item("Origin", "COL", 500, "main", "册页"),
        statement="说明核对", signed_at=_at(1),
    )
    d5 = recv.file(doc_2hop_bad, referrals=[r1, r2])
    print(f"  outside narrowed prefix ({doc_2hop_bad.item.call_number}) accepted="
          f"{d5.decision.accepted}  ({d5.decision.reason})")

    hr("6. 翻译处路线 / Translation-desk route")
    from .authority import TranslationDesk

    desk = TranslationDesk("CentralDesk")
    print(f"  Desk key {desk.fp[:16]}")
    desk_recv = Receiver("DeskStation", route=ROUTE_DESK)
    desk_recv.acknowledge_desk(desk)
    desk_recv.trust_root(origin)
    # Desk may see the chain (here a single direct referral to the new station).
    direct = origin.issue_referral(
        audience=desk_recv, subject_key=lib_a.key, from_name="陈静", to_name="陈静·甲",
        scope=scope, purposes={VERIFY_DESCRIPTION, CLAIM_EXHIBIT}, validity=_val(),
    )
    paths = ReferralGraph([direct]).find_paths(
        desk_recv, {origin.fp}, subject_fp=lib_a.fp
    )
    doc_desk = lib_a.sign_document(
        doc_type="exhibit_claim", item=Item("Origin", "COL", 1234, "main", "青瓷碗"),
        statement="签领展品", signed_at=_at(1),
    )
    stmt = desk.issue(
        document=doc_desk, receiver=desk_recv, referral_path=paths[0],
        not_after=iso(T0 + timedelta(days=30)),
    )
    print("  desk statement bound to station/digest/purpose:", stmt.station,
          stmt.document_digest[:12] + "...", stmt.purpose)
    dd = desk_recv.file(doc_desk, statement=stmt)  # receiver holds only stmt
    print(f"  receiver (desk route) accepted={dd.decision.accepted} as "
          f"{dd.decision.accepted_name!r}")
    # reuse on another digest fails
    other = lib_a.sign_document(
        doc_type="exhibit_claim", item=Item("Origin", "COL", 1550, "main", "青铜鼎"),
        statement="另一件", signed_at=_at(1),
    )
    dr = desk_recv.file(other, statement=stmt)
    print(f"  reuse for different digest accepted={dr.decision.accepted} "
          f"({dr.decision.reason})")
    # desk offline -> nothing to present
    dn = desk_recv.file(
        lib_a.sign_document(doc_type="exhibit_claim",
                            item=Item("Origin", "COL", 1300, "main", "新件"),
                            statement="新件", signed_at=_at(1))
    )
    print(f"  desk offline, new item accepted={dn.decision.accepted} "
          f"({dn.decision.reason})")

    hr("7. 归档复查 / Successor re-checks from archive only")
    archived = json.loads(json.dumps(d1.to_json(), ensure_ascii=False))
    restored = Dossier.from_json(archived)
    re = recv.recheck_dossier(restored)
    print(f"  archived catalog_correction re-check accepted={re.accepted} "
          f"as {re.accepted_name!r}")
    # tamper and re-check
    archived["document"]["statement"] = "被篡改的说明"
    re2 = recv.recheck_dossier(Dossier.from_json(archived))
    print(f"  after tampering accepted={re2.accepted} ({re2.reason})")

    hr("8. 分册迁件 / Move one item between subledgers")
    led = recv.ledger
    led.add(Item("Origin", "COL", 4000, "甲册", "文物X"))
    led.add(Item("Origin", "COL", 4000, "乙册", "文物X"))
    print("  before: 甲册", len(led.in_subledger("甲册")),
          "乙册", len(led.in_subledger("乙册")))
    led.move_subledger("Origin", "COL", 4000, "甲册", "乙册")
    print("  after : 甲册", len(led.in_subledger("甲册")),
          "乙册", len(led.in_subledger("乙册")))

    hr("9. 一次性接件材料 / One-time acceptance material re-homing")
    doc_mat = lib_a.sign_document(
        doc_type="exhibit_claim", item=Item("Origin", "COL", 1234, "main", "青瓷碗"),
        statement="签领", signed_at=_at(1),
    )
    mat = origin.issue_acceptance_material(
        station="Receiver", document=doc_mat, purpose=CLAIM_EXHIBIT,
        not_after=iso(T0 + timedelta(days=10)),
    )
    okA = lib_a.consume_material(mat, station="Receiver",
                                 document_digest=doc_mat.digest(),
                                 purpose=CLAIM_EXHIBIT, at=T0 + timedelta(days=2))
    okB = lib_b.consume_material(mat, station="OtherMuseum",
                                 document_digest=doc_mat.digest(),
                                 purpose=CLAIM_EXHIBIT, at=T0 + timedelta(days=2))
    print(f"  original person at own station: {okA} (expect True)")
    print(f"  same name, re-homed to other museum: {okB} (expect False)")


if __name__ == "__main__":
    main()
