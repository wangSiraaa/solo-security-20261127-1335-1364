from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from subtitle_sim.cli import main
from subtitle_sim.common import Store, sign_doc, verify_doc
from subtitle_sim.engine import verify_receipt_for_session


CONSENT_DIRECT = "trusted/consents/CONS-A-17-DIRECT-OLD1.consent.json"
CONSENT_RELAY = "trusted/consents/CONS-A-17-RELAY-OLD1.consent.json"


class SimulationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "scene"
        self.rc("init", "--force")
        self.store = Store(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def rc(self, *args: str) -> int:
        return main([*args, "--root", str(self.root)])

    def rc_fail(self, *args: str) -> str:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self.rc(*args)
        self.assertEqual(code, 2, f"expected policy failure for {args}; stderr={err.getvalue()}")
        return err.getvalue()

    def negotiate_direct(self, consent: str = CONSENT_DIRECT) -> None:
        self.assertEqual(0, self.rc("negotiate", "--consent", str(self.root / consent)))

    def view_both(self, message: str, old_seat: str = "old-1") -> None:
        self.assertEqual(0, self.rc("new-viewer", "view", message))
        self.assertEqual(0, self.rc("old-viewer", "--seat", old_seat, "view", message))

    def proof_json(self, name: str) -> dict:
        return json.loads((self.root / "access/proofs" / name).read_text(encoding="utf-8"))

    def test_login_success_does_not_establish_convention(self) -> None:
        self.assertEqual(0, self.rc("login", "--seat", "new-1"))
        err = self.rc_fail("send", "--sequence", "X", "--text", "尚未约定")
        self.assertIn("尚未核对/建立本期会话约定", err)

    def test_direct_same_text_revision_and_skew_block(self) -> None:
        self.rc("login", "--seat", "new-1")
        self.negotiate_direct()

        # Ordinary subtitle both actually display.
        self.assertEqual(
            0,
            self.rc("send", "--sequence", "SUB-001", "--version", "1", "--text", "今晚联播准时开始。"),
        )
        self.view_both("MSG-SUB-001-v1")
        self.assertEqual(0, self.rc("confirm-same-text", "--sequence", "SUB-001", "--version", "1"))
        proof_path = self.root / "access/proofs/MSG-SUB-001-v1.same-text.proof.json"
        self.assertEqual(0, self.rc("verify-proof", str(proof_path)))
        proof = self.proof_json("MSG-SUB-001-v1.same-text.proof.json")
        self.assertEqual(2, len(proof["receipts"]))
        old_receipt = next(r for r in proof["receipts"].values() if r["receipt_type"] == "old-received/v1")
        self.assertFalse(old_receipt["note_endorsed"])
        self.assertIsNone(old_receipt["displayed_note_sha256"])
        self.assertIn("今晚联播准时开始。", proof["screens"]["old-1"])
        self.assertIn("当班场记备注", proof["screens"]["new-1"])
        self.assertNotIn("当班场记备注", proof["screens"]["old-1"])

        # Director-authored revision: both ends get display drafts/receipts.
        revision = self.root / "director-revision.txt"
        revision.write_text("导播自写改词：一分钟后收网。", encoding="utf-8")
        self.assertEqual(
            0,
            self.rc("send", "--sequence", "SUB-002", "--version", "1", "--text", "旧词：马上收网。"),
        )
        self.view_both("MSG-SUB-002-v1")
        self.assertEqual(
            0,
            self.rc(
                "send",
                "--sequence",
                "SUB-002",
                "--version",
                "2",
                "--body-file",
                str(revision),
                "--kind",
                "director-revision",
            ),
        )
        self.view_both("MSG-SUB-002-v2")
        self.assertEqual(0, self.rc("confirm-same-text", "--sequence", "SUB-002", "--version", "2"))

        # Another edit: only new seat moves to v2; old remains at v1.
        self.assertEqual(0, self.rc("send", "--sequence", "SUB-003", "--version", "1", "--text", "改词前句子。"))
        self.view_both("MSG-SUB-003-v1")
        self.assertEqual(0, self.rc("send", "--sequence", "SUB-003", "--version", "2", "--text", "新词。"))
        self.assertEqual(0, self.rc("new-viewer", "view", "MSG-SUB-003-v2"))
        err = self.rc_fail("confirm-same-text", "--sequence", "SUB-003", "--version", "2")
        self.assertIn("VERSION_SKEW", err)
        self.assertIn("old-1", err)
        self.assertIn("MSG-SUB-003-v1", err)

    def test_one_capability_scene_change_forces_session_rebuild(self) -> None:
        self.negotiate_direct()
        self.assertEqual(
            0, self.rc("send", "--sequence", "SUB-001", "--text", "约定有效期内的普通字幕")
        )
        self.rc("new-viewer", "view", "MSG-SUB-001-v1")
        self.rc("old-viewer", "view", "MSG-SUB-001-v1")
        self.assertEqual(0, self.rc("confirm-same-text", "--sequence", "SUB-001", "--version", "1"))

        self.assertEqual(0, self.rc("set-scene", "--seat", "new-1", "--scene", "B-209"))
        err = self.rc_fail("send", "--sequence", "SUB-002", "--text", "不应进入旧会话")
        self.assertIn("CAPABILITY_DRIFT", err)
        err = self.rc_fail("negotiate", "--consent", str(self.root / CONSENT_DIRECT))
        self.assertIn("场次不一致", err)

        # A valid rebuild requires a coherent trusted statement + fresh consent.
        self.assertEqual(0, self.rc("set-scene", "--seat", "new-1", "--scene", "A-17"))
        self.assertEqual(
            0,
            self.rc(
                "issue-consent",
                "--profile",
                "direct-common-text/v1",
                "--seats",
                "new-1,old-1",
                "--consent-id",
                "CONS-REBUILT",
            ),
        )
        self.assertEqual(0, self.rc("negotiate", "--consent", str(self.root / "trusted/consents/CONS-REBUILT.consent.json")))
        pointer = self.store.active_pointer()
        self.assertTrue(pointer["convention_valid"])
        self.assertNotEqual("SES-A-17-001", pointer["active_session_id"])

    def test_mid_show_old_seat_replacement_cannot_inherit_convention(self) -> None:
        self.negotiate_direct()
        old_session = self.store.load_session("SES-A-17-001")

        self.assertEqual(
            0,
            self.rc("suspend-session", "--reason", "联播中途更换旧席硬件", "--replacement-seat", "old-2"),
        )
        err = self.rc_fail("send", "--sequence", "AFTER-REPLACE", "--text", "旧约定不应继续")
        self.assertIn("原会话约定已失效", err)

        self.assertEqual(
            0,
            self.rc(
                "issue-consent",
                "--profile",
                "direct-common-text/v1",
                "--seats",
                "new-1,old-2",
                "--consent-id",
                "CONS-OLD2",
            ),
        )
        self.assertEqual(0, self.rc("negotiate", "--consent", str(self.root / "trusted/consents/CONS-OLD2.consent.json")))
        self.assertEqual(0, self.rc("send", "--sequence", "SUB-009", "--text", "新端点重新核对后同文。"))
        self.assertEqual(0, self.rc("new-viewer", "view", "MSG-SUB-009-v1"))
        self.assertEqual(0, self.rc("old-viewer", "--seat", "old-2", "view", "MSG-SUB-009-v1"))

        # old-2's signed receipt is not accepted against the original session.
        receipt = self.store.read_json(self.store.seat_receipts("old-2") / "MSG-SUB-009-v1.receipt.json")
        with self.assertRaises(Exception) as ctx:
            verify_receipt_for_session(receipt, old_session, self.store)
        self.assertIn("UNKNOWN_SEAT", str(ctx.exception))

        self.assertEqual(0, self.rc("confirm-same-text", "--sequence", "SUB-009", "--version", "1"))

    def test_relay_conversion_records_turntable_only_attestation(self) -> None:
        self.assertEqual(0, self.rc("negotiate", "--consent", str(self.root / CONSENT_RELAY)))
        note = (
            "@relay 方音：{DY-01}；{DY-02}。\n"
            "@duty 仅当班看见，不能进入旧席或观众稿。"
        )
        revision = self.root / "dao-bo-revision.txt"
        revision.write_text("导播自写改词：慢些收网。", encoding="utf-8")
        self.assertEqual(
            0,
            self.rc(
                "send",
                "--sequence",
                "SUB-002",
                "--version",
                "2",
                "--body-file",
                str(revision),
                "--note",
                note,
                "--kind",
                "director-revision",
            ),
        )
        self.assertEqual(0, self.rc("turntable", "process-job", "JOB-MSG-SUB-002-v2"))
        self.assertEqual(0, self.rc("new-viewer", "view", "MSG-SUB-002-v2"))
        self.assertEqual(0, self.rc("old-viewer", "view", "MSG-SUB-002-v2"))

        old_screen = (self.root / "seats/old-1/screens/MSG-SUB-002-v2.screen.txt").read_text(encoding="utf-8")
        self.assertIn("“伲”读如“尼”", old_screen)
        self.assertIn("“朆”读如“芬”", old_screen)
        self.assertNotIn("@duty", old_screen)
        self.assertNotIn("仅当班看见", old_screen)

        cert = json.loads((self.root / "access/conversion-certs/CERT-MSG-SUB-002-v2.cert.json").read_text(encoding="utf-8"))
        self.assertEqual("turntable", cert["attestor_role"])
        self.assertFalse(cert["duty_only_section_seen"])
        kinds = [s["kind"] for s in cert["segments"]]
        self.assertIn("entrusted-dialect-code", kinds)

        # Access/new-seat keys cannot forge the turntable-only attestation.
        forged = {k: v for k, v in cert.items() if k != "signature"}
        forged["attestor_role"] = "access"
        forged = sign_doc(forged, self.store.key("access")["secret_hex"])
        self.assertFalse(verify_doc(forged, self.store.public_for("turntable")))

        self.assertEqual(0, self.rc("confirm-relay", "--sequence", "SUB-002", "--version", "2"))
        proof_path = self.root / "access/proofs/MSG-SUB-002-v2.relay-handover.proof.json"
        self.assertEqual(0, self.rc("verify-proof", str(proof_path)))

        # The pronunciation table must not be copied into the new seat area.
        new_area = (self.root / "seats/new-1").rglob("*")
        for p in new_area:
            if p.is_file():
                self.assertNotIn("DY-01\t", p.read_text(encoding="utf-8", errors="ignore"))


if __name__ == "__main__":
    unittest.main()
