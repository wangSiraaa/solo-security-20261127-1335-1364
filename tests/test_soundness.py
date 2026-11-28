"""Adversarial / soundness tests that probe the hard corners."""

import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/workspace")

from museum import (
    CLAIM_EXHIBIT,
    ROUTE_DESK,
    CollectionScope,
    Item,
    Librarian,
    Museum,
    Receiver,
    TranslationDesk,
    Validity,
    iso,
    parse_prefix,
)
from museum.authority import ReferralGraph

T0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


def val(days=90, start=T0):
    return Validity(start=iso(start), end=iso(start + timedelta(days=days)))


def at(d=1):
    return iso(T0 + timedelta(days=d))


def mkitem(owner="Origin", series="COL", seq=100, sub="main"):
    return Item(owner=owner, series=series, seq=seq, subledger=sub, title="x")


class TestGraphSoundness(unittest.TestCase):
    def setUp(self):
        self.origin = Museum("Origin")
        self.recv = Receiver("Receiver")
        self.lib = Librarian("陈")
        self.origin.register("陈", self.lib.key)
        self.recv.trust_root(self.origin)
        self.scope = CollectionScope.of("Origin", [parse_prefix("COL-0")])
        self.ref = self.origin.issue_referral(
            audience=self.recv, subject_key=self.lib.key, from_name="陈",
            to_name="甲", scope=self.scope, purposes={CLAIM_EXHIBIT}, validity=val(),
        )

    def test_multiple_paths_one_dead_other_valid(self):
        # Add a second, structurally broken path (wrong audience key) plus the
        # genuine one; the genuine one must still be found.
        broken = self.origin.issue_referral(
            audience=self.recv, subject_key=self.lib.key, from_name="陈",
            to_name="甲", scope=self.scope, purposes={CLAIM_EXHIBIT}, validity=val(),
            nonce="broken",
        )
        obj = broken.to_json()
        obj["audience_key_fp"] = "0" * 64  # breaks chaining to receiver
        from museum import Referral
        broken2 = Referral.from_json(obj)
        paths = ReferralGraph([broken2, self.ref]).find_paths(
            self.recv, {self.origin.fp}, subject_fp=self.lib.fp
        )
        self.assertEqual(len(paths), 1)
        doc = self.lib.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=100), statement="s",
            signed_at=at(),
        )
        d = self.recv.file(doc, referrals=[broken2, self.ref])
        self.assertTrue(d.decision.accepted, d.decision.reason)

    def test_cycle_does_not_loop_forever(self):
        # Origin -> Receiver and Receiver -> Origin form a back-reference loop.
        self.recv.register("甲", self.lib.key)
        back = self.recv.issue_referral(
            audience=self.origin, subject_key=self.lib.key, from_name="甲",
            to_name="陈", scope=self.scope, purposes={CLAIM_EXHIBIT}, validity=val(),
        )
        # Should terminate and still find the direct Origin->Receiver path.
        paths = ReferralGraph([self.ref, back]).find_paths(
            self.recv, {self.origin.fp}, subject_fp=self.lib.fp
        )
        self.assertTrue(any(p.accepted_name == "甲" for p in paths))

    def test_receiver_must_be_exact_key(self):
        # A referral addressed to a same-named but different-keyed receiver
        # must NOT be accepted by our receiver.
        impostor_recv = Receiver("Receiver")  # same name, different key
        ref_wrong = self.origin.issue_referral(
            audience=impostor_recv, subject_key=self.lib.key, from_name="陈",
            to_name="甲", scope=self.scope, purposes={CLAIM_EXHIBIT}, validity=val(),
        )
        doc = self.lib.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=100), statement="s",
            signed_at=at(),
        )
        d = self.recv.file(doc, referrals=[ref_wrong])
        self.assertFalse(d.decision.accepted)

    def test_trusted_root_must_sign_the_first_edge(self):
        # If the trusted root did not actually issue the edge, fail.
        other = Museum("Other")
        other.register("陈", self.lib.key)
        ref_other = other.issue_referral(
            audience=self.recv, subject_key=self.lib.key, from_name="陈",
            to_name="甲", scope=self.scope, purposes={CLAIM_EXHIBIT}, validity=val(),
        )
        # recv trusts Origin, not Other.
        paths = ReferralGraph([ref_other]).find_paths(
            self.recv, {self.origin.fp}, subject_fp=self.lib.fp
        )
        self.assertEqual(paths, [])


class TestDeskIndependence(unittest.TestCase):
    def test_receiver_needs_only_statement_not_chain(self):
        origin = Museum("Origin")
        recv = Receiver("Station", route=ROUTE_DESK)
        desk = TranslationDesk("Desk")
        recv.acknowledge_desk(desk)
        recv.trust_root(origin)
        lib = Librarian("李")
        origin.register("李", lib.key)
        scope = CollectionScope.of("Origin", [parse_prefix("COL-00")])
        ref = origin.issue_referral(
            audience=recv, subject_key=lib.key, from_name="李", to_name="李·甲",
            scope=scope, purposes={CLAIM_EXHIBIT, "verify_description"},
            validity=val(),
        )
        doc = lib.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=123), statement="s", signed_at=at(),
        )
        paths = ReferralGraph([ref]).find_paths(recv, {origin.fp}, subject_fp=lib.fp)
        stmt = desk.issue(document=doc, receiver=recv, referral_path=paths[0],
                          not_after=iso(T0 + timedelta(days=30)))
        # Receiver never sees ref: only stmt + doc.
        d = recv.file(doc, statement=stmt)
        self.assertTrue(d.decision.accepted, d.decision.reason)
        # Forged desk statement (signed by another key) is rejected.
        fake_desk = TranslationDesk("Desk")  # same name, different key
        stmt2 = fake_desk.issue(document=doc, receiver=recv, referral_path=paths[0],
                                not_after=iso(T0 + timedelta(days=30)))
        d2 = recv.file(doc, statement=stmt2)
        self.assertFalse(d2.decision.accepted)

    def test_desk_statement_expiry_enforced(self):
        origin = Museum("Origin")
        recv = Receiver("Station", route=ROUTE_DESK)
        desk = TranslationDesk("Desk")
        recv.acknowledge_desk(desk)
        recv.trust_root(origin)
        lib = Librarian("李")
        origin.register("李", lib.key)
        scope = CollectionScope.of("Origin", [parse_prefix("COL-00")])
        ref = origin.issue_referral(
            audience=recv, subject_key=lib.key, from_name="李", to_name="李·甲",
            scope=scope, purposes={CLAIM_EXHIBIT}, validity=val(),
        )
        doc = lib.sign_document(
            doc_type="exhibit_claim", item=mkitem(seq=123), statement="s",
            signed_at=at(20),
        )
        paths = ReferralGraph([ref]).find_paths(recv, {origin.fp}, subject_fp=lib.fp)
        stmt = desk.issue(document=doc, receiver=recv, referral_path=paths[0],
                          not_after=iso(T0 + timedelta(days=10)))  # expires before doc time
        d = recv.file(doc, statement=stmt)
        self.assertFalse(d.decision.accepted)


class TestDelegationLimits(unittest.TestCase):
    def _setup(self):
        origin = Museum("Origin")
        mid = Museum("Mid")
        recv = Receiver("Recv")
        lib = Librarian("陈")
        origin.register("陈", lib.key)
        recv.trust_root(origin)
        scope = CollectionScope.of("Origin", [parse_prefix("COL-0")])
        return origin, mid, recv, lib, scope

    def test_can_refer_false_blocks_extension(self):
        origin, mid, recv, lib, scope = self._setup()
        r1 = origin.issue_referral(
            audience=mid, subject_key=lib.key, from_name="陈", to_name="中",
            scope=scope, purposes={CLAIM_EXHIBIT}, validity=val(),
            can_refer=False,  # forbids further delegation
        )
        mid.register("中", lib.key)
        r2 = mid.issue_referral(
            audience=recv, subject_key=lib.key, from_name="中", to_name="终",
            scope=scope, purposes={CLAIM_EXHIBIT}, validity=val(),
        )
        doc = lib.sign_document(doc_type="exhibit_claim", item=mkitem(seq=100),
                                statement="s", signed_at=at())
        d = recv.file(doc, referrals=[r1, r2])
        self.assertFalse(d.decision.accepted)

    def test_hop_budget_capped_by_root(self):
        origin, mid, recv, lib, scope = self._setup()
        # Root permits only 1 edge; a two-hop chain must not be accepted.
        r1 = origin.issue_referral(
            audience=mid, subject_key=lib.key, from_name="陈", to_name="中",
            scope=scope, purposes={CLAIM_EXHIBIT}, validity=val(),
            delegation_depth=1,
        )
        mid.register("中", lib.key)
        r2 = mid.issue_referral(
            audience=recv, subject_key=lib.key, from_name="中", to_name="终",
            scope=scope, purposes={CLAIM_EXHIBIT}, validity=val(),
        )
        doc = lib.sign_document(doc_type="exhibit_claim", item=mkitem(seq=100),
                                statement="s", signed_at=at())
        d = recv.file(doc, referrals=[r1, r2])
        self.assertFalse(d.decision.accepted)

    def test_direct_one_hop_with_budget_one_ok(self):
        origin, mid, recv, lib, scope = self._setup()
        r1 = origin.issue_referral(
            audience=recv, subject_key=lib.key, from_name="陈", to_name="终",
            scope=scope, purposes={CLAIM_EXHIBIT}, validity=val(),
            delegation_depth=1,
        )
        doc = lib.sign_document(doc_type="exhibit_claim", item=mkitem(seq=100),
                                statement="s", signed_at=at())
        d = recv.file(doc, referrals=[r1])
        self.assertTrue(d.decision.accepted, d.decision.reason)


class TestScopeMath(unittest.TestCase):
    def test_subset_intersection_validity(self):
        big = CollectionScope.of("M", [parse_prefix("COL-0")])
        small = CollectionScope.of("M", [parse_prefix("COL-00")], [parse_prefix("COL-0050")])
        self.assertTrue(small.is_subset_of(big))
        self.assertFalse(big.is_subset_of(small))
        inter = big.intersect(small)
        self.assertEqual(inter.to_json(), small.to_json())

    def test_owner_mismatch_is_empty(self):
        a = CollectionScope.of("A", [parse_prefix("COL-")])
        b = CollectionScope.of("B", [parse_prefix("COL-")])
        inter = a.intersect(b)
        self.assertTrue(inter.is_empty())
        self.assertFalse(a.is_subset_of(b))


if __name__ == "__main__":
    unittest.main(verbosity=2)
