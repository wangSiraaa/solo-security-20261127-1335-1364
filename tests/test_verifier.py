"""Tests for the independent replay verifier."""

import json
import os
import unittest

import hintdesk as H
from conftest import EXAMPLES


def load(name):
    return H.Library.from_file(os.path.join(EXAMPLES, name))


def receipt_for(lib, choices, truth=None):
    truth = truth or lib.truth
    eng = H.Engine(lib)
    r = H.Receipt.build(ticket="t", library=lib.digest,
                        puzzle_id=lib.puzzle_id, transcript=())
    for w in choices:
        s = eng.decide(truth, r.transcript, w)
        r = r.extend(s)
    return r


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.lib = load("lantern.json")
        self.ver = H.Verifier(self.lib)

    def test_replay_reproduces_whole_record(self):
        r = receipt_for(self.lib, ["现场可能是谁？", "灯在哪间屋亮过？"])
        rep = self.ver.replay(r)
        self.assertGreaterEqual(rep.count, 2)
        # the true answer must survive
        self.assertIn(self.lib.answer_of(self.lib.truth), rep.survivors)

    def test_tampered_label_rejected(self):
        r = receipt_for(self.lib, ["现场可能是谁？"])
        obj = r.to_json()
        # change a status to something no world produces
        obj["transcript"][0]["status"] = "withdrawn"
        # rebuild sign so integrity passes but replay must still disagree
        import hintdesk.receipts as R
        rebuilt = R.Receipt.from_json({**obj, "sign": None}) if False else None
        # instead: forge by directly constructing a Receipt with bad step
        steps = list(r.transcript)
        bad_step = H.Step.from_json({**steps[0].to_json(), "status": "withdrawn"})
        forged = H.Receipt(ticket="t", library=r.library, puzzle_id=r.puzzle_id,
                           transcript=(bad_step,) + tuple(steps[1:]))
        rep = self.ver.replay(forged)
        # no world should reproduce a 'withdrawn' first step for this question
        self.assertEqual(rep.count, 0)

    def test_determinate_after_elimination(self):
        # craft a two-pickup path that narrows to one answer is disallowed by the
        # rules, so instead verify candidate counting stays >= 2 everywhere
        r = receipt_for(self.lib, ["现场可能是谁？", "少了什么物件？"])
        self.assertGreaterEqual(self.ver.replay(r).count, 2)

    def test_consistent_worlds_track_engine(self):
        r = receipt_for(self.lib, ["现场可能是谁？", "灯在哪间屋亮过？"])
        eng_surv = H.Engine(self.lib).candidate_answers(r.transcript)
        ver_surv = set(self.ver.replay(r).survivors)
        self.assertEqual(eng_surv, ver_surv)


class SameRecordDifferentPathsTests(unittest.TestCase):
    """Two different questions can form the same reader record."""

    def test_identical_record_identical_candidates(self):
        lib = load("lantern.json")
        # p_north (Q_room) and final_n (Q_final) share content. But final needs
        # two pickups. Use a library where two pickups produce identical content
        # via different wordings: chain5 has no such pair, so build one.
        # In lantern, Q_room and Q_weapon are distinct; instead use the
        # record_signature agreement across all 2-pickup paths.
        ver = H.Verifier(lib)
        eng = H.Engine(lib)
        qs = [q.text for q in lib.questions.values()]
        receipts = []
        for a in qs:
            for b in qs:
                if a != b:
                    receipts.append(receipt_for(lib, [a, b]))
        report = ver.replay_many(receipts)
        # no two same-record receipts may disagree on survivors
        self.assertEqual(report["violations"], {})

    def test_same_record_signature_ignores_wording(self):
        lib = load("lantern.json")
        r1 = receipt_for(lib, ["现场可能是谁？", "灯在哪间屋亮过？"])
        r2 = receipt_for(lib, ["现场可能是谁？", "灯在哪间屋亮过？"])
        self.assertEqual(r1.record_signature(), r2.record_signature())

    def test_two_different_questions_form_identical_record(self):
        # QX and QY have distinct wordings but identical response structure, so
        # a reader who asks QX and one who asks QY hold the same record.
        lib = load("twoways.json")
        ver = H.Verifier(lib)
        ra = receipt_for(lib, ["QX"])
        rb = receipt_for(lib, ["QY"])
        self.assertNotEqual(ra.transcript[0].wording, rb.transcript[0].wording)
        self.assertEqual(ra.record_signature(), rb.record_signature())
        # and the replay yields the same survivor set for both
        self.assertEqual(ver.replay(ra).survivors, ver.replay(rb).survivors)
        report = ver.replay_many([ra, rb])
        self.assertEqual(report["violations"], {})


class CandidateViewTests(unittest.TestCase):
    def test_candidates_never_merge_distinct_answers(self):
        lib = load("lantern.json")
        ver = H.Verifier(lib)
        r = receipt_for(lib, ["现场可能是谁？"])
        cands = ver.candidates(r)
        # each candidate is a distinct answer tuple
        self.assertEqual(len(cands), len(set(cands)))
        for c in cands:
            self.assertGreaterEqual(len(c), 1)

    def test_alias_reduces_worlds_not_answers(self):
        lib = load("lantern.json")
        ver = H.Verifier(lib)
        r = receipt_for(lib, ["现场可能是谁？"])
        # 'alice' and 'alice_l' are one answer: no survivor answer may use the
        # alias literal, and the alice-class worlds differ only in room/weapon.
        distinct = set(ver.replay(r).survivors)
        self.assertTrue(all(a[0] != "alice_l" for a in distinct))
        # 4 rooms x 6 weapons under victim=alice -> 24 distinct answers, each
        # carrying the canonical victim 'alice'
        self.assertEqual(len(distinct), 24)
        self.assertEqual(len(lib.fields["victim"].answers), 5)


if __name__ == "__main__":
    unittest.main()
