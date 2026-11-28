"""Tests for the content model: fields, aliases, statements, validation."""

import json
import os
import unittest

import hintdesk as H
from conftest import EXAMPLES


def load(name):
    return H.Library.from_file(os.path.join(EXAMPLES, name))


def minimal(**over):
    data = {
        "puzzle_id": "min",
        "fields": {"k": {"literals": ["a", "b", "c"]}},
        "truth": {"k": "a"},
        "statements": [
            {"key": "eq_a", "kind": "eq", "var": "k", "value": "a", "text": "a"},
            {"key": "eq_b", "kind": "eq", "var": "k", "value": "b", "text": "b"},
            {"key": "eq_c", "kind": "eq", "var": "k", "value": "c", "text": "c"},
        ],
        "pages": {
            "p_a": {"text": "A", "give": ["eq_a"]},
            "p_b": {"text": "B", "give": ["eq_b"]},
            "p_c": {"text": "C", "give": ["eq_c"]},
        },
        "questions": [
            {"key": "q", "text": "q?", "responses": [
                {"when": {"k": "a"}, "page": "p_a"},
                {"when": {"k": "b"}, "page": "p_b"},
                {"when": {"k": "c"}, "page": "p_c"},
            ]},
        ],
        "final_questions": [],
    }
    data.update(over)
    return data


class FieldAndAliasTests(unittest.TestCase):
    def test_aliases_collapse_to_one_answer(self):
        d = minimal()
        d["fields"]["k"] = {"literals": ["a", "a2", "b", "c"], "aliases": [["a", "a2"]]}
        d["truth"] = {"k": "a2"}
        lib = H.Library.from_dict(d)
        f = lib.fields["k"]
        self.assertEqual(f.answers, ("a", "b", "c"))
        self.assertEqual(f.c("a2"), "a")
        self.assertEqual(lib.truth["k"], "a")
        # three worlds, not four
        self.assertEqual(len(lib.worlds()), 3)

    def test_alias_must_be_a_literal(self):
        d = minimal()
        d["fields"]["k"] = {"literals": ["a", "b", "c"], "aliases": [["a", "zzz"]]}
        with self.assertRaises(H.ContentError):
            H.Library.from_dict(d)

    def test_truth_must_be_a_legal_literal(self):
        d = minimal()
        d["truth"] = {"k": "zzz"}
        with self.assertRaises(H.ContentError):
            H.Library.from_dict(d)

    def test_missing_truth_field_rejected(self):
        d = minimal()
        d["truth"] = {}
        with self.assertRaises(H.ContentError):
            H.Library.from_dict(d)


class StatementTests(unittest.TestCase):
    def test_eq_and_or_and_neq(self):
        lib = load("lantern.json")
        w = lib.truth
        self.assertTrue(lib.statements["v_alice"].evaluate(w))
        self.assertFalse(lib.statements["v_bob"].evaluate(w))
        # or statement
        st = H.Statement(key="x", kind="or", text="", spec={"var": "weapon",
                        "values": ("candle", "rope")}, deps=("weapon",))
        self.assertTrue(st.evaluate(w))

    def test_atleast(self):
        lib = load("lantern.json")
        st = H.Statement(key="x", kind="atleast", text="",
                         spec={"n": 2, "conditions": (("victim", "alice"),
                                                      ("room", "north"),
                                                      ("weapon", "rope"))},
                         deps=("victim", "room", "weapon"))
        # truth = alice/north/candle -> 2 of 3 hold
        self.assertTrue(st.evaluate(lib.truth))


class ValidationTests(unittest.TestCase):
    def test_untruthful_primary_page_rejected(self):
        d = minimal()
        d["questions"][0]["default"] = "p_a"  # but responses cover all, fine
        # remove the c response so default p_a would serve world c
        d["questions"][0]["responses"] = [
            {"when": {"k": "a"}, "page": "p_a"},
            {"when": {"k": "b"}, "page": "p_b"},
        ]
        d["questions"][0]["default"] = "p_a"
        with self.assertRaises(H.ContentError):
            H.Library.from_dict(d)

    def test_identical_page_content_within_question_rejected(self):
        d = minimal()
        # make p_b and p_c identical content by sharing the same give
        d["pages"]["p_b"] = {"text": "C again", "give": ["eq_c"]}
        d["pages"]["p_c"] = {"text": "C", "give": ["eq_c"]}
        # recompute responses so both b and c map to their pages
        with self.assertRaises(H.ContentError):
            H.Library.from_dict(d)

    def test_duplicate_question_wording_rejected_by_engine(self):
        d = minimal()
        d["questions"].append({"key": "q_dup", "text": "q?", "responses": [
            {"when": {"k": "a"}, "page": "p_a"},
            {"when": {"k": "b"}, "page": "p_b"},
            {"when": {"k": "c"}, "page": "p_c"}]})
        lib = H.Library.from_dict(d)   # library itself allows it
        with self.assertRaises(ValueError):
            H.Engine(lib)              # engine needs unique wording

    def test_unknown_statement_in_page_rejected(self):
        d = minimal()
        d["pages"]["p_a"] = {"text": "A", "give": ["nope"]}
        with self.assertRaises(H.ContentError):
            H.Library.from_dict(d)

    def test_content_digest_ignores_page_id(self):
        lib = load("lantern.json")
        # p_north and final_n carry the same statement
        self.assertEqual(lib.pages["p_north"].content_digest(),
                         lib.pages["final_n"].content_digest())


if __name__ == "__main__":
    unittest.main()
