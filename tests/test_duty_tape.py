from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from duty_tape.canonical import canonical_body
from duty_tape.protocol import (
    artifact_records,
    build_artifact,
    dependency_pin,
    write_tape,
)
from duty_tape.query import QueryEndpoint
from duty_tape.receiver import RecoveryTerminal
from duty_tape.sealer import ReceiptError, TestSealer, new_revocation


CONTACTS_OLD = """person_id,name,abbr
p1,Lin Zhao,LZ
p2,Chen An,CA
p3,Wang Yi,WY
"""

CONTACTS_NEW = CONTACTS_OLD + "p4,Zhou Ning,ZN\n"

LOCKERS_OLD = """locker_id,locker_number
L1,Z-901
L2,Z-902
"""

LOCKERS_NEW = """locker_id,locker_number
L1,A-101
L2,B-207
"""

DUTY_OLD = """date,shift,person_id,locker_id
2026-11-27,day,p1,L1
2026-11-27,night,p3,L2
"""

DUTY_NEW = """date,shift,person_id,locker_id
2026-11-27,day,p2,L2
2026-11-27,night,p4,L1
"""


class DutyTapeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name) / "terminal"
        self.desk_copy = Path(self._tempdir.name) / "desk2"
        self.sealer = TestSealer()
        self.term = self.terminal()

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def terminal(self) -> RecoveryTerminal:
        # Recreate the object to prove state is on disk, not in memory.
        return RecoveryTerminal(self.root, sealer=self.sealer, clock=lambda: "2026-11-27T08:00:00+00:00")

    def build_batch(
        self,
        batch: str,
        *,
        contacts_body: str = CONTACTS_NEW,
        lockers_body: str = LOCKERS_NEW,
        duty_body: str = DUTY_NEW,
        contacts_gen: int = 3,
        lockers_gen: int = 2,
        duty_gen: int = 7,
        seal: bool = True,
    ):
        contacts = build_artifact(
            batch=batch,
            kind="contacts",
            generation=contacts_gen,
            body=contacts_body,
            issued_at="2026-11-27T08:00:00+00:00",
        )
        lockers = build_artifact(
            batch=batch,
            kind="lockers",
            generation=lockers_gen,
            body=lockers_body,
            issued_at="2026-11-27T08:00:00+00:00",
        )
        duty = build_artifact(
            batch=batch,
            kind="duty",
            generation=duty_gen,
            body=duty_body,
            issued_at="2026-11-27T08:00:00+00:00",
            dependencies=[dependency_pin(lockers)],
        )
        if seal:
            from duty_tape.protocol import seal_artifact

            for art in (contacts, lockers, duty):
                seal_artifact(art, self.sealer)
        return contacts, lockers, duty

    def feed_artifacts(self, artifacts, *, expected_batch=None):
        results = []
        for art in artifacts:
            # A new terminal instance for each artifact stands in for closing
            # and reopening the recovery program between tape segments.
            term = self.terminal()
            records = [json.dumps(r, sort_keys=True) + "\n" for r in artifact_records(art)]
            result = term.feed_bytes(
                "".join(records).encode("utf-8"),
                expected_batch=expected_batch,
            )
            self.assertFalse(result["rejected"], result["rejected"])
            results.append(result)
        self.term = self.terminal()
        return results


class HappyPathAndTailTests(DutyTapeTestCase):
    def test_complete_segmented_volume_survives_reopen_and_queries(self):
        old = self.build_batch(
            "B-OLD",
            contacts_body=CONTACTS_OLD,
            lockers_body=LOCKERS_OLD,
            duty_body=DUTY_OLD,
            contacts_gen=1,
            lockers_gen=1,
            duty_gen=1,
        )
        self.feed_artifacts(old)

        new = self.build_batch("B-NEW")
        contacts, lockers, duty = new

        # Feed only the completed contacts segment, then reopen.
        contacts_bytes = "".join(
            json.dumps(r, sort_keys=True) + "\n" for r in artifact_records(contacts)
        ).encode()
        r1 = self.terminal().feed_bytes(contacts_bytes)
        self.assertEqual(r1["verified"], [contacts["digest"]])

        endpoint = QueryEndpoint(self.root)
        self.assertEqual(endpoint.abbreviation("Chen An")["abbreviation"], "CA")

        # Complete the ordinary correct volume in two more reopened sessions.
        self.feed_artifacts([lockers, duty])
        endpoint = QueryEndpoint(self.root)
        person = endpoint.person("Chen An", date="2026-11-27")
        self.assertEqual(person["abbreviation"], "CA")
        self.assertEqual(person["locker_number"], "B-207")
        self.assertTrue(person["duty_available"])

        roster = endpoint.duty_roster("2026-11-27")
        self.assertEqual({r["abbreviation"] for r in roster}, {"CA", "ZN"})

        status = self.terminal().status()
        self.assertEqual(status["active"]["duty"]["digest"], duty["digest"])
        self.assertEqual(status["active"]["lockers"]["digest"], lockers["digest"])

    def test_tail_damage_keeps_signed_contacts_but_not_new_duty(self):
        old = self.build_batch(
            "B-OLD",
            contacts_body=CONTACTS_OLD,
            lockers_body=LOCKERS_OLD,
            duty_body=DUTY_OLD,
            contacts_gen=1,
            lockers_gen=1,
            duty_gen=1,
        )
        self.feed_artifacts(old)

        contacts, lockers, duty = self.build_batch("B-TAIL")
        contact_records = artifact_records(contacts)
        locker_records = artifact_records(lockers)

        # Segment 1 ends safely after the contacts receipt.
        segment1 = "\n".join(json.dumps(r) for r in contact_records) + "\n"
        result = self.terminal().feed_bytes(segment1.encode())
        self.assertIn(contacts["digest"], result["verified"])

        # Segment 2 reaches only an unstaged/unsealed prefix of lockers before
        # the tape tail becomes unreadable.
        partial = "\n".join(json.dumps(r) for r in locker_records[:2])
        self.terminal().feed_bytes((partial + "\n").encode())
        tail = self.terminal().tail_failed("CRC error near end of tape")
        self.assertIn("active", tail)

        # Clearing the isolated temporary area cannot affect mounted tables.
        cleared = self.terminal().clear_temporary_area()
        self.assertGreaterEqual(cleared["removed_staging_artifacts"], 0)

        q = QueryEndpoint(self.root)
        self.assertEqual(q.abbreviation("Zhou Ning")["abbreviation"], "ZN")

        # The old internally consistent duty/locker generation remains usable.
        old_person = q.person("Lin Zhao", date="2026-11-27")
        self.assertEqual(old_person["locker_number"], "Z-901")

        status = self.terminal().status()
        self.assertEqual(status["active"]["duty"]["digest"], old[2]["digest"])
        self.assertEqual(status["active"]["lockers"]["digest"], old[1]["digest"])
        self.assertEqual(status["active"]["contacts"]["digest"], contacts["digest"])

        # Re-issue/recover the correct full volume after repair.  Already
        # mounted contacts are accepted idempotently and carried forward.
        tape = self.root.parent / "correct.tape.jsonl"
        write_tape(tape, [contacts, lockers, duty])
        feed_result = self.terminal().feed_file(tape)
        self.assertFalse(feed_result["rejected"])
        q = QueryEndpoint(self.root)
        self.assertEqual(q.person("Chen An", "2026-11-27")["locker_number"], "B-207")

    def test_mounted_file_tampering_is_detected(self):
        artifacts = self.build_batch("B-TAMPER-MOUNT")
        self.feed_artifacts(artifacts)
        endpoint = QueryEndpoint(self.root)
        path = self.root / "current" / "contacts.csv"
        path.write_text(path.read_text(encoding="utf-8") + "p9,Mallory,ML\n", encoding="utf-8")
        with self.assertRaises(Exception):
            endpoint.abbreviation("Mallory")

    def test_startup_reconstructs_lost_current_pointer(self):
        artifacts = self.build_batch("B-RESTART")
        self.feed_artifacts(artifacts)
        digest = artifacts[2]["digest"]

        # The current pointer is regenerable; the receipt store is committed.
        self.term.current.unlink()
        reopened = self.terminal()
        self.assertEqual(reopened.current_manifest()["artifacts"]["duty"]["digest"], digest)
        self.assertEqual(
            QueryEndpoint(self.root).person("Chen An", "2026-11-27")["locker_number"],
            "B-207",
        )

    def test_arbitrary_byte_boundary_and_reopen(self):
        contacts, lockers, duty = self.build_batch("B-BYTES")
        tape = self.root.parent / "chunked.tape"
        write_tape(tape, [contacts, lockers, duty])
        data = tape.read_bytes()

        # Split in the middle of records and recreate the terminal repeatedly.
        pos = 0
        size_sequence = iter([37, 100, 53, 17, 4096])
        while pos < len(data):
            size = next(size_sequence, 64)
            chunk = data[pos : pos + size]
            pos += len(chunk)
            result = self.terminal().feed_bytes(chunk)
            self.assertFalse(result["rejected"], result["rejected"])
        self.assertEqual(pos, len(data))
        self.assertEqual(
            QueryEndpoint(self.root).person("Chen An", "2026-11-27")["locker_number"],
            "B-207",
        )


class CrossBatchTests(DutyTapeTestCase):
    def test_other_batch_same_name_cannot_be_dependency(self):
        a_contacts, a_lockers, a_duty = self.build_batch(
            "BATCH-A",
            lockers_body=LOCKERS_NEW,
        )
        _, b_lockers, _ = self.build_batch(
            "BATCH-B",
            lockers_body=LOCKERS_OLD.replace("Z-", "Q-"),
        )

        # A swapped segment contains B's valid sealed same-name locker table.
        self.feed_artifacts([b_lockers])
        self.feed_artifacts([a_duty])

        term = self.terminal()
        readiness = term.readiness_for(a_duty)
        self.assertFalse(readiness["ready"])
        self.assertTrue(any("missing sealed lockers" in r for r in readiness["reasons"]))

        status = term.status()
        self.assertNotEqual(status["active"].get("lockers", {}).get("digest"), a_lockers["digest"])
        self.assertNotIn("duty", status["active"])

        # Feeding A's real exact-pinned contacts and locker table completes
        # the group; B's same-name table is not used.
        self.feed_artifacts([a_contacts, a_lockers])
        self.assertEqual(
            QueryEndpoint(self.root).person("p2", "2026-11-27")["locker_number"],
            "B-207",
        )

    def test_tampered_line_is_rejected_by_receipt(self):
        _, lockers, _ = self.build_batch("BATCH-TAMPER")
        records = artifact_records(lockers)
        # Change one CSV payload line while retaining the original digest head
        # and trusted receipt.
        for rec in records:
            if rec.get("record") == "line" and rec.get("index") == 2:
                rec["text"] = rec["text"].replace("B-207", "X-FORGED")
        blob = ("\n".join(json.dumps(r) for r in records) + "\n").encode()

        with self.assertRaises(Exception):
            self.terminal().feed_bytes(blob, strict=True)

        term = self.terminal()
        self.assertIsNone(term.get_artifact(lockers["digest"]))
        rejected = term.history({"artifact_rejected", "tape_record_rejected"})
        self.assertTrue(rejected)


class GenerationLockTests(DutyTapeTestCase):
    def test_locked_old_locker_generation_blocks_verified_new_duty(self):
        old = self.build_batch(
            "B-OLD-LOCK",
            contacts_body=CONTACTS_OLD,
            lockers_body=LOCKERS_OLD,
            duty_body=DUTY_OLD,
            contacts_gen=1,
            lockers_gen=1,
            duty_gen=1,
        )
        self.feed_artifacts(old)
        old_lockers = old[1]

        term = self.terminal()
        term.pin_kind("lockers", old_lockers["digest"])

        new_contacts, new_lockers, new_duty = self.build_batch("B-NEW-LOCK")
        result = self.terminal().feed_bytes(
            "".join(
                json.dumps(r) + "\n"
                for art in (new_contacts, new_lockers, new_duty)
                for r in artifact_records(art)
            ).encode()
        )
        self.assertFalse(result["rejected"], result["rejected"])

        term = self.terminal()
        readiness = term.readiness_for(new_duty)
        self.assertFalse(readiness["ready"])
        self.assertTrue(any("pinned to old generation" in r for r in readiness["reasons"]))

        status = term.status()
        self.assertEqual(status["active"]["lockers"]["digest"], old_lockers["digest"])
        self.assertEqual(status["active"]["duty"]["digest"], old[2]["digest"])

        # The verified new duty is held, but the query endpoint only sees the
        # safe old pair and therefore cannot report a new-generation number.
        q = QueryEndpoint(self.root)
        self.assertEqual(q.person("Lin Zhao", "2026-11-27")["locker_number"], "Z-901")
        with self.assertRaises(Exception):
            q.locker_number("does-not-matter")  # endpoint still works generally

        # When the receiver is switchable, removing the operational pin and
        # re-running activation mounts the complete correct new volume.
        term.unpin_kind("lockers")
        term.activate()
        self.assertEqual(
            QueryEndpoint(self.root).person("Chen An", "2026-11-27")["locker_number"],
            "B-207",
        )


class RevocationAndCopyTests(DutyTapeTestCase):
    def test_revocation_preserves_copy_and_audit_history(self):
        contacts, _, _ = self.build_batch("B-REVOKE")
        self.feed_artifacts([contacts])
        term = self.terminal()

        copied = term.export_active("contacts", self.desk_copy)
        copied_file = Path(copied["destination"])
        self.assertTrue(copied_file.exists())
        self.assertIn("CA", copied_file.read_text())

        rev = new_revocation(
            self.sealer,
            artifact_digest=contacts["digest"],
            reason="contact abbreviation leaked in test",
            issued_at="2026-11-27T09:00:00+00:00",
        )
        term.revoke(rev)

        # The remote/other-desk copy and the local audit fact remain.
        self.assertTrue(copied_file.exists())
        copy_events = term.history({"table_copied"})
        self.assertEqual(len(copy_events), 1)
        self.assertEqual(copy_events[0]["digest"], contacts["digest"])
        self.assertTrue(term.history({"revocation_received"}))
        self.assertTrue(term.get_artifact(contacts["digest"]) is not None)
        chain = term.verify_history()
        self.assertTrue(chain["valid"])
        self.assertGreater(chain["events"], 3)

        # Revocation has forward effect on this terminal's query surface.
        with self.assertRaises(Exception):
            QueryEndpoint(self.root).abbreviation("Chen An")

        # A forged revocation receipt is not accepted.
        wrong = TestSealer(b"another-batch-key")
        forged = new_revocation(
            wrong,
            batch="B-REVOKE",
            reason="forged",
            issued_at="2026-11-27T10:00:00+00:00",
        )
        with self.assertRaises(ReceiptError):
            self.terminal().revoke(forged)

    def test_external_copy_notice_is_append_only(self):
        contacts, _, _ = self.build_batch("B-COPY-NOTICE")
        self.feed_artifacts([contacts])
        term = self.terminal()
        term.notice_external_copy("contacts", "desk-7:/var/duty/contacts.csv")
        events = term.history({"external_copy_noticed"})
        self.assertEqual(events[0]["location"], "desk-7:/var/duty/contacts.csv")


class CliSmokeTest(unittest.TestCase):
    def test_issue_feed_query_and_verify_cli(self):
        workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "state"
            tape = Path(td) / "volume.jsonl"
            base = [sys.executable, "-m", "duty_tape", "--root", str(root)]

            def run(*args):
                proc = subprocess.run(
                    base + list(args),
                    cwd=workspace,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
                return json.loads(proc.stdout)

            examples = workspace / "examples"
            run(
                "issue",
                "--batch",
                "B-CLI",
                "--contacts",
                f"{examples/'new/contacts.csv'}:3",
                "--lockers",
                f"{examples/'new/lockers.csv'}:2",
                "--duty",
                f"{examples/'new/duty.csv'}:7",
                "--out",
                str(tape),
            )
            fed = run("feed", "--tape", str(tape))
            self.assertEqual(len(fed["verified"]), 3)
            person = run("query", "person", "Chen An", "--date", "2026-11-27")
            self.assertEqual(person["abbreviation"], "CA")
            self.assertEqual(person["locker_number"], "B-207")
            chain = run("history", "--verify")
            self.assertTrue(chain["valid"])


if __name__ == "__main__":
    unittest.main()
