"""Behavior tests. Most checks spawn a fresh CLI process against temp files."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


class StationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.state = self.root / "state"
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = str(ROOT)
        self.env.pop("ANON_STATION_NOW", None)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def cli(self, command: str, *args: Any, now: int | float | None = None):
        env = self.env.copy()
        if now is not None:
            env["ANON_STATION_NOW"] = str(int(now))
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "anon_station",
                command,
                "--state",
                str(self.state),
                *[str(arg) for arg in args],
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {"raw_stdout": proc.stdout, "stderr": proc.stderr}
        return proc.returncode, payload

    def init(self, *args: Any) -> None:
        code, payload = self.cli("init", *args)
        self.assertEqual(code, 0, payload)

    def witness(self, channel: str, name: str = "witness.json") -> Path:
        path = self.root / name
        code, payload = self.cli("roundtrip", "--channel", channel, "--out", path)
        self.assertEqual(code, 0, payload)
        return path

    def test_reservation_survives_new_process_line_change_and_delivery(self) -> None:
        self.init("--slots", 1)
        old_w = self.witness("old")
        ticket = self.root / "ticket.json"

        code, ask = self.cli(
            "ask", "--witness", old_w, "--segments", "2", "--ticket-out", ticket
        )
        self.assertEqual(code, 0, ask)
        self.assertTrue(ask["seat_held"])

        # A new witness/process for another channel is not enough by itself.
        new_w = self.witness("new", "new.json")
        code, rejected = self.cli(
            "write", "--ticket-file", ticket, "--witness", new_w,
            "--index", "0", "--data", "a",
        )
        self.assertEqual(code, 7)
        self.assertEqual(rejected["code"], "CHANNEL_MIGRATION_REQUIRED")

        code, migrated = self.cli(
            "migrate", "--ticket-file", ticket, "--witness", new_w
        )
        self.assertEqual(code, 0, migrated)

        code, first = self.cli(
            "write", "--ticket-file", ticket, "--witness", new_w,
            "--index", "0", "--data", "hello-",
        )
        self.assertEqual(code, 0, first)
        self.assertFalse(first["duplicate"])

        code, second = self.cli(
            "write", "--ticket-file", ticket, "--witness", new_w,
            "--index", "1", "--data", "world",
        )
        self.assertEqual(code, 0, second)
        self.assertEqual(second["state"], "complete")

        code, delivered = self.cli(
            "deliver", "--ticket-file", ticket, "--signoff", "tester"
        )
        self.assertEqual(code, 0, delivered)
        self.assertEqual(delivered["artifact"]["assembled"], "hello-world")

        code, inspect = self.cli("inspect", "--json")
        self.assertEqual(code, 0, inspect)
        self.assertEqual(inspect["slots"][0]["state"], "delivered")

    def test_tampered_roundtrip_report_is_rejected(self) -> None:
        self.init("--slots", 1)
        witness = self.witness("home")
        ticket = self.root / "ticket.json"
        code, ask = self.cli("ask", "--witness", witness, "--segments", "1", "--ticket-out", ticket)
        self.assertEqual(code, 0, ask)

        report = json.loads(witness.read_text())
        report["channel"] = "elsewhere"
        witness.write_text(json.dumps(report))

        code, payload = self.cli(
            "write", "--ticket-file", ticket, "--witness", witness,
            "--index", "0", "--data", "x",
        )
        self.assertEqual(code, 4)
        self.assertEqual(payload["code"], "REPORT_CHANNEL_MISMATCH")

    def test_half_item_keeps_slot_until_deadline(self) -> None:
        self.init("--slots", "1", "--partial-ttl", "60")
        witness = self.witness("old")
        ticket = self.root / "ticket.json"
        self.cli("ask", "--witness", witness, "--segments", "3", "--ticket-out", ticket)
        self.cli(
            "write", "--ticket-file", ticket, "--witness", witness,
            "--index", "0", "--data", "0",
        )
        self.cli(
            "write", "--ticket-file", ticket, "--witness", witness,
            "--index", "1", "--data", "1",
        )

        code, blocked = self.cli("clear-slot", "--slot", "1")
        self.assertEqual(code, 5)
        self.assertEqual(blocked["code"], "SLOT_NOT_RELEASABLE")

        candidate = self.witness("candidate", "candidate.json")
        code, full = self.cli("ask", "--witness", candidate, "--segments", "1")
        self.assertEqual(code, 3)
        self.assertEqual(full["code"], "CABINET_FULL")
        self.assertFalse(full["seat_held"])
        self.assertTrue(full["retry_allowed"])

        code, not_reaped = self.cli("reap")
        self.assertEqual(code, 0)
        self.assertEqual(not_reaped["count"], 0)

        future = time.time() + 120
        code, reaped = self.cli("reap", now=future)
        self.assertEqual(code, 0)
        self.assertEqual(reaped["count"], 1)

        code, status = self.cli("inspect", "--json")
        self.assertEqual(status["slots"][0]["state"], "empty")

    def test_duplicate_first_segment_is_idempotent_but_conflict_is_not(self) -> None:
        self.init("--slots", 1)
        witness = self.witness("ch")
        ticket = self.root / "ticket.json"
        self.cli("ask", "--witness", witness, "--segments", "2", "--ticket-out", ticket)

        args = (
            "write", "--ticket-file", ticket, "--witness", witness,
            "--index", "0", "--data", "same",
        )
        code, first = self.cli(*args)
        self.assertEqual(code, 0, first)
        code, duplicate = self.cli(*args)
        self.assertEqual(code, 0, duplicate)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["segments_received"], 1)

        code, conflict = self.cli(
            "write", "--ticket-file", ticket, "--witness", witness,
            "--index", "0", "--data", "different",
        )
        self.assertEqual(code, 5)
        self.assertEqual(conflict["code"], "SEGMENT_CONFLICT")

    def test_onwrite_ask_does_not_hold_slot(self) -> None:
        self.init("--slots", "1", "--policy", "onwrite")
        wx = self.witness("x", "wx.json")
        wy = self.witness("y", "wy.json")
        vx = self.root / "vx.json"
        vy = self.root / "vy.json"
        ty = self.root / "ty.json"

        code, ax = self.cli("ask", "--witness", wx, "--segments", "1", "--voucher-out", vx)
        self.assertEqual(code, 0, ax)
        self.assertFalse(ax["seat_held"])

        code, ay = self.cli("ask", "--witness", wy, "--segments", "1", "--voucher-out", vy)
        self.assertEqual(code, 0, ay)
        self.assertFalse(ay["seat_held"])

        code, write_y = self.cli(
            "write", "--voucher-file", vy, "--witness", wy,
            "--index", "0", "--data", "y", "--ticket-out", ty,
        )
        self.assertEqual(code, 0, write_y)
        self.assertTrue(write_y["allocated"])

        code, write_x = self.cli(
            "write", "--voucher-file", vx, "--witness", wx,
            "--index", "0", "--data", "x",
        )
        self.assertEqual(code, 3)
        self.assertEqual(write_x["code"], "FULL_AT_FIRST_SEGMENT")
        self.assertFalse(write_x["seat_held"])

        code, status = self.cli("inspect", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(status["orders"]), 1)

    def test_delivered_hold_is_released_but_half_item_is_not(self) -> None:
        self.init("--slots", "1")
        witness = self.witness("ch")
        ticket = self.root / "ticket.json"
        self.cli("ask", "--witness", witness, "--segments", "1", "--ticket-out", ticket)
        self.cli(
            "write", "--ticket-file", ticket, "--witness", witness,
            "--index", "0", "--data", "done",
        )
        code, delivered = self.cli("deliver", "--ticket-file", ticket, "--signoff", "clerk")
        self.assertEqual(code, 0, delivered)

        # The finished record remains visible, but its hold is released and the
        # one-cell shift can accept a fresh order without an explicit clear.
        code, inspect = self.cli("inspect", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(inspect["slots"][0]["state"], "delivered")
        self.assertTrue(inspect["slots"][0]["hold_released"])

        second_witness = self.witness("ch2", "w2.json")
        second_ticket = self.root / "ticket2.json"
        code, second = self.cli(
            "ask", "--witness", second_witness, "--segments", "1",
            "--ticket-out", second_ticket,
        )
        self.assertEqual(code, 0, second)
        self.assertEqual(second["slot"], 1)

    def test_concurrent_one_slot_asks_have_exactly_one_winner(self) -> None:
        self.init("--slots", "1")
        witness = self.witness("shared-channel")
        processes = []
        for index in range(8):
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable, "-m", "anon_station", "ask",
                        "--state", str(self.state),
                        "--witness", str(witness),
                        "--segments", "1",
                    ],
                    cwd=ROOT,
                    env=self.env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            )
        results = []
        for proc in processes:
            stdout, stderr = proc.communicate(timeout=30)
            self.assertEqual(stderr, "")
            results.append((proc.returncode, json.loads(stdout)))

        winners = [item for code, item in results if code == 0]
        full = [item for code, item in results if code == 3]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(len(full), 7, results)
        self.assertTrue(all(item["code"] == "CABINET_FULL" for item in full))

    def test_demo_script_passes(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "demo.py")],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            self.fail(f"demo failed\nstdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("DEMO PASSED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
