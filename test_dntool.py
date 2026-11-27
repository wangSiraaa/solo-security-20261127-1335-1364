#!/usr/bin/env python3
"""Tests for dntool.py -- run with `python3 test_dntool.py` (no pytest needed)."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("dntool", os.path.join(HERE, "dntool.py"))
dt = importlib.util.module_from_spec(spec)
sys.modules["dntool"] = dt
spec.loader.exec_module(dt)
Net = dt.Net


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self.dir)
        self.cfg = dt.default_config()
        self.state = dt.RealState([r.copy() for r in self.cfg.routes], 0, 0, 0)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self.dir, ignore_errors=True)


class TestMirror(Base):
    def test_containment_preserved(self):
        # a /30 under the /29 scope mirrors to a /30 under the fake /29
        fake = dt.mirror(self.cfg, Net("10.0.0.0/30"))
        self.assertEqual(str(fake), "198.18.0.0/30")
        self.assertEqual(str(dt.unmirror(self.cfg, fake)), "10.0.0.0/30")
        fake4 = dt.mirror(self.cfg, Net("10.0.0.4/30"))
        self.assertEqual(str(fake4), "198.18.0.4/30")

    def test_out_of_scope_rejected(self):
        with self.assertRaises(dt.Unrepresentable):
            dt.mirror(self.cfg, Net("10.0.0.8/29"))

    def test_roundtrip_all_scope_prefixes(self):
        for plen in (29, 30, 32):
            for n in self.cfg.scope.subnets(new_prefix=plen) if plen != 32 else \
                    [Net((int(a), 32)) for a in self.cfg.scope]:
                self.assertEqual(dt.unmirror(self.cfg, dt.mirror(self.cfg, n)), n)


class TestPackageAnon(Base):
    def test_no_raw_addresses_or_names(self):
        rows = dt.build_package(self.cfg, self.state)
        text = dt.dump_package(self.cfg, rows)
        self.assertNotIn("10.0.0.", text)
        self.assertNotIn("coreA", text)
        self.assertNotIn("192.0.2.", text)
        self.assertNotIn("B", [r.get("part") for r in rows])

    def test_hidden_b_not_emitted(self):
        rows = dt.build_package(self.cfg, self.state)
        # every emitted prefix lies inside the fake scope
        for r in rows:
            self.assertTrue(Net(r["prefix"]).subnet_of(self.cfg.fake_scope))

    def test_no_private_node_names(self):
        rows = dt.build_package(self.cfg, self.state)
        for r in rows:
            self.assertEqual(r["nbr"], 0)


class TestDivert(Base):
    def test_divert_one_segment_sibling_untouched(self):
        design = dt.build_package(self.cfg, self.state)
        tok_l2 = dt.build_tokens(self.cfg)[0][self.cfg.landing_names["L2"]]
        for d in design:
            if d["prefix"] == "198.18.0.0/30":
                d["ifc"] = tok_l2
        new, rep = dt.apply_package(self.cfg, self.state, design)
        self.assertTrue(rep.accepted, rep.reasons)
        # diverted segment now on L2 (ifc 21)
        self.assertEqual(dt.lpm(new.routes, "A", "10.0.0.2").ifc, 21)
        # sibling same-number segment in the SAME partition keeps ifc 10
        self.assertEqual(dt.lpm(new.routes, "A", "10.0.0.5").ifc, 10)
        # partition B untouched
        self.assertTrue(dt.tables_equal(self.state, new, "B"))
        self.assertEqual(dt.lpm(new.routes, "B", "10.0.0.2").ifc, 30)

    def test_landing_token_required(self):
        design = dt.build_package(self.cfg, self.state)
        for d in design:
            if d["prefix"] == "198.18.0.0/30":
                d["ifc"] = 999  # not an allowed landing
        _, rep = dt.apply_package(self.cfg, self.state, design)
        self.assertFalse(rep.accepted)


class TestAmbiguity(Base):
    def test_same_short_address_two_candidates(self):
        real = Net("10.0.0.0/30")
        cand = dt.candidates(self.cfg, real)
        self.assertEqual(len(cand), 2)          # A and B both
        parts = {c.part for c in cand}
        self.assertEqual(parts, {"A", "B"})

    def test_scoped_apply_is_unambiguous(self):
        # even though 10.0.0.0/30 exists in A and B, an apply is fixed to A
        design = dt.build_package(self.cfg, self.state)
        tok = dt.build_tokens(self.cfg)[0][self.cfg.landing_names["L1"]]
        for d in design:
            if d["prefix"] == "198.18.0.0/30":
                d["ifc"] = tok
        new, rep = dt.apply_package(self.cfg, self.state, design)
        self.assertTrue(rep.accepted)
        # A changed, B identical -> unambiguously expressed
        self.assertEqual(dt.lpm(new.routes, "A", "10.0.0.2").ifc, 20)
        self.assertEqual(dt.lpm(new.routes, "B", "10.0.0.2").ifc, 30)


class TestRejectWhole(Base):
    def test_out_of_scope_row_rejects_whole_change(self):
        design = dt.build_package(self.cfg, self.state)
        tok = dt.build_tokens(self.cfg)[0][self.cfg.landing_names["L1"]]
        # add an unrepresentable row plus a valid edit
        for d in design:
            if d["prefix"] == "198.18.0.4/30":
                d["ifc"] = tok
        design.append({"prefix": "198.18.0.8/30", "ifc": 1, "nbr": 0, "pri": 100,
                       "tag": dt.ACTIVE})
        new, rep = dt.apply_package(self.cfg, self.state, design)
        self.assertFalse(rep.accepted)
        self.assertEqual(new.epoch, self.state.epoch)      # nothing applied
        self.assertEqual([str(r.prefix) for r in new.routes],
                         [str(r.prefix) for r in self.state.routes])

    def test_incomplete_scope_rejected(self):
        design = dt.build_package(self.cfg, self.state)
        # drop the row that gives the /29 default coverage
        design = [d for d in design if d["prefix"] != "198.18.0.0/29"]
        _, rep = dt.apply_package(self.cfg, self.state, design)
        # still covered by the two /30s, so this should be ACCEPTED
        self.assertTrue(rep.accepted)
        # now drop a /30 too -> .4-.7 only covered by the /30; remove both /30s
        design2 = [d for d in dt.build_package(self.cfg, self.state)
                   if d["prefix"] not in ("198.18.0.0/29", "198.18.0.0/30", "198.18.0.4/30")]
        _, rep2 = dt.apply_package(self.cfg, self.state, design2)
        self.assertFalse(rep2.accepted)     # .0-.7 no longer fully covered

    def test_uncovered_scope_rejected_directly(self):
        design = [d for d in dt.build_package(self.cfg, self.state)
                  if d["prefix"] not in ("198.18.0.0/29", "198.18.0.0/30", "198.18.0.4/30")]
        _, rep = dt.apply_package(self.cfg, self.state, design)
        self.assertFalse(rep.accepted)
        self.assertTrue(any("cover" in r for r in rep.reasons))


class TestPriorityAndExpiry(Base):
    def test_priority_only_change_supersedes_return_object(self):
        before = dt.lpm(self.state.routes, "A", "10.0.0.12")
        self.assertEqual(before.tag, "return")
        for r in self.state.routes:
            if r.part == "A" and str(r.prefix) == "10.0.0.8/29" and r.ifc == 25:
                r.pri = 300
        after = dt.lpm(self.state.routes, "A", "10.0.0.12")
        self.assertEqual(after.tag, "ACTIVE")
        self.assertEqual(after.ifc, 25)
        # only priorities changed
        self.assertEqual(len(self.state.routes), len(self.cfg.routes))

    def test_return_route_expires_and_loses_object(self):
        # tick rounds until the ttl return row disappears
        routes = [r.copy() for r in self.state.routes]
        for _ in range(2):
            nxt = []
            for r in routes:
                if r.tag == "return" or r.ttl > 0:
                    if r.ttl > 1:
                        nxt.append(r.copy(ttl=r.ttl - 1))
                else:
                    nxt.append(r)
            routes = nxt
        self.assertIsNone(
            next((r for r in routes if r.tag == "return"), None))
        # .12 now has no applicable route at all (return was its only /29)
        # note: an idle alternate also exists, so confirm return-object is gone
        tags = {r.tag for r in routes if str(r.prefix) == "10.0.0.8/29"}
        self.assertNotIn("return", tags)


class TestRefreshReturn(Base):
    def test_subtract_local_correction(self):
        base = Net("10.0.0.8/29")
        usable = dt.subtract(base, [Net("10.0.0.12/31")])
        addrs = set()
        for n in usable:
            addrs |= {int(a) for a in n}
        self.assertEqual(addrs, {int(a) for a in base} - {int(a) for a in Net("10.0.0.12/31")})
        self.assertEqual(sum(int(n.num_addresses) for n in usable), 6)

    def test_partial_usable_fraction(self):
        usable = dt.subtract(Net("10.0.0.8/29"), [Net("10.0.0.12/31")])
        self.assertEqual(sum(int(n.num_addresses) for n in usable), 6)  # 6/8 = 75%


class TestAlias(Base):
    def test_withdraw_one_backup_affects_exactly_that_flow(self):
        # withdraw 10.0.0.6/32 on ifc 20 -> .6 falls back to .4/30 ifc 10
        before = dt.lpm(self.state.routes, "A", "10.0.0.6")
        self.assertEqual(before.ifc, 20)
        idx = next(i for i, r in enumerate(self.state.routes)
                   if r.part == "A" and r.ifc == 20 and str(r.prefix) == "10.0.0.6/32")
        new = self.state.copy()
        new.routes = [r for i, r in enumerate(self.state.routes) if i != idx]
        after = dt.lpm(new.routes, "A", "10.0.0.6")
        self.assertEqual(after.ifc, 10)
        # .1 stays on its other landing (ifc 20 or 21)
        self.assertIn(dt.lpm(new.routes, "A", "10.0.0.1").ifc, (20, 21))


class TestLeakScan(Base):
    def test_check_flags_raw_addresses(self):
        dt.cmd_init(_ns(config=None))
        # a clean package passes
        self.assertEqual(dt.cmd_check(_ns(file=dt.PKG)), 0)
        # a tampered package with a raw address fails
        with open("bad.anon", "w") as f:
            f.write("198.18.0.0/29 ifc=1 nbr=0 pri=200 tag=ACTIVE\n10.0.0.5/32 ifc=1\n")
        self.assertEqual(dt.cmd_check(_ns(file="bad.anon")), 2)

    def test_package_has_no_hidden_markers(self):
        rows = dt.build_package(self.cfg, self.state)
        blob = json.dumps(rows)
        self.assertNotIn("coreA", blob)
        self.assertNotIn("10.0.0.8", blob)   # untrusted/return half hidden
        self.assertNotIn("coreA", dt.dump_package(self.cfg, rows))


class TestDesignViewsEquivalent(Base):
    def test_group_view_matches_prefix_view(self):
        # setting group segment-a to L2 must equal editing its fake prefix to L2
        tok = dt.build_tokens(self.cfg)[0][self.cfg.landing_names["L2"]]
        by_group = dt.build_package(self.cfg, self.state)
        grp = next(g for g in self.cfg.groups if g["name"] == "segment-a")
        for d in by_group:
            if d["prefix"] in grp["fake"]:
                d["ifc"] = tok
        by_prefix = dt.build_package(self.cfg, self.state)
        for d in by_prefix:
            if d["prefix"] == "198.18.0.0/30":
                d["ifc"] = tok
        self.assertEqual(by_group, by_prefix)
        g_new, g_rep = dt.apply_package(self.cfg, self.state, by_group)
        p_new, p_rep = dt.apply_package(self.cfg, self.state, by_prefix)
        self.assertEqual(g_rep.accepted, p_rep.accepted)
        self.assertEqual(dt.trial_table(self.cfg, g_new), dt.trial_table(self.cfg, p_new))


class TestTrialDeterminism(Base):
    def test_trial_is_exhaustive_small_width(self):
        table = dt.trial_table(self.cfg, self.state)
        self.assertEqual(len(table), 2 * int(self.cfg.space.num_addresses))
        parts = [t["part"] for t in table]
        self.assertEqual(parts.count("A"), 16)
        self.assertEqual(parts.count("B"), 16)


def _ns(**kw):
    class N:
        pass
    n = N()
    for k, v in kw.items():
        setattr(n, k, v)
    return n


if __name__ == "__main__":
    unittest.main(verbosity=2)
