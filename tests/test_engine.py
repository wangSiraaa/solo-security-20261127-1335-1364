"""Tests for the shared issuance engine: promises, counting, re-issue, material."""

import os
import unittest

import hintdesk as H
from conftest import EXAMPLES


def load(name):
    return H.Library.from_file(os.path.join(EXAMPLES, name))


class PromiseTests(unittest.TestCase):
    def setUp(self):
        self.lib = load("lantern.json")
        self.eng = H.Engine(self.lib)

    def test_useful_pickup_strictly_shrinks_answers(self):
        before = self.eng.candidate_answers(())
        step = self.eng.decide(self.lib.truth, (), "现场可能是谁？")
        self.assertEqual(step.status, "spent")
        after = self.eng.candidate_answers((step,))
        self.assertLess(len(after), len(before))
        self.assertGreaterEqual(len(after), 2)

    def test_no_delivery_over_reveals_at_start(self):
        # no drawable first pickup may leave fewer than two answers
        for q in self.eng.question_texts():
            step = self.eng.decide(self.lib.truth, (), q)
            if step.delivered_flag:
                self.assertGreaterEqual(
                    len(self.eng.candidate_answers((step,))), 2)

    def test_distinct_answers_never_pooled(self):
        # 5 victim answers x 4 rooms x 6 weapons = 120 distinct answers
        self.assertEqual(len(self.eng.candidate_answers(())), 120)

    def test_alias_does_not_inflate_count(self):
        lib = self.lib
        # alice and alice_l are aliases: only 5 victim answers, not 6
        self.assertEqual(len(lib.fields["victim"].answers), 5)


class ReissueTests(unittest.TestCase):
    def setUp(self):
        self.lib = load("final_asym.json")
        self.eng = H.Engine(self.lib)

    def test_final_page_reissued_when_it_would_over_reveal(self):
        truth = self.lib.truth
        r = H.Receipt.build(ticket="t", library=self.lib.digest,
                            puzzle_id=self.lib.puzzle_id, transcript=())
        for q in ("Q0 split a or bcd", "Q2 split a,b or cde"):
            s = self.eng.decide(truth, r.transcript, q)
            self.assertEqual(s.status, "spent")
            r = r.extend(s)
        # at this point only c,d remain; QF primary 'de' would leave {d} -> over-reveal
        qf = self.eng.decide(truth, r.transcript, "QF final de")
        self.assertEqual(qf.status, "answered")
        self.assertEqual(qf.reason, "substituted")
        self.assertEqual(qf.delivered[0].text, "k in cd")
        after = self.eng.candidate_answers(r.transcript + (qf,))
        self.assertGreaterEqual(len(after), 2)


class MaterialTests(unittest.TestCase):
    def test_withheld_material_forces_substitute(self):
        lib = load("final_asym.json")
        pa = H.content_of(lib, "p_def")
        eng = H.Engine(lib, material=[pa])
        step = eng.decide(lib.truth, (), "Q1 split b or def")
        # p_def withheld; substitute pages available -> answered/substituted
        self.assertEqual(step.status, "answered")
        self.assertEqual(step.reason, "substituted")
        self.assertNotEqual(step.content, pa)

    def test_withheld_without_substitute_is_no_material(self):
        lib = load("final_asym.json")
        # withhold every page Q1 could draw (primaries and its substitute)
        contents = {H.content_of(lib, pid) for pid in
                    ["p_def", "p_a", "p_b", "p_c", "p_de"]}
        eng = H.Engine(lib, material=contents)
        step = eng.decide(lib.truth, (), "Q1 split b or def")
        self.assertEqual(step.status, "withdrawn")
        self.assertEqual(step.reason, "no_material")
        self.assertEqual(step.delivered, ())


class MenuAndStatusTests(unittest.TestCase):
    def setUp(self):
        self.lib = load("lantern.json")
        self.eng = H.Engine(self.lib)

    def test_menu_offers_match_drawable_contents(self):
        for row in self.eng.menu(()):
            self.assertGreater(len(row["offered"]), 0)
            self.assertEqual(row["status"], "live")

    def test_second_pickup_refused_after_two(self):
        truth = self.lib.truth
        r = ()
        for _ in range(2):
            menu = [m for m in self.eng.menu(r)
                    if m["kind"] == "pickup" and m["status"] == "live"]
            s = self.eng.decide(truth, r, menu[0]["text"])
            r = r + (s,)
        # a third distinct pickup must be refused
        s = self.eng.decide(truth, r, "少了什么物件？")
        self.assertEqual(s.status, "refused")
        self.assertEqual(s.reason, "no_more_pickups")

    def test_final_locked_before_two_pickups(self):
        s = self.eng.decide(self.lib.truth, (), "最后核对灯的位置。")
        self.assertEqual(s.status, "refused")
        self.assertEqual(s.reason, "need_two_pickups")

    def test_reusing_same_question_refused(self):
        truth = self.lib.truth
        s = self.eng.decide(truth, (), "现场可能是谁？")
        s2 = self.eng.decide(truth, (s,), "现场可能是谁？")
        self.assertEqual(s2.status, "refused")
        self.assertEqual(s2.reason, "already_used")


if __name__ == "__main__":
    unittest.main()
