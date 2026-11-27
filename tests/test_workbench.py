from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mojiao.dictionary import DictionarySet
from mojiao.reviewed_resolver import resolve
from reviewdesk.cabinet import append_event, query_word
from reviewdesk.hashing import canonical_hash
from reviewdesk.ledger import verify_chain

ROOT = Path(__file__).resolve().parents[1]


class DictionaryTests(unittest.TestCase):
    def test_main_reviewed_resolver_handles_modern_legacy_and_text(self) -> None:
        dictionaries = DictionarySet.load_paths([ROOT / "wordlists/school-promised-modern-2.1.0.json"])
        by_modern = resolve({"id": "W-1001"}, dictionaries)
        by_legacy = resolve({"id": "1002"}, dictionaries)
        by_text = resolve({"text": "按装"}, dictionaries)
        self.assertEqual(by_modern["canonical"], "安装")
        self.assertEqual(by_legacy["canonical"], "图像")
        self.assertEqual(by_text["canonical"], "安装")
        self.assertFalse(by_modern["egress_performed"])

    def test_legacy_table_loads_old_ids(self) -> None:
        dictionaries = DictionarySet.load_paths([ROOT / "wordlists/school-promised-legacy-1.4.0.json"])
        self.assertIn("1001", dictionaries.by_legacy_id)
        result = resolve({"id": "1003"}, dictionaries)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["canonical"], "订正")


class CabinetTests(unittest.TestCase):
    def test_cabinet_can_query_specific_word(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "cabinet.jsonl"
            append_event(state, cabinet_id="test", word="1001", source="unit")
            append_event(state, cabinet_id="test", word="1002", source="unit")
            self.assertEqual(len(query_word(state)), 2)
            self.assertEqual([e["word"] for e in query_word(state, "1001")], ["1001"])
            self.assertEqual(query_word(state, "missing"), [])


class LedgerTests(unittest.TestCase):
    def test_existing_demo_ledger_chain_is_valid_if_present(self) -> None:
        result = verify_chain()
        if (ROOT / "review-desk/ledger.jsonl").exists():
            self.assertTrue(result["ok"], result)

    def test_canonical_hash_stable(self) -> None:
        self.assertEqual(canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1}))


if __name__ == "__main__":
    unittest.main()
