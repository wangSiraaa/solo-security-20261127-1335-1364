"""Acceptance scenarios: the two mandated rounds, refusals, alias-merge, and
the final-page asymmetry.  These encode the reviewer's checklist directly.
"""

import copy
import json
import os
import unittest

import hintdesk as H
from conftest import EXAMPLES


def load(name):
    return H.Library.from_file(os.path.join(EXAMPLES, name))


def data(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as f:
        return json.load(f)


class TwoEffectivePickupsBothRoutes(unittest.TestCase):
    """Both routes must complete two effective pickups for a normal round."""

    def _run(self, route):
        lib = load("lantern.json")
        cur = H.Curator(lib)
        out = cur.run_round(route, choose=lambda r, m: m[0]["text"])
        return out

    def test_booklet_two_effective_pickups(self):
        out = self._run("booklet")
        self.assertTrue(out.ok, out.reason)
        self.assertEqual(len(out.pickups), 2)
        for p in out.pickups:
            self.assertTrue(p.useful)
            self.assertTrue(p.safe)
            self.assertGreaterEqual(len(p.eliminated), 1)
        self.assertGreaterEqual(len(out.pickups[-1].survivors), 2)

    def test_office_two_effective_pickups(self):
        out = self._run("office")
        self.assertTrue(out.ok, out.reason)
        self.assertEqual(len(out.pickups), 2)
        for p in out.pickups:
            self.assertTrue(p.useful)
            self.assertTrue(p.safe)
        self.assertGreaterEqual(len(out.pickups[-1].survivors), 2)

    def test_each_pickup_eliminates_a_prior_answer(self):
        lib = load("lantern.json")
        prev = H.Engine(lib).candidate_answers(())
        out = H.Curator(lib).run_round("booklet",
                                       choose=lambda r, m: m[0]["text"])
        survivors = prev
        for p in out.pickups:
            self.assertLess(len(p.survivors), len(survivors))
            survivors = set(p.survivors)


class UnservableRoundHasReviewableReason(unittest.TestCase):
    """A round that cannot be fulfilled must yield a reviewable reason."""

    def test_material_loss_makes_round_unfulfillable(self):
        lib = load("chain5.json")
        # both useful pickups need Q1's pages; withhold them all
        contents = {H.content_of(lib, pid) for pid in ["p_a", "p_b", "p_cde"]}
        cur = H.Curator(lib, material=list(contents))
        rep = cur.admit()
        self.assertFalse(rep.two_useful)
        self.assertEqual(rep.reason, "no_two_useful_pickups")
        for route in ("office", "booklet"):
            out = cur.run_round(route)
            self.assertFalse(out.ok)
            self.assertEqual(out.reason, "no_two_useful_pickups")

    def test_too_few_answers_admission_refusal(self):
        d = {
            "puzzle_id": "only-two",
            "fields": {"k": {"literals": ["a", "b"]}},
            "truth": {"k": "a"},
            "statements": [
                {"key": "eq_a", "kind": "eq", "var": "k", "value": "a", "text": "a"},
                {"key": "eq_b", "kind": "eq", "var": "k", "value": "b", "text": "b"},
            ],
            "pages": {"p_a": {"text": "A", "give": ["eq_a"]},
                      "p_b": {"text": "B", "give": ["eq_b"]}},
            "questions": [{"key": "q", "text": "q?", "responses": [
                {"when": {"k": "a"}, "page": "p_a"},
                {"when": {"k": "b"}, "page": "p_b"}]}],
            "final_questions": [],
        }
        lib = H.Library.from_dict(d)
        rep = H.Curator(lib).admit()
        self.assertFalse(rep.two_useful)
        self.assertEqual(rep.reason, "too_few_answers")

    def test_round_failure_reason_is_reported(self):
        # A library where the only question over-reveals cannot complete.
        d = {
            "puzzle_id": "overreveal",
            "fields": {"k": {"literals": ["a", "b"]}},
            "truth": {"k": "a"},
            "statements": [
                {"key": "eq_a", "kind": "eq", "var": "k", "value": "a", "text": "a"},
                {"key": "eq_b", "kind": "eq", "var": "k", "value": "b", "text": "b"},
                {"key": "in_ab", "kind": "or", "var": "k",
                 "values": ["a", "b"], "text": "ab"},
            ],
            "pages": {
                "p_a": {"text": "A", "give": ["eq_a"]},
                "p_b": {"text": "B", "give": ["eq_b"]},
                "p_ab": {"text": "AB", "give": ["in_ab"]},
            },
            "questions": [
                {"key": "q_over", "text": "over?", "responses": [
                    {"when": {"k": "a"}, "page": "p_a"},
                    {"when": {"k": "b"}, "page": "p_b"}]},
                {"key": "q_safe", "text": "safe?", "responses": [], "default": "p_ab"},
            ],
            "final_questions": [],
        }
        lib = H.Library.from_dict(d)
        cur = H.Curator(lib)
        # The over-revealing question is never drawable (over_reveal).
        out = cur.run_round("office",
                            choose=lambda r, m: "over?" if any(
                                x["text"] == "over?" for x in m) else m[0]["text"])
        # 'over?' is withdrawn at menu level, so it's not in the live menu;
        # the round instead draws q_safe, which is useless -> reviewable reason.
        self.assertFalse(out.ok)
        self.assertIn(out.reason,
                      ("pickup_not_useful", "pickup_over_reveal", "menu_exhausted"))
        self.assertTrue(out.detail)


class AliasMergeBreaksPromise(unittest.TestCase):
    """Merging two aliases into one answer must break the two-useful promise."""

    def test_baseline_admits_but_merged_does_not(self):
        base = data("chain5.json")
        lib_base = H.Library.from_dict(base)
        self.assertTrue(H.Curator(lib_base).admit().two_useful)

        merged = copy.deepcopy(base)
        merged["fields"]["k"]["aliases"] = [["d", "e"]]
        merged["truth"] = {"k": "d"}
        lib_merged = H.Library.from_dict(merged)
        # five answers -> four after the merge
        self.assertEqual(len(lib_merged.fields["k"].answers), 4)
        rep = H.Curator(lib_merged).admit()
        self.assertFalse(rep.two_useful)
        self.assertEqual(rep.reason, "no_two_useful_pickups")

    def test_merged_answers_collapse_in_count(self):
        base = data("chain5.json")
        lib_base = H.Library.from_dict(base)
        eng_base = H.Engine(lib_base)
        merged = copy.deepcopy(base)
        merged["fields"]["k"]["aliases"] = [["d", "e"]]
        merged["truth"] = {"k": "d"}
        lib_merged = H.Library.from_dict(merged)
        eng_merged = H.Engine(lib_merged)
        self.assertEqual(len(eng_base.candidate_answers(())), 5)
        self.assertEqual(len(eng_merged.candidate_answers(())), 4)


class FinalPageAsymmetry(unittest.TestCase):
    """Same final page: usable on one ticket's history, over-revealing on another."""

    def _drive(self, lib, choices):
        eng = H.Engine(lib)
        r = H.Receipt.build(ticket="t", library=lib.digest,
                            puzzle_id=lib.puzzle_id, transcript=())
        for w in choices:
            s = eng.decide(lib.truth, r.transcript, w)
            r = r.extend(s)
        return eng, r

    def test_same_final_page_ok_on_one_ticket_over_reveals_on_other(self):
        lib = load("final_asym.json")
        # Both tickets reach the point where the final question's *primary* page
        # is the same content ('de').  Ticket A can take it; ticket B cannot.
        eng = H.Engine(lib)
        primary_final = H.content_of(lib, eng.question("QF final de").select(lib.truth))
        self.assertEqual(primary_final, H.content_of(lib, "p_de"))

        # Ticket A: Q1 -> {d,e,f}; final 'de' leaves {d,e} = 2, safely delivered.
        engA, rA = self._drive(lib, ["Q1 split b or def", "Q2 split a,b or cde"])
        fA = engA.decide(lib.truth, rA.transcript, "QF final de")
        self.assertEqual(fA.status, "spent")
        self.assertEqual(fA.content, primary_final)          # the primary page
        self.assertGreaterEqual(
            len(engA.candidate_answers(rA.transcript + (fA,))), 2)

        # Ticket B: Q0 -> {b,c,d}; the SAME primary 'de' would leave {d} = 1 ->
        # over-reveal, so it is re-issued as the substitute 'cd' ({c,d}).
        engB, rB = self._drive(lib, ["Q0 split a or bcd", "Q2 split a,b or cde"])
        fB = engB.decide(lib.truth, rB.transcript, "QF final de")
        self.assertEqual(fB.status, "answered")
        self.assertNotEqual(fB.content, primary_final)       # substitute, not primary
        self.assertEqual(fB.delivered[0].text, "k in cd")
        self.assertGreaterEqual(
            len(engB.candidate_answers(rB.transcript + (fB,))), 2)

    def test_both_stations_can_retrieve_what_they_handed_over(self):
        lib = load("final_asym.json")
        office = H.Station(H.OfficeRouteAdapter(H.InnerOffice(lib)))
        book = H.Station(H.BookletRouteAdapter(H.Booklet.compile(lib), lib.truth))
        r = office.open("t")
        for w in ["Q0 split a or bcd", "Q2 split a,b or cde"]:
            r, _, _ = office.ask(r, w)
        fB = book.route.serve(book.receive(r), "QF final de")
        # the booklet hands over the same substitute the office would
        office_f = office.route.serve(r, "QF final de")
        self.assertEqual(fB.to_json(), office_f.to_json())
        self.assertEqual(fB.status, "answered")
        self.assertEqual(fB.delivered[0].text, "k in cd")

        # each station can retrieve the pages it *actually* handed over
        book.ask(book.receive(r), "QF final de")   # route through the station
        lab = office.handed_over("t")
        lab2 = book.handed_over("t")
        self.assertEqual([e["wording"] for e in lab],
                         ["Q0 split a or bcd", "Q2 split a,b or cde"])
        self.assertEqual([e["wording"] for e in lab2], ["QF final de"])
        self.assertEqual(lab2[0]["delivered"][0]["text"], "k in cd")


class FirstQuestionThenChooseSecond(unittest.TestCase):
    """Same opening question, different pages per truth; choose 2nd after menu."""

    def test_same_first_question_different_pages_then_choose(self):
        lib = load("lantern.json")
        ver = H.Verifier(lib)
        # same opening question on several truths -> different delivered content
        q = "现场可能是谁？"
        for victim in lib.fields["victim"].answers:
            truth = dict(lib.truth, victim=victim)
            eng = H.Engine(lib)
            s = eng.decide(truth, (), q)
            self.assertEqual(s.status, "spent")
            self.assertIsNotNone(s.content)
        # contents differ across truths (distinct victims draw distinct pages)
        contents = set()
        for victim in lib.fields["victim"].answers:
            eng = H.Engine(lib)
            s = eng.decide(dict(lib.truth, victim=victim), (), q)
            contents.add(s.content)
        self.assertEqual(len(contents), 5)

    def test_second_question_chosen_after_seeing_menu(self):
        lib = load("lantern.json")
        eng = H.Engine(lib)
        r = H.Receipt.build(ticket="t", library=lib.digest,
                            puzzle_id=lib.puzzle_id, transcript=())
        s1 = eng.decide(lib.truth, r.transcript, "现场可能是谁？")
        r = r.extend(s1)
        menu = [m for m in eng.menu(r.transcript)
                if m["kind"] == "pickup" and m["status"] == "live"]
        # pick the second question only after seeing the live menu
        self.assertTrue(menu)
        second = menu[0]["text"]
        s2 = eng.decide(lib.truth, r.transcript, second)
        self.assertEqual(s2.status, "spent")
        self.assertGreaterEqual(
            len(eng.candidate_answers(r.transcript + (s2,))), 2)


if __name__ == "__main__":
    unittest.main()
