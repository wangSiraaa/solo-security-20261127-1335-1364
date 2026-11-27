from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from coupon_demo.service import CouponService
from coupon_demo.web import create_server


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CouponService(":memory:")
        self.server: ThreadingHTTPServer = create_server(
            self.service, host="127.0.0.1", port=0
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.service.close()

    def _get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()

    def _post(self, path: str, body: dict):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())

    def test_ui_and_state_are_served(self) -> None:
        status, content_type, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("刮刮券", body.decode("utf-8"))

        status, _, raw = self._get("/api/state")
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertTrue(payload["ok"])
        self.assertIn("decision", payload["state"])

    def test_seed_and_redeem_api_do_not_echo_submitted_secret(self) -> None:
        status, seeded = self._post("/api/demo/seed", {"reset": True})
        self.assertEqual(status, 200)
        self.assertTrue(seeded["ok"])
        codes = {
            item["ticket_no"]: item["secret"]
            for item in seeded["result"]["incident"]["replacement_manifest_once"]
        }
        self._post(
            "/api/coupons/N-002/claim",
            {"counter_id": "COUNTER-2", "holder_name": "网页测试"},
        )
        status, redeemed = self._post(
            "/api/redeem",
            {
                "ticket_no": "N-002",
                "secret": codes["N-002"],
                "counter_id": "COUNTER-2",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(redeemed["ok"])
        self.assertNotIn(codes["N-002"], json.dumps(redeemed, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
