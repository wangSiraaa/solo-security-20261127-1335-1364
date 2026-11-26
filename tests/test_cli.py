"""End-to-end CLI tests: every demo command must exit 0 and behave correctly."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "lottery", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


class TestCLI(unittest.TestCase):
    def test_baseline_exit_zero(self):
        r = run("baseline")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("outside verification: OK", r.stdout)
        self.assertIn("claim receipt issued", r.stdout)

    def test_tamper_demo(self):
        r = run("tamper")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("verification: FAIL", r.stdout)
        self.assertIn("verification: OK", r.stdout)

    def test_duplicate_claim_demo(self):
        r = run("duplicate-claim")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("refused", r.stdout)

    def test_incident_escrow_absent(self):
        r = run("escrow-absent")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("opened: True", r.stdout)

    def test_incident_cabinet_offline(self):
        r = run("cabinet-offline")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("opened: True", r.stdout)

    def test_postpone_reopen(self):
        r = run("postpone-reopen")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("postponed: True", r.stdout)
        self.assertIn("SUPPLEMENTARY_OPENING", r.stdout)

    def test_reissue(self):
        r = run("reissue")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("packet hash unchanged: True", r.stdout)

    def test_shift_tradeoff(self):
        r = run("shift")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"can_escrow": true', r.stdout)
        self.assertIn('"can_host_opening_seal": true', r.stdout)

    def test_verify_roundtrip_via_files(self):
        from lottery import DrawService, verify_package, write_package
        from lottery.scenarios import commit_all, escrow_all, make_participants

        svc = DrawService(package_dir=tempfile.mkdtemp())
        parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="escrow")
        svc.create_round("R1", [p.as_ticket() for p in parts], 2, "m", "escrow")
        commit_all(svc, "R1", parts)
        escrow_all(svc, "R1", parts)
        svc.open_round("R1", witness="r")
        d = tempfile.mkdtemp()
        path = write_package(d, "R1", svc.package("R1"))
        r = run("verify", path)
        self.assertEqual(r.returncode, 0, r.stderr)
        parsed = json.loads(r.stdout)
        self.assertTrue(parsed["ok"])

    def test_audit_via_file(self):
        from lottery import DrawService
        from lottery.scenarios import commit_all, make_participants

        svc = DrawService(package_dir=tempfile.mkdtemp())
        parts = make_participants([("T1", "H1"), ("T2", "H2")], source="onsite")
        svc.create_round("R1", [p.as_ticket() for p in parts], 1, "m", "opening")
        commit_all(svc, "R1", parts)
        svc.submit_reveal("R1", "T1", parts[0].salt, parts[0].value)
        svc.open_round("R1", witness="w")
        d = tempfile.mkdtemp()
        path = os.path.join(d, "log.json")
        with open(path, "w") as fh:
            json.dump(svc.log(), fh)
        r = run("audit", path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("POSTPONED", r.stdout)


if __name__ == "__main__":
    unittest.main()
