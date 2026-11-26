"""Tests for the append-only ledger and public-receipt semantics."""

import pytest

from privacy_desk.ledger import Ledger
from privacy_desk.model import Constraint, PrivacyError


def test_append_only_no_delete_api():
    led = Ledger()
    assert not hasattr(led, "delete")
    assert not hasattr(led, "remove")
    assert not hasattr(led, "retract")
    assert not hasattr(led, "replace")
    assert not hasattr(led, "clear")


def test_record_and_query():
    led = Ledger()
    led.record(week="W", group="北翼", constraint=Constraint.exact(("甲", "乙"), 1), origin="published")
    led.record(week="W", group="整层", constraint=Constraint.range(("甲", "乙", "丙"), 1, 2), origin="mailed")
    assert len(led) == 2
    assert len(led.for_week("W")) == 2
    assert len(led.constraints_for_week("W")) == 2
    assert led.exact_receipt_for_group("W", "北翼").value == 1
    assert led.exact_receipt_for_group("W", "整层") is None


def test_duplicate_id_rejected():
    led = Ledger()
    led.record(week="W", group="北翼", constraint=Constraint.exact(("甲", "乙"), 1),
               origin="published", receipt_id="X")
    with pytest.raises(PrivacyError):
        led.record(week="W", group="北翼", constraint=Constraint.exact(("甲", "乙"), 1),
                   origin="published", receipt_id="X")


def test_correction_cannot_remove_earlier_number():
    """A same-week correction appends; the earlier mailed number stays public."""
    led = Ledger()
    first = led.record(week="W", group="整层",
                       constraint=Constraint.exact(("甲", "乙", "丙"), 1), origin="mailed")
    led.record(week="W", group="整层",
               constraint=Constraint.range(("甲", "乙", "丙"), 0, 3), origin="correction")
    # both receipts are present; the exact one never disappeared
    kinds = [r.constraint.kind for r in led.for_week("W")]
    assert kinds == ["exact", "range"]
    assert led.get(first.receipt_id).value == 1


def test_audit_notes_do_not_affect_receipts():
    led = Ledger()
    led.record(week="W", group="北翼", constraint=Constraint.exact(("甲", "乙"), 1), origin="published")
    led.note_audit({"finding": "leak"})
    assert len(led) == 1
    assert len(led.audit_log()) == 1


def test_unknown_origin_rejected():
    led = Ledger()
    with pytest.raises(PrivacyError):
        led.record(week="W", group="北翼", constraint=Constraint.exact(("甲", "乙"), 1), origin="nope")


def test_signature_present_and_stable():
    led = Ledger()
    r = led.record(week="W", group="北翼", constraint=Constraint.exact(("甲", "乙"), 1),
                   origin="published")
    assert r.signature and len(r.signature) == 16
    assert r.bookable is True
