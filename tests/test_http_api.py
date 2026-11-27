from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from forensic_desk import make_tree_writable
from forensic_desk.fixtures import write_all_fixtures
from forensic_desk.server import DeskHttpServer


class HttpApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="forensic-http-"))
        self.disks = write_all_fixtures(self.tmp / "disks")
        self.server = DeskHttpServer(self.tmp / "cases", host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        make_tree_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post(self, path: str, payload: dict, headers: dict | None = None) -> dict:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_intake_review_and_shared_record_over_http(self) -> None:
        intake = self._post(
            "/intake/cases/http-demo",
            {
                "disk_path": str(self.disks["second"]),
                "source": "HTTP 测试盘",
                "generation": "G-HTTP",
                "signed_by": "委托人",
                "collection_mode": "custodian_cow",
                "readable_blocks": ["DIRECTORY", "JOURNAL", "LAYOUT", "BLK001"],
                "page_ranges": ["P001"],
            },
        )
        record_id = intake["intake_record"]["id"]
        self.assertEqual(record_id, "REC-000001")

        response = self._post(
            "/review/cases/http-demo/requests",
            {"kind": "entry", "name": "接盘时已存在的行.txt"},
            {"X-Investigator": "http-reviewer"},
        )
        self.assertEqual(
            response["conclusion"]["payload"]["status"],
            "PHYSICAL_PRESENT_AT_INTAKE",
        )

        shared = self._get(f"/cases/http-demo/records/{record_id}")
        self.assertEqual(shared["id"], record_id)
        self.assertTrue(self._get("/health")["ok"])


if __name__ == "__main__":
    unittest.main()
