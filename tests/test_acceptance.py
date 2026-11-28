"""Acceptance tests, one class per requirement.

Run: python3 tests/test_acceptance.py     (or python3 -m unittest discover tests)
"""

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/workspace")

from museum import (
    CLAIM_EXHIBIT,
    ROUTE_DESK,
    ROUTE_STEP,
    CollectionScope,
    Document,
    Dossier,
    Item,
    Key,
    Librarian,
    Museum,
    Receiver,
    TranslationDesk,
    Validity,
    canonical_json,
    format_call_number,
    iso,
    parse_prefix,
    verify,
)

T0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


def val(days=90, start=T0):
    return Validity(start=iso(start), end=iso(start + timedelta(days=days)))


def at(day=1):
    return iso(T0 + timedelta(days=day))


def mkitem(owner="Origin", series="COL", seq=1234, sub="main", title="文物"):
    return Item(owner=owner, series=series, seq=seq, subledger=sub, title=title)


class Fixture(unittest.TestCase):
    """Three museums, two same-named librarians, standard scopes."""

    def setUp(self):
        self.origin = Museum("Origin")
        self.mid = Museum("Middle")
        self.recv = Receiver("Receiver")
        self.desk = TranslationDesk("CentralDesk")

        self.libA = Librarian("陈静")  # the real travelling librarian
        self.libB = Librarian("陈静")  # a different person, same short name

        # Origin registers BOTH — same short name, distinct keys.
        self.regA = self.origin.register("陈静", self.libA.key, issued_at=at(0))
        self.regB = self.origin.register("陈静", self.libB.key, issued_at=at(0))

        # Overlapping prefixes COL-00 (0..9999) and COL-001 (1000..1999),
        # with an exception range COL-0015 (1500..1599).
        self.scope = CollectionScope.of(
            "Origin",
            [parse_prefix("COL-00"), parse_prefix("COL-001")],
            [parse_prefix("COL-0015")],
        )
        self.recv.trust_root(self.origin)

    def referral(self, **kw):
        base = dict(
            audience=self.recv,
            subject_key=self.libA.key,
            from_name="陈静",
            to_name="陈静·甲",
            scope=self.scope,
            purposes={CLAIM_EXHIBIT, "verify_description"},
            validity=val(),
        )
        base.update(kw)
        return self.origin.issue_referral(**base)


# R1: registration / referral side -----------------------------------------


class TestRegistrationAndReferral(Fixture):
    def test_distinct_people_share_short_name(self):
        self.assertNotEqual(self.libA.fp, self.libB.fp)
        self.assertEqual(self.regA.short_name, self.regB.short_name)
        self.assertNotEqual(self.regA.subject_fp, self.regB.subject_fp)

    def test_referral_signatures_verify_offline(self):
        ref = self.referral()
        self.assertTrue(ref.signature_ok())
        self.assertTrue(ref.registration_ok())
        # round-trip through JSON keeps it verifiable
        ref2 = type(ref).from_json(json.loads(json.dumps(ref.to_json())))
        self.assertTrue(ref2.signature_ok())
        self.assertTrue(ref2.registration_ok())

    def test_tampered_referral_rejected(self):
        ref = self.referral()
        obj = ref.to_json()
        obj["to_name"] = "冒名"
        self.assertFalse(type(ref).from_json(obj).signature_ok())

    def test_unregistered_name_refused(self):
        with self.assertRaises(ValueError):
            self.origin.issue_referral(
                audience=self.recv, subject_key=self.libA.key,
                from_name="不存在", to_name="X", scope=self.scope,
                purposes={CLAIM_EXHIBIT}, validity=val(),
            )

    def test_registration_cannot_be_forged_without_registry_key(self):
        # A rogue museum cannot mint a registration that verifies under Origin.
        rogue = Museum("Rogue")
        bogus = rogue.register("陈静", self.libA.key)
        self.assertFalse(bogus.verify_under(self.origin.key.public_der))
        self.assertTrue(bogus.verify_under(rogue.key.public_der))


# R4: acceptance clerk's two-part drill ------------------------------------


class TestAcceptanceDrill(Fixture):
    def test_catalog_correction_filed_into_catalog(self):
        ref = self.referral()
        doc = self.libA.sign_document(
            doc_type="catalog_correction", item=mkitem(seq=1234),
            statement="更正年代", signed_at=at(1),
        )
        d = self.recv.file(doc, referrals=[ref])
        self.assertTrue(d.decision.accepted, d.decision.reason)
        self.assertEqual(d.decision.accepted_name, "陈静·甲")
        self.recv.file_into_catalog(d)
        self.assertIsNotNone(self.recv.ledger.get("Origin", "COL", 1234, "accepted"))

    def test_exhibit_claim_in_excluded_range_held_for_evidence(self):
        ref = self.referral()
        doc = self.libA.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=1550),  # inside COL-0015
            statement="签领展品", signed_at=at(1),
        )
        d = self.recv.file(doc, referrals=[ref])
        self.assertFalse(d.decision.accepted)
        self.recv.hold_for_evidence(d)
        self.assertIsNone(self.recv.ledger.get("Origin", "COL", 1550, "accepted"))

    def test_scope_exception_is_exact_not_overbroad(self):
        ref = self.referral()
        # boundary: 1499 ok, 1500 excluded, 1599 excluded, 1600 ok
        for seq, exp in [(1499, True), (1500, False), (1599, False), (1600, True)]:
            doc = self.libA.sign_document(
                doc_type="exhibit_claim", item=mkitem(seq=seq),
                statement="签领", signed_at=at(1),
            )
            d = self.recv.file(doc, referrals=[ref])
            self.assertEqual(d.decision.accepted, exp, f"seq {seq}")


# R2 (cross-museum): step-by-step route ------------------------------------


class TestStepwiseChain(Fixture):
    def two_hop(self, mid_scope=None, mid_to_name="陈静·中"):
        r1 = self.origin.issue_referral(
            audience=self.mid, subject_key=self.libA.key,
            from_name="陈静", to_name="陈静·中", scope=self.scope,
            purposes={CLAIM_EXHIBIT, "verify_description"}, validity=val(),
        )
        # Middle re-registers the same person under its own space and refers on.
        self.mid.register("陈静·中", self.libA.key)
        r2 = self.mid.issue_referral(
            audience=self.recv, subject_key=self.libA.key,
            from_name="陈静·中", to_name=mid_to_name,
            scope=mid_scope or self.scope,
            purposes={CLAIM_EXHIBIT, "verify_description"}, validity=val(),
        )
        return r1, r2

    def test_two_hop_name_conversion(self):
        r1, r2 = self.two_hop()
        doc = self.libA.sign_document(
            doc_type="catalog_correction", item=mkitem(seq=500),
            statement="说明", signed_at=at(1),
        )
        d = self.recv.file(doc, referrals=[r1, r2])
        self.assertTrue(d.decision.accepted, d.decision.reason)
        self.assertEqual(d.decision.accepted_name, "陈静·中")

    def test_single_intermediate_narrows_prefix_changes_decision(self):
        # Middle narrows to exclude seq 500 even though all signatures are real.
        narrow = CollectionScope.of("Origin", [parse_prefix("COL-002")])
        r1, r2 = self.two_hop(mid_scope=narrow)
        doc = self.libA.sign_document(
            doc_type="catalog_correction", item=mkitem(seq=500),
            statement="说明", signed_at=at(1),
        )
        d = self.recv.file(doc, referrals=[r1, r2])
        self.assertFalse(d.decision.accepted)
        # the same document at an in-prefix number is still accepted
        doc2 = self.libA.sign_document(
            doc_type="catalog_correction", item=mkitem(seq=2500),
            statement="说明", signed_at=at(1),
        )
        d2 = self.recv.file(doc2, referrals=[r1, r2])
        self.assertTrue(d2.decision.accepted, d2.decision.reason)

    def test_broken_chain_breaks_only_that_path(self):
        # r1 genuine but r2 not reachable by name-mapping -> no path
        r1 = self.origin.issue_referral(
            audience=self.mid, subject_key=self.libA.key,
            from_name="陈静", to_name="甲名", scope=self.scope,
            purposes={CLAIM_EXHIBIT, "verify_description"}, validity=val(),
        )
        self.mid.register("乙名", self.libA.key)  # different name than r1.to_name
        r2 = self.mid.issue_referral(
            audience=self.recv, subject_key=self.libA.key,
            from_name="乙名", to_name="最终名", scope=self.scope,
            purposes={CLAIM_EXHIBIT, "verify_description"}, validity=val(),
        )
        doc = self.libA.sign_document(
            doc_type="catalog_correction", item=mkitem(seq=100),
            statement="x", signed_at=at(1),
        )
        d = self.recv.file(doc, referrals=[r1, r2])
        self.assertFalse(d.decision.accepted)


# R3: no borrowing name from one path, authority from another --------------


class TestNoSplicing(Fixture):
    def test_name_only_and_scope_only_paths_cannot_combine(self):
        # Path 1: correct name, but purpose limited to verify_description only.
        name_only = self.origin.issue_referral(
            audience=self.recv, subject_key=self.libA.key,
            from_name="陈静", to_name="签名甲", scope=self.scope,
            purposes={"verify_description"}, validity=val(),
            nonce="n1",
        )
        # Path 2: can claim exhibits, but scoped to ANOTHER museum's holdings.
        other_scope = CollectionScope.of("Elsewhere", [parse_prefix("COL-00")])
        self.origin.register("陈静", self.libA.key)
        authority_only = self.origin.issue_referral(
            audience=self.recv, subject_key=self.libA.key,
            from_name="陈静", to_name="签名甲", scope=other_scope,
            purposes={CLAIM_EXHIBIT}, validity=val(),
            nonce="n2",
        )
        doc = self.libA.sign_document(
            doc_type="exhibit_claim", item=mkitem(owner="Origin", seq=100),
            statement="签领", signed_at=at(1),
        )
        d = self.recv.file(doc, referrals=[name_only, authority_only])
        self.assertFalse(d.decision.accepted, "must not splice name and authority")
        # Both individual paths fail on their own terms
        d1 = self.recv.file(doc, referrals=[name_only])
        d2 = self.recv.file(doc, referrals=[authority_only])
        self.assertFalse(d1.decision.accepted)
        self.assertFalse(d2.decision.accepted)

    def test_single_complete_path_succeeds(self):
        complete = self.origin.issue_referral(
            audience=self.recv, subject_key=self.libA.key,
            from_name="陈静", to_name="签名甲", scope=self.scope,
            purposes={CLAIM_EXHIBIT}, validity=val(),
        )
        doc = self.libA.sign_document(
            doc_type="exhibit_claim", item=mkitem(owner="Origin", series="COL", seq=100),
            statement="签领", signed_at=at(1),
        )
        d = self.recv.file(doc, referrals=[complete])
        self.assertTrue(d.decision.accepted, d.decision.reason)


# R5: no scope amplification from back-refs / overlapping maps ------------


class TestNoAmplification(Fixture):
    def test_back_reference_cannot_widen_scope(self):
        # r1: Origin -> Receiver, narrow scope.
        narrow = CollectionScope.of("Origin", [parse_prefix("COL-001")])  # 1000..1999
        r1 = self.origin.issue_referral(
            audience=self.recv, subject_key=self.libA.key,
            from_name="陈静", to_name="甲", scope=narrow,
            purposes={CLAIM_EXHIBIT}, validity=val(),
        )
        # r2: Receiver -> Origin (a back-reference), wide scope.  A naive union
        # would let the return edge expand what Receiver accepts.
        self.recv.register("甲", self.libA.key)
        wide = CollectionScope.of("Origin", [parse_prefix("COL-00")])
        r2 = self.recv.issue_referral(
            audience=self.origin, subject_key=self.libA.key,
            from_name="甲", to_name="陈静", scope=wide,
            purposes={CLAIM_EXHIBIT}, validity=val(),
        )
        # item at 500 is outside r1's narrow scope
        doc = self.libA.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=500),
            statement="签领", signed_at=at(1),
        )
        d = self.recv.file(doc, referrals=[r1, r2])
        self.assertFalse(d.decision.accepted)
        # in-scope item still accepted
        doc2 = self.libA.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=1500),
            statement="签领", signed_at=at(1),
        )
        d2 = self.recv.file(doc2, referrals=[r1, r2])
        self.assertTrue(d2.decision.accepted, d2.decision.reason)

    def test_overlapping_name_maps_do_not_widen(self):
        # Two edges with overlapping prefixes must intersect, not union.
        a = self.origin.issue_referral(
            audience=self.recv, subject_key=self.libA.key,
            from_name="陈静", to_name="名一",
            scope=CollectionScope.of("Origin", [parse_prefix("COL-001")]),
            purposes={CLAIM_EXHIBIT}, validity=val(), nonce="a",
        )
        b = self.origin.issue_referral(
            audience=self.recv, subject_key=self.libA.key,
            from_name="陈静", to_name="名二",
            scope=CollectionScope.of("Origin", [parse_prefix("COL-0015")]),
            purposes={CLAIM_EXHIBIT}, validity=val(), nonce="b",
        )
        # item in COL-001 but not COL-0015 -> neither single path covers wide
        doc = self.libA.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=1200),
            statement="签领", signed_at=at(1),
        )
        d = self.recv.file(doc, referrals=[a, b])
        # Path a alone covers 1200, so it IS accepted (a real referral holds).
        self.assertTrue(d.decision.accepted)
        self.assertEqual(d.decision.accepted_name, "名一")


# R7: successor re-check from archive --------------------------------------


class TestSuccessorRecheck(Fixture):
    def test_archive_round_trip_recheck(self):
        ref = self.referral()
        doc = self.libA.sign_document(
            doc_type="catalog_correction", item=mkitem(seq=1234),
            statement="更正", signed_at=at(1),
        )
        d = self.recv.file(doc, referrals=[ref])
        self.assertTrue(d.decision.accepted)
        # Archive to JSON, drop everything live, rebuild.
        archived = json.loads(json.dumps(d.to_json(), ensure_ascii=False))
        restored = Dossier.from_json(archived)
        re = self.recv.recheck_dossier(restored)
        self.assertTrue(re.accepted, re.reason)
        self.assertEqual(re.accepted_name, "陈静·甲")

    def test_tampered_archive_recheck_fails(self):
        ref = self.referral()
        doc = self.libA.sign_document(
            doc_type="catalog_correction", item=mkitem(seq=1234),
            statement="更正", signed_at=at(1),
        )
        d = self.recv.file(doc, referrals=[ref])
        archived = json.loads(json.dumps(d.to_json(), ensure_ascii=False))
        # Tamper the archived document statement after the fact.
        archived["document"]["statement"] = "伪造的更正"
        restored = Dossier.from_json(archived)
        re = self.recv.recheck_dossier(restored)
        self.assertFalse(re.accepted)


# R6: translation desk route -----------------------------------------------


class TestTranslationDesk(Fixture):
    def setUp(self):
        super().setUp()
        # Desk is independently acknowledged by the receiver.
        self.recv.route = ROUTE_DESK
        # Desk must itself be able to see the chain; it is trusted by receiver.
        self.recv.acknowledge_desk(self.desk)
        self.desk.trusted_root_fps = {self.origin.fp}

    def test_desk_statement_files_document(self):
        from museum import ReferralGraph
        ref = self.referral()
        from museum.authority import _compose

        paths = ReferralGraph([ref]).find_paths(
            self.recv, {self.origin.fp}, subject_fp=self.libA.fp
        )
        self.assertTrue(paths)
        doc = self.libA.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=1234),
            statement="签领", signed_at=at(1),
        )
        stmt = self.desk.issue(
            document=doc, receiver=self.recv, referral_path=paths[0],
            not_after=iso(T0 + timedelta(days=30)),
        )
        # Receiver holds ONLY its trust root, the statement, and the doc.
        d = self.recv.file(doc, statement=stmt)
        self.assertTrue(d.decision.accepted, d.decision.reason)
        self.assertEqual(d.decision.accepted_name, "陈静·甲")

    def test_desk_statement_bound_to_station(self):
        from museum import ReferralGraph

        ref = self.referral()
        paths = ReferralGraph([ref]).find_paths(
            self.recv, {self.origin.fp}, subject_fp=self.libA.fp
        )
        doc = self.libA.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=1234),
            statement="签领", signed_at=at(1),
        )
        stmt = self.desk.issue(document=doc, receiver=self.recv,
                               referral_path=paths[0],
                               not_after=iso(T0 + timedelta(days=30)))
        # Present the same statement to a *different* receiver/station.
        other = Receiver("OtherStation")
        other.route = ROUTE_DESK
        other.acknowledge_desk(self.desk)
        d = other.file(doc, statement=stmt)
        self.assertFalse(d.decision.accepted)

    def test_desk_statement_bound_to_digest(self):
        from museum import ReferralGraph

        ref = self.referral()
        paths = ReferralGraph([ref]).find_paths(
            self.recv, {self.origin.fp}, subject_fp=self.libA.fp
        )
        doc = self.libA.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=1234),
            statement="签领", signed_at=at(1),
        )
        stmt = self.desk.issue(document=doc, receiver=self.recv,
                               referral_path=paths[0],
                               not_after=iso(T0 + timedelta(days=30)))
        # Try to reuse the statement for a *different* document.
        doc2 = self.libA.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=1550),  # excluded range
            statement="另一件", signed_at=at(1),
        )
        d = self.recv.file(doc2, statement=stmt)
        self.assertFalse(d.decision.accepted)

    def test_desk_offline_blocks_new_item(self):
        # No statement -> new document cannot be credentialed.
        doc = self.libA.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=1234),
            statement="签领", signed_at=at(1),
        )
        d = self.recv.file(doc)  # desk offline: nothing to present
        self.assertFalse(d.decision.accepted)


# R8: ledger split / same call number in two subledgers --------------------


class TestSubledgerSplit(unittest.TestCase):
    def test_move_between_subledgers_is_separable(self):
        from museum.receiver import LocalLedger
        led = LocalLedger("Origin")
        led.add(mkitem(owner="Origin", series="COL", seq=1234, sub="甲册"))
        led.add(mkitem(owner="Origin", series="COL", seq=1234, sub="乙册"))
        self.assertIsNotNone(led.get("Origin", "COL", 1234, "甲册"))
        self.assertIsNotNone(led.get("Origin", "COL", 1234, "乙册"))
        led.move_subledger("Origin", "COL", 1234, "甲册", "乙册")
        self.assertIsNone(led.get("Origin", "COL", 1234, "甲册"))
        # the 甲册 item now lives in 乙册 (two entries there)
        self.assertEqual(len(led.in_subledger("乙册")), 2)
        self.assertEqual(len(led.find("Origin", "COL", 1234)), 2)
        # and the other item at the same call number was untouched
        self.assertEqual(len(led.in_subledger("甲册")), 0)


# R9: re-homing one-time acceptance material -------------------------------


class TestOneTimeMaterial(Fixture):
    def _material(self):
        doc = self.libA.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=1234),
            statement="签领", signed_at=at(1),
        )
        mat = self.origin.issue_acceptance_material(
            station="Receiver", document=doc, purpose=CLAIM_EXHIBIT,
            not_after=iso(T0 + timedelta(days=10)),
        )
        return doc, mat

    def test_material_spends_once_for_right_person(self):
        doc, mat = self._material()
        self.assertTrue(mat.verify(self.origin.key.public_der))
        ok = self.libA.consume_material(
            mat, station="Receiver", document_digest=doc.digest(),
            purpose=CLAIM_EXHIBIT, at=T0 + timedelta(days=2),
        )
        self.assertTrue(ok)
        # cannot spend twice
        ok2 = self.libA.consume_material(
            mat, station="Receiver", document_digest=doc.digest(),
            purpose=CLAIM_EXHIBIT, at=T0 + timedelta(days=2),
        )
        self.assertFalse(ok2)

    def test_rehoming_material_to_new_museum_fails(self):
        doc, mat = self._material()
        # Hand the *same* material to the same-named libB at a DIFFERENT station.
        ok = self.libB.consume_material(
            mat, station="OtherMuseum", document_digest=doc.digest(),
            purpose=CLAIM_EXHIBIT, at=T0 + timedelta(days=2),
        )
        self.assertFalse(ok)
        # Even right station, wrong person (same short name) fails.
        ok2 = self.libB.consume_material(
            mat, station="Receiver", document_digest=doc.digest(),
            purpose=CLAIM_EXHIBIT, at=T0 + timedelta(days=2),
        )
        self.assertFalse(ok2)

    def test_material_wrong_digest_or_purpose_fails(self):
        doc, mat = self._material()
        self.assertFalse(
            self.libA.consume_material(
                mat, station="Receiver", document_digest="deadbeef",
                purpose=CLAIM_EXHIBIT, at=T0 + timedelta(days=2),
            )
        )
        self.assertFalse(
            self.libA.consume_material(
                mat, station="Receiver", document_digest=doc.digest(),
                purpose="verify_description", at=T0 + timedelta(days=2),
            )
        )
        # expired
        self.assertFalse(
            self.libA.consume_material(
                mat, station="Receiver", document_digest=doc.digest(),
                purpose=CLAIM_EXHIBIT, at=T0 + timedelta(days=11),
            )
        )


# R1 cont.: offline evidence chain for the desk statement -------------------


class TestOfflineEvidence(unittest.TestCase):
    def test_entire_dossier_json_roundtrips_and_verifies(self):
        origin = Museum("Origin")
        recv = Receiver("Receiver")
        lib = Librarian("李")
        origin.register("李", lib.key)
        recv.trust_root(origin)
        scope = CollectionScope.of("Origin", [parse_prefix("COL-0")])
        ref = origin.issue_referral(
            audience=recv, subject_key=lib.key, from_name="李",
            to_name="李·甲", scope=scope,
            purposes={CLAIM_EXHIBIT}, validity=val(),
        )
        doc = lib.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=42),
            statement="签领", signed_at=at(1),
        )
        d = recv.file(doc, referrals=[ref])
        blob = json.dumps(d.to_json(), ensure_ascii=False)
        restored = Dossier.from_json(json.loads(blob))
        self.assertTrue(recv.recheck_dossier(restored).accepted)
        # a second round-trip is stable (referrals survive intact)
        self.assertEqual(len(restored.referrals), 1)
        self.assertTrue(restored.referrals[0].signature_ok())
        self.assertTrue(restored.document.verify())


if __name__ == "__main__":
    unittest.main(verbosity=2)
