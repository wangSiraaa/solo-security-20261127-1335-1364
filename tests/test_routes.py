"""Tests for the two routes: booklet (A) vs inner office (B), and stations."""

import json
import os
import unittest

import hintdesk as H
from conftest import EXAMPLES


def load(name):
    return H.Library.from_file(os.path.join(EXAMPLES, name))


class RouteEquivalenceTests(unittest.TestCase):
    """Both routes must emit byte-identical receipts for the same choices."""

    def _drive(self, station, choices):
        r = station.open("t")
        for w in choices:
            r, step, note = station.ask(r, w)
        return r

    def test_booklet_and_office_agree_lantern(self):
        lib = load("lantern.json")
        choices = ["现场可能是谁？", "灯在哪间屋亮过？"]
        office = H.Station(H.OfficeRouteAdapter(H.InnerOffice(lib)))
        booklet = H.Booklet.compile(lib)
        book = H.Station(H.BookletRouteAdapter(booklet, lib.truth))
        ro = self._drive(office, choices)
        rb = self._drive(book, choices)
        self.assertEqual(ro.canonical(), rb.canonical())

    def test_booklet_and_office_agree_all_paths(self):
        # exhaustively compare every 2-question path on both routes
        lib = load("lantern.json")
        qs = [q.text for q in lib.questions.values()]
        office_route = H.OfficeRouteAdapter(H.InnerOffice(lib))
        book_route = H.BookletRouteAdapter(H.Booklet.compile(lib), lib.truth)
        for a in qs:
            for b in qs:
                if a == b:
                    continue
                ro = self._drive(H.Station(office_route), [a, b])
                rb = self._drive(H.Station(book_route), [a, b])
                self.assertEqual(ro.canonical(), rb.canonical(),
                                 msg=f"diverged on {a!r} then {b!r}")

    def test_booklet_handles_withheld_material_like_office(self):
        lib = load("final_asym.json")
        material = [H.content_of(lib, "p_def")]
        office = H.Station(H.OfficeRouteAdapter(
            H.InnerOffice(lib, withheld=material)))
        book = H.Station(H.BookletRouteAdapter(
            H.Booklet.compile(lib, material=material), lib.truth))
        w = "Q1 split b or def"
        ro = self._drive(office, [w])
        rb = self._drive(book, [w])
        self.assertEqual(ro.canonical(), rb.canonical())

    def test_routes_agree_on_refusals_and_ordering(self):
        lib = load("lantern.json")
        final = "最后核对灯的位置。"
        office = H.Station(H.OfficeRouteAdapter(H.InnerOffice(lib)))
        book = H.Station(H.BookletRouteAdapter(H.Booklet.compile(lib), lib.truth))
        # ask the final question before two pickups -> refused by both
        fo = office.route.serve(office.open("t"), final)
        fb = book.route.serve(book.open("t"), final)
        self.assertEqual(fo.to_json(), fb.to_json())
        self.assertEqual(fo.status, "refused")
        self.assertEqual(fo.reason, "need_two_pickups")
        # re-using a question -> refused by both
        r = office.open("t")
        r, _, _ = office.ask(r, "现场可能是谁？")
        re = office.route.serve(r, "现场可能是谁？")
        rb = book.route.serve(book.receive(r), "现场可能是谁？")
        self.assertEqual(re.to_json(), rb.to_json())
        self.assertEqual(re.reason, "already_used")


class CrossStationTests(unittest.TestCase):
    def test_continue_from_receipt_at_other_route(self):
        lib = load("lantern.json")
        st1 = H.Station(H.OfficeRouteAdapter(H.InnerOffice(lib)))
        r = st1.open("ticket-7")
        r, _, _ = st1.ask(r, "现场可能是谁？")
        # carry the receipt as pure JSON to a booklet station
        carried = H.Receipt.from_json(json.loads(json.dumps(r.to_json())))
        st2 = H.Station(H.BookletRouteAdapter(H.Booklet.compile(lib), lib.truth))
        r2, step, _ = st2.ask(st2.receive(carried), "灯在哪间屋亮过？")
        self.assertIsNotNone(step)
        self.assertEqual(len(H.Engine(lib).candidate_answers(r2.transcript)), 6)

    def test_receipt_integrity_enforced_on_receive(self):
        lib = load("lantern.json")
        st = H.Station(H.OfficeRouteAdapter(H.InnerOffice(lib)))
        r, _, _ = st.ask(st.open("t"), "现场可能是谁？")
        bad = r.to_json()
        bad["transcript"][0]["delivered"][0]["text"] = "tampered"
        with self.assertRaises(H.ReceiptError):
            H.Receipt.from_json(bad)

    def test_wrong_library_receipt_rejected(self):
        lib = load("lantern.json")
        other = load("chain5.json")
        st = H.Station(H.OfficeRouteAdapter(H.InnerOffice(other)))
        r = H.Receipt.build(ticket="t", library=lib.digest,
                            puzzle_id=lib.puzzle_id, transcript=())
        with self.assertRaises(H.CannotFulfil):
            st.receive(r)

    def test_receipt_with_refused_step_continues_at_other_route(self):
        lib = load("lantern.json")
        office = H.Station(H.OfficeRouteAdapter(H.InnerOffice(lib)))
        r = office.open("t")
        r, step, note = office.ask(r, "最后核对灯的位置。")   # refused early
        self.assertIsNone(step)
        r, step, _ = office.ask(r, "现场可能是谁？")
        self.assertEqual(step.status, "spent")
        book = H.Station(H.BookletRouteAdapter(H.Booklet.compile(lib), lib.truth))
        carried = H.Receipt.from_json(json.loads(json.dumps(r.to_json())))
        r2, step2, _ = book.ask(book.receive(carried), "灯在哪间屋亮过？")
        self.assertIsNotNone(step2)
        self.assertEqual(len(H.Engine(lib).candidate_answers(r2.transcript)), 6)


class NoInventionTests(unittest.TestCase):
    def test_booklet_refuses_unknown_state(self):
        lib = load("lantern.json")
        other = load("chain5.json")
        booklet = H.Booklet.compile(lib)
        # craft a valid receipt from another library
        r = H.Receipt.build(ticket="t", library=other.digest,
                            puzzle_id=other.puzzle_id, transcript=())
        with self.assertRaises(H.CannotFulfil) as ctx:
            booklet.menu(r)
        self.assertEqual(ctx.exception.reason, "wrong_library")

    def test_booklet_holds_no_engine(self):
        lib = load("lantern.json")
        booklet = H.Booklet.compile(lib)
        self.assertFalse(hasattr(booklet, "engine"))
        self.assertFalse(hasattr(booklet, "_engine"))


if __name__ == "__main__":
    unittest.main()
